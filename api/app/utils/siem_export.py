"""Thin SIEM sender used by the API's "Send Test Event" button.

Mirrors ``worker/tasks/modules/siem_export.py`` for the single-event test
case. The real streaming loop runs in the worker's Beat task; this side
only needs to verify connectivity from a button click.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

_TIMEOUT_SECONDS = 15


def _ssl_context(verify: bool) -> ssl.SSLContext | None:
    if verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def send_test_event(
    endpoint_url: str,
    token: str,
    *,
    fmt: str = "splunk_hec",
    verify_tls: bool = True,
    host: str = "ipsolis",
) -> tuple[bool, str]:
    if fmt != "splunk_hec":
        return False, f"Unknown SIEM format: {fmt!r}"
    if not endpoint_url.strip() or not token.strip():
        return False, "Endpoint URL or HEC token is missing."

    event: dict[str, Any] = {
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
    wrapper = {
        "event": event,
        "sourcetype": "ipsolis:audit",
        "source": "ipsolis",
        "host": host,
        "time": datetime.utcnow().timestamp(),
    }
    payload = (json.dumps(wrapper, separators=(",", ":")) + "\n").encode("utf-8")

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
                return True, f"HEC accepted test event (HTTP {status})."
            return False, f"HEC returned HTTP {status}."
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
