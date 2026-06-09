"""SCIM 2.0 endpoint — Okta / SailPoint / Ping deprovision integration.

Slice 1 scope: a leaver-focused subset of RFC 7644 — enough for an
upstream IDP to sync ipSolis as a deprovision target.

Implemented today:

* ``GET /scim/v2/ServiceProviderConfig``
* ``GET /scim/v2/ResourceTypes``
* ``GET /scim/v2/Schemas``
* ``GET /scim/v2/Users`` (list, with optional ``filter=userName eq "..."``,
  ``startIndex``, ``count``)
* ``GET /scim/v2/Users/{id}``
* ``POST /scim/v2/Users`` (acknowledged, no real storage — see below)
* ``PUT /scim/v2/Users/{id}`` (acknowledged)
* ``PATCH /scim/v2/Users/{id}`` — a value of ``active: false`` triggers
  the leaver flow.
* ``DELETE /scim/v2/Users/{id}`` — triggers the leaver flow.

**Why is user storage a no-op?** ip·Solis users live in Entra ID / AD
(real source of truth) and we only see them when they create an order.
A SCIM provisioning event from Okta saying "create user" doesn't need
to make a row in ipSolis — the user implicitly exists as soon as they
authenticate, and they show up in our SCIM list view because they have
orders. The valuable signal is **deprovision**: when Okta deletes or
deactivates a user, we trigger the leaver flow that revokes all their
active orders.

Authentication: bearer token only (no HMAC fallback — SCIM clients are
modern, Okta / Ping all use OAuth-style tokens). Required scope is
``scim:read`` for GET, ``scim:write`` for everything else.

Out of scope for slice 1:

* ``/scim/v2/Groups`` — ip·Solis doesn't model user-group membership;
  groups live in AD and are managed by ``target_executor``.
* SCIM filter syntax beyond ``userName eq "..."`` (full SCIM filter
  grammar is its own multi-week implementation effort).
* SCIM ``Bulk`` operations.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.order import Order
from app.utils.api_tokens import token_has_scope, verify_raw_token, mark_used
from app.utils.leaver import process_leaver

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/scim/v2",
    tags=["scim"],
)


_USER_RESOURCE_TYPE = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
    "id": "User",
    "name": "User",
    "endpoint": "/Users",
    "description": "User Account",
    "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
}

_USER_SCHEMA = {
    "id": "urn:ietf:params:scim:schemas:core:2.0:User",
    "name": "User",
    "description": "Core SCIM 2.0 User schema",
    "attributes": [
        {
            "name": "userName", "type": "string", "multiValued": False,
            "required": True, "caseExact": False, "mutability": "readWrite",
            "returned": "default", "uniqueness": "server",
        },
        {
            "name": "active", "type": "boolean", "multiValued": False,
            "required": False, "caseExact": False, "mutability": "readWrite",
            "returned": "default",
        },
        {
            "name": "displayName", "type": "string", "multiValued": False,
            "required": False, "caseExact": False, "mutability": "readWrite",
            "returned": "default",
        },
        {
            "name": "emails", "type": "complex", "multiValued": True,
            "required": False, "mutability": "readWrite", "returned": "default",
        },
        {
            "name": "externalId", "type": "string", "multiValued": False,
            "required": False, "caseExact": True, "mutability": "readWrite",
            "returned": "default",
        },
    ],
}

_SERVICE_PROVIDER_CONFIG = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
    "documentationUri": "https://datatracker.ietf.org/doc/html/rfc7644",
    "patch":         {"supported": True},
    "bulk":          {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
    "filter":        {"supported": True, "maxResults": 200},
    "changePassword": {"supported": False},
    "sort":          {"supported": False},
    "etag":          {"supported": False},
    "authenticationSchemes": [
        {
            "type": "oauthbearertoken",
            "name": "OAuth Bearer Token",
            "description": "Authentication scheme using ip·Solis API tokens (xpat_*)",
            "specUri": "https://datatracker.ietf.org/doc/html/rfc6750",
        }
    ],
}


# ── Auth dependency (scope-gated bearer only) ────────────────────────────────

async def _scim_auth(
    request: Request,
    db: AsyncSession,
    *,
    write: bool,
) -> str:
    """Verify the SCIM caller has the right scope.

    Returns an actor string suitable for audit attribution. Raises
    ``HTTPException`` on any auth failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SCIM endpoint requires Authorization: Bearer xpat_…",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw = auth_header.split(" ", 1)[1].strip()
    token = await verify_raw_token(db, raw)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    needed = "scim:write" if write else "scim:read"
    scopes = list(token.scopes or [])
    if not token_has_scope(scopes, needed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Token '{token.name}' lacks required scope '{needed}'. "
                f"Granted: {', '.join(sorted(scopes)) or '(none)'}."
            ),
        )
    await mark_used(db, token.id)
    await db.commit()
    return f"scim:token:{token.name}"


def _scim_error(status_code: int, detail: str, scim_type: str | None = None) -> JSONResponse:
    """Render the RFC 7644 §3.12 error envelope."""
    body: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return JSONResponse(status_code=status_code, content=body)


# ── Discovery endpoints ──────────────────────────────────────────────────────

@router.get("/ServiceProviderConfig")
async def service_provider_config(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    await _scim_auth(request, db, write=False)
    return _SERVICE_PROVIDER_CONFIG


@router.get("/ResourceTypes")
async def resource_types(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    await _scim_auth(request, db, write=False)
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "Resources": [_USER_RESOURCE_TYPE],
    }


@router.get("/Schemas")
async def schemas(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    await _scim_auth(request, db, write=False)
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 1,
        "Resources": [_USER_SCHEMA],
    }


# ── Users — list / read / create / update / delete ──────────────────────────

def _user_resource(email: str, display_name: str | None = None) -> dict:
    """Render a SCIM User resource for an ip·Solis user.

    The ``id`` is the lowercase email — stable, unique, and guaranteed
    to exist for any user who's interacted with ip·Solis. ``externalId``
    isn't tracked since we don't store users; SCIM clients map their
    own external id to ``userName`` (which equals ``id`` here).
    """
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": email,
        "userName": email,
        "active": True,
        "displayName": display_name or email,
        "emails": [{"value": email, "primary": True, "type": "work"}],
        "meta": {
            "resourceType": "User",
            "location": f"/scim/v2/Users/{email}",
        },
    }


@router.get("/Users")
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    filter: str | None = Query(default=None, alias="filter"),
    startIndex: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=0, le=200),
) -> dict:
    """List users (= distinct ``orders.user_email`` values).

    Filters: only ``userName eq "<email>"`` is supported. Anything
    else returns the unfiltered list with a ``Warning`` header.
    Implementing the full SCIM filter grammar is queued for slice 2.
    """
    await _scim_auth(request, db, write=False)

    target_email: str | None = None
    if filter:
        # Naive parse — sufficient for the most common Okta / SailPoint pattern.
        f = filter.strip()
        for keyword in ('userName eq "', 'username eq "', 'emails eq "'):
            if f.lower().startswith(keyword.lower()):
                tail = f[len(keyword):]
                if tail.endswith('"'):
                    target_email = tail[:-1].strip().lower()
                    break

    if target_email:
        rows = await db.execute(
            select(Order.user_email, Order.user_name)
            .where(func.lower(Order.user_email) == target_email)
            .order_by(Order.id.desc())
            .limit(1)
        )
        row = rows.first()
        if row is None:
            return {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": 0,
                "startIndex": startIndex,
                "itemsPerPage": 0,
                "Resources": [],
            }
        email_l = row[0].lower()
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 1,
            "startIndex": startIndex,
            "itemsPerPage": 1,
            "Resources": [_user_resource(email_l, row[1])],
        }

    # Unfiltered list — distinct lowercased emails, paginated.
    total_q = select(func.count(distinct(func.lower(Order.user_email))))
    total = (await db.execute(total_q)).scalar_one()

    page_q = (
        select(distinct(func.lower(Order.user_email)).label("email"))
        .order_by(func.lower(Order.user_email))
        .offset(max(0, startIndex - 1))
        .limit(count)
    )
    rows = (await db.execute(page_q)).all()
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": int(total or 0),
        "startIndex": startIndex,
        "itemsPerPage": len(rows),
        "Resources": [_user_resource(r[0]) for r in rows],
    }


@router.get("/Users/{user_id}")
async def get_user(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    await _scim_auth(request, db, write=False)
    email_l = (user_id or "").strip().lower()
    if not email_l:
        return _scim_error(400, "User id (email) required")
    row = await db.execute(
        select(Order.user_email, Order.user_name)
        .where(func.lower(Order.user_email) == email_l)
        .order_by(Order.id.desc())
        .limit(1)
    )
    found = row.first()
    if found is None:
        return _scim_error(404, f"User {user_id!r} not found")
    return _user_resource(found[0].lower(), found[1])


@router.post("/Users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge a SCIM Create — no-op storage, returns the resource.

    ip·Solis users live in Entra ID / AD; SCIM Create from Okta is
    accepted to keep IDP integrations clean (Okta marks the user as
    "provisioned in ipSolis"), but we don't actually create anything.
    The user becomes real in ip·Solis when they make their first order.
    """
    await _scim_auth(request, db, write=True)
    try:
        payload = await request.json()
    except Exception:
        return _scim_error(400, "Body must be valid JSON")

    email = (
        payload.get("userName")
        or _first_email(payload.get("emails"))
    )
    if not email:
        return _scim_error(400, "userName or emails[].value required", scim_type="invalidValue")

    return _user_resource(email.strip().lower(), payload.get("displayName"))


@router.put("/Users/{user_id}")
async def replace_user(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge a SCIM Replace.

    No real storage to replace — the only side-effect ip·Solis cares
    about is ``active=false`` triggering the leaver flow, which we
    handle uniformly here.
    """
    actor = await _scim_auth(request, db, write=True)
    try:
        payload = await request.json()
    except Exception:
        return _scim_error(400, "Body must be valid JSON")

    email_l = (user_id or "").strip().lower()
    active = payload.get("active", True)
    if active is False:
        await _maybe_run_leaver(db, email=email_l, actor=actor, raw=payload)
    return _user_resource(email_l, payload.get("displayName"))


@router.patch("/Users/{user_id}")
async def patch_user(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """SCIM PATCH (RFC 7644 §3.5.2) — only ``active=false`` is acted on."""
    actor = await _scim_auth(request, db, write=True)
    try:
        payload = await request.json()
    except Exception:
        return _scim_error(400, "Body must be valid JSON")

    email_l = (user_id or "").strip().lower()
    operations = payload.get("Operations") or []
    triggered_leaver = False
    for op in operations:
        if not isinstance(op, dict):
            continue
        # Look for an op that flips active to false. SCIM PATCH ops can
        # take several shapes — handle the two common ones explicitly.
        op_name = (op.get("op") or "").lower()
        if op_name not in ("replace", "add"):
            continue
        path = op.get("path")
        value = op.get("value")
        # Shape 1: {op: replace, path: "active", value: false}
        if path == "active" and value is False:
            triggered_leaver = True
            break
        # Shape 2: {op: replace, value: {active: false, ...}}
        if path is None and isinstance(value, dict) and value.get("active") is False:
            triggered_leaver = True
            break

    if triggered_leaver:
        await _maybe_run_leaver(db, email=email_l, actor=actor, raw=payload)

    return _user_resource(email_l)


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_user(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """SCIM DELETE → trigger leaver flow."""
    actor = await _scim_auth(request, db, write=True)
    email_l = (user_id or "").strip().lower()
    if not email_l:
        return _scim_error(400, "User id (email) required")
    await _maybe_run_leaver(db, email=email_l, actor=actor, raw=None)


async def _maybe_run_leaver(
    db: AsyncSession, *, email: str, actor: str, raw: dict | None
) -> None:
    """Trigger the leaver flow with audit attribution.

    Suppresses ``ValueError`` on a missing email — SCIM clients sometimes
    PATCH with active=false against a placeholder user; surfacing 400
    in that case is more annoying than ignoring the no-op.
    """
    try:
        await process_leaver(
            db,
            user_email=email,
            source="scim",
            triggered_by=f"api:scim ({actor})",
            user_external_id=None,
            raw_payload=raw,
        )
    except ValueError as exc:
        logger.info("scim leaver no-op: %s", exc)


def _first_email(emails: list[dict] | None) -> str | None:
    if not isinstance(emails, list):
        return None
    for entry in emails:
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            return entry["value"]
    return None
