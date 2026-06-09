"""Ipsolis license module — expiry, user limits, and grace period.

Offline, trust-based license system:
- Reads a signed JSON license file at ``/app/license/ipsolis.lic``
- Verifies the signature against the multi-key trust list in ``tasks.license``
- Checks expiry and enforces ``max_users`` / ``max_asset_types`` limits
- Applies a 30-day grace period after expiry before reverting to evaluation mode
- Caches the result in a process-local variable

Missing file or any validation failure silently falls back to evaluation mode.
No phone-home, no telemetry, no online checks.

Grace period
------------
When a license expires, the instance remains fully operational for
``GRACE_PERIOD_DAYS`` (30) additional days. This covers procurement delays
and prevents operational outages from a missed renewal. After the grace
period the instance reverts to evaluation mode automatically.
The daily Beat task (``license_check``) sends warning emails throughout
the grace period so operators have ample notice.

KEEP IN SYNC: api/app/utils/license.py <-> worker/tasks/utils/license.py
(byte-identical copies except for the package prefix in trust-list imports —
Docker build contexts are separate so we duplicate).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# ── License file location ───────────────────────────────────────────────────
LICENSE_PATH = Path(os.environ.get("IPSOLIS_LICENSE_PATH", "/app/license/ipsolis.lic"))

# ── Edition constants ───────────────────────────────────────────────────────
COMMUNITY_EDITION = "community"
PRO_EDITION       = "pro"

# All features remain active for this many days after expiry.
GRACE_PERIOD_DAYS = 30

# Legacy aliases emitted by older signing tools — normalised to PRO_EDITION on load.
_LEGACY_PRO_ALIASES = {"business", "enterprise", "professional"}


class LicenseInfo(BaseModel):
    """Effective license state for the running process."""

    model_config = ConfigDict(frozen=True)

    license_id: str = "community"
    licensee: str = "ip·Solis"
    edition: Literal["community", "pro"] = "community"
    max_users: int = 0
    max_asset_types: int = 0
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    features: list[str] = []
    # True when the license has expired but the 30-day grace period is still active.
    # All features remain enabled; the UI shows a renewal warning.
    in_grace_period: bool = False
    valid: bool = True
    message: str = ""
    # Trust list entry that successfully verified the signature.
    # None for Community fallback (no license file) or verification failures.
    verified_by_key_id: str | None = None
    verified_by_description: str | None = None


_COMMUNITY_FALLBACK = LicenseInfo()
_CACHED_INFO: LicenseInfo | None = None
_CACHED_MTIME: float | None = None  # mtime of the license file at cache time (None = no file)


def _community(message: str = "") -> LicenseInfo:
    return LicenseInfo(message=message) if message else _COMMUNITY_FALLBACK


def _current_mtime() -> float | None:
    try:
        return LICENSE_PATH.stat().st_mtime
    except FileNotFoundError:
        return None


def _verify_signature(payload: dict, signature_b64: str) -> bool:
    """Backwards-compatible shim — delegates to the trust-list verifier.

    Kept so existing callers continue to work without changes.
    New code should use ``verify_license_payload`` directly.
    """
    from tasks.license.verify import verify_license_payload
    try:
        sig_bytes = base64.b64decode(signature_b64)
    except Exception:
        return False
    result = verify_license_payload(payload, sig_bytes)
    return result.verified


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    # Support trailing "Z" (UTC) — datetime.fromisoformat only accepts +00:00 in <3.11
    s = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_license(force_reload: bool = False) -> LicenseInfo:
    """Load, verify, and cache the license file.

    The cache is keyed on the file's mtime, so overwriting ``ipsolis.lic`` at
    runtime (e.g. via the admin upload endpoint) is automatically picked up
    by all processes on the next call — no broadcast needed.

    Returns a Community fallback on any failure.
    """
    global _CACHED_INFO, _CACHED_MTIME

    current_mtime = _current_mtime()
    if (
        _CACHED_INFO is not None
        and not force_reload
        and _CACHED_MTIME == current_mtime
    ):
        return _CACHED_INFO

    if current_mtime is None:
        # Normal for Community installs — do not warn.
        _CACHED_INFO = _community()
        _CACHED_MTIME = None
        return _CACHED_INFO

    try:
        raw = LICENSE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("License file exists but could not be parsed: %s", exc)
        _CACHED_INFO = _community(f"License file invalid: {exc}")
        _CACHED_MTIME = current_mtime
        return _CACHED_INFO

    if not isinstance(data, dict) or "signature" not in data:
        logger.warning("License file missing 'signature' field")
        _CACHED_INFO = _community("License file malformed (no signature)")
        _CACHED_MTIME = current_mtime
        return _CACHED_INFO

    signature_b64 = data.pop("signature")
    try:
        sig_bytes = base64.b64decode(signature_b64)
    except Exception:
        logger.warning("License signature field is not valid base64")
        _CACHED_INFO = _community("License signature malformed")
        _CACHED_MTIME = current_mtime
        return _CACHED_INFO

    from tasks.license.verify import verify_license_payload
    verification = verify_license_payload(data, sig_bytes)
    if not verification.verified:
        logger.warning("License signature verification failed: %s", verification.reason)
        _CACHED_INFO = _community(f"License signature invalid: {verification.reason}")
        _CACHED_MTIME = current_mtime
        return _CACHED_INFO

    issued_at = _parse_datetime(data.get("issued_at"))
    expires_at = _parse_datetime(data.get("expires_at"))
    now = datetime.now(timezone.utc)

    in_grace_period = False
    if expires_at:
        grace_deadline = expires_at + timedelta(days=GRACE_PERIOD_DAYS)
        if now > grace_deadline:
            # Grace period exhausted — revert to evaluation mode.
            logger.warning(
                "License expired %s, grace period ended %s — reverting to evaluation mode",
                expires_at.date().isoformat(), grace_deadline.date().isoformat(),
            )
            _CACHED_INFO = LicenseInfo(
                license_id=str(data.get("license_id") or "community"),
                licensee=str(data.get("licensee") or "ip·Solis"),
                edition=COMMUNITY_EDITION,
                max_users=int(data.get("max_users") or 0),
                max_asset_types=int(data.get("max_asset_types") or 0),
                issued_at=issued_at,
                expires_at=expires_at,
                features=[],
                valid=False,
                message=(
                    f"License expired {expires_at.date().isoformat()}. "
                    f"30-day grace period ended {grace_deadline.date().isoformat()}."
                ),
            )
            _CACHED_MTIME = current_mtime
            return _CACHED_INFO
        elif now > expires_at:
            # Expired but within the grace window — keep Pro, flag the state.
            in_grace_period = True
            days_left = (grace_deadline - now).days
            logger.warning(
                "License expired %s — grace period active, %d day(s) until evaluation mode",
                expires_at.date().isoformat(), days_left,
            )

    edition = str(data.get("edition") or COMMUNITY_EDITION)
    if edition in _LEGACY_PRO_ALIASES:
        edition = PRO_EDITION
    elif edition != PRO_EDITION:
        edition = COMMUNITY_EDITION

    features_raw = data.get("features") or []
    features = list(features_raw) if isinstance(features_raw, list) else []

    grace_msg = ""
    if in_grace_period and expires_at:
        grace_deadline = expires_at + timedelta(days=GRACE_PERIOD_DAYS)
        days_left = (grace_deadline - now).days
        grace_msg = (
            f"License expired {expires_at.date().isoformat()}. "
            f"All features active during 30-day grace period — "
            f"{days_left} day(s) remaining. Renew to avoid reverting to evaluation mode."
        )

    info = LicenseInfo(
        license_id=str(data.get("license_id") or "community"),
        licensee=str(data.get("licensee") or "ip·Solis"),
        edition=edition,  # type: ignore[arg-type]
        max_users=int(data.get("max_users") or 0),
        max_asset_types=int(data.get("max_asset_types") or 0),
        issued_at=issued_at,
        expires_at=expires_at,
        features=features,
        in_grace_period=in_grace_period,
        valid=True,
        message=grace_msg,
        verified_by_key_id=verification.key.key_id if verification.key else None,
        verified_by_description=verification.key.description if verification.key else None,
    )
    logger.info(
        "License loaded: edition=%s licensee=%s expires=%s grace=%s",
        info.edition, info.licensee,
        info.expires_at.isoformat() if info.expires_at else "never",
        info.in_grace_period,
    )
    _CACHED_INFO = info
    _CACHED_MTIME = current_mtime
    return _CACHED_INFO


def get_license_info() -> LicenseInfo:
    """Return cached license info (loading on first access)."""
    return load_license()
