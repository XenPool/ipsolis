"""Unified leaver workflow — same code path for HR webhooks and SCIM.

A "leaver" is a user who has left the organisation. ip·Solis acts on
that signal by:

1. **Revoking every active order** owned by the user. Same path the
   certification auto-revoke uses: order → ``REVOKING`` + action
   ``DELETE``, deprovision runbook dispatched via ``dynamic_runner``.
   The runbook actually pulls the access (group memberships, VM
   destruction, SCCM cleanup) so this is not just a flag flip.

2. **Superseding pending approvals** where the leaver was the
   approver. Otherwise an order's quorum logic could get stuck
   waiting on someone who's gone forever. Mark them ``superseded``
   and let the remaining quorum reach a decision (or stall, which
   is then visible in the audit log).

3. **Superseding pending certification reviews** where the leaver
   was the reviewer. The cert campaign's overdue + auto-revoke
   logic will then handle the remaining unreviewed access on the
   normal cycle.

The flow is best-effort: if the deprovision runbook fails, the order
ends up in ``failed`` status — but the leaver-event row is still
counted as ``processed`` since we did our part. Operators triage
failures via the standard order list.

The `process_leaver` function commits the session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import OrderApproval
from app.models.certification import CertificationReview
from app.models.hr_leaver_event import HrLeaverEvent
from app.models.order import Order, OrderAction, OrderStatus
from app.utils.audit import aaudit

logger = logging.getLogger(__name__)


# Same set of statuses the cost report / capacity enforcement use as
# "active". These are the orders the leaver still has live access on
# and that we therefore need to revoke.
_ACTIVE_ORDER_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.PENDING_APPROVAL,
    OrderStatus.SCHEDULED,
    OrderStatus.PROCESSING,
    OrderStatus.PROVISIONING,
    OrderStatus.PROVISIONED,
    OrderStatus.DELIVERED,
)


async def process_leaver(
    db: AsyncSession,
    *,
    user_email: str,
    source: str,
    triggered_by: str,
    user_external_id: str | None = None,
    raw_payload: dict | None = None,
) -> dict:
    """Run the full leaver flow for ``user_email``.

    Returns a dict mirroring the ``hr_leaver_events`` row shape so the
    caller can serialise it back to the SCIM / HR-webhook client.

    Idempotency: re-firing a leaver event for the same user is harmless.
    Active orders that were revoked on the first call are no longer in
    the active set on the second call, so the count just goes to 0.
    """
    email = (user_email or "").strip().lower()
    if not email:
        raise ValueError("user_email is required")

    event = HrLeaverEvent(
        source=source,
        user_email=email,
        user_external_id=user_external_id,
        raw_payload=raw_payload,
        status="received",
        triggered_by=triggered_by,
    )
    db.add(event)
    await db.flush()

    try:
        orders_revoked = await _revoke_active_orders(
            db, email=email, triggered_by=triggered_by, leaver_event_id=event.id,
        )
        approvals_superseded = await _supersede_pending_approvals(
            db, email=email, triggered_by=triggered_by,
        )
        reviews_superseded = await _supersede_pending_reviews(
            db, email=email, triggered_by=triggered_by,
        )

        event.orders_revoked = orders_revoked
        event.approvals_superseded = approvals_superseded
        event.reviews_superseded = reviews_superseded
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)

        await aaudit(
            db, "hr_leaver_event", event.id, "processed",
            new={
                "user_email": email,
                "user_external_id": user_external_id,
                "source": source,
                "orders_revoked": orders_revoked,
                "approvals_superseded": approvals_superseded,
                "reviews_superseded": reviews_superseded,
            },
            by=triggered_by,
        )
        await db.commit()
        await db.refresh(event)

        logger.info(
            "leaver: processed %s (source=%s) — orders_revoked=%d, "
            "approvals_superseded=%d, reviews_superseded=%d",
            email, source, orders_revoked, approvals_superseded, reviews_superseded,
        )
    except Exception as exc:  # noqa: BLE001
        # Defensive — a leaver flow failure is unusual (DB outage, schema
        # drift). Mark the event as failed and re-raise so the HTTP
        # caller sees a 500 and the IDP retries on its cadence.
        event.status = "failed"
        event.error_message = f"{type(exc).__name__}: {exc}"
        event.processed_at = datetime.now(timezone.utc)
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
        logger.exception("leaver: processing failed for %s (source=%s)", email, source)
        raise

    return {
        "id": event.id,
        "user_email": event.user_email,
        "user_external_id": event.user_external_id,
        "source": event.source,
        "status": event.status,
        "orders_revoked": event.orders_revoked,
        "approvals_superseded": event.approvals_superseded,
        "reviews_superseded": event.reviews_superseded,
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "processed_at": event.processed_at.isoformat() if event.processed_at else None,
    }


async def _revoke_active_orders(
    db: AsyncSession,
    *,
    email: str,
    triggered_by: str,
    leaver_event_id: int,
) -> int:
    """Find every active order owned by ``email`` and dispatch revoke."""
    from app.routes.webhook import _dispatch_runbook  # local — avoid circular at import time

    rows = await db.execute(
        select(Order)
        .where(Order.user_email.ilike(email))
        .where(Order.status.in_(_ACTIVE_ORDER_STATUSES))
    )
    orders = list(rows.scalars().all())
    if not orders:
        return 0

    revoked = 0
    for order in orders:
        old_status = order.status.value
        order.status = OrderStatus.REVOKING
        order.action = OrderAction.DELETE
        order.error_message = (
            f"Leaver flow #{leaver_event_id}: user {email} marked as having left the organisation"
        )
        _dispatch_runbook(order)

        await aaudit(
            db, "order", order.id, "status_changed",
            old={"status": old_status},
            new={
                "status": OrderStatus.REVOKING.value,
                "reason": f"Leaver event #{leaver_event_id}",
            },
            by=triggered_by,
        )
        revoked += 1
    return revoked


async def _supersede_pending_approvals(
    db: AsyncSession,
    *,
    email: str,
    triggered_by: str,
) -> int:
    """Mark every pending approval where the leaver was the approver as superseded.

    Otherwise the order's quorum logic stalls forever waiting on a
    decision from someone who's gone. Same `superseded` status the
    N-of-M evaluator uses when the threshold is met by other approvers.
    """
    rows = await db.execute(
        select(OrderApproval)
        .where(OrderApproval.approver_email.ilike(email))
        .where(OrderApproval.status == "pending")
    )
    approvals = list(rows.scalars().all())
    if not approvals:
        return 0

    now = datetime.now(timezone.utc)
    for approval in approvals:
        approval.status = "superseded"
        approval.decided_at = now
        approval.comment = (
            (approval.comment + " | " if approval.comment else "")
            + "superseded: approver left the organisation"
        )
        await aaudit(
            db, "order_approval", approval.id, "superseded",
            new={
                "approver_email": approval.approver_email,
                "reason": "leaver",
            },
            by=triggered_by,
        )
    return len(approvals)


async def _supersede_pending_reviews(
    db: AsyncSession,
    *,
    email: str,
    triggered_by: str,
) -> int:
    """Mark every pending certification review where the leaver was the
    reviewer as superseded.

    The campaign's normal overdue + auto-revoke flow then handles the
    remaining unreviewed access on its own cycle. We don't reassign to
    the reviewer's manager-of-manager here — that's a slice-2 enrichment
    if customers ask for it; for now operators handle reassignment via
    the admin UI.
    """
    # No "superseded" status in the review enum yet — use a comment
    # update + the existing `auto_revoked` status would be wrong since
    # we're not actually revoking. Use a synthetic ``superseded`` status
    # value; the schema is a free-form String(20) so this is allowed.
    rows = await db.execute(
        select(CertificationReview)
        .where(CertificationReview.reviewer_email.ilike(email))
        .where(CertificationReview.status == "pending")
    )
    reviews = list(rows.scalars().all())
    if not reviews:
        return 0

    now = datetime.now(timezone.utc)
    for review in reviews:
        review.status = "superseded"
        review.decided_at = now
        review.decided_by = triggered_by
        review.comment = (
            (review.comment + " | " if review.comment else "")
            + "superseded: reviewer left the organisation — admin should reassign or close the campaign"
        )
        await aaudit(
            db, "certification_review", review.id, "superseded",
            new={
                "reviewer_email": review.reviewer_email,
                "campaign_id": review.campaign_id,
                "order_id": review.order_id,
                "reason": "leaver",
            },
            by=triggered_by,
        )
    return len(reviews)
