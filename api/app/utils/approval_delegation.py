"""Resolve an approver to their active delegate, if any.

Order-creation paths call ``resolve_active_delegate(db, email)`` before
inserting an ``OrderApproval`` row. If the assigned approver has an
active delegation in flight, the row is created against the delegate
and a comment notes the original assignee — so the audit trail still
shows who was meant to act.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.approval_delegation import ApprovalDelegation

logger = logging.getLogger(__name__)


async def resolve_active_delegate(
    db: AsyncSession,
    approver_email: str,
) -> ApprovalDelegation | None:
    """Return the active delegation for ``approver_email`` or ``None``.

    "Active" means: not revoked, ``from_at <= NOW() < until_at``,
    case-insensitive match on the email. When more than one delegation
    is somehow active for the same approver (overlapping windows we
    didn't reject), the one with the latest ``from_at`` wins so the
    most recently configured delegation takes precedence.
    """
    if not approver_email:
        return None
    norm = approver_email.strip().lower()
    if not norm:
        return None

    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(ApprovalDelegation)
        .where(
            func.lower(ApprovalDelegation.approver_email) == norm,
            ApprovalDelegation.revoked_at.is_(None),
            ApprovalDelegation.from_at <= now,
            ApprovalDelegation.until_at > now,
        )
        .order_by(ApprovalDelegation.from_at.desc())
        .limit(1)
    )
    return rows.scalar_one_or_none()
