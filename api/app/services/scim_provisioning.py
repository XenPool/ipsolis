"""SCIM joiner/mover provisioning glue.

Maps SCIM User payloads (core + enterprise extension) to the attribute dict the
assignment-rule engine consumes, maintains the ``ScimIdentity`` projection, and
runs the **joiner**: on a new/reactivated identity (opt-in via
``scim.joiner_enabled``) it evaluates assignment rules and orders the matched
bundles for the user via the same self-contained bundle-order service the admin
and self-service paths use. Mover reconciliation (diff + revoke) is slice 2.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scim_identity import ScimIdentity

logger = logging.getLogger(__name__)

_ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


def _first_email(emails: Any) -> str | None:
    if not isinstance(emails, list):
        return None
    for e in emails:
        if isinstance(e, dict) and isinstance(e.get("value"), str):
            return e["value"]
    return None


def extract_scim_attrs(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull email + display name + rule-eval attributes from a SCIM User payload.

    Attribute mapping (core + enterprise extension → ip·Solis attr keys):
    ``department`` → department, ``costCenter`` → cost_center,
    ``employeeNumber`` → employee_id, ``organization`` → company,
    core ``title`` → title.
    """
    email = (payload.get("userName") or _first_email(payload.get("emails")) or "").strip().lower()
    ent = payload.get(_ENTERPRISE) or {}
    if not isinstance(ent, dict):
        ent = {}
    attrs = {
        "department": ent.get("department"),
        "cost_center": ent.get("costCenter"),
        "employee_id": ent.get("employeeNumber"),
        "company": ent.get("organization"),
        "title": payload.get("title"),
    }
    attrs = {k: v for k, v in attrs.items() if v not in (None, "")}
    return {
        "email": email,
        "display_name": payload.get("displayName"),
        "external_id": payload.get("externalId"),
        "active": bool(payload.get("active", True)),
        "attributes": attrs,
    }


# SCIM PATCH path suffix → ip·Solis attribute key (case-insensitive match).
_PATCH_ATTR_MAP = {
    "department": "department",
    "costcenter": "cost_center",
    "employeenumber": "employee_id",
    "organization": "company",
    "title": "title",
}


def patch_ops_to_attrs(
    old_attrs: dict[str, Any], operations: list[Any]
) -> tuple[dict[str, Any], bool]:
    """Apply SCIM PATCH replace/add ops to a copy of ``old_attrs``.

    Returns ``(new_attrs, changed)``. Handles the common IdP shapes: a
    path-targeted op (``…enterprise:2.0:User:department`` / bare ``department``
    / core ``title``) and a path-less op whose value is a (partial) User object.
    Ops we don't recognise are ignored (they simply don't move an attribute).
    """
    new = dict(old_attrs or {})
    changed = False

    def _set(attr: str, value: Any) -> None:
        nonlocal changed
        if value in (None, ""):
            return
        if new.get(attr) != value:
            new[attr] = value
            changed = True

    for op in operations or []:
        if not isinstance(op, dict):
            continue
        if (op.get("op") or "").lower() not in ("replace", "add"):
            continue
        path = op.get("path")
        value = op.get("value")
        if isinstance(path, str):
            low = path.lower()
            for suffix, attr in _PATCH_ATTR_MAP.items():
                if low == suffix or low.endswith(":" + suffix) or low.endswith("." + suffix):
                    _set(attr, value)
                    break
        elif path is None and isinstance(value, dict):
            ent = value.get(_ENTERPRISE) if isinstance(value.get(_ENTERPRISE), dict) else {}
            _set("department", ent.get("department") or value.get("department"))
            _set("cost_center", ent.get("costCenter") or value.get("costCenter"))
            _set("employee_id", ent.get("employeeNumber") or value.get("employeeNumber"))
            _set("company", ent.get("organization") or value.get("organization"))
            _set("title", value.get("title"))
    return new, changed


async def upsert_identity(
    db: AsyncSession, ext: dict[str, Any], *, raw: dict[str, Any] | None = None
) -> tuple[ScimIdentity, bool, bool, dict[str, Any]]:
    """Create/update the projection.

    Returns ``(identity, is_new, reactivated, old_attributes)`` — ``old_attributes``
    is the attribute snapshot *before* this upsert (empty dict when new), so the
    caller can detect a mover (attribute change). ``reactivated`` is True when an
    existing identity flips from inactive to active (treated as a joiner). Does
    not commit — the caller owns the transaction.
    """
    email = ext["email"]
    now = datetime.now(timezone.utc)
    ident = (await db.execute(
        select(ScimIdentity).where(func.lower(ScimIdentity.user_email) == email)
    )).scalars().first()

    is_new = ident is None
    reactivated = False
    old_attributes: dict[str, Any] = {}
    if ident is None:
        ident = ScimIdentity(user_email=email, first_seen_at=now)
        db.add(ident)
    else:
        old_attributes = dict(ident.attributes or {})
        reactivated = (not ident.active) and ext["active"]

    ident.external_id = ext.get("external_id") or ident.external_id
    ident.display_name = ext.get("display_name") or ident.display_name
    ident.active = ext["active"]
    ident.attributes = ext.get("attributes") or {}
    if raw is not None:
        ident.raw = raw
    ident.last_seen_at = now
    await db.flush()
    return ident, is_new, reactivated, old_attributes


async def run_joiner(
    db: AsyncSession, *, email: str, display_name: str | None,
    attributes: dict[str, Any], actor: str,
) -> list[dict[str, Any]]:
    """Evaluate assignment rules for the user and order the matched bundles."""
    from app.models.bundle import Bundle
    from app.services.bundle_order import order_bundle
    from app.services.onboarding import build_user_context, evaluate_assignment_rules

    context = build_user_context(attributes)
    matched = await evaluate_assignment_rules(db, context)
    results: list[dict[str, Any]] = []
    for m in matched:
        bundle = await db.get(Bundle, m["bundle_id"])
        if not bundle or not bundle.is_active:
            continue
        summary = await order_bundle(
            db, bundle=bundle,
            recipient_email=email, recipient_name=display_name or email,
            requester_email=None, requester_name=actor,
            origin="scim", actor=actor,
        )
        results.append(summary)
    logger.info("scim joiner: %s matched %d bundle(s)", email, len(results))
    return results


# Active order statuses that count as "the user currently holds this".
_ACTIVE = ("pending", "pending_approval", "scheduled", "processing",
           "provisioning", "provisioned", "delivered")
# Only entitlements provisioned by a rule-driven trigger may be auto-revoked by
# the mover. Self-service (portal single orders = NULL group; bundle_catalog =
# user chose the package) and ServiceNow/api orders are protected.
_REVOCABLE_ORIGINS = ("scim", "rule_based")


async def run_mover(
    db: AsyncSession, *, email: str, display_name: str | None,
    attributes: dict[str, Any], actor: str, mode: str,
) -> dict[str, Any]:
    """Reconcile a user's entitlements after an attribute change.

    ``mode`` ∈ {``additions_only``, ``reconcile``}. Re-evaluates assignment
    rules against the new attributes, then:
      * **additions** — orders newly-entitled bundles (idempotent; only asset
        types the user doesn't already hold are ordered);
      * **removals** — asset types the user currently holds *via a rule-driven
        order* that are no longer entitled; revoked only when ``mode ==
        'reconcile'`` (and only rule-provisioned orders — see ``_REVOCABLE_ORIGINS``).
    """
    from sqlalchemy import text as _text

    from app.models.bundle import Bundle, BundlePosition
    from app.models.order import Order, OrderAction, OrderStatus
    from app.services.bundle_order import order_bundle
    from app.services.onboarding import build_user_context, evaluate_assignment_rules
    from app.utils.audit import aaudit

    context = build_user_context(attributes)
    matched = await evaluate_assignment_rules(db, context)
    matched_bundle_ids = [m["bundle_id"] for m in matched]

    # Target asset-type set = union of positions across matched bundles.
    target: set[int] = set()
    if matched_bundle_ids:
        rows = (await db.execute(
            select(BundlePosition.asset_type_id)
            .where(BundlePosition.bundle_id.in_(matched_bundle_ids))
        )).all()
        target = {int(r[0]) for r in rows}

    # Current rule-provisioned holdings: asset_type_id → [order ids].
    cur_rows = (await db.execute(_text(
        f"""
        SELECT o.asset_type_id, o.id
        FROM orders o JOIN order_groups og ON og.id = o.order_group_id
        WHERE lower(o.user_email) = lower(:e)
          AND o.status::text = ANY(:st)
          AND og.origin = ANY(:origins)
        """
    ), {"e": email, "st": list(_ACTIVE), "origins": list(_REVOCABLE_ORIGINS)})).all()
    current: dict[int, list[int]] = {}
    for at_id, oid in cur_rows:
        current.setdefault(int(at_id), []).append(int(oid))

    additions = target - set(current.keys())
    removals = set(current.keys()) - target

    # ── Additions: order matched bundles (idempotency skips held types) ──
    added: list[dict[str, Any]] = []
    if additions:
        for m in matched:
            bundle = await db.get(Bundle, m["bundle_id"])
            if not bundle or not bundle.is_active:
                continue
            summary = await order_bundle(
                db, bundle=bundle, recipient_email=email,
                recipient_name=display_name or email,
                requester_email=None, requester_name=actor,
                origin="scim", actor=actor,
            )
            added.append(summary)

    # ── Removals: revoke lost rule-provisioned entitlements (reconcile only) ──
    revoked_ids: list[int] = []
    if mode == "reconcile" and removals:
        to_dispatch: list[int] = []
        for at_id in removals:
            for oid in current[at_id]:
                order = await db.get(Order, oid)
                if not order or order.status in (
                    OrderStatus.REVOKING, OrderStatus.REVOKED,
                    OrderStatus.CANCELLED, OrderStatus.REJECTED,
                ):
                    continue
                old = order.status.value
                order.status = OrderStatus.REVOKING
                order.action = OrderAction.DELETE
                await aaudit(
                    db, "order", order.id, "status_changed",
                    old={"status": old},
                    new={"status": "revoking", "reason": "SCIM mover: entitlement no longer matched"},
                    by=actor,
                )
                to_dispatch.append(order.id)
                revoked_ids.append(order.id)
        await db.commit()
        from app.routes.webhook import _dispatch_runbook
        for oid in to_dispatch:
            try:
                _dispatch_runbook(oid, "delete")
            except Exception as exc:  # noqa: BLE001
                logger.warning("scim mover: revoke dispatch failed for order %s: %s", oid, exc)

    logger.info(
        "scim mover: %s mode=%s additions=%d removals=%d revoked=%d",
        email, mode, len(additions), len(removals), len(revoked_ids),
    )
    return {
        "mode": mode,
        "target_asset_types": sorted(target),
        "additions": sorted(additions),
        "removals": sorted(removals),
        "revoked_order_ids": revoked_ids,
        "added": added,
    }
