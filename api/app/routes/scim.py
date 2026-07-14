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

from sqlalchemy import text

from app.database import get_db
from app.models.order import Order
from app.utils.api_tokens import token_has_scope, verify_raw_token, mark_used
from app.utils.leaver import process_leaver


async def _joiner_enabled(db: AsyncSession) -> bool:
    row = (await db.execute(
        text("SELECT value FROM app_config WHERE key = 'scim.joiner_enabled'")
    )).first()
    return bool(row) and (row[0] or "").strip().lower() in ("1", "true", "yes", "on")


async def _mover_mode(db: AsyncSession) -> str:
    """Return the mover reconciliation mode: disabled | additions_only | reconcile."""
    row = (await db.execute(
        text("SELECT value FROM app_config WHERE key = 'scim.mover_mode'")
    )).first()
    mode = ((row[0] if row else None) or "disabled").strip().lower()
    return mode if mode in ("additions_only", "reconcile") else "disabled"

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

def _user_resource(
    email: str, display_name: str | None = None, *,
    active: bool = True, external_id: str | None = None,
) -> dict:
    """Render a SCIM User resource for an ip·Solis user.

    The ``id`` is the lowercase email — stable, unique, and guaranteed to exist
    for any user who's interacted with ip·Solis. ``active`` / ``externalId`` come
    from the SCIM identity projection when present (else active defaults True).
    """
    res: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": email,
        "userName": email,
        "active": active,
        "displayName": display_name or email,
        "emails": [{"value": email, "primary": True, "type": "work"}],
        "meta": {
            "resourceType": "User",
            "location": f"/scim/v2/Users/{email}",
        },
    }
    if external_id:
        res["externalId"] = external_id
    return res


async def _all_user_resources(db: AsyncSession) -> list[dict[str, Any]]:
    """Build the candidate set for in-memory filtering.

    Merges distinct ``orders.user_email`` (with the latest name) and the SCIM
    identity projection (name / active / externalId). Keyed by lowercased email.
    Used only for complex filters; simple ``userName eq`` and unfiltered lists
    take DB-paginated fast paths.
    """
    merged: dict[str, dict[str, Any]] = {}
    order_rows = (await db.execute(text(
        "SELECT DISTINCT ON (lower(user_email)) lower(user_email) AS email, user_name AS name "
        "FROM orders ORDER BY lower(user_email), id DESC"
    ))).all()
    for email, name in order_rows:
        merged[email] = {"email": email, "name": name, "active": True, "external_id": None}
    ident_rows = (await db.execute(text(
        "SELECT lower(user_email) AS email, display_name, active, external_id FROM scim_identities"
    ))).all()
    for email, dname, active, ext in ident_rows:
        row = merged.setdefault(email, {"email": email, "name": None, "active": True, "external_id": None})
        row["name"] = row.get("name") or dname
        row["active"] = bool(active)
        row["external_id"] = ext
    return sorted(merged.values(), key=lambda r: r["email"])


@router.get("/Users")
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    filter: str | None = Query(default=None, alias="filter"),
    startIndex: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=0, le=200),
) -> dict:
    """List users, with full SCIM filter-grammar support (RFC 7644 §3.4.2.2).

    Users are derived from ``orders.user_email`` + the SCIM identity projection.
    A ``userName``/``id``/``emails eq`` filter takes an indexed single-lookup
    fast path; any other filter is parsed to an AST and evaluated in memory over
    the full user set. A malformed filter returns 400 ``invalidFilter``.
    """
    await _scim_auth(request, db, write=False)

    from app.utils.scim_filter import (
        SCIMFilterError, evaluate, parse_filter, simple_email_equality,
    )

    def _list(resources: list[dict], total: int) -> dict:
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": startIndex,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    if filter:
        try:
            node = parse_filter(filter)
        except SCIMFilterError as exc:
            return _scim_error(400, f"Invalid filter: {exc}", scim_type="invalidFilter")

        # Fast path — the ubiquitous single-user lookup.
        email = simple_email_equality(node)
        if email is not None:
            row = (await db.execute(
                select(Order.user_email, Order.user_name)
                .where(func.lower(Order.user_email) == email)
                .order_by(Order.id.desc()).limit(1)
            )).first()
            if row is not None:
                return _list([_user_resource(row[0].lower(), row[1])], 1)
            # May still exist as a SCIM projection with no orders yet.
            from app.models.scim_identity import ScimIdentity
            ident = (await db.execute(
                select(ScimIdentity).where(func.lower(ScimIdentity.user_email) == email)
            )).scalars().first()
            if ident is None:
                return _list([], 0)
            return _list([_user_resource(
                ident.user_email.lower(), ident.display_name,
                active=ident.active, external_id=ident.external_id,
            )], 1)

        # General path — parse + evaluate over the full user set.
        candidates = await _all_user_resources(db)
        matched = [r for r in candidates if evaluate(node, r)]
        total = len(matched)
        page = matched[max(0, startIndex - 1): max(0, startIndex - 1) + count]
        return _list(
            [_user_resource(r["email"], r["name"], active=r["active"], external_id=r["external_id"])
             for r in page],
            total,
        )

    # Unfiltered list — distinct lowercased emails, DB-paginated.
    total = (await db.execute(
        select(func.count(distinct(func.lower(Order.user_email))))
    )).scalar_one()
    rows = (await db.execute(
        select(distinct(func.lower(Order.user_email)).label("email"))
        .order_by(func.lower(Order.user_email))
        .offset(max(0, startIndex - 1)).limit(count)
    )).all()
    return _list([_user_resource(r[0]) for r in rows], int(total or 0))


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
    """SCIM Create → joiner.

    Persists the identity projection and, when ``scim.joiner_enabled`` is on,
    evaluates assignment rules for the user's attributes and orders the matched
    bundles (idempotent — asset types the user already holds are skipped).
    Opt-in and gated: with the flag off this is the previous accept-only no-op,
    so existing IdP integrations are unaffected.
    """
    actor = await _scim_auth(request, db, write=True)
    try:
        payload = await request.json()
    except Exception:
        return _scim_error(400, "Body must be valid JSON")

    from app.services.scim_provisioning import extract_scim_attrs, run_joiner, upsert_identity
    ext = extract_scim_attrs(payload)
    if not ext["email"]:
        return _scim_error(400, "userName or emails[].value required", scim_type="invalidValue")

    ident, is_new, reactivated, _old = await upsert_identity(db, ext, raw=payload)
    if ext["active"] and (is_new or reactivated) and await _joiner_enabled(db):
        await run_joiner(
            db, email=ext["email"], display_name=ext["display_name"],
            attributes=ext["attributes"], actor=f"api:scim ({actor})",
        )
    await db.commit()
    return _user_resource(ext["email"], ext["display_name"])


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

    from app.services.scim_provisioning import (
        extract_scim_attrs, run_joiner, run_mover, upsert_identity,
    )
    ext = extract_scim_attrs(payload)
    if not ext["email"]:
        ext["email"] = (user_id or "").strip().lower()
    email_l = ext["email"]

    active = payload.get("active", True)
    if active is False:
        # Mark the projection inactive + run the leaver flow.
        await upsert_identity(db, ext, raw=payload)
        await db.commit()
        await _maybe_run_leaver(db, email=email_l, actor=actor, raw=payload)
        return _user_resource(email_l, payload.get("displayName"))

    # Active replace — upsert projection; capture pre-change attrs to detect a mover.
    ident, is_new, reactivated, old_attrs = await upsert_identity(db, ext, raw=payload)
    if (is_new or reactivated) and await _joiner_enabled(db):
        await run_joiner(
            db, email=email_l, display_name=ext["display_name"],
            attributes=ext["attributes"], actor=f"api:scim ({actor})",
        )
    elif not is_new and not reactivated and ext["attributes"] != old_attrs:
        mode = await _mover_mode(db)
        if mode != "disabled":
            await db.commit()  # persist the new projection before reconciling
            await run_mover(
                db, email=email_l, display_name=ext["display_name"],
                attributes=ext["attributes"], actor=f"api:scim ({actor})", mode=mode,
            )
    await db.commit()
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

    # Attribute-change PATCH → mover reconciliation (existing identity only).
    from app.models.scim_identity import ScimIdentity
    from app.services.scim_provisioning import patch_ops_to_attrs, run_mover
    ident = (await db.execute(
        select(ScimIdentity).where(func.lower(ScimIdentity.user_email) == email_l)
    )).scalars().first()
    if ident is not None:
        new_attrs, changed = patch_ops_to_attrs(dict(ident.attributes or {}), operations)
        if changed:
            ident.attributes = new_attrs
            await db.commit()
            mode = await _mover_mode(db)
            if mode != "disabled":
                await run_mover(
                    db, email=email_l, display_name=ident.display_name,
                    attributes=new_attrs, actor=f"api:scim ({actor})", mode=mode,
                )

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
