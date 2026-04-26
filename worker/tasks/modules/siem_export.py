"""SIEM audit-log streaming.

Streams new ``audit_log`` rows to an external SIEM endpoint. Three
adapters today:

* ``splunk_hec`` — Splunk HTTP Event Collector (token in
  ``Authorization: Splunk …`` header, NDJSON body).
* ``sentinel`` — Microsoft Sentinel / Azure Monitor Data Collector
  API (workspace id + shared key, HMAC-SHA256 signed, JSON array body).
* ``webhook`` — Generic HMAC-signed JSON webhook (GitHub-compatible
  ``X-Hub-Signature-256: sha256=<hex>`` header by default, header
  name configurable). Targets Elastic / Datadog / Sumo / Loki /
  homegrown receivers — anything that consumes signed JSON.

Designed to be:

* **Idempotent on retry** — last-streamed audit_log id is persisted in
  ``app_config`` (key ``siem.last_id``) so a crash mid-batch resumes from
  the last acknowledged id, never duplicates.
* **Cheap** — pulls a bounded batch per tick, uses indexed
  ``WHERE id > :last`` lookups.
* **Best-effort** — network failures are logged at WARNING and increment
  a failure counter; they never abort the worker. The next tick retries.

Configuration in ``app_config``:

| Key | Purpose |
|---|---|
| ``siem.enabled``                | ``true``/``false`` master switch |
| ``siem.format``                 | ``splunk_hec``, ``sentinel``, or ``webhook`` |
| ``siem.endpoint_url``           | Splunk HEC endpoint (Splunk only) |
| ``siem.token``                  | HEC token sent as ``Authorization: Splunk <token>`` (Splunk only) |
| ``siem.workspace_id``           | Log Analytics workspace GUID (Sentinel only) |
| ``siem.shared_key``             | Workspace shared key, base64-encoded (Sentinel only) |
| ``siem.log_type``               | Custom log table name, e.g. ``IpsolisAudit`` → ``IpsolisAudit_CL`` (Sentinel only) |
| ``siem.webhook_url``            | Webhook URL (webhook only) |
| ``siem.webhook_secret``         | HMAC-SHA256 key for body signing (webhook only) |
| ``siem.webhook_signature_header`` | Header name carrying ``sha256=<hex>``; default ``X-Hub-Signature-256`` |
| ``siem.webhook_extra_headers``  | JSON object of additional headers, e.g. ``{"Authorization":"Bearer …"}`` |
| ``siem.batch_size``             | Max events per POST (default 200) |
| ``siem.last_id``                | Auto-managed cursor, last successfully forwarded id |
| ``siem.verify_tls``             | ``true``/``false``; default ``true``. Set false for self-signed labs only. |
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15
_DEFAULT_BATCH = 200
_SENTINEL_API_VERSION = "2016-04-01"


def _ssl_context(verify: bool) -> ssl.SSLContext | None:
    if verify:
        return None  # urllib uses the system default verify context
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _row_to_event(row: Any) -> dict[str, Any]:
    """Convert an audit_log row (Row or dict) to a normalized dict."""
    ts = getattr(row, "timestamp", None)
    if isinstance(ts, datetime):
        ts_iso = ts.isoformat()
    else:
        ts_iso = str(ts) if ts is not None else None
    return {
        "id": row.id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "old_value": row.old_value,
        "new_value": row.new_value,
        "triggered_by": row.triggered_by,
        "context": row.context,
        "timestamp": ts_iso,
    }


def build_splunk_hec_payload(events: list[dict[str, Any]], host: str = "ipsolis") -> bytes:
    """Build a newline-delimited Splunk HEC payload.

    Splunk HEC accepts multiple events as concatenated JSON objects (no
    surrounding array, no commas — one ``{"event": ...}`` per line).
    """
    lines: list[str] = []
    for ev in events:
        ts_str = ev.get("timestamp")
        # Splunk wants epoch seconds for the ``time`` field; fall back to
        # "now" if the row's timestamp is malformed.
        ts_epoch: float | None = None
        if isinstance(ts_str, str):
            try:
                ts_epoch = datetime.fromisoformat(ts_str).timestamp()
            except ValueError:
                ts_epoch = None
        wrapper: dict[str, Any] = {
            "event": ev,
            "sourcetype": "ipsolis:audit",
            "source": "ipsolis",
            "host": host,
        }
        if ts_epoch is not None:
            wrapper["time"] = ts_epoch
        lines.append(json.dumps(wrapper, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8")


def post_splunk_hec(
    endpoint_url: str,
    token: str,
    payload: bytes,
    *,
    verify_tls: bool = True,
) -> tuple[bool, str]:
    """POST a HEC payload. Returns ``(success, message)``. Never raises."""
    if not endpoint_url.strip() or not token.strip():
        return False, "Endpoint URL or HEC token is missing."

    req = urllib.request.Request(
        endpoint_url.strip(),
        data=payload,
        headers={
            "Authorization": f"Splunk {token.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT_SECONDS, context=_ssl_context(verify_tls)
        ) as resp:
            status = resp.status
            if 200 <= status < 300:
                return True, f"HEC accepted (HTTP {status})."
            return False, f"HEC returned HTTP {status}."
    except urllib.error.HTTPError as e:
        # Splunk usually returns a JSON body on error — surface it.
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"HTTP {e.code}: {e.reason} {detail}".rstrip()
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ── Microsoft Sentinel / Azure Monitor Data Collector API ─────────────────────

def build_sentinel_payload(events: list[dict[str, Any]]) -> bytes:
    """Build a JSON array body for the Azure Monitor Data Collector API.

    Sentinel's HTTP Data Collector API expects ``[{...}, {...}]`` — a
    plain JSON array of events. The custom log table is selected by the
    ``Log-Type`` header, not the body. ``timestamp`` is preserved on
    each event; if you set ``time-generated-field`` on the workspace
    side, Sentinel uses it as the row timestamp instead of ingest time.
    """
    return json.dumps(events, separators=(",", ":")).encode("utf-8")


def _sentinel_signature(shared_key: str, string_to_sign: str) -> str:
    """HMAC-SHA256 sign (string_to_sign) with base64-decoded shared_key.

    Returns base64-encoded signature. Mirrors the algorithm spelled out
    in Microsoft's ``Send-OMSAPIIngestionFile`` PowerShell sample —
    invariant under SDK version, so we reimplement with stdlib only.
    ``validate=True`` is important: lets a pasted-with-typos shared key
    fail loudly instead of silently stripping characters and producing
    a wrong signature that Sentinel rejects with an opaque 403.
    """
    decoded_key = base64.b64decode(shared_key, validate=True)
    hashed = hmac.new(decoded_key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(hashed).decode("utf-8")


def post_sentinel(
    workspace_id: str,
    shared_key: str,
    payload: bytes,
    *,
    log_type: str = "IpsolisAudit",
    verify_tls: bool = True,
) -> tuple[bool, str]:
    """POST a payload to Azure Monitor Data Collector. Returns ``(success, message)``.

    Endpoint: ``https://{workspace_id}.ods.opinsights.azure.com/api/logs?api-version=2016-04-01``.
    Sentinel materialises ingest into a custom table named ``{log_type}_CL``
    in the connected Log Analytics workspace. Successful ingest returns
    HTTP 200 with empty body. Errors are JSON; we surface the body so
    admins can self-diagnose (most common: bad shared key → 403, bad
    log-type → 400 with a hint).
    """
    if not workspace_id.strip() or not shared_key.strip():
        return False, "Workspace ID or shared key is missing."

    log_type = (log_type or "IpsolisAudit").strip() or "IpsolisAudit"
    rfc1123_date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    content_length = len(payload)
    string_to_sign = (
        f"POST\n{content_length}\napplication/json\n"
        f"x-ms-date:{rfc1123_date}\n/api/logs"
    )
    try:
        signature = _sentinel_signature(shared_key.strip(), string_to_sign)
    except (binascii.Error, ValueError) as e:
        return False, f"Shared key is not valid base64: {e}"

    auth = f"SharedKey {workspace_id.strip()}:{signature}"
    url = (
        f"https://{workspace_id.strip()}.ods.opinsights.azure.com"
        f"/api/logs?api-version={_SENTINEL_API_VERSION}"
    )

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": auth,
            "Log-Type": log_type,
            "x-ms-date": rfc1123_date,
            "time-generated-field": "timestamp",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT_SECONDS, context=_ssl_context(verify_tls)
        ) as resp:
            status = resp.status
            if 200 <= status < 300:
                return True, f"Sentinel accepted (HTTP {status})."
            return False, f"Sentinel returned HTTP {status}."
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"HTTP {e.code}: {e.reason} {detail}".rstrip()
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ── Generic HMAC-signed JSON webhook ──────────────────────────────────────────

_DEFAULT_SIGNATURE_HEADER = "X-Hub-Signature-256"


def build_webhook_payload(events: list[dict[str, Any]]) -> bytes:
    """Build a JSON array body for the generic webhook adapter.

    Mirrors the Sentinel shape: one ``[{...}, {...}]`` array per POST.
    Receivers iterate the array and dedupe on ``event.id``. Compact
    separators keep the body small and the HMAC over the exact bytes
    we send.
    """
    return json.dumps(events, separators=(",", ":")).encode("utf-8")


def _webhook_signature(secret: str, payload: bytes) -> str:
    """HMAC-SHA256 sign ``payload`` with ``secret`` and return ``sha256=<hex>``.

    GitHub-compatible format so receivers can reuse standard libraries
    (``hmac.compare_digest`` against the computed digest is the canonical
    check). We always emit lowercase hex to match GitHub's reference
    implementation.
    """
    digest = hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _parse_extra_headers(raw: str | None) -> dict[str, str]:
    """Parse ``siem.webhook_extra_headers`` from a JSON object string.

    Returns an empty dict on parse failure / non-object / non-string
    values — so a malformed config can't tank the streamer. Keys with
    empty / whitespace values are dropped.
    """
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("siem.webhook_extra_headers is not valid JSON; ignoring")
        return {}
    if not isinstance(parsed, dict):
        logger.warning("siem.webhook_extra_headers must be a JSON object; ignoring")
        return {}
    out: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k_clean = k.strip()
        v_clean = v.strip()
        if k_clean and v_clean:
            out[k_clean] = v_clean
    return out


def post_webhook(
    webhook_url: str,
    secret: str,
    payload: bytes,
    *,
    signature_header: str = _DEFAULT_SIGNATURE_HEADER,
    extra_headers: dict[str, str] | None = None,
    verify_tls: bool = True,
) -> tuple[bool, str]:
    """POST a signed payload to a generic webhook. Returns ``(success, message)``.

    Headers always sent:

    * ``Content-Type: application/json``
    * ``User-Agent: ipsolis-siem/1.0``
    * ``X-Ipsolis-Event: audit.batch``
    * ``<signature_header>: sha256=<hex>`` — HMAC-SHA256 over the raw body

    Plus any keys in ``extra_headers`` (e.g. a static
    ``Authorization: Bearer …`` for receivers that prefer bearer auth
    over HMAC verification, or service-specific headers like
    ``DD-API-KEY`` for Datadog). ``extra_headers`` keys silently
    override the always-sent set, except the signature header itself
    which is always written by us.
    """
    if not webhook_url.strip() or not secret.strip():
        return False, "Webhook URL or secret is missing."

    sig_header = (signature_header or _DEFAULT_SIGNATURE_HEADER).strip() or _DEFAULT_SIGNATURE_HEADER
    sig = _webhook_signature(secret.strip(), payload)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "ipsolis-siem/1.0",
        "X-Ipsolis-Event": "audit.batch",
    }
    if extra_headers:
        headers.update(extra_headers)
    headers[sig_header] = sig  # signature always wins, even over extras

    req = urllib.request.Request(
        webhook_url.strip(),
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT_SECONDS, context=_ssl_context(verify_tls)
        ) as resp:
            status = resp.status
            if 200 <= status < 300:
                return True, f"Webhook accepted (HTTP {status})."
            return False, f"Webhook returned HTTP {status}."
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"HTTP {e.code}: {e.reason} {detail}".rstrip()
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ── Convenience: single-event "send a test" path used by the API ───────────────

def send_test_event(
    endpoint_url: str,
    token: str,
    *,
    fmt: str = "splunk_hec",
    verify_tls: bool = True,
    host: str = "ipsolis",
    workspace_id: str = "",
    shared_key: str = "",
    log_type: str = "IpsolisAudit",
    webhook_url: str = "",
    webhook_secret: str = "",
    webhook_signature_header: str = _DEFAULT_SIGNATURE_HEADER,
    webhook_extra_headers: str = "",
) -> tuple[bool, str]:
    """Post a single synthetic audit event so admins can verify the
    SIEM endpoint accepts our payload before they enable streaming."""
    test_event = {
        "id": 0,
        "entity_type": "siem_test",
        "entity_id": 0,
        "action": "test_connection",
        "old_value": None,
        "new_value": {"note": "ipsolis SIEM connectivity test"},
        "triggered_by": "api:siem_test",
        "context": "test",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if fmt == "splunk_hec":
        payload = build_splunk_hec_payload([test_event], host=host)
        return post_splunk_hec(endpoint_url, token, payload, verify_tls=verify_tls)
    if fmt == "sentinel":
        payload = build_sentinel_payload([test_event])
        return post_sentinel(
            workspace_id, shared_key, payload,
            log_type=log_type, verify_tls=verify_tls,
        )
    if fmt == "webhook":
        payload = build_webhook_payload([test_event])
        return post_webhook(
            webhook_url, webhook_secret, payload,
            signature_header=webhook_signature_header,
            extra_headers=_parse_extra_headers(webhook_extra_headers),
            verify_tls=verify_tls,
        )
    return False, f"Unknown SIEM format: {fmt!r}"
