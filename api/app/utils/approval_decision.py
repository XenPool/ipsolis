"""Shared approval-decision logic.

Both the portal route (session-authenticated) and the tokenized external
route call this helper so the two paths can never drift on what counts as
"approved" or how downstream effects are dispatched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.approval import OrderApproval
from app.models.order import Order, OrderStatus

logger = logging.getLogger(__name__)


class DecisionResult:
    __slots__ = ("status", "all_granted")

    def __init__(self, status: str, all_granted: bool) -> None:
        self.status = status              # "approved" | "declined" | "already_decided"
        self.all_granted = all_granted    # True iff this decision unblocked the order


async def apply_approval_decision(
    db: AsyncSession,
    approval: OrderApproval,
    decision: str,
    comment: str | None,
) -> DecisionResult:
    """Record ``decision`` on ``approval`` and trigger downstream effects.

    Caller is responsible for verifying that the actor is authorized to
    decide on this approval (portal session match, or valid signed token).
    The function commits the session.

    Returns a ``DecisionResult`` describing what happened so the caller can
    render an appropriate response.
    """
    if approval.status != "pending":
        return DecisionResult(status="already_decided", all_granted=False)

    norm = "approved" if decision == "approve" else "declined"
    approval.status = norm
    approval.decided_at = datetime.now(timezone.utc)
    approval.comment = (comment or "").strip() or None

    order = await db.get(Order, approval.order_id)
    if not order:
        # Should never happen given FK; defensively roll back the partial mutation.
        logger.error("Approval %s references missing order %s", approval.id, approval.order_id)
        await db.rollback()
        return DecisionResult(status="already_decided", all_granted=False)

    from celery import Celery
    celery_app = Celery(broker=settings.CELERY_BROKER_URL)

    if norm == "declined":
        order.status = OrderStatus.REJECTED
        order.error_message = (
            f"Declined by {approval.approver_name}: "
            f"{approval.comment or 'no reason given'}"
        )
        celery_app.send_task(
            "tasks.workflows.dynamic_runner.send_approval_result_email",
            args=[order.id, False, approval.approver_name, approval.comment],
            queue="provision",
        )
        await db.commit()
        logger.info("Approval %s declined for order %s", approval.id, order.id)
        return DecisionResult(status="declined", all_granted=False)

    # Approved — check whether all approvals are now granted.
    rows = await db.execute(
        select(OrderApproval).where(OrderApproval.order_id == order.id)
    )
    all_approvals = list(rows.scalars().all())
    all_granted = all(a.status == "approved" for a in all_approvals)

    if all_granted:
        # Local import — _post_approval_dispatch lives in the portal route module
        # so the side-effects (asset reservation, runbook dispatch) stay there.
        from app.routes.portal import _post_approval_dispatch
        await _post_approval_dispatch(order, db, celery_app)
        celery_app.send_task(
            "tasks.workflows.dynamic_runner.send_approval_result_email",
            args=[order.id, True],
            queue="provision",
        )
        logger.info("All approvals granted for order %s — dispatching", order.id)
    else:
        logger.info(
            "Approval %s approved for order %s (still pending others)",
            approval.id, order.id,
        )

    await db.commit()
    return DecisionResult(status="approved", all_granted=all_granted)
