"""Portal authentication routes — generic OIDC (any compliant IdP) + on-prem LDAP.

A single OIDC code path serves every provider in the registry (Entra, Okta, Ping,
Google, Keycloak, …); see `app.utils.oidc`. On-prem LDAP username/password login is
offered alongside as a non-OIDC method.

Routes:
  GET  /portal/login                       → pick a method (auto-skipped when only one)
  GET  /portal/auth/{provider_id}/callback → exchange code, set session, redirect to portal
  POST /portal/auth/ldap                   → LDAP bind, set session, redirect to portal
  GET  /portal/logout                      → clear session, RP-initiated IdP logout
"""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.templates_instance import templates
from app.utils import oidc
from app.utils.ad_lookup import authenticate_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portal", tags=["auth"])


async def _maybe_first_login_onboarding(db: AsyncSession, email: str, name: str | None) -> None:
    """Opt-in: on a user's first portal login, evaluate assignment rules and
    order the matched bundles (idempotent). Best-effort — never blocks login.

    Gated by ``onboarding.eval_on_first_login`` and a **zero-orders** check, so
    it fires once (once the user has any order the gate closes). AD attributes
    are resolved fresh; the bundle-order service handles idempotency.
    """
    try:
        import asyncio

        from sqlalchemy import func, select, text

        from app.models.config import AppConfig
        from app.models.order import Order

        flag = (await db.execute(
            select(AppConfig.value).where(AppConfig.key == "onboarding.eval_on_first_login")
        )).scalar_one_or_none()
        if (flag or "false").strip().lower() not in ("1", "true", "yes", "on"):
            return
        if not email:
            return
        # First login = no orders yet.
        has_order = (await db.execute(
            select(Order.id).where(func.lower(Order.user_email) == email.lower()).limit(1)
        )).first()
        if has_order:
            return

        from app.services.bundle_order import order_bundle
        from app.services.onboarding import build_user_context, evaluate_assignment_rules
        from app.utils.ad_lookup import lookup_user

        ad = await asyncio.to_thread(lookup_user, email)
        attrs = {k: ad.get(k) for k in
                 ("department", "cost_center", "company", "employee_id", "title")
                 if ad.get(k) is not None} if ad.get("success") else {}
        matched = await evaluate_assignment_rules(db, build_user_context(attrs))
        if not matched:
            return
        from app.models.bundle import Bundle
        for m in matched:
            bundle = await db.get(Bundle, m["bundle_id"])
            if bundle and bundle.is_active:
                await order_bundle(
                    db, bundle=bundle, recipient_email=email, recipient_name=name or email,
                    requester_email=email, requester_name=name or email,
                    origin="rule_based", actor="api:onboarding:first_login",
                )
        logger.info("[auth] first-login onboarding: %s matched %d bundle(s)", email, len(matched))
    except Exception as exc:  # noqa: BLE001 — onboarding must never break login
        logger.warning("[auth] first-login onboarding failed for %s: %s", email, exc)


def _auth_error(request: Request, title: str, message: str, status: int):
    return templates.TemplateResponse(
        request, "portal/auth_error.html",
        {"title": title, "message": message},
        status_code=status,
    )


def _redirect_uri_for(request: Request, provider: dict) -> str:
    """Per-provider redirect URI: explicit override, else derived from the route."""
    return provider["redirect_uri"] or str(
        request.url_for("portal_auth_callback", provider_id=provider["id"])
    )


async def _start_oidc(request: Request, provider: dict):
    """Begins the auth-code flow: stash CSRF state + nonce, redirect to the IdP."""
    try:
        metadata = oidc.discover(provider["issuer"])
    except ValueError as exc:
        logger.error("[auth] Discovery failed for provider '%s': %s", provider["id"], exc)
        return _auth_error(
            request, "Login unavailable",
            f"The identity provider '{provider['display_name']}' could not be reached. {exc}",
            502,
        )

    state = oidc.new_state()
    nonce = oidc.new_nonce()
    request.session["oauth_state"] = state
    request.session["oauth_nonce"] = nonce

    redirect_uri = _redirect_uri_for(request, provider)
    auth_url = oidc.build_auth_url(provider, metadata, redirect_uri, state, nonce)
    logger.info("[auth] Redirecting to IdP '%s' login", provider["id"])
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/login")
async def portal_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
    provider: str = "",
    method: str = "",
):
    """Routes the user to a login method.

    - explicit `?provider=<id>` → start that OIDC provider
    - explicit `?method=ldap`   → render the LDAP form
    - otherwise: auto-skip to the single enabled method, or show the picker
    """
    providers = await oidc.enabled_providers(db)
    ldap = await oidc.ldap_enabled(db)

    # Explicit selection from the picker
    if provider:
        chosen = next((p for p in providers if p["id"] == provider), None)
        if chosen:
            return await _start_oidc(request, chosen)
        return _auth_error(request, "Login failed", "Unknown or disabled identity provider.", 404)
    if method == "ldap" and ldap:
        return templates.TemplateResponse(
            request, "portal/login.html", {"error": None, "username": ""}
        )

    total = len(providers) + (1 if ldap else 0)

    if total == 0:
        # No method configured — treat the portal as open.
        return RedirectResponse(url="/portal/", status_code=302)

    if total == 1:
        if providers:
            return await _start_oidc(request, providers[0])
        return templates.TemplateResponse(
            request, "portal/login.html", {"error": None, "username": ""}
        )

    # Multiple methods → picker
    return templates.TemplateResponse(
        request, "portal/login_select.html",
        {"providers": providers, "ldap_enabled": ldap},
    )


@router.post("/auth/ldap", response_class=HTMLResponse)
async def portal_ldap_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate portal user via LDAP bind (on-prem AD)."""
    result = authenticate_user(username, password)

    if not result.get("success"):
        error_msg = result.get("error", "Authentication failed")
        logger.warning("[auth/ldap] Failed login for '%s': %s", username, error_msg)
        return templates.TemplateResponse(
            request, "portal/login.html",
            {"error": error_msg, "username": username},
            status_code=401,
        )

    request.session["portal_user"] = {
        "email": result["email"],
        "name": result["name"],
        "oid": result["sam_account"],
        "upn": result["upn"],
        "provider": "ldap",
    }
    logger.info("[auth/ldap] Login successful: %s (%s)", result["name"], result["email"])

    await _maybe_first_login_onboarding(db, result["email"], result.get("name"))
    next_url = request.session.pop("login_next", "/portal/")
    return RedirectResponse(url=next_url, status_code=302)


@router.get("/auth/{provider_id}/callback", response_class=HTMLResponse, name="portal_auth_callback")
async def portal_auth_callback(
    request: Request,
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Handles the IdP redirect after authentication for `provider_id`."""
    if error:
        logger.warning("[auth] IdP '%s' returned error: %s – %s", provider_id, error, error_description)
        return _auth_error(request, "Login failed", error_description or error, 401)

    # CSRF state check
    expected_state = request.session.pop("oauth_state", None)
    expected_nonce = request.session.pop("oauth_nonce", None)
    if not state or state != expected_state:
        logger.warning("[auth] OAuth state mismatch — possible CSRF attempt")
        return _auth_error(
            request, "Login failed",
            "Invalid state parameter. Please try again.", 400,
        )

    provider = await oidc.get_provider(db, provider_id)
    if provider is None or not provider["enabled"]:
        return _auth_error(request, "Login failed", "Unknown or disabled identity provider.", 404)

    try:
        metadata = oidc.discover(provider["issuer"])
    except ValueError as exc:
        logger.error("[auth] Discovery failed during callback for '%s': %s", provider_id, exc)
        return _auth_error(request, "Login failed", str(exc), 502)

    redirect_uri = _redirect_uri_for(request, provider)
    try:
        claims = oidc.exchange_code(
            provider, metadata, code=code, redirect_uri=redirect_uri,
            expected_nonce=expected_nonce or "",
        )
    except ValueError as exc:
        logger.error("[auth] Token exchange failed for '%s': %s", provider_id, exc)
        return _auth_error(request, "Login failed", str(exc), 401)

    user = oidc.extract_user(provider, claims)

    if not oidc.check_allowed_domains(user, provider["allowed_domains"]):
        logger.warning("[auth] Login rejected — domain not allowed: %s", user.get("upn"))
        return _auth_error(
            request, "Access denied",
            (
                f"Your account ({user.get('upn')}) is not permitted to access this portal. "
                "Please contact your IT administrator."
            ),
            403,
        )

    request.session["portal_user"] = user
    logger.info("[auth] Login successful via '%s': %s (%s)", provider_id, user.get("name"), user.get("email"))

    await _maybe_first_login_onboarding(db, user.get("email") or "", user.get("name"))
    next_url = request.session.pop("login_next", "/portal/")
    return RedirectResponse(url=next_url, status_code=302)


@router.get("/logout")
async def portal_logout(request: Request, db: AsyncSession = Depends(get_db)):
    """Clears the session and performs RP-initiated logout at the IdP when possible."""
    user = request.session.get("portal_user") or {}
    provider_id = user.get("provider")
    request.session.clear()

    post_logout_uri = str(request.base_url).rstrip("/") + "/portal/login"

    if provider_id and provider_id != "ldap":
        provider = await oidc.get_provider(db, provider_id)
        if provider:
            try:
                metadata = oidc.discover(provider["issuer"])
                url = oidc.logout_url(provider, metadata, post_logout_uri)
                if url:
                    return RedirectResponse(url=url, status_code=302)
            except ValueError:
                logger.warning("[auth] Logout discovery failed for '%s'; local logout only", provider_id)

    return RedirectResponse(url="/portal/login", status_code=302)
