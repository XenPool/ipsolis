"""Portal-side certification reviews — manager-facing review queue.

Mirrors the no-login signed-token endpoints in
``certifications_external.py`` but uses the portal session as the
identity source. The signed-token URL still works for users without a
portal account; this page is for users who already have SSO and want
to see their full pending queue rather than jumping in via an email
link.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import AssetType
from app.models.certification import CertificationCampaign, CertificationReview
from app.models.order import Order, OrderAction, OrderStatus
from app.routes.portal import require_portal_auth
from app.templates_instance import templates
from app.utils.audit import aaudit, portal_actor_by

logger = logging.getLogger(__name__)
router = APIRouter(tags=["portal-certifications"])


@router.get("/portal/certifications", response_class=HTMLResponse)
async def my_certifications_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_portal_auth),
):
    """Render the manager's pending review queue."""
    email = (current_user.get("email") or "").lower()
    return templates.TemplateResponse("portal/certifications.html", {
        "request": request,
        "active_page": "certifications",
        "user": current_user,
        "email": email,
    })


@router.get(
    "/portal/api/certifications/reviews",

)
async def api_my_reviews(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_portal_auth),
) -> list[dict]:
    """JSON: every review row addressed to the SSO-authenticated user.

    Includes pending + recently-decided rows so the page can show "what
    I just confirmed" without a stale-cache feel.
    """
    email = (current_user.get("email") or "").lower().strip()
    if not email:
        return []

    rows = await db.execute(
        select(CertificationReview)
        .where(CertificationReview.reviewer_email == email)
        .order_by(CertificationReview.status, CertificationReview.id.desc())
    )
    reviews = list(rows.scalars().all())

    # Hydrate context (campaign name, order info) per row.
    out: list[dict] = []
    for r in reviews:
        order = await db.get(Order, r.order_id)
        asset_type = await db.get(AssetType, order.asset_type_id) if order and order.asset_type_id else None
        campaign = await db.get(CertificationCampaign, r.campaign_id)
        out.append({
            "id": r.id,
            "campaign_id": r.campaign_id,
            "campaign_name": campaign.name if campaign else "(unknown)",
            "campaign_status": campaign.status if campaign else "(unknown)",
            "campaign_due_at": campaign.due_at.isoformat() if campaign and campaign.due_at else None,
            "order_id": r.order_id,
            "user_email": order.user_email if order else "",
            "user_name": order.user_name if order else "",
            "asset_type_name": asset_type.name if asset_type else "(unknown)",
            "status": r.status,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "comment": r.comment,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


@router.post(
    "/portal/api/certifications/reviews/{review_id}/decide",

)
async def api_decide_review(
    review_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_portal_auth),
    decision: str = Form(...),
    comment: str = Form(default=""),
) -> dict:
    """Record a confirm/revoke decision via the portal session.

    Identity is enforced server-side: the review's ``reviewer_email``
    must match the SSO user. Cross-user attempts return 404 (not 403)
    so the existence of someone else's review row isn't leaked.
    """
    email = (current_user.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Portal anonymous mode — sign in via Entra ID to record decisions.",
        )

    review = await db.get(CertificationReview, review_id)
    if review is None or review.reviewer_email.lower() != email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review {review_id} not found",
        )

    if decision not in ("confirm", "revoke"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision must be 'confirm' or 'revoke'",
        )
    decision_norm = "confirmed" if decision == "confirm" else "revoked"

    if review.status != "pending":
        return {"id": review.id, "status": review.status, "skipped": True, "reason": "already_decided"}

    campaign = await db.get(CertificationCampaign, review.campaign_id)
    if campaign and campaign.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Campaign is {campaign.status} — decisions only allowed while running",
        )

    now = datetime.now(timezone.utc)
    actor = portal_actor_by(current_user, "decide_certification_review")
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
            "via": "portal_session",
        },
        by=actor,
    )

    _revoke_order_id = None
    if decision_norm == "revoked":
        order = await db.get(Order, review.order_id)
        if order and order.status not in (
            OrderStatus.REVOKED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
        ):
            old_status = order.status.value
            order.status = OrderStatus.REVOKING
            order.action = OrderAction.DELETE
            # Capture id before commit — ORM objects are expired after commit
            _revoke_order_id = order.id
            await aaudit(
                db, "order", order.id, "status_changed",
                old={"status": old_status},
                new={
                    "status": OrderStatus.REVOKING.value,
                    "reason": f"Revoked by certification review #{review.id}",
                },
                by=actor,
            )

    # Commit FIRST so the row is visible to the worker before dispatch
    await db.commit()

    if _revoke_order_id is not None:
        from app.routes.webhook import _dispatch_runbook
        _dispatch_runbook(_revoke_order_id, "delete")
    return {"id": review.id, "status": review.status, "decided_at": review.decided_at.isoformat()}
