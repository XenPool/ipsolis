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


async def upsert_identity(
    db: AsyncSession, ext: dict[str, Any], *, raw: dict[str, Any] | None = None
) -> tuple[ScimIdentity, bool, bool]:
    """Create/update the projection. Returns ``(identity, is_new, reactivated)``.

    ``reactivated`` is True when an existing identity flips from inactive to
    active (an IdP re-enabling a previously-disabled user → treat as a joiner).
    Does not commit — the caller owns the transaction.
    """
    email = ext["email"]
    now = datetime.now(timezone.utc)
    ident = (await db.execute(
        select(ScimIdentity).where(func.lower(ScimIdentity.user_email) == email)
    )).scalars().first()

    is_new = ident is None
    reactivated = False
    if ident is None:
        ident = ScimIdentity(user_email=email, first_seen_at=now)
        db.add(ident)
    else:
        reactivated = (not ident.active) and ext["active"]

    ident.external_id = ext.get("external_id") or ident.external_id
    ident.display_name = ext.get("display_name") or ident.display_name
    ident.active = ext["active"]
    ident.attributes = ext.get("attributes") or {}
    if raw is not None:
        ident.raw = raw
    ident.last_seen_at = now
    await db.flush()
    return ident, is_new, reactivated


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
