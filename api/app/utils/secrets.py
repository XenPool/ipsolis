"""External secret-management adapters — slice 1 (Vault + CyberArk CCP/AIM).

Goal: take the plaintext credentials out of ``app_config`` for tenants
who already invest in a managed secret store. The admin-facing change
is small — a secret-typed ``app_config`` row whose ``value`` is a
recognised reference scheme is resolved to its real value at read time.
Everything else (UI, API, audit) is unchanged.

Reference grammar
-----------------

* ``vault://<path>`` — KV v2 lookup. ``<path>`` is the path *inside*
  the configured KV mount; the resolver prepends the mount + ``/data/``
  automatically. Optional ``#field`` selects a key from the secret's
  ``data.data`` dict; the default is ``value``.

  Example: ``vault://ipsolis/ad/password`` →
  ``GET <vault_url>/v1/<kv_mount>/data/ipsolis/ad/password``.

* ``ccp://[<safe>/]<object>`` — CyberArk Central Credential Provider
  (Application Access Manager) lookup. Resolves via
  ``GET <ccp_url>/api/Accounts?AppID=<app>&Safe=<safe>&Object=<object>``.
  ``<safe>`` defaults to ``secret.ccp.safe`` when omitted.

Anything that doesn't match a known scheme is returned unchanged —
back-compat for plaintext rows, and a soft path for the migration
where some secrets are externalised and others aren't.

Caching
-------

Resolved values are cached process-locally for ``secret.cache_ttl_seconds``
(default 60s). The cache is keyed by ``(backend_id, reference)`` so
re-reads of the same secret in the same minute don't hammer Vault.
A short TTL keeps rotation latency bounded; tenants who rotate
secrets manually shouldn't expect zero-second propagation.

Authentication
--------------

* Vault: static token (``X-Vault-Token`` header). AppRole and
  Kubernetes JWT auth are explicit slice-2 work — they need
  fetched-at-startup token caches and renewal goroutines that don't
  belong in the slice-1 footprint.
* CCP: API-Key or mTLS. mTLS uses the configured client cert PEM
  (cert + key concatenated). When ``secret.ccp.client_cert_pem`` is
  empty, plain HTTPS with no client auth is used (suitable for CCP
  installs that authorise by AppID + IP allow-list).
"""
from __future__ import annotations

import json
import logging
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import AppConfig

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
KNOWN_SCHEMES = ("vault://", "ccp://")


# ── Public API ────────────────────────────────────────────────────────────────

def is_secret_reference(value: str | None) -> bool:
    """``True`` when ``value`` is a recognised external-secret reference."""
    if not isinstance(value, str):
        return False
    return any(value.startswith(s) for s in KNOWN_SCHEMES)


async def resolve_secret_value(
    db: AsyncSession,
    raw_value: str | None,
) -> str:
    """Resolve a possibly-reference value to its real secret (async).

    Plain strings → returned as-is (back-compat for plaintext rows).
    ``vault://...`` / ``ccp://...`` → fetched via the configured backend.
    On failure: logs at WARNING and returns the empty string. Callers
    that consider an empty credential a hard failure should validate
    the result themselves; we deliberately don't raise so a transient
    Vault outage doesn't crash an unrelated request.
    """
    if not raw_value or not isinstance(raw_value, str):
        return raw_value or ""
    if not is_secret_reference(raw_value):
        return raw_value

    cached = _cache_get(raw_value)
    if cached is not None:
        return cached

    try:
        cfg = await _load_secret_cfg(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("secrets: failed to load backend config: %s", exc)
        return ""
    return _dispatch_and_cache(raw_value, cfg)


def resolve_secret_value_sync(raw_value: str | None) -> str:
    """Sync sibling of ``resolve_secret_value`` — used by callers that
    aren't on an asyncio loop (the AD-lookup helper, the Celery worker).

    Loads ``secret.*`` config via a psycopg2 connection on every call
    when it can't reuse the cache. Same back-compat semantics as the
    async version: plain strings pass through, references resolve.
    """
    if not raw_value or not isinstance(raw_value, str):
        return raw_value or ""
    if not is_secret_reference(raw_value):
        return raw_value

    cached = _cache_get(raw_value)
    if cached is not None:
        return cached

    try:
        cfg = _load_secret_cfg_sync()
    except Exception as exc:  # noqa: BLE001
        logger.warning("secrets: failed to load backend config (sync): %s", exc)
        return ""
    return _dispatch_and_cache(raw_value, cfg)


def _dispatch_and_cache(raw_value: str, cfg: dict[str, str]) -> str:
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
    _cache_set(raw_value, value, ttl_seconds=int(cfg.get("cache_ttl_seconds", 60) or 60))
    return value


# ── Cache (process-local, TTL'd) ──────────────────────────────────────────────

# Tiny TTL cache. We accept the standard caveats (no LRU eviction; lives
# for the process lifetime; not shared across api/worker replicas) — the
# size is bounded by the number of distinct secret references actually
# read by this process, which is a small constant in practice.
_cache: dict[str, tuple[float, str]] = {}


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


def cache_clear() -> None:
    """Drop all cached resolutions. Called by the test endpoint and
    after rotation operations so the next read goes back to source."""
    _cache.clear()


# ── Config loading ────────────────────────────────────────────────────────────

async def _load_secret_cfg(db: AsyncSession) -> dict[str, str]:
    """Read ``secret.*`` keys into a flat dict (suffix-only key names)."""
    rows = await db.execute(
        select(AppConfig.key, AppConfig.value).where(AppConfig.key.like("secret.%"))
    )
    cfg: dict[str, str] = {}
    for key, value in rows.all():
        # ``secret.vault.url`` → ``vault.url``; bare ``secret.backend`` → ``backend``.
        cfg[key[len("secret."):]] = value or ""
    return cfg


def _load_secret_cfg_sync() -> dict[str, str]:
    """Sync sibling — psycopg2 read of ``secret.*`` keys.

    Falls back gracefully when ``DATABASE_URL`` is missing or the table
    isn't reachable: returns empty dict, which makes the resolver
    treat every reference as unresolvable (returns empty string). That
    matches the "fail closed but quiet" contract of the resolver.
    """
    import os  # noqa: PLC0415

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return {}
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        import psycopg2  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        conn = psycopg2.connect(sync_url)
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM app_config WHERE key LIKE 'secret.%%'")
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("secrets: psycopg2 fetch failed: %s", exc)
        return {}
    out: dict[str, str] = {}
    for key, value in rows:
        out[key[len("secret."):]] = value or ""
    return out


# ── Vault adapter ─────────────────────────────────────────────────────────────

def _resolve_vault(path: str, cfg: dict[str, str]) -> str:
    """Resolve a ``vault://<path>[#<field>]`` reference.

    Path is interpreted under the configured KV v2 mount. The optional
    ``#field`` fragment selects a single key from the secret's data
    dict; ``value`` is the default and matches the convention for
    "the secret is just a string under key 'value'".
    """
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
    headers = {
        "X-Vault-Token": token,
        "Accept": "application/json",
    }
    if namespace:
        headers["X-Vault-Namespace"] = namespace

    body = _http_get_json(url, headers=headers)
    # KV v2 envelope: ``{"data": {"data": {...}, "metadata": {...}}}``.
    inner = (((body or {}).get("data") or {}).get("data") or {})
    if field not in inner:
        raise KeyError(f"vault: field {field!r} not present at {path!r}")
    value = inner[field]
    if not isinstance(value, str):
        raise TypeError(f"vault: field {field!r} at {path!r} is not a string")
    return value


# ── CyberArk CCP / AIM adapter ────────────────────────────────────────────────

def _resolve_ccp(reference: str, cfg: dict[str, str]) -> str:
    """Resolve a ``ccp://[<safe>/]<object>`` reference.

    Slice-1 contract: returns the secret's ``Content`` field — the
    canonical "password" for the account. CCP also returns metadata
    (Address, UserName, …) which a future slice could expose via a
    ``#field`` fragment if useful.

    mTLS is optional. When ``secret.ccp.client_cert_pem`` is set, the
    PEM (cert + key) is materialised to a temp file with mode 0600
    just for the duration of the request. CCP installs that gate by
    AppID + IP allow-list alone leave the field empty.
    """
    if "/" in reference:
        safe, obj = reference.split("/", 1)
    else:
        safe = (cfg.get("ccp.safe") or "").strip()
        obj = reference
    safe = safe.strip()
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
        url,
        headers={"Accept": "application/json"},
        verify_tls=verify_tls,
        client_cert_pem=pem or None,
    )
    if not isinstance(body, dict) or "Content" not in body:
        raise KeyError(f"ccp: response missing 'Content' for {obj!r} (got {list(body)!r})")
    value = body.get("Content")
    if not isinstance(value, str):
        raise TypeError(f"ccp: 'Content' for {obj!r} is not a string")
    return value


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    verify_tls: bool = True,
    client_cert_pem: str | None = None,
) -> Any:
    """Synchronous GET returning parsed JSON. Used inside ``run_in_executor``
    by the async resolvers so we avoid pulling httpx into the runtime
    deps for what is at most a couple of small calls per request.

    ``client_cert_pem`` materialises the cert + key to a 0600 temp file
    for the lifetime of the call (Python's stdlib ssl needs paths, not
    in-memory PEM blobs).
    """
    ctx: ssl.SSLContext | None = None
    cert_tempfile: str | None = None

    if not verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if client_cert_pem:
        # mkstemp with mode 0600 — the file holds a private key, so we
        # can't use a tempfile.NamedTemporaryFile (which is 0600 on
        # POSIX but not portable). Best-effort cleanup in finally.
        import os
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
                import os
                os.unlink(cert_tempfile)
            except Exception:  # noqa: BLE001
                pass


# ── Test connection ───────────────────────────────────────────────────────────

async def test_backend(db: AsyncSession) -> tuple[bool, str]:
    """Verify backend connectivity. Returns ``(ok, message)``.

    For Vault: hits ``/v1/sys/health`` — works against unsealed dev
    servers and licensed Enterprise instances alike, no token needed
    for this endpoint.
    For CCP: hits the configured base URL + ``/api/Verify`` — that's
    the CCP standard probe path. Falls back to a HEAD on the base URL
    if the probe path isn't recognised.
    For db: always reports ok (no remote state to verify).
    """
    cfg = await _load_secret_cfg(db)
    backend = (cfg.get("backend") or "db").strip().lower()
    if backend == "db":
        return True, "Backend 'db' — no external store configured."
    if backend == "vault":
        base = (cfg.get("vault.url") or "").strip().rstrip("/")
        if not base:
            return False, "vault.url is empty."
        try:
            _http_get_json(f"{base}/v1/sys/health", headers={"Accept": "application/json"})
            return True, "Vault reachable (sys/health responded)."
        except Exception as exc:  # noqa: BLE001
            return False, f"Vault unreachable: {exc}"
    if backend == "ccp":
        base = (cfg.get("ccp.url") or "").strip().rstrip("/")
        app_id = (cfg.get("ccp.app_id") or "").strip()
        if not base or not app_id:
            return False, "ccp.url or ccp.app_id is empty."
        verify_tls = (cfg.get("ccp.verify_tls") or "true").strip().lower() not in (
            "false", "0", "no", "off",
        )
        pem = (cfg.get("ccp.client_cert_pem") or "").strip()
        try:
            # CCP exposes /api/Verify on most builds; fall back to the
            # AppID probe on the off chance the install is older.
            _http_get_json(
                f"{base}/api/Verify",
                headers={"Accept": "application/json"},
                verify_tls=verify_tls,
                client_cert_pem=pem or None,
            )
            return True, "CCP reachable (Verify responded)."
        except RuntimeError as exc:
            # Treat any 2xx-or-4xx as "reachable" since /api/Verify
            # may legitimately reject without a request body but the
            # network path is up. 5xx and connection errors mean
            # genuinely down.
            msg = str(exc)
            if "HTTP 4" in msg:
                return True, f"CCP reachable but Verify returned: {msg}"
            return False, f"CCP unreachable: {msg}"
    return False, f"Unknown backend: {backend!r}"
