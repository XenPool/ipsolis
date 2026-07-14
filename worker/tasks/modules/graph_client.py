"""Microsoft Graph client for Entra (cloud-only) group provisioning.

App-only (client-credentials) auth against Microsoft Graph. Grants/revokes
Entra security-group membership for the ``entra_group`` access-target type,
mirroring the AD-group handlers in ``target_executor``. Uses only stdlib
``urllib`` (no extra worker deps), same as the Slack/Teams senders.

Credentials come from ``graph.*`` app_config (a dedicated app registration with
Application permissions **GroupMember.ReadWrite.All** + **User.Read.All**, admin
consent granted). Distinct from the OIDC login app — that is delegated sign-in.

Group ``identifier`` is the Entra group **object id** (GUID); the principal is
an email / UPN, resolved to the user's object id via Graph.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 20

# In-process token cache keyed by (tenant, client): (access_token, expiry_epoch).
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


def _graph_config(db: Session) -> tuple[str, str, str]:
    from tasks.modules.config_reader import get_config
    from tasks.modules.secrets import get_secret_config
    tenant = (get_config(db, "graph.tenant_id", "") or "").strip()
    client = (get_config(db, "graph.client_id", "") or "").strip()
    secret = (get_secret_config(db, "graph.client_secret", "") or "").strip()
    return tenant, client, secret


def _graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Acquire (and cache) an app-only Graph token via client_credentials."""
    key = (tenant_id, client_id)
    now = time.time()
    cached = _token_cache.get(key)
    if cached and cached[1] - 60 > now:
        return cached[0]

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error_description", "")
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Graph token acquire failed (HTTP {e.code}): {detail[:200]}") from e
    token = body["access_token"]
    _token_cache[key] = (token, now + int(body.get("expires_in", 3600)))
    return token


def _graph_request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw.decode("utf-8", "replace")[:300]}


def _resolve_user_id(token: str, principal: str) -> str:
    """Resolve an email / UPN to the Entra user's object id."""
    ident = urllib.parse.quote(principal, safe="")
    status, body = _graph_request("GET", f"{_GRAPH}/users/{ident}?$select=id", token)
    if status == 200 and body.get("id"):
        return body["id"]
    # Fallback: filter on mail / UPN (covers guest / alias mismatches).
    p = principal.replace("'", "''")
    filt = urllib.parse.quote(f"mail eq '{p}' or userPrincipalName eq '{p}'", safe="")
    status, body = _graph_request("GET", f"{_GRAPH}/users?$filter={filt}&$select=id", token)
    values = body.get("value") or []
    if status == 200 and values:
        return values[0]["id"]
    raise ValueError(f"Entra user '{principal}' not found (HTTP {status})")


def _client(db: Session) -> tuple[str, str]:
    tenant, client, secret = _graph_config(db)
    if not (tenant and client and secret):
        raise RuntimeError(
            "Entra group provisioning is not configured — set graph.tenant_id / "
            "graph.client_id / graph.client_secret (Settings → Compliance)."
        )
    return _graph_token(tenant, client, secret), tenant


def graph_add_member(db: Session, group_id: str, principal: str) -> str:
    """Add ``principal`` to the Entra group (idempotent). Returns the user id."""
    token, _ = _client(db)
    uid = _resolve_user_id(token, principal)
    body = {"@odata.id": f"{_GRAPH}/directoryObjects/{uid}"}
    status, resp = _graph_request(
        "POST", f"{_GRAPH}/groups/{group_id}/members/$ref", token, body,
    )
    if status in (200, 204):
        return uid
    msg = json.dumps(resp).lower()
    # Idempotent success — already a member.
    if status == 400 and ("already exist" in msg or "references already exist" in msg):
        return uid
    raise RuntimeError(f"Graph add member failed (HTTP {status}): {json.dumps(resp)[:300]}")


def graph_remove_member(db: Session, group_id: str, principal: str) -> str:
    """Remove ``principal`` from the Entra group (idempotent). Returns the user id."""
    token, _ = _client(db)
    uid = _resolve_user_id(token, principal)
    status, resp = _graph_request(
        "DELETE", f"{_GRAPH}/groups/{group_id}/members/{uid}/$ref", token,
    )
    if status in (200, 204, 404):  # 404 = not a member → idempotent
        return uid
    raise RuntimeError(f"Graph remove member failed (HTTP {status}): {json.dumps(resp)[:300]}")
