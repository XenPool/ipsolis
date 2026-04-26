"""Stateless HMAC-signed tokens for tokenized approval links.

The token authenticates that the bearer is the designated approver for an
``OrderApproval`` row, so we can put a clickable Approve / Reject link in an
email or a Teams adaptive card without forcing the approver to log into the
portal first.

Format:  base64url(payload_json) + "." + hex(hmac_sha256(payload, secret))
Payload: {"aid": <approval_id>, "exp": <unix_ts>, "v": 1}

Replay protection: when an approval transitions out of ``pending`` the token
is naturally invalidated by the GET/POST handler — it checks the row status
before showing the form / recording the decision. There is no per-token
nonce table; that's deliberate to keep the system stateless.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.config import settings

_DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days


def _signing_key() -> bytes:
    """Returns the HMAC signing key as bytes.

    Pulled from ``API_SECRET_KEY`` so rotating that env var invalidates all
    outstanding approval links — usually the right thing on incident response.
    """
    return settings.API_SECRET_KEY.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def make_token(approval_id: int, ttl_seconds: int | None = None) -> str:
    """Returns a signed token granting access to approval ``approval_id``."""
    payload: dict[str, Any] = {
        "aid": int(approval_id),
        "exp": int(time.time()) + int(ttl_seconds or _DEFAULT_TTL_SECONDS),
        "v": 1,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64url_encode(raw)
    sig = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str) -> dict[str, Any] | None:
    """Returns the decoded payload if ``token`` is valid and not expired,
    otherwise ``None``. Never raises — safe to call on any user input.
    """
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    aid = payload.get("aid")
    if not isinstance(aid, int):
        return None
    return payload
