from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

_api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
_authorization_header = APIKeyHeader(name="Authorization", auto_error=False)


async def require_admin_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str | None = Security(_api_key_header),
    authorization: str | None = Security(_authorization_header),
) -> None:
    """Dependency: validates either of three credential paths:

    1. ``X-Admin-Key: <ADMIN_API_KEY>`` (legacy env-driven shared key)
    2. Admin session cookie (browser UI flow)
    3. ``Authorization: Bearer <xpat_…>`` (per-integration token from
       the ``api_tokens`` table)

    On success stores attribution metadata on ``request.state`` so
    audit handlers can see "which token did this":

    * ``request.state.actor``      = "admin:legacy_key" / "admin:session" / "token:<name>"
    * ``request.state.api_token``  = ``ApiToken`` ORM row when path 3 was used
    """
    # Path 1: legacy env shared key
    if api_key and api_key == settings.ADMIN_API_KEY:
        request.state.actor = "admin:legacy_key"
        return

    # Path 2: admin session
    if request.session.get("admin_authenticated"):
        admin_user = request.session.get("admin_user") or "admin"
        request.state.actor = f"admin:session:{admin_user}"
        return

    # Path 3: bearer token from api_tokens
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
        from app.utils.api_tokens import mark_used, verify_raw_token

        token = await verify_raw_token(db, raw)
        if token is not None:
            await mark_used(db, token.id)
            await db.commit()
            request.state.api_token = token
            request.state.actor = f"token:{token.name}"
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (X-Admin-Key, session, or Bearer token).",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_admin_session(request: Request) -> None:
    """Dependency: validates admin session cookie for browser-based UI access.

    Redirects unauthenticated requests to /ui/login, preserving the intended URL.
    """
    if not request.session.get("admin_authenticated"):
        request.session["admin_next"] = str(request.url)
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/ui/login"},
        )
