"""Stateless HMAC-signed tokens for tokenized certification-review links.

Same shape as ``approval_token.py`` but distinct payload key (``rid``)
and ``kind`` field so a token minted for a certification review can't
be used against an approval row (and vice-versa). The signing key is
the same ``API_SECRET_KEY``, so rotating that env var invalidates all
outstanding tokens — typical on incident response.

Token grants access to a single ``CertificationReview`` row identified
by ``rid`` (the row primary key). The reviewer-portal page bundles the
full set of a reviewer's tokens into one URL, but each row is its own
token so revoking access to a single row in the future stays simple.

Format:  base64url(payload_json) + "." + hex(hmac_sha256(payload, secret))
Payload: {"rid": <review_id>, "exp": <unix_ts>, "v": 1, "kind": "cert_review"}
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
_KIND = "cert_review"


def _signing_key() -> bytes:
    return settings.API_SECRET_KEY.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def make_review_token(review_id: int, ttl_seconds: int | None = None) -> str:
    """Returns a signed token granting access to review ``review_id``."""
    payload: dict[str, Any] = {
        "rid": int(review_id),
        "exp": int(time.time()) + int(ttl_seconds or _DEFAULT_TTL_SECONDS),
        "v": 1,
        "kind": _KIND,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64url_encode(raw)
    sig = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_review_token(token: str) -> dict[str, Any] | None:
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
    rid = payload.get("rid")
    if not isinstance(rid, int):
        return None
    return payload
