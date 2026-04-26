"""Tokenized approval endpoint — no portal session required.

The approver clicks a link from their email or Teams card. The token in the
URL identifies which OrderApproval row they're acting on; we trust the token
because it's HMAC-signed with our internal API_SECRET_KEY (see
``app.utils.approval_token``). The link works from any client (Outlook,
Teams, mobile mail) without forcing the user through Entra SSO first.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.approval import OrderApproval
from app.models.asset import AssetType
from app.models.order import Order
from app.templates_instance import templates
from app.utils.approval_decision import apply_approval_decision
from app.utils.approval_token import verify_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["approvals-external"])


async def _load_approval_by_token(db: AsyncSession, token: str) -> OrderApproval | None:
    payload = verify_token(token)
    if payload is None:
        return None
    result = await db.execute(
        select(OrderApproval).where(OrderApproval.id == payload["aid"])
    )
    return result.scalar_one_or_none()


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
        "approve_status.html",
        {
            "request": request,
            "title": title,
            "headline": headline,
            "message": message,
            "tone": tone,  # "info" | "success" | "warning" | "error"
        },
        status_code=status_code,
    )


@router.get("/approve/{token}", response_class=HTMLResponse)
async def approve_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Render the approve / decline confirmation page for a tokenized link."""
    approval = await _load_approval_by_token(db, token)
    if approval is None:
        return _render_status_page(
            request,
            title="Link expired",
            headline="This approval link is no longer valid.",
            message=(
                "The link may have expired or the secret used to sign it has rotated. "
                "Open the portal directly to find pending approvals."
            ),
            tone="warning",
            status_code=410,
        )

    if approval.status != "pending":
        return _render_status_page(
            request,
            title="Already decided",
            headline=f"This request has already been {approval.status}.",
            message="No further action is needed.",
            tone="info",
        )

    # Hydrate context for the form
    order_result = await db.execute(
        select(Order).options(selectinload(Order.steps)).where(Order.id == approval.order_id)
    )
    order = order_result.scalar_one_or_none()
    asset_type = await db.get(AssetType, order.asset_type_id) if order else None

    return templates.TemplateResponse(
        "approve_confirm.html",
        {
            "request": request,
            "approval": approval,
            "order": order,
            "asset_type": asset_type,
            "token": token,
        },
    )


@router.post("/approve/{token}", response_class=HTMLResponse)
async def approve_post(
    request: Request,
    token: str,
    decision: str = Form(...),
    comment: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Record the decision encoded in the form against the token's approval row."""
    approval = await _load_approval_by_token(db, token)
    if approval is None:
        raise HTTPException(status_code=410, detail="Approval link expired or invalid")

    if decision not in ("approve", "reject", "decline"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    # Normalize: portal uses 'approve' | else; we accept reject/decline as decline.
    norm = "approve" if decision == "approve" else "reject"

    result = await apply_approval_decision(db, approval, norm, comment)

    if result.status == "already_decided":
        return _render_status_page(
            request,
            title="Already decided",
            headline=f"This request has already been {approval.status}.",
            message="No further action is needed.",
            tone="info",
        )

    if result.status == "approved":
        if result.all_granted:
            return _render_status_page(
                request,
                title="Approved",
                headline="Approval recorded — order is being dispatched.",
                message="The requester will receive a confirmation email.",
                tone="success",
            )
        return _render_status_page(
            request,
            title="Approved",
            headline="Your approval is recorded.",
            message="One or more additional approvers still need to review this request.",
            tone="success",
        )

    return _render_status_page(
        request,
        title="Declined",
        headline="Decision recorded.",
        message="The requester has been notified that the order was declined.",
        tone="info",
    )
