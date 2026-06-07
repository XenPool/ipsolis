"""Ipsolis license module — expiry, user limits, and install binding.

Offline, trust-based license system:
- Reads a signed JSON license file at ``/app/license/ipsolis.lic``
- Verifies the signature against the multi-key trust list in ``tasks.license``
- Checks expiry and enforces ``max_users`` / ``max_asset_types`` limits
- Optionally enforces an install-bound ``install_uuid`` (see below)
- Caches the result in a process-local variable

Missing file or any validation failure silently falls back to Community edition.
No phone-home, no telemetry, no online checks.
No runtime feature gating — feature availability is controlled by which code is
present in the Docker image (Community vs. Pro), not by license key checks.

Install binding
---------------
Licenses MAY include an ``install_uuid`` field. When present, the verifier
compares it against the local install UUID (seeded by migration 0094 into
``app_config['install.uuid']`` and registered with this module via
``set_install_uuid()`` at application startup). Mismatches fall back to
Community with an explanatory message. Licenses without ``install_uuid``
remain valid — backwards-compat for legacy issuances.

KEEP IN SYNC: api/app/utils/license.py <-> worker/tasks/utils/license.py
(byte-identical copies except for the package prefix in trust-list imports —
Docker build contexts are separate so we duplicate).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# ── License file location ───────────────────────────────────────────────────
LICENSE_PATH = Path(os.environ.get("IPSOLIS_LICENSE_PATH", "/app/license/ipsolis.lic"))

# ── Edition constants ───────────────────────────────────────────────────────
COMMUNITY_EDITION = "community"
PRO_EDITION       = "pro"

# Legacy aliases emitted by older signing tools — normalised to PRO_EDITION on load.
_LEGACY_PRO_ALIASES = {"business", "enterprise", "professional"}


class LicenseInfo(BaseModel):
    """Effective license state for the running process."""

    model_config = ConfigDict(frozen=True)

    license_id: str = "community"
    licensee: str = "Community Edition"
    edition: Literal["community", "pro"] = "community"
    max_users: int = 0
    max_asset_types: int = 0
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    features: list[str] = []
    install_uuid: str | None = None  # set when the license is install-bound
    valid: bool = True
    message: str = ""
    # Trust list entry that successfully verified the signature.
    # None for Community fallback (no license file) or verification failures.
    verified_by_key_id: str | None = None
    verified_by_description: str | None = None


_COMMUNITY_FALLBACK = LicenseInfo()
_CACHED_INFO: LicenseInfo | None = None
_CACHED_MTIME: float | None = None  # mtime of the license file at cache time (None = no file)

# Per-install identifier registered by application bootstrap (api lifespan or
# worker task init). When set, the verifier enforces install-bound licenses;
# when None, install-bound licenses fail closed (treated as Community) so a
# bootstrap-order race can't accidentally grant Pro without a binding
# check.
_INSTALL_UUID: str | None = None


def set_install_uuid(value: str | None) -> None:
    """Register the per-install UUID for license-binding verification.

    Application bootstrap is responsible for reading ``install.uuid`` from
    ``app_config`` and calling this once before any license check runs.
    Calling it again invalidates the cache so the next ``load_license()``
    call re-evaluates with the updated binding.
    """
    global _INSTALL_UUID, _CACHED_INFO, _CACHED_MTIME
    _INSTALL_UUID = (value or None)
    # Invalidate the license cache so the binding takes effect immediately.
    _CACHED_INFO = None
    _CACHED_MTIME = None


def get_install_uuid() -> str | None:
    """Return the locally-registered install UUID (None if not set yet)."""
    return _INSTALL_UUID


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

    if expires_at and expires_at < now:
        logger.warning(
            "License expired on %s — falling back to Community edition",
            expires_at.isoformat(),
        )
        # Preserve expires_at/licensee so the Beat task can still log details.
        _CACHED_INFO = LicenseInfo(
            license_id=str(data.get("license_id") or "community"),
            licensee=str(data.get("licensee") or "Community Edition"),
            edition=COMMUNITY_EDITION,
            max_users=int(data.get("max_users") or 0),
            max_asset_types=int(data.get("max_asset_types") or 0),
            issued_at=issued_at,
            expires_at=expires_at,
            features=[],
            valid=False,
            message=f"License expired on {expires_at.date().isoformat()}",
        )
        _CACHED_MTIME = current_mtime
        return _CACHED_INFO

    # Install-bound licenses: ``install_uuid`` in the payload must match the
    # locally-registered install UUID. Fail closed if the binding can't be
    # checked (bootstrap hasn't registered it yet) — better to drop to
    # Community than grant Pro to an install we can't verify.
    license_install_uuid = data.get("install_uuid")
    if license_install_uuid:
        license_install_uuid = str(license_install_uuid).strip()
        if not _INSTALL_UUID:
            logger.warning(
                "License is install-bound (install_uuid=%s) but the local "
                "install UUID has not been registered yet — falling back "
                "to Community.",
                license_install_uuid,
            )
            _CACHED_INFO = LicenseInfo(
                license_id=str(data.get("license_id") or "community"),
                licensee=str(data.get("licensee") or "Community Edition"),
                edition=COMMUNITY_EDITION,
                install_uuid=license_install_uuid,
                valid=False,
                message="License install binding cannot be verified yet",
            )
            _CACHED_MTIME = current_mtime
            return _CACHED_INFO
        if license_install_uuid != _INSTALL_UUID:
            logger.warning(
                "License install_uuid mismatch — license=%s local=%s. "
                "Falling back to Community.",
                license_install_uuid, _INSTALL_UUID,
            )
            _CACHED_INFO = LicenseInfo(
                license_id=str(data.get("license_id") or "community"),
                licensee=str(data.get("licensee") or "Community Edition"),
                edition=COMMUNITY_EDITION,
                install_uuid=license_install_uuid,
                valid=False,
                message=(
                    "License is bound to a different install. "
                    "Request a new license bound to this install's UUID."
                ),
            )
            _CACHED_MTIME = current_mtime
            return _CACHED_INFO

    edition = str(data.get("edition") or COMMUNITY_EDITION)
    if edition in _LEGACY_PRO_ALIASES:
        edition = PRO_EDITION
    elif edition != PRO_EDITION:
        edition = COMMUNITY_EDITION

    features_raw = data.get("features") or []
    features = list(features_raw) if isinstance(features_raw, list) else []

    info = LicenseInfo(
        license_id=str(data.get("license_id") or "community"),
        licensee=str(data.get("licensee") or "Community Edition"),
        edition=edition,  # type: ignore[arg-type]
        max_users=int(data.get("max_users") or 0),
        max_asset_types=int(data.get("max_asset_types") or 0),
        issued_at=issued_at,
        expires_at=expires_at,
        features=features,
        install_uuid=license_install_uuid or None,
        valid=True,
        message="",
        verified_by_key_id=verification.key.key_id if verification.key else None,
        verified_by_description=verification.key.description if verification.key else None,
    )
    logger.info(
        "License loaded: edition=%s licensee=%s expires=%s",
        info.edition, info.licensee,
        info.expires_at.isoformat() if info.expires_at else "never",
    )
    _CACHED_INFO = info
    _CACHED_MTIME = current_mtime
    return _CACHED_INFO


def get_license_info() -> LicenseInfo:
    """Return cached license info (loading on first access)."""
    return load_license()


