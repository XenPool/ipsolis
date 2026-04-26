"""SIEM audit-log streaming.

Streams new ``audit_log`` rows to an external SIEM endpoint (default
adapter: Splunk HEC). Designed to be:

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
| ``siem.enabled``       | ``true``/``false`` master switch |
| ``siem.format``        | ``splunk_hec`` (only adapter today) |
| ``siem.endpoint_url``  | Splunk HEC endpoint, e.g. ``https://splunk:8088/services/collector/event`` |
| ``siem.token``         | HEC token (sent as ``Authorization: Splunk <token>``) |
| ``siem.batch_size``    | Max events per POST (default 200) |
| ``siem.last_id``       | Auto-managed cursor, last successfully forwarded id |
| ``siem.verify_tls``    | ``true``/``false``; default ``true``. Set false for self-signed labs only. |
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15
_DEFAULT_BATCH = 200


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


# ── Convenience: single-event "send a test" path used by the API ───────────────

def send_test_event(
    endpoint_url: str,
    token: str,
    *,
    fmt: str = "splunk_hec",
    verify_tls: bool = True,
    host: str = "ipsolis",
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
    return False, f"Unknown SIEM format: {fmt!r}"
