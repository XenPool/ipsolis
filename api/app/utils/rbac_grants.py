"""Per-asset-type ACL helpers — RBAC slice 2.

The grant table is opt-in scoping: a user with zero grants sees every
asset type (back-compat for single-team installs); attaching even one
grant flips them into "see only the granted ones" mode. The flip is
deliberate — a hard "no grants = see nothing" default would silently
hide every asset type from upgraded admins on day-one.

Visibility rules:

* ``superadmin`` → everything, always.
* ``admin`` with no grants → everything (back-compat).
* ``admin`` with ≥1 grant → only the granted ``asset_type_id`` set.
* ``approver`` / ``auditor`` / ``helpdesk`` → everything (read-only
  roles aren't scoped in slice 2 — their privileges are bounded by
  role gates).
* Bearer tokens → everything (token authz is by scope, not user).
* Legacy ``X-Admin-Key`` → everything (virtual superadmin).

Returns:

* ``visible_asset_type_ids(request, db)`` → ``None`` to mean
  "unrestricted" (no filter applied) or ``set[int]`` of allowed ids.
  Callers do ``if visible is not None: query = query.where(... .in_(visible))``.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import AdminUser
from app.models.admin_user_grant import AdminUserAssetTypeGrant


# Roles that participate in scoping. Adding a role here makes it
# subject to the "no grants = see all, ≥1 grant = see only those" rule.
# Slice-2 ships with admin only.
_SCOPED_ROLES = frozenset({"admin"})


async def visible_asset_type_ids(
    request: Request,
    db: AsyncSession,
) -> set[int] | None:
    """Return ``None`` (unrestricted) or the set of asset_type ids the actor sees."""
    actor = getattr(request.state, "actor", "") or ""

    # Bypass paths — same shape as ``require_role``'s skip rules.
    if not actor.startswith("admin:session:"):
        # Legacy key, bearer tokens, anonymous portal — no per-asset-type scoping.
        return None

    role = (request.session.get("admin_role") or "").strip()
    if role not in _SCOPED_ROLES:
        # Superadmin / approver / auditor / helpdesk → everything.
        return None

    username = (request.session.get("admin_user") or "").strip().lower()
    if not username:
        return set()  # malformed session — fail closed

    # Look up the user id (may have been deleted mid-session — fail closed).
    user_row = await db.execute(
        select(AdminUser.id).where(AdminUser.username == username, AdminUser.is_active.is_(True))
    )
    user_id = user_row.scalar_one_or_none()
    if user_id is None:
        return set()

    grants = await db.execute(
        select(AdminUserAssetTypeGrant.asset_type_id).where(
            AdminUserAssetTypeGrant.admin_user_id == user_id,
        )
    )
    granted_ids = {int(r) for r in grants.scalars().all()}

    # No grants → back-compat "see all".
    if not granted_ids:
        return None
    return granted_ids


async def assert_asset_type_visible(
    request: "Request",
    db: AsyncSession,
    asset_type_id: int,
) -> None:
    """Raise HTTP 404 when the actor isn't allowed to see ``asset_type_id``.

    404 (rather than 403) avoids leaking the existence of asset types
    the user has no business knowing about — a scoped admin asking
    for an out-of-scope id gets the same response as for a missing id.
    """
    from fastapi import HTTPException, status  # local import — avoids module cycle for callers
    visible = await visible_asset_type_ids(request, db)
    if visible is None:
        return
    if asset_type_id in visible:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Asset type {asset_type_id} not found",
    )
