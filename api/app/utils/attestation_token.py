"""Stateless HMAC-signed tokens for attestation-artifact links.

Same shape as ``certification_token.py`` / ``approval_token.py`` but a distinct
payload key (``aid``) and ``kind`` field so a token minted for an attestation
artifact can't be replayed against an approval or review row. Signed with the
same ``API_SECRET_KEY`` (rotating it invalidates all outstanding tokens).

TTL is longer than approval/review links (90 days) because handover
acknowledgments and revocation-evidence pages have a longer natural life; the
recipient archives the page via browser print for permanent evidence.

Format:  base64url(payload_json) + "." + hex(hmac_sha256(payload, secret))
Payload: {"aid": <artifact_id>, "exp": <unix_ts>, "v": 1, "kind": "attestation"}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.config import settings

_DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 90  # 90 days
_KIND = "attestation"


def _signing_key() -> bytes:
    return settings.API_SECRET_KEY.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def make_attestation_token(artifact_id: int, ttl_seconds: int | None = None) -> str:
    """Returns a signed token granting access to artifact ``artifact_id``."""
    payload: dict[str, Any] = {
        "aid": int(artifact_id),
        "exp": int(time.time()) + int(ttl_seconds or _DEFAULT_TTL_SECONDS),
        "v": 1,
        "kind": _KIND,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64url_encode(raw)
    sig = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_attestation_token(token: str) -> dict[str, Any] | None:
    """Returns the decoded payload if valid + not expired, else ``None``.
    Never raises — safe on any user input.
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
    if payload.get("v") != 1 or payload.get("kind") != _KIND:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    aid = payload.get("aid")
    if not isinstance(aid, int):
        return None
    return payload
