"""Async audit helper for FastAPI routes.

All writes land in the same transaction as the main change –
no separate commit needed. Entries in audit_log are immutable (no UPDATE/DELETE).
"""
from __future__ import annotations

from typing import Any, Iterable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


# Sensitivity ordering for classify_asset_type. Higher index = stricter
# class. ``pci`` requires PCI DSS controls; ``phi`` HIPAA / equivalent;
# ``pii`` GDPR / similar. ``internal`` is the default — non-public but
# not regulated, falling under the global retention window.
_CLASS_RANK = {"internal": 0, "pii": 1, "phi": 2, "pci": 3}
CLASSIFICATIONS = ("internal", "pii", "phi", "pci")


def classify_attrs(attrs: Iterable[dict[str, Any]] | None) -> str:
    """Pick the strictest classification declared on a list of attribute defs.

    Each attribute may carry a ``classification`` field set to one of
    ``pii`` / ``phi`` / ``pci``. Anything else (including missing) is
    treated as ``internal``. The strictest class wins so an asset type
    with even one PHI attribute taints every audit row touching it.
    """
    best = "internal"
    best_rank = 0
    for attr in attrs or ():
        if not isinstance(attr, dict):
            continue
        cls = (attr.get("classification") or "").lower()
        rank = _CLASS_RANK.get(cls, 0)
        if rank > best_rank:
            best_rank = rank
            best = cls
    return best


def classify_asset_type(asset_type: Any) -> str:
    """Classification of an asset type = strictest class on its config attrs."""
    if asset_type is None:
        return "internal"
    return classify_attrs(getattr(asset_type, "config", None))


async def classify_for_asset_type_id(db: AsyncSession, asset_type_id: int | None) -> str:
    """Resolve an asset type's classification given just its id.

    Convenience for audit-write paths that don't already hold the
    ``AssetType`` row (e.g. order modify / cancel routes that operate
    on an Order without eager-loading its parent type). Falls back to
    ``internal`` when the id is missing or the type can't be resolved.
    """
    if not asset_type_id:
        return "internal"
    # Local import — keep utils/audit.py free of model deps so the
    # snapshot helpers below can be imported by tests / scripts that
    # don't initialise the full ORM registry.
    from sqlalchemy import select  # noqa: PLC0415
    from app.models.asset import AssetType  # noqa: PLC0415

    res = await db.execute(select(AssetType).where(AssetType.id == asset_type_id))
    at = res.scalar_one_or_none()
    return classify_asset_type(at) if at else "internal"


def actor_by(request: Request | None, label: str) -> str:
    """Build an audit ``triggered_by`` string from the request's actor.

    ``request.state.actor`` is populated by ``require_admin_key`` and
    ``_authenticate_webhook`` and identifies the credential used for
    the call (e.g. ``token:servicenow-int``, ``admin:session:alice``,
    ``admin:legacy_key``, ``webhook:hmac``). Wrapping it with the
    route's logical label gives auditors both *what* happened and
    *who* triggered it.

    Falls back to plain ``api:<label>`` when no actor is on state
    (unauthenticated routes), preserving back-compat.
    """
    if request is None:
        return f"api:{label}"
    actor = getattr(getattr(request, "state", None), "actor", None)
    if actor:
        return f"api:{label} ({actor})"
    return f"api:{label}"


async def aaudit(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: str,
    *,
    old: dict | None = None,
    new: dict | None = None,
    by: str,
    ctx: str | None = None,
    classification: str | None = None,
) -> None:
    """Schreibt einen Audit-Log-Eintrag in die laufende Transaktion.

    Args:
        db:             Aktive AsyncSession (wird vom Caller committed)
        entity_type:    "order" | "asset" | "asset_type" | "app_config"
        entity_id:      PK of the changed record
        action:         "created" | "updated" | "status_changed" | "deleted"
        old:            Snapshot before the change (None on created)
        new:            Snapshot after the change (None on deleted)
        by:             Trigger, e.g. "api:create_order"
        ctx:             Optionaler Kontext (servicenow_ref, celery_task_id, ...)
        classification: Data class of the touched entity. One of
                        ``internal`` (default), ``pii``, ``phi``, ``pci``.
                        Drives per-class retention windows. Pass the
                        result of ``classify_asset_type(asset_type)`` for
                        any audit row that touches asset / order /
                        asset_type data.
    """
    cls = classification if classification in CLASSIFICATIONS else None
    db.add(AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=old,
        new_value=new,
        triggered_by=by,
        context=ctx,
        classification=cls or "internal",
    ))


# ── Snapshot-Helfer ────────────────────────────────────────────────────────────

def _order_snap(order) -> dict:
    return {
        "id": order.id,
        "status": order.status.value if hasattr(order.status, "value") else order.status,
        "action": order.action.value if hasattr(order.action, "value") else order.action,
        "user_email": order.user_email,
        "asset_type_id": order.asset_type_id,
        "assigned_asset_id": order.assigned_asset_id,
        "rdp_users": list(order.rdp_users or []),
        "admin_users": list(order.admin_users or []),
        "requested_until": order.requested_until.isoformat() if order.requested_until else None,
    }


def _asset_snap(asset) -> dict:
    return {
        "id": asset.id,
        "name": asset.name,
        "status": asset.status.value if hasattr(asset.status, "value") else asset.status,
        "asset_type_id": asset.asset_type_id,
        "current_order_id": asset.current_order_id,
        "expires_at": asset.expires_at.isoformat() if asset.expires_at else None,
    }


def _config_snap(cfg) -> dict:
    return {
        "id": cfg.id,
        "key": cfg.key,
        "value": "***" if cfg.is_secret else cfg.value,
        "is_secret": cfg.is_secret,
        "description": cfg.description,
    }


def _type_snap(t) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "is_active": getattr(t, "is_active", True),
        "category": t.category.value if hasattr(t.category, "value") else t.category,
        "description": t.description,
        "help_text": getattr(t, "help_text", None),
        "config": t.config,
        "assignment_model": t.assignment_model,
        "automation_mode": t.automation_mode,
        "automation_strategy": t.automation_strategy,
        "composite_steps": t.composite_steps,
        "deprovision_policy": t.deprovision_policy,
        "personal_provisioning_strategy": t.personal_provisioning_strategy,
        "naming_pattern": t.naming_pattern,
        "max_per_user": t.max_per_user,
        "min_approvals_required": getattr(t, "min_approvals_required", None),
        "approval_rules": getattr(t, "approval_rules", None),
        "monthly_cost": str(t.monthly_cost) if t.monthly_cost is not None else None,
        "currency": t.currency,
        "cost_center": t.cost_center,
    }
