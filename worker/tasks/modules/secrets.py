"""Worker-side mirror of ``app.utils.secrets`` (sync only).

The worker package intentionally doesn't import from the API package
(``audit_helper.py`` follows the same boundary). This module duplicates
the small sync resolver so worker code reading credentials from
``app_config`` can dereference ``vault://`` / ``ccp://`` references the
same way the API does.

Reference grammar matches ``app.utils.secrets`` exactly:

* ``vault://<path>[#<field>]`` — KV v2 lookup against the configured mount.
* ``ccp://[<safe>/]<object>`` — CyberArk CCP/AIM lookup.

Plain strings pass through unchanged. Resolution failures are logged
and return the empty string — the worker never raises on a Vault
outage; the credential just becomes invalid and the calling task
fails with the underlying-system's auth error, which is a clearer
signal than "ipSolis blew up".
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_KNOWN_SCHEMES = ("vault://", "ccp://")
_cache: dict[str, tuple[float, str]] = {}


def is_secret_reference(value: str | None) -> bool:
    return isinstance(value, str) and any(value.startswith(s) for s in _KNOWN_SCHEMES)


def resolve_secret_value(db: Session, raw_value: str | None) -> str:
    """Resolve a possibly-reference value. Sync — caller passes a live Session."""
    if not raw_value or not isinstance(raw_value, str):
        return raw_value or ""
    if not is_secret_reference(raw_value):
        return raw_value

    cached = _cache_get(raw_value)
    if cached is not None:
        return cached

    cfg = _load_secret_cfg(db)
    return _dispatch(raw_value, cfg)


def get_secret_config(db: Session, key: str, default: str = "") -> str:
    """Convenience: ``get_config`` + ``resolve_secret_value`` in one call.

    Worker code that reads a credential row (vsphere.password, etc.)
    should funnel through here instead of ``get_config`` directly so
    upgraded installs get external-secret support without touching
    every call site.
    """
    row = db.execute(
        text("SELECT value FROM app_config WHERE key = :key"),
        {"key": key},
    ).fetchone()
    raw = row[0] if row and row[0] else default
    return resolve_secret_value(db, raw)


# ── Internals ────────────────────────────────────────────────────────────────

def _load_secret_cfg(db: Session) -> dict[str, str]:
    rows = db.execute(
        text("SELECT key, value FROM app_config WHERE key LIKE 'secret.%%'")
    ).fetchall()
    return {row[0][len("secret."):]: (row[1] or "") for row in rows}


def _cache_get(key: str) -> str | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at < time.time():
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: str, *, ttl_seconds: int) -> None:
    _cache[key] = (time.time() + max(1, ttl_seconds), value)


def _dispatch(raw_value: str, cfg: dict[str, str]) -> str:
    try:
        if raw_value.startswith("vault://"):
            value = _resolve_vault(raw_value[len("vault://"):], cfg)
        elif raw_value.startswith("ccp://"):
            value = _resolve_ccp(raw_value[len("ccp://"):], cfg)
        else:
            return raw_value
    except Exception as exc:  # noqa: BLE001
        logger.warning("secrets: resolution failed for %r: %s", raw_value, exc)
        return ""
    try:
        ttl = int(cfg.get("cache_ttl_seconds", "60") or "60")
    except ValueError:
        ttl = 60
    _cache_set(raw_value, value, ttl_seconds=ttl)
    return value


def _resolve_vault(path: str, cfg: dict[str, str]) -> str:
    if "#" in path:
        path, field = path.split("#", 1)
    else:
        field = "value"
    path = path.strip().strip("/")
    if not path:
        raise ValueError("empty vault path")

    base = (cfg.get("vault.url") or "").strip().rstrip("/")
    token = (cfg.get("vault.token") or "").strip()
    mount = (cfg.get("vault.kv_mount") or "secret").strip().strip("/") or "secret"
    namespace = (cfg.get("vault.namespace") or "").strip()
    if not base or not token:
        raise ValueError("vault.url or vault.token is empty")

    url = f"{base}/v1/{mount}/data/{path}"
    headers = {"X-Vault-Token": token, "Accept": "application/json"}
    if namespace:
        headers["X-Vault-Namespace"] = namespace

    body = _http_get_json(url, headers=headers)
    inner = (((body or {}).get("data") or {}).get("data") or {})
    if field not in inner:
        raise KeyError(f"vault: field {field!r} not present at {path!r}")
    value = inner[field]
    if not isinstance(value, str):
        raise TypeError(f"vault: field {field!r} at {path!r} is not a string")
    return value


def _resolve_ccp(reference: str, cfg: dict[str, str]) -> str:
    if "/" in reference:
        safe, obj = reference.split("/", 1)
    else:
        safe = (cfg.get("ccp.safe") or "").strip()
        obj = reference
    obj = obj.strip()
    if not obj:
        raise ValueError("empty ccp object")

    base = (cfg.get("ccp.url") or "").strip().rstrip("/")
    app_id = (cfg.get("ccp.app_id") or "").strip()
    if not base or not app_id:
        raise ValueError("ccp.url or ccp.app_id is empty")

    qs = {"AppID": app_id, "Object": obj}
    if safe:
        qs["Safe"] = safe
    url = f"{base}/api/Accounts?" + urllib.parse.urlencode(qs)
    verify_tls = (cfg.get("ccp.verify_tls") or "true").strip().lower() not in (
        "false", "0", "no", "off",
    )
    pem = (cfg.get("ccp.client_cert_pem") or "").strip()

    body = _http_get_json(
        url, headers={"Accept": "application/json"},
        verify_tls=verify_tls, client_cert_pem=pem or None,
    )
    if not isinstance(body, dict) or "Content" not in body:
        raise KeyError(f"ccp: response missing 'Content' for {obj!r}")
    value = body.get("Content")
    if not isinstance(value, str):
        raise TypeError(f"ccp: 'Content' for {obj!r} is not a string")
    return value


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    verify_tls: bool = True,
    client_cert_pem: str | None = None,
) -> Any:
    ctx: ssl.SSLContext | None = None
    cert_tempfile: str | None = None
    if not verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if client_cert_pem:
        fd, cert_tempfile = tempfile.mkstemp(suffix=".pem", prefix="ipsolis-ccp-")
        try:
            os.write(fd, client_cert_pem.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(cert_tempfile, 0o600)
        if ctx is None:
            ctx = ssl.create_default_context()
        ctx.load_cert_chain(certfile=cert_tempfile)

    try:
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS, context=ctx) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}") from e
    finally:
        if cert_tempfile:
            try:
                os.unlink(cert_tempfile)
            except Exception:  # noqa: BLE001
                pass
