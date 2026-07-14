"""Admin API: operations / fulfillment dashboard (SLA & remediation).

The capacity dashboard answers "how full are the pools?". This answers the
*operator's* daily question — "what needs my attention right now?" — by
aggregating fulfillment state that today is scattered across the orders
list, approval reminders and per-user expiry emails:

* **Failed provisionings** with aging, plus a **batch retry** that wraps the
  existing single-order retry (``POST /ui/orders/{id}/retry``) N-fold.
* **Stuck in progress** — orders sitting in a transitional state past a
  threshold (informational; not auto-retried since they may still complete).
* **Overdue approvals** — pending ``order_approvals`` older than the SLA.
* **Upcoming expirations** — active orders expiring within a horizon.
* **Drift alerts** — placeholder that graceful-degrades until the drift
  reconciliation task (B1) lands; ``available: false`` for now.

Thresholds are configurable via ``app_config`` (``ops.*``), mirroring the
``retention.*`` / ``backup.*`` pattern. No new data model — a query over
existing tables plus a batch wrapper over the existing retry action.

Gated at ``admin``: the summary is read-only but the page carries a retry
action that re-runs provisioning, so the whole surface is admin-tier.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.config import AppConfig
from app.models.order import Order, OrderStatus
from app.utils.audit import aaudit, actor_by
from app.utils.auth import require_admin_key, require_scopes
from app.utils.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/operations",
    tags=["admin-operations"],
    dependencies=[Depends(require_admin_key), require_role("admin")],
)

# Config keys + defaults for the SLA/aging thresholds.
_DEFAULTS = {
    "ops.approval_sla_hours": 48,
    "ops.stuck_hours": 2,
    "ops.expiry_horizon_days": 7,
}

# Transitional states that, if held too long, indicate a stuck order.
_STUCK_STATES = ("processing", "provisioning", "revoking")
# States from which an asset actively expires.
_EXPIRABLE_STATES = ("provisioned", "delivered")

_LIST_CAP = 500


async def _read_int_cfg(db: AsyncSession, key: str, default: int) -> int:
    val = (
        await db.execute(select(AppConfig.value).where(AppConfig.key == key))
    ).scalar_one_or_none()
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


@router.get("/summary", dependencies=[require_scopes("orders:read")])
async def operations_summary(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Aggregate the operator attention list. Read-only."""
    now = datetime.now(timezone.utc)
    approval_sla_hours = await _read_int_cfg(db, "ops.approval_sla_hours", _DEFAULTS["ops.approval_sla_hours"])
    stuck_hours = await _read_int_cfg(db, "ops.stuck_hours", _DEFAULTS["ops.stuck_hours"])
    expiry_horizon_days = await _read_int_cfg(db, "ops.expiry_horizon_days", _DEFAULTS["ops.expiry_horizon_days"])

    approval_cutoff = now - timedelta(hours=approval_sla_hours)
    stuck_cutoff = now - timedelta(hours=stuck_hours)
    expiry_cutoff = now + timedelta(days=expiry_horizon_days)

    def _age_hours(ts: datetime | None) -> float | None:
        if ts is None:
            return None
        return round((now - ts).total_seconds() / 3600.0, 1)

    # ── Failed provisionings (retryable) ───────────────────────────────
    failed_rows = (await db.execute(
        text(
            """
            SELECT o.id, o.user_email, o.user_name, at.name AS asset_type_name,
                   o.created_at, o.updated_at, o.error_message
            FROM orders o JOIN asset_types at ON at.id = o.asset_type_id
            WHERE o.status::text = 'failed'
            ORDER BY o.updated_at ASC
            LIMIT :cap
            """
        ),
        {"cap": _LIST_CAP},
    )).mappings().all()
    failed = [
        {
            "order_id": r["id"],
            "user_email": r["user_email"],
            "asset_type_name": r["asset_type_name"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "failed_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "age_hours": _age_hours(r["updated_at"]),
            "error": (r["error_message"] or "")[:300],
        }
        for r in failed_rows
    ]

    # ── Stuck in progress (informational) ──────────────────────────────
    stuck_rows = (await db.execute(
        text(
            """
            SELECT o.id, o.user_email, at.name AS asset_type_name,
                   o.status::text AS status, o.updated_at
            FROM orders o JOIN asset_types at ON at.id = o.asset_type_id
            WHERE o.status::text = ANY(:states) AND o.updated_at < :cutoff
            ORDER BY o.updated_at ASC
            LIMIT :cap
            """
        ),
        {"states": list(_STUCK_STATES), "cutoff": stuck_cutoff, "cap": _LIST_CAP},
    )).mappings().all()
    stuck = [
        {
            "order_id": r["id"],
            "user_email": r["user_email"],
            "asset_type_name": r["asset_type_name"],
            "status": r["status"],
            "age_hours": _age_hours(r["updated_at"]),
        }
        for r in stuck_rows
    ]

    # ── Overdue approvals ──────────────────────────────────────────────
    overdue_rows = (await db.execute(
        text(
            """
            SELECT oa.id, oa.order_id, oa.approver_type, oa.approver_email,
                   oa.created_at, o.user_email, at.name AS asset_type_name
            FROM order_approvals oa
            JOIN orders o ON o.id = oa.order_id
            JOIN asset_types at ON at.id = o.asset_type_id
            WHERE oa.status = 'pending' AND oa.created_at < :cutoff
            ORDER BY oa.created_at ASC
            LIMIT :cap
            """
        ),
        {"cutoff": approval_cutoff, "cap": _LIST_CAP},
    )).mappings().all()
    overdue_approvals = [
        {
            "approval_id": r["id"],
            "order_id": r["order_id"],
            "approver_type": r["approver_type"],
            "approver_email": r["approver_email"],
            "requester_email": r["user_email"],
            "asset_type_name": r["asset_type_name"],
            "age_hours": _age_hours(r["created_at"]),
        }
        for r in overdue_rows
    ]

    # ── Upcoming expirations ───────────────────────────────────────────
    expiry_rows = (await db.execute(
        text(
            """
            SELECT o.id, o.user_email, at.name AS asset_type_name,
                   o.requested_until, o.status::text AS status
            FROM orders o JOIN asset_types at ON at.id = o.asset_type_id
            WHERE o.status::text = ANY(:states)
              AND o.requested_until >= :now AND o.requested_until <= :cutoff
            ORDER BY o.requested_until ASC
            LIMIT :cap
            """
        ),
        {"states": list(_EXPIRABLE_STATES), "now": now, "cutoff": expiry_cutoff, "cap": _LIST_CAP},
    )).mappings().all()
    upcoming_expirations = [
        {
            "order_id": r["id"],
            "user_email": r["user_email"],
            "asset_type_name": r["asset_type_name"],
            "expires_at": r["requested_until"].isoformat() if r["requested_until"] else None,
            "days_left": (
                round((r["requested_until"] - now).total_seconds() / 86400.0, 1)
                if r["requested_until"] else None
            ),
        }
        for r in expiry_rows
    ]

    return {
        "generated_at": now.isoformat(),
        "thresholds": {
            "approval_sla_hours": approval_sla_hours,
            "stuck_hours": stuck_hours,
            "expiry_horizon_days": expiry_horizon_days,
        },
        "failed": failed,
        "stuck": stuck,
        "overdue_approvals": overdue_approvals,
        "upcoming_expirations": upcoming_expirations,
        # Tile 4 — depends on the drift reconciliation task (B1). Until that
        # lands there is no drift signal to surface, so the UI hides the tile.
        "drift": {"available": False},
        "counts": {
            "failed": len(failed),
            "stuck": len(stuck),
            "overdue_approvals": len(overdue_approvals),
            "upcoming_expirations": len(upcoming_expirations),
        },
    }


class RetryBatch(BaseModel):
    order_ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/retry-batch")
async def retry_batch(
    payload: RetryBatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retry multiple failed orders — the single-order retry, applied N-fold.

    Each order is only retried when it is in ``FAILED`` status (same guard
    as the single retry); others are reported back with a reason and left
    untouched. Mirrors ``admin_retry_order`` in ``ui.py``.
    """
    from app.routes.webhook import _dispatch_runbook  # local — avoids import cycle

    results: list[dict[str, Any]] = []
    retried = 0
    actor = actor_by(request, "operations_retry_batch")

    for order_id in payload.order_ids:
        order = (
            await db.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order:
            results.append({"order_id": order_id, "ok": False, "reason": "not found"})
            continue
        if order.status != OrderStatus.FAILED:
            results.append({
                "order_id": order_id, "ok": False,
                "reason": f"not failed (status={order.status.value})",
            })
            continue

        await db.execute(
            text("DELETE FROM order_steps WHERE order_id = :id"), {"id": order_id}
        )
        order.status = OrderStatus.PROCESSING
        order.error_message = None
        await aaudit(db, "order", order.id, "retry_requested", by=actor)
        await db.commit()

        task_id = _dispatch_runbook(order_id, order.action.value)
        await db.execute(
            text("UPDATE orders SET celery_task_id = :t WHERE id = :id"),
            {"t": task_id, "id": order_id},
        )
        await db.commit()
        retried += 1
        results.append({"order_id": order_id, "ok": True})

    logger.info("admin: operations batch retry — %d/%d retried by=%s",
                retried, len(payload.order_ids), actor)
    return {"retried": retried, "results": results}
