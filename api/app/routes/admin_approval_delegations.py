"""Admin CRUD for approval delegations.

Slice scope: admin manages delegations on behalf of users (helpdesk
pattern). Self-service portal flow can be layered on later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.approval_delegation import ApprovalDelegation
from app.utils.audit import aaudit, actor_by
from app.utils.auth import require_admin_key, require_scopes

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/admin/approval-delegations",
    tags=["admin-approval-delegations"],
    dependencies=[Depends(require_admin_key)],
)


class DelegationCreate(BaseModel):
    approver_email: str = Field(min_length=1, max_length=255)
    approver_name: str | None = None
    delegate_email: str = Field(min_length=1, max_length=255)
    delegate_name: str | None = None
    from_at: datetime
    until_at: datetime
    reason: str | None = Field(default=None, max_length=500)


class DelegationRow(BaseModel):
    id: int
    approver_email: str
    approver_name: str | None
    delegate_email: str
    delegate_name: str | None
    from_at: datetime
    until_at: datetime
    reason: str | None
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    status: str

    model_config = {"from_attributes": True}


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
        "from_at": d.from_at,
        "until_at": d.until_at,
        "reason": d.reason,
        "created_by": d.created_by,
        "created_at": d.created_at,
        "revoked_at": d.revoked_at,
        "status": _row_status(d),
    }


@router.get(
    "",
    response_model=list[DelegationRow],
    dependencies=[require_scopes("approvals:read")],
)
async def list_delegations(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = await db.execute(
        select(ApprovalDelegation).order_by(ApprovalDelegation.created_at.desc())
    )
    return [_to_row(d) for d in rows.scalars().all()]


@router.post(
    "",
    response_model=DelegationRow,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_scopes("approvals:write")],
)
async def create_delegation(
    request: Request,
    payload: DelegationCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.until_at <= payload.from_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`until_at` must be after `from_at`.",
        )
    if payload.delegate_email.strip().lower() == payload.approver_email.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Delegate must be different from the approver.",
        )

    row = ApprovalDelegation(
        approver_email=payload.approver_email.strip(),
        approver_name=(payload.approver_name or "").strip() or None,
        delegate_email=payload.delegate_email.strip(),
        delegate_name=(payload.delegate_name or "").strip() or None,
        from_at=payload.from_at,
        until_at=payload.until_at,
        reason=(payload.reason or "").strip() or None,
        created_by=getattr(request.state, "actor", None) or "admin:unknown",
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
        by=actor_by(request, "create_approval_delegation"),
    )
    await db.commit()
    await db.refresh(row)
    logger.info("admin: created approval delegation id=%s %s→%s",
                row.id, row.approver_email, row.delegate_email)
    return _to_row(row)


@router.delete(
    "/{delegation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[require_scopes("approvals:write")],
)
async def revoke_delegation(
    delegation_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(ApprovalDelegation).where(ApprovalDelegation.id == delegation_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delegation not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await aaudit(
            db, "approval_delegation", row.id, "revoked",
            new={"revoked_at": row.revoked_at.isoformat()},
            by=actor_by(request, "revoke_approval_delegation"),
        )
        await db.commit()
        logger.info("admin: revoked approval delegation id=%s", row.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
