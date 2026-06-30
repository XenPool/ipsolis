"""Free-tier / commercial volume-band user-count enforcement.

Counting metric — "active user" (authoritative definition)
----------------------------------------------------------
A **user** is a distinct active end-user identity:

    a distinct (case-insensitive) ``orders.user_email`` that has at least one
    order in an *active* status.

"Active" reuses the same non-terminal status set as the per-user/pool quota
checks (``app.utils.capacity._ACTIVE_STATUSES``): ``pending``,
``pending_approval``, ``scheduled``, ``processing``, ``provisioning``,
``provisioned``, ``delivered``. Terminal / historical states
(``cancelled``, ``expired``, ``revoked``, ``rejected``, ``failed``) are **not**
counted — a user who once placed a cancelled order is not a current user.

This is the same identity the SCIM ``/Users`` endpoint exposes (distinct order
email), refined to *active* orders. Admin operators (``admin_users``) are NOT
counted — they are platform operators, not the licensed end-user population.

Limit
-----
    active users <= 25                  without a valid COMMERCIAL license (free tier)
    active users <= license.max_users   with a valid commercial band license
                                        (max_users == 0 → unlimited)

The band rides on the existing 30-day license-expiry grace: while in grace the
band still applies; once grace is exhausted the license reverts to community and
the free-tier 25 applies again. Demo / evaluation licenses never raise the limit.

Enforcement is soft: existing operation is untouched; only the creation of an
order for a **new** (not-yet-counted) identity is blocked once the active-user
count is at or above the effective limit (existing active users always pass).
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.utils.capacity import _ACTIVE_STATUSES as ACTIVE_ORDER_STATUSES
from app.utils.license import FREE_TIER_MAX_USERS, effective_user_limit, get_license_info


# ── Counting ────────────────────────────────────────────────────────────────

async def count_active_users(db: AsyncSession) -> int:
    """Distinct lowercased ``user_email`` across orders in an active status."""
    res = await db.execute(
        select(func.count(distinct(func.lower(Order.user_email))))
        .where(Order.status.in_(ACTIVE_ORDER_STATUSES))
    )
    return int(res.scalar_one() or 0)


async def is_existing_active_user(db: AsyncSession, email: str) -> bool:
    """True if ``email`` already has an active order (already counted)."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return False
    res = await db.execute(
        select(Order.id)
        .where(
            func.lower(Order.user_email) == normalized,
            Order.status.in_(ACTIVE_ORDER_STATUSES),
        )
        .limit(1)
    )
    return res.scalar_one_or_none() is not None


# ── Pure decision (DB-free, exhaustively unit-tested) ────────────────────────

def is_new_user_blocked(active_count: int, is_existing: bool, limit: int | None) -> bool:
    """Whether creating an order for this identity must be blocked.

    A NEW identity (one not already counted) is blocked once the active-user
    count has reached the limit. Existing users and unlimited tiers never block.
    """
    if limit is None:        # unlimited (commercial max_users == 0)
        return False
    if is_existing:          # already counted — adding their order is free
        return False
    return active_count >= limit


# ── Enforcement + status ─────────────────────────────────────────────────────

async def enforce_user_tier_limit(db: AsyncSession, email: str) -> None:
    """Block order creation for a new identity over the effective tier limit.

    Raises ``HTTPException(403)`` for a blocked new user. Existing active users
    and unlimited tiers pass silently.
    """
    info = get_license_info()
    limit = effective_user_limit(info)
    if limit is None:
        return
    if await is_existing_active_user(db, email):
        return
    active = await count_active_users(db)
    if not is_new_user_blocked(active, is_existing=False, limit=limit):
        return

    if info.is_commercial:
        remedy = "upgrade your ip·Solis license to a larger user band."
        tier = "licensed user band"
    else:
        remedy = (
            f"purchase a commercial ip·Solis license to serve more than "
            f"{FREE_TIER_MAX_USERS} users."
        )
        tier = "free tier"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"User limit reached for the {tier}: {active}/{limit} active users. "
            f"Adding a new user ({email}) is blocked — reduce active users below "
            f"{limit}, or {remedy}"
        ),
    )


async def tier_status(db: AsyncSession) -> dict:
    """Current tier/usage snapshot for the admin banner."""
    info = get_license_info()
    limit = effective_user_limit(info)
    active = await count_active_users(db)
    at_or_over = limit is not None and active >= limit
    # Soft "approaching" hint at >=80% of a finite limit.
    near = limit is not None and not at_or_over and active >= max(1, int(limit * 0.8))
    return {
        "active_users": active,
        "limit": limit,                       # None = unlimited
        "free_tier_limit": FREE_TIER_MAX_USERS,
        "is_commercial": info.is_commercial,
        "is_evaluation": info.is_evaluation,
        "edition": info.edition,
        "in_grace_period": info.in_grace_period,
        "at_or_over_limit": at_or_over,
        "approaching_limit": near,
        "remaining": None if limit is None else max(0, limit - active),
    }
