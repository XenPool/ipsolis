"""Tokenized certification-review endpoint — no portal session required.

A reviewer clicks the link in their kickoff / reminder email. The token
in the URL identifies which ``CertificationReview`` row they're acting
on; we trust the token because it's HMAC-signed with the internal
``API_SECRET_KEY`` (see ``app.utils.certification_token``). The link
works from any client (Outlook web, mobile mail, Teams, browser)
without forcing the user through Entra SSO first.

The "queue" form is intentionally minimal:

* ``GET /review/{token}`` — render a one-row confirmation page for a
  single review row. Reviewer clicks Confirm or Revoke (with optional
  comment), POSTs to the same URL.
* ``GET /review-queue/{token}`` — when the kickoff email links to a
  reviewer's full pending queue, this token type lists every pending
  row for the same ``reviewer_email`` and links each to its own
  ``/review/{rowToken}`` page. Avoids minting one big multi-row
  signed token (per-row tokens are easier to revoke later if a
  reviewer leaves).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import AssetType
from app.models.certification import CertificationCampaign, CertificationReview
from app.models.order import Order, OrderAction, OrderStatus
from app.templates_instance import templates
from app.utils.audit import _order_snap, aaudit
from app.utils.certification_token import make_review_token, verify_review_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["certifications-external"])


def _render_status_page(
    request: Request,
    *,
    title: str,
    headline: str,
    message: str,
    tone: str = "info",
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        "review_status.html",
        {
            "request": request,
            "title": title,
            "headline": headline,
            "message": message,
            "tone": tone,
        },
        status_code=status_code,
    )


async def _load_review_by_token(
    db: AsyncSession, token: str
) -> tuple[CertificationReview | None, str]:
    """Returns (review, reason). reason ∈ {ok, bad_token, missing_row}."""
    payload = verify_review_token(token)
    if payload is None:
        return None, "bad_token"
    review = await db.get(CertificationReview, payload["rid"])
    if review is None:
        return None, "missing_row"
    return review, "ok"


# ── Single-row confirmation page ─────────────────────────────────────────────

@router.get("/review/{token}", response_class=HTMLResponse)
async def review_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    review, reason = await _load_review_by_token(db, token)
    if reason == "bad_token":
        return _render_status_page(
            request,
            title="Link invalid",
            headline="This review link is invalid or has expired.",
            message=(
                "Review links are signed and valid for 14 days. The portal at "
                "/portal/certifications lists your current pending reviews if you "
                "have an account."
            ),
            tone="warning",
            status_code=410,
        )
    if reason == "missing_row":
        return _render_status_page(
            request,
            title="Review no longer exists",
            headline="This review is no longer in the system.",
            message=(
                "The associated order may have been cancelled or deleted, or the "
                "campaign was wiped before you reached this page."
            ),
            tone="info",
            status_code=404,
        )

    if review.status != "pending":
        return _render_status_page(
            request,
            title="Already decided",
            headline=f"This review has already been {review.status.replace('_', ' ')}.",
            message="No further action is needed.",
            tone="info",
        )

    campaign = await db.get(CertificationCampaign, review.campaign_id)
    if campaign and campaign.status not in ("running",):
        return _render_status_page(
            request,
            title="Campaign closed",
            headline=f"The certification campaign is {campaign.status}.",
            message=(
                "Decisions can only be recorded while the parent campaign is "
                "running. Contact your administrator if you believe this was "
                "closed by mistake."
            ),
            tone="warning",
        )

    order = await db.get(Order, review.order_id)
    asset_type = await db.get(AssetType, order.asset_type_id) if order and order.asset_type_id else None

    return templates.TemplateResponse(
        "review_confirm.html",
        {
            "request": request,
            "review": review,
            "campaign": campaign,
            "order": order,
            "asset_type": asset_type,
            "token": token,
        },
    )


@router.post("/review/{token}", response_class=HTMLResponse)
async def review_post(
    request: Request,
    token: str,
    decision: str = Form(...),
    comment: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    review, reason = await _load_review_by_token(db, token)
    if reason == "bad_token":
        raise HTTPException(status_code=410, detail="Review link invalid or expired")
    if reason == "missing_row":
        raise HTTPException(status_code=404, detail="Review no longer exists")

    if decision not in ("confirm", "revoke"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    decision_norm = "confirmed" if decision == "confirm" else "revoked"

    if review.status != "pending":
        return _render_status_page(
            request,
            title="Already decided",
            headline=f"This review has already been {review.status.replace('_', ' ')}.",
            message="No further action is needed.",
            tone="info",
        )

    campaign = await db.get(CertificationCampaign, review.campaign_id)
    if campaign and campaign.status != "running":
        return _render_status_page(
            request,
            title="Campaign closed",
            headline=f"The certification campaign is {campaign.status}.",
            message="Decisions can only be recorded while the parent campaign is running.",
            tone="warning",
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    actor = f"api:certification_token (reviewer:{review.reviewer_email})"
    review.status = decision_norm
    review.decided_at = now
    review.decided_by = actor
    review.comment = (comment or "").strip() or None

    await aaudit(
        db, "certification_review", review.id, decision_norm,
        new={
            "campaign_id": review.campaign_id,
            "order_id": review.order_id,
            "reviewer_email": review.reviewer_email,
            "comment": review.comment,
            "via": "signed_token",
        },
        by=actor,
    )

    if decision_norm == "revoked":
        order = await db.get(Order, review.order_id)
        if order and order.status not in (
            OrderStatus.REVOKED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
        ):
            old_status = order.status.value
            order.status = OrderStatus.REVOKING
            order.action = OrderAction.DELETE
            from app.routes.webhook import _dispatch_runbook
            _dispatch_runbook(order)
            await aaudit(
                db, "order", order.id, "status_changed",
                old={"status": old_status},
                new={
                    "status": OrderStatus.REVOKING.value,
                    "reason": f"Revoked by certification review #{review.id}",
                },
                by=actor,
            )

    await db.commit()

    if decision_norm == "confirmed":
        return _render_status_page(
            request,
            title="Confirmed",
            headline="Access confirmed — thank you.",
            message=(
                "Your decision has been recorded. The user keeps their access "
                "and the audit trail captures your confirmation."
            ),
            tone="success",
        )
    return _render_status_page(
        request,
        title="Revoked",
        headline="Access revocation triggered.",
        message=(
            "ip·Solis is now removing the user's access via the asset's "
            "deprovision runbook. The requester will be notified."
        ),
        tone="info",
    )


# ── Reviewer queue (when the kickoff link points at multiple reviews) ───────

@router.get("/review-queue/{token}", response_class=HTMLResponse)
async def review_queue_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """List all pending rows for the same reviewer as the token's review row.

    The kickoff email links to ``/review-queue/<token>`` so a reviewer
    with N pending decisions doesn't get N separate emails — they see
    one queue. From there each row links to its own
    ``/review/{rowToken}`` confirmation page (per-row tokens are easier
    to revoke individually if a reviewer leaves).
    """
    review, reason = await _load_review_by_token(db, token)
    if reason == "bad_token":
        return _render_status_page(
            request,
            title="Link invalid",
            headline="This review queue link is invalid or has expired.",
            message="Open /portal/certifications if you have a portal account.",
            tone="warning",
            status_code=410,
        )
    if reason == "missing_row":
        return _render_status_page(
            request,
            title="Review no longer exists",
            headline="The original review row is gone.",
            message="The campaign may have been wiped or the order deleted.",
            tone="info",
            status_code=404,
        )

    reviewer_email = review.reviewer_email
    rows = await db.execute(
        select(CertificationReview)
        .where(
            CertificationReview.reviewer_email == reviewer_email,
            CertificationReview.status == "pending",
        )
        .order_by(CertificationReview.campaign_id, CertificationReview.id)
    )
    pending = list(rows.scalars().all())

    # Build a per-row token + minimal context for rendering.
    enriched: list[dict] = []
    for r in pending:
        order = await db.get(Order, r.order_id)
        asset_type = await db.get(AssetType, order.asset_type_id) if order and order.asset_type_id else None
        enriched.append({
            "id": r.id,
            "campaign_id": r.campaign_id,
            "order_id": r.order_id,
            "user_email": order.user_email if order else "",
            "user_name": order.user_name if order else "",
            "asset_type_name": asset_type.name if asset_type else "(unknown)",
            "token": make_review_token(r.id),
        })

    return templates.TemplateResponse(
        "review_queue.html",
        {
            "request": request,
            "reviewer_email": reviewer_email,
            "reviewer_name": review.reviewer_name,
            "rows": enriched,
        },
    )
