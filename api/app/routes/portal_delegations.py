"""Self-service approval delegations for portal users.

Each portal user manages **their own** delegations only — the API forces
``approver_email`` to the authenticated user's email on every write,
regardless of what the client sends. So a portal user can never set up
a delegation that re-routes someone else's approvals.

Anonymous mode (``entra.mode = disabled``) is rejected at the route
boundary — there is no real identity to delegate from.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.database import get_db
from app.models.approval_delegation import ApprovalDelegation
from app.routes.portal import require_portal_auth
from app.templates_instance import templates
from app.utils.audit import aaudit, portal_actor_by

logger = logging.getLogger(__name__)
router = APIRouter(tags=["portal-delegations"])


class DelegationCreate(BaseModel):
    delegate_email: str = Field(min_length=1, max_length=255)
    delegate_name: str | None = None
    from_at: datetime
    until_at: datetime
    reason: str | None = Field(default=None, max_length=500)


def _row_status(row: ApprovalDelegation) -> str:
    if row.revoked_at is not None:
        return "revoked"
    now = datetime.now(timezone.utc)
    if row.until_at <= now:
        return "expired"
    if row.from_at > now:
        return "scheduled"
    return "active"


def _to_row(d: ApprovalDelegation) -> dict:
    return {
        "id": d.id,
        "approver_email": d.approver_email,
        "approver_name": d.approver_name,
        "delegate_email": d.delegate_email,
        "delegate_name": d.delegate_name,
        "from_at": d.from_at.isoformat() if d.from_at else None,
        "until_at": d.until_at.isoformat() if d.until_at else None,
        "reason": d.reason,
        "created_by": d.created_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None,
        "status": _row_status(d),
    }


def _require_real_user(current_user: dict) -> str:
    """Return the user's email or raise 403 if running in anonymous mode."""
    email = (current_user.get("email") or "").strip()
    if not email or current_user.get("anonymous"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Delegations require a real authenticated user. Enable Entra ID SSO.",
        )
    return email


@router.get("/portal/delegations", response_class=HTMLResponse)
async def delegations_page(
    request: Request,
    current_user: dict = Depends(require_portal_auth),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "portal/delegations.html",
        {"active_page": "delegations", "user": current_user},
    )


@router.get("/portal/api/delegations")
async def list_my_delegations(
    current_user: dict = Depends(require_portal_auth),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    email = _require_real_user(current_user)
    rows = await db.execute(
        select(ApprovalDelegation)
        .where(func.lower(ApprovalDelegation.approver_email) == email.lower())
        .order_by(ApprovalDelegation.created_at.desc())
    )
    return [_to_row(d) for d in rows.scalars().all()]


@router.post("/portal/api/delegations", status_code=status.HTTP_201_CREATED)
async def create_my_delegation(
    payload: DelegationCreate,
    current_user: dict = Depends(require_portal_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    email = _require_real_user(current_user)

    if payload.until_at <= payload.from_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`until_at` must be after `from_at`.",
        )
    if payload.delegate_email.strip().lower() == email.lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You can't delegate your approvals to yourself.",
        )

    row = ApprovalDelegation(
        approver_email=email,
        approver_name=current_user.get("name") or None,
        delegate_email=payload.delegate_email.strip(),
        delegate_name=(payload.delegate_name or "").strip() or None,
        from_at=payload.from_at,
        until_at=payload.until_at,
        reason=(payload.reason or "").strip() or None,
        created_by=f"portal:{email}",
    )
    db.add(row)
    await db.flush()
    await aaudit(
        db, "approval_delegation", row.id, "created",
        new={
            "approver_email": row.approver_email,
            "delegate_email": row.delegate_email,
            "from_at": row.from_at.isoformat(),
            "until_at": row.until_at.isoformat(),
            "reason": row.reason,
        },
        by=portal_actor_by(current_user, "portal_create_delegation"),
    )
    await db.commit()
    await db.refresh(row)
    logger.info("Portal: %s created self-service delegation id=%s → %s",
                email, row.id, row.delegate_email)
    return _to_row(row)


@router.delete(
    "/portal/api/delegations/{delegation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_my_delegation(
    delegation_id: int,
    current_user: dict = Depends(require_portal_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    email = _require_real_user(current_user)
    result = await db.execute(
        select(ApprovalDelegation).where(ApprovalDelegation.id == delegation_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delegation not found")
    if row.approver_email.strip().lower() != email.lower():
        # Cross-user revoke attempts are treated as 404 to avoid leaking
        # the existence of the delegation.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delegation not found")

    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await aaudit(
            db, "approval_delegation", row.id, "revoked",
            new={"revoked_at": row.revoked_at.isoformat()},
            by=portal_actor_by(current_user, "portal_revoke_delegation"),
        )
        await db.commit()
        logger.info("Portal: %s revoked self-service delegation id=%s", email, row.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
