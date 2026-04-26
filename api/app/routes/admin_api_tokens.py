"""Admin endpoints for managing per-integration API tokens.

The raw token is returned **only** in the create response; afterward only
the prefix is visible in the list. Revocation is a soft delete (sets
``revoked_at``); we keep the row so historical audit attribution by name
still resolves.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_token import ApiToken
from app.utils.api_tokens import (
    AVAILABLE_SCOPES,
    create_token,
    filter_valid_scopes,
    status as token_status,
)
from app.utils.auth import require_admin_key

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/admin/api-tokens",
    tags=["admin-api-tokens"],
    dependencies=[Depends(require_admin_key)],
)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    # Empty/missing → defaults to ``["admin:*"]`` for back-compat with the
    # slice-1 token UX. Unknown scopes are filtered out silently.
    scopes: list[str] | None = None


class TokenRow(BaseModel):
    id: int
    name: str
    token_prefix: str
    scopes: list[str]
    created_by: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: str

    model_config = {"from_attributes": True}


class TokenCreated(TokenRow):
    raw_token: str  # Plaintext token — only present on creation response


def _to_row(t: ApiToken) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "token_prefix": t.token_prefix,
        "scopes": list(t.scopes or []),
        "created_by": t.created_by,
        "created_at": t.created_at,
        "expires_at": t.expires_at,
        "last_used_at": t.last_used_at,
        "revoked_at": t.revoked_at,
        "status": token_status(t),
    }


@router.get("", response_model=list[TokenRow])
async def list_tokens(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = await db.execute(select(ApiToken).order_by(ApiToken.created_at.desc()))
    return [_to_row(t) for t in rows.scalars().all()]


@router.get("/scopes")
async def list_scopes() -> dict:
    """Return the scope catalog so the UI can render checkboxes dynamically."""
    return {"scopes": [{"name": k, "description": v} for k, v in AVAILABLE_SCOPES.items()]}


@router.post("", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
async def create_api_token(
    payload: TokenCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    expires_at: datetime | None = None
    if payload.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

    actor = getattr(request.state, "actor", "admin:unknown")
    requested_scopes = filter_valid_scopes(payload.scopes) or ["admin:*"]
    token, raw = await create_token(
        db,
        name=payload.name,
        created_by=actor,
        expires_at=expires_at,
        scopes=requested_scopes,
    )
    await db.commit()
    await db.refresh(token)
    logger.info("admin: created API token id=%s name=%r by=%s", token.id, token.name, actor)

    out = _to_row(token)
    out["raw_token"] = raw  # one-time reveal
    return out


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_api_token(
    token_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(select(ApiToken).where(ApiToken.id == token_id))
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        actor = getattr(request.state, "actor", "admin:unknown")
        logger.info("admin: revoked API token id=%s name=%r by=%s", token.id, token.name, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
