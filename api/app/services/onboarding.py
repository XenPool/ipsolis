"""Onboarding rule-evaluation service (pure, order-path-independent).

Matches a user-attribute dict against ``AssignmentRule`` conditions (reusing the
approval-rule ``_eval_condition`` tree matcher) to find target ``Bundle``s, then
resolves each bundle's positions into the concrete items that *would* be ordered
— applying the idempotency rule: never order an asset type the user already has
an **active** (non-revoked / non-expired) order for.

This module does **not** create orders. Ordering a resolved bundle is a separate
step (see the onboarding order service) so evaluation can be previewed safely.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assignment_rule import AssignmentRule
from app.models.asset import AssetType
from app.models.bundle import Bundle, BundlePosition
from app.utils.approval_rules import _eval_condition

# Order statuses that count as the user already "having" an asset type — used
# for the idempotency skip. Mirrors the active set used elsewhere.
_ACTIVE_STATUSES = (
    "pending", "pending_approval", "scheduled", "processing",
    "provisioning", "provisioned", "delivered",
)


def build_user_context(attrs: dict[str, Any]) -> dict[str, Any]:
    """Map an AD-resolved attribute dict to the ``attr.*`` keys the condition
    evaluator expects (the built-in allowlist only permits ``attr.*`` for
    arbitrary fields). Keys are lower-cased attribute names.
    """
    ctx: dict[str, Any] = {}
    for key in ("department", "cost_center", "company", "employee_id", "title",
                "sam_account", "email", "display_name"):
        val = attrs.get(key)
        if val is not None:
            ctx[f"attr.{key}"] = val
    return ctx


async def _active_asset_type_ids(db: AsyncSession, user_email: str) -> set[int]:
    if not user_email:
        return set()
    rows = (await db.execute(text(
        "SELECT DISTINCT asset_type_id FROM orders "
        "WHERE lower(user_email) = lower(:e) AND status::text = ANY(:st)"
    ), {"e": user_email, "st": list(_ACTIVE_STATUSES)})).all()
    return {int(r[0]) for r in rows}


async def evaluate_assignment_rules(
    db: AsyncSession, context: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return the bundles whose rules match ``context``, deduped, priority-ordered.

    Each entry: ``{bundle_id, bundle_name, matched_rules: [names]}``.
    """
    rules = (await db.execute(
        select(AssignmentRule)
        .where(AssignmentRule.is_active.is_(True))
        .order_by(AssignmentRule.priority, AssignmentRule.id)
    )).scalars().all()

    by_bundle: dict[int, dict[str, Any]] = {}
    for rule in rules:
        try:
            matched = _eval_condition(rule.condition or {"op": "and", "clauses": []}, context)
        except Exception:  # noqa: BLE001 — a bad rule must never break evaluation
            matched = False
        if not matched:
            continue
        entry = by_bundle.setdefault(rule.bundle_id, {
            "bundle_id": rule.bundle_id, "bundle_name": None, "matched_rules": [],
        })
        entry["matched_rules"].append(rule.name)

    if not by_bundle:
        return []
    # Fill in bundle names (active only).
    bundles = (await db.execute(
        select(Bundle).where(Bundle.id.in_(list(by_bundle.keys())))
    )).scalars().all()
    name_active = {b.id: (b.name, b.is_active) for b in bundles}
    out = []
    for bid, entry in by_bundle.items():
        na = name_active.get(bid)
        if not na or not na[1]:  # bundle gone or inactive → drop
            continue
        entry["bundle_name"] = na[0]
        out.append(entry)
    return out


async def resolve_bundle_items(
    db: AsyncSession, bundle_id: int, user_email: str
) -> dict[str, Any]:
    """Resolve a bundle's positions into concrete would-be-ordered items.

    Returns ``{bundle_id, bundle_name, items: [...]}`` where each item carries
    the asset type + required flag + a ``skip`` reason when the user already
    holds an active order for that type (idempotency).
    """
    bundle = await db.get(Bundle, bundle_id)
    if not bundle:
        return {"bundle_id": bundle_id, "bundle_name": None, "items": []}

    positions = (await db.execute(
        select(BundlePosition)
        .where(BundlePosition.bundle_id == bundle_id)
        .order_by(BundlePosition.sort_order, BundlePosition.id)
    )).scalars().all()
    held = await _active_asset_type_ids(db, user_email)

    # Resolve asset-type names in one query.
    at_ids = [p.asset_type_id for p in positions]
    names: dict[int, tuple[str, bool]] = {}
    if at_ids:
        for at in (await db.execute(
            select(AssetType).where(AssetType.id.in_(at_ids))
        )).scalars().all():
            names[at.id] = (at.name, at.is_active)

    items = []
    for p in positions:
        nm = names.get(p.asset_type_id)
        skip = None
        if nm is None:
            skip = "asset_type_missing"
        elif not nm[1]:
            skip = "asset_type_inactive"
        elif p.asset_type_id in held:
            skip = "already_held"
        items.append({
            "position_id": p.id,
            "asset_type_id": p.asset_type_id,
            "asset_type_name": nm[0] if nm else "(unknown)",
            "required": p.required,
            "default_config": p.default_config,
            "skip": skip,          # None = would be ordered
        })
    return {"bundle_id": bundle_id, "bundle_name": bundle.name, "items": items}
