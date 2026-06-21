"""Generic OIDC helper for portal SSO — works with any standards-compliant IdP.

A single code path serves every provider (Entra ID, Okta, Ping, Google, Keycloak,
Authentik, Zitadel, …). Each provider self-configures from its discovery document
(`<issuer>/.well-known/openid-configuration`); there is no vendor-specific code.

Configuration is a provider *registry* stored in app_config under `idp.<id>.*` keys,
editable at runtime via the admin settings page (no restart):

    portal.auth_required        "true" | "false"   – portal-wide login gate
    auth.ldap_enabled           "true" | "false"   – offer on-prem LDAP login too
    idp.<id>.enabled            "true" | "false"
    idp.<id>.display_name       button label, e.g. "Entra ID"
    idp.<id>.issuer             OIDC issuer URL (discovery is derived from this)
    idp.<id>.client_id
    idp.<id>.client_secret      plain, or a vault://… / ccp://… secret reference
    idp.<id>.redirect_uri       optional override (else derived from the request)
    idp.<id>.allowed_domains    comma-separated UPN/email domain allow-list (empty = any)
    idp.<id>.scopes             optional, default "openid profile email"
    idp.<id>.username_claim     optional, default "preferred_username"
    idp.<id>.email_claim        optional, default "email"
    idp.<id>.name_claim         optional, default "name"

`<id>` is a stable, URL-safe slug ([a-z0-9_-]) that also appears in the parametric
callback route `/portal/auth/{provider_id}/callback`.
"""

import logging
import re
import secrets
import time
from typing import Optional

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PROVIDER_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")

DEFAULT_SCOPES = "openid profile email"
DEFAULT_USERNAME_CLAIM = "preferred_username"
DEFAULT_EMAIL_CLAIM = "email"
DEFAULT_NAME_CLAIM = "name"

# Discovery documents change rarely; cache per-issuer for an hour.
_DISCOVERY_TTL = 3600.0
_discovery_cache: dict[str, tuple[float, dict]] = {}
# One PyJWKClient per jwks_uri (it maintains its own signing-key cache).
_jwk_clients: dict[str, "jwt.PyJWKClient"] = {}

_HTTP_TIMEOUT = httpx.Timeout(10.0)


# ── config / registry ────────────────────────────────────────────────────────

async def _load_config(db: AsyncSession, prefixes: tuple[str, ...]) -> dict:
    """Loads all app_config rows whose key starts with any of `prefixes`."""
    from app.models.config import AppConfig

    clauses = [AppConfig.key.like(f"{p}%") for p in prefixes]
    from sqlalchemy import or_

    result = await db.execute(select(AppConfig).where(or_(*clauses)))
    return {row.key: (row.value or "") for row in result.scalars().all()}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _build_provider(provider_id: str, cfg: dict) -> dict:
    """Assembles one provider dict from the flat `idp.<id>.*` config slice."""
    p = f"idp.{provider_id}."
    from app.utils.secrets import resolve_secret_value_sync

    return {
        "id": provider_id,
        "enabled": _as_bool(cfg.get(p + "enabled", "")),
        "display_name": cfg.get(p + "display_name", "").strip() or provider_id,
        "issuer": cfg.get(p + "issuer", "").strip().rstrip("/"),
        "client_id": cfg.get(p + "client_id", "").strip(),
        "client_secret": resolve_secret_value_sync(cfg.get(p + "client_secret", "").strip()),
        "redirect_uri": cfg.get(p + "redirect_uri", "").strip(),
        "allowed_domains": cfg.get(p + "allowed_domains", ""),
        "scopes": cfg.get(p + "scopes", "").strip() or DEFAULT_SCOPES,
        "username_claim": cfg.get(p + "username_claim", "").strip() or DEFAULT_USERNAME_CLAIM,
        "email_claim": cfg.get(p + "email_claim", "").strip() or DEFAULT_EMAIL_CLAIM,
        "name_claim": cfg.get(p + "name_claim", "").strip() or DEFAULT_NAME_CLAIM,
    }


async def load_providers(db: AsyncSession) -> list[dict]:
    """Returns all configured providers (enabled or not), sorted by display name."""
    cfg = await _load_config(db, ("idp.",))
    ids: set[str] = set()
    for key in cfg:
        # key shape: idp.<id>.<field>
        parts = key.split(".", 2)
        if len(parts) == 3 and PROVIDER_ID_RE.match(parts[1]):
            ids.add(parts[1])
    providers = [_build_provider(pid, cfg) for pid in ids]
    providers.sort(key=lambda p: p["display_name"].lower())
    return providers


async def get_provider(db: AsyncSession, provider_id: str) -> Optional[dict]:
    """Returns a single provider by id, or None if it has no config rows."""
    if not PROVIDER_ID_RE.match(provider_id or ""):
        return None
    cfg = await _load_config(db, (f"idp.{provider_id}.",))
    if not cfg:
        return None
    return _build_provider(provider_id, cfg)


def provider_is_usable(provider: dict) -> bool:
    """True if the provider has the minimum config needed to attempt a login."""
    return bool(provider.get("issuer") and provider.get("client_id") and provider.get("client_secret"))


async def enabled_providers(db: AsyncSession) -> list[dict]:
    """Providers that are both enabled and minimally configured."""
    return [p for p in await load_providers(db) if p["enabled"] and provider_is_usable(p)]


async def auth_required(db: AsyncSession) -> bool:
    cfg = await _load_config(db, ("portal.auth_required",))
    return _as_bool(cfg.get("portal.auth_required", ""))


async def ldap_enabled(db: AsyncSession) -> bool:
    cfg = await _load_config(db, ("auth.ldap_enabled",))
    return _as_bool(cfg.get("auth.ldap_enabled", ""))


# ── discovery ────────────────────────────────────────────────────────────────

def discover(issuer: str) -> dict:
    """Fetches and caches the provider's OIDC discovery document.

    Raises ValueError on network/format errors with a human-readable message.
    """
    issuer = (issuer or "").strip().rstrip("/")
    if not issuer:
        raise ValueError("No issuer URL configured for this provider.")

    cached = _discovery_cache.get(issuer)
    if cached and (time.time() - cached[0]) < _DISCOVERY_TTL:
        return cached[1]

    url = f"{issuer}/.well-known/openid-configuration"
    try:
        resp = httpx.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        meta = resp.json()
    except httpx.HTTPError as exc:
        raise ValueError(f"Could not fetch discovery document from {url}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"Discovery document at {url} is not valid JSON: {exc}") from exc

    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if required not in meta:
            raise ValueError(f"Discovery document at {url} is missing '{required}'.")

    _discovery_cache[issuer] = (time.time(), meta)
    return meta


def _jwk_client(jwks_uri: str) -> "jwt.PyJWKClient":
    client = _jwk_clients.get(jwks_uri)
    if client is None:
        client = jwt.PyJWKClient(jwks_uri)
        _jwk_clients[jwks_uri] = client
    return client


# ── auth code flow ───────────────────────────────────────────────────────────

def new_state() -> str:
    """Cryptographically random CSRF state token."""
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    """Cryptographically random OIDC nonce (replay protection for the ID token)."""
    return secrets.token_urlsafe(32)


def build_auth_url(provider: dict, metadata: dict, redirect_uri: str, state: str, nonce: str) -> str:
    """Builds the authorization-endpoint redirect URL for this provider."""
    from urllib.parse import urlencode

    params = {
        "client_id": provider["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": provider["scopes"],
        "state": state,
        "nonce": nonce,
    }
    sep = "&" if "?" in metadata["authorization_endpoint"] else "?"
    return f"{metadata['authorization_endpoint']}{sep}{urlencode(params)}"


def exchange_code(
    provider: dict,
    metadata: dict,
    code: str,
    redirect_uri: str,
    expected_nonce: str,
) -> dict:
    """Exchanges an auth code for tokens and returns the *validated* ID-token claims.

    Validates the ID-token signature against the provider JWKS plus the standard
    iss / aud / exp claims and the OIDC nonce. Raises ValueError on any failure.
    """
    try:
        resp = httpx.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider["client_id"],
                "client_secret": provider["client_secret"],
            },
            headers={"Accept": "application/json"},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ValueError(f"Token request failed: {exc}") from exc

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code != 200 or "error" in payload:
        err = payload.get("error", f"HTTP {resp.status_code}")
        desc = payload.get("error_description", "")
        raise ValueError(f"Token exchange failed: {err} – {desc}".strip(" –"))

    id_token = payload.get("id_token")
    if not id_token:
        raise ValueError("Token response did not include an id_token.")

    algorithms = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]
    try:
        signing_key = _jwk_client(metadata["jwks_uri"]).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=algorithms,
            audience=provider["client_id"],
            issuer=metadata["issuer"],
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"ID token validation failed: {exc}") from exc
    except Exception as exc:  # JWKS fetch / key errors
        raise ValueError(f"Could not validate ID token signature: {exc}") from exc

    if expected_nonce and claims.get("nonce") != expected_nonce:
        raise ValueError("ID token nonce mismatch — possible replay attack.")

    return claims


def extract_user(provider: dict, claims: dict) -> dict:
    """Maps validated ID-token claims to the portal_user dict via the provider's
    claim-mapping config. Returns {"email", "name", "oid", "upn", "provider"}."""
    upn = (
        claims.get(provider["username_claim"])
        or claims.get("preferred_username")
        or claims.get("upn")
        or ""
    )
    email = claims.get(provider["email_claim"]) or upn
    name = claims.get(provider["name_claim"]) or (email.split("@")[0] if email else "")
    oid = claims.get("oid") or claims.get("sub") or ""
    return {"email": email, "name": name, "oid": oid, "upn": upn, "provider": provider["id"]}


def check_allowed_domains(user: dict, allowed_domains: str) -> bool:
    """True if the user's UPN/email domain is allowed.

    allowed_domains: comma-separated; empty string = allow any domain.
    """
    if not allowed_domains.strip():
        return True
    domains = [d.strip().lower() for d in allowed_domains.split(",") if d.strip()]
    if not domains:
        return True
    upn = user.get("upn") or user.get("email") or ""
    user_domain = upn.split("@")[-1].lower() if "@" in upn else ""
    return user_domain in domains


def logout_url(provider: dict, metadata: dict, post_logout_uri: str) -> Optional[str]:
    """RP-initiated logout URL via the provider's end_session_endpoint, or None
    if the provider does not advertise one."""
    from urllib.parse import urlencode

    endpoint = metadata.get("end_session_endpoint")
    if not endpoint:
        return None
    params = {
        "post_logout_redirect_uri": post_logout_uri,
        "client_id": provider["client_id"],
    }
    sep = "&" if "?" in endpoint else "?"
    return f"{endpoint}{sep}{urlencode(params)}"
