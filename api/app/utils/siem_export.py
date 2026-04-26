"""Thin SIEM sender used by the API's "Send Test Event" button.

Mirrors ``worker/tasks/modules/siem_export.py`` for the single-event test
case. The real streaming loop runs in the worker's Beat task; this side
only needs to verify connectivity from a button click.

Two adapters today: ``splunk_hec`` (token in Authorization header) and
``sentinel`` (Azure Monitor Data Collector API — workspace id + HMAC-
SHA256 signed shared key).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any

_TIMEOUT_SECONDS = 15
_SENTINEL_API_VERSION = "2016-04-01"


def _ssl_context(verify: bool) -> ssl.SSLContext | None:
    if verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _test_event() -> dict[str, Any]:
    return {
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


def _send_splunk(endpoint_url: str, token: str, *, verify_tls: bool, host: str) -> tuple[bool, str]:
    if not endpoint_url.strip() or not token.strip():
        return False, "Endpoint URL or HEC token is missing."

    wrapper = {
        "event": _test_event(),
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


def _send_sentinel(
    workspace_id: str,
    shared_key: str,
    *,
    log_type: str,
    verify_tls: bool,
) -> tuple[bool, str]:
    if not workspace_id.strip() or not shared_key.strip():
        return False, "Workspace ID or shared key is missing."

    log_type = (log_type or "IpsolisAudit").strip() or "IpsolisAudit"
    payload = json.dumps([_test_event()], separators=(",", ":")).encode("utf-8")
    rfc1123_date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    string_to_sign = (
        f"POST\n{len(payload)}\napplication/json\n"
        f"x-ms-date:{rfc1123_date}\n/api/logs"
    )
    try:
        decoded_key = base64.b64decode(shared_key.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        return False, f"Shared key is not valid base64: {e}"
    signature = base64.b64encode(
        hmac.new(decoded_key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    url = (
        f"https://{workspace_id.strip()}.ods.opinsights.azure.com"
        f"/api/logs?api-version={_SENTINEL_API_VERSION}"
    )
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"SharedKey {workspace_id.strip()}:{signature}",
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
                return True, f"Sentinel accepted test event (HTTP {status})."
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


_DEFAULT_SIGNATURE_HEADER = "X-Hub-Signature-256"


def _send_webhook(
    webhook_url: str,
    secret: str,
    *,
    signature_header: str,
    extra_headers_raw: str,
    verify_tls: bool,
) -> tuple[bool, str]:
    if not webhook_url.strip() or not secret.strip():
        return False, "Webhook URL or secret is missing."

    payload = json.dumps([_test_event()], separators=(",", ":")).encode("utf-8")
    sig = "sha256=" + hmac.new(
        secret.strip().encode("utf-8"), payload, hashlib.sha256,
    ).hexdigest()
    sig_header = (signature_header or _DEFAULT_SIGNATURE_HEADER).strip() or _DEFAULT_SIGNATURE_HEADER

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "ipsolis-siem/1.0",
        "X-Ipsolis-Event": "audit.batch",
    }
    # Best-effort parse of the extra-headers JSON. A malformed value is
    # ignored so the test button still surfaces the network result.
    if extra_headers_raw and extra_headers_raw.strip():
        try:
            parsed = json.loads(extra_headers_raw)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        headers[k.strip()] = v.strip()
        except (TypeError, ValueError):
            pass
    headers[sig_header] = sig

    req = urllib.request.Request(
        webhook_url.strip(), data=payload, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT_SECONDS, context=_ssl_context(verify_tls)
        ) as resp:
            status = resp.status
            if 200 <= status < 300:
                return True, f"Webhook accepted test event (HTTP {status})."
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
    if fmt == "splunk_hec":
        return _send_splunk(endpoint_url, token, verify_tls=verify_tls, host=host)
    if fmt == "sentinel":
        return _send_sentinel(
            workspace_id, shared_key,
            log_type=log_type, verify_tls=verify_tls,
        )
    if fmt == "webhook":
        return _send_webhook(
            webhook_url, webhook_secret,
            signature_header=webhook_signature_header,
            extra_headers_raw=webhook_extra_headers,
            verify_tls=verify_tls,
        )
    return False, f"Unknown SIEM format: {fmt!r}"
