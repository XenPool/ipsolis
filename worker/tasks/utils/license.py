"""Ipsolis license module (Community / Business / Enterprise edition gating).

Offline, trust-based license system:
- Reads a signed JSON license file at ``/app/license/ipsolis.lic``
- Verifies an Ed25519 signature against the embedded public key
- Checks expiry
- Optionally enforces an install-bound ``install_uuid`` (see below)
- Caches the result in a process-local variable

Missing file or any validation failure silently falls back to Community edition.
No phone-home, no telemetry, no online checks.

Install binding
---------------
Licenses MAY include an ``install_uuid`` field. When present, the verifier
compares it against the local install UUID (seeded by migration 0094 into
``app_config['install.uuid']`` and registered with this module via
``set_install_uuid()`` at application startup). Mismatches fall back to
Community with an explanatory message. Licenses without ``install_uuid``
remain valid — backwards-compat for legacy issuances.

KEEP IN SYNC: api/app/utils/license.py <-> worker/tasks/utils/license.py
(byte-identical copies — Docker build contexts are separate so we duplicate).
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

# ── Embedded public key (Ed25519, 32 bytes hex-encoded) ──────────────────────
# Generate with: python tools/license/generate_keypair.py
# Paste the hex output below. Until set, all signature verification fails and
# the instance runs as Community edition.
PUBLIC_KEY_HEX: str = "e2b380f0d1c5205b119c96e7802165b55398c15f5b429e60c334a0e63315f23d"

# ── License file location ───────────────────────────────────────────────────
LICENSE_PATH = Path(os.environ.get("IPSOLIS_LICENSE_PATH", "/app/license/ipsolis.lic"))

# ── Edition constants ───────────────────────────────────────────────────────
COMMUNITY_EDITION  = "community"
BUSINESS_EDITION   = "business"
ENTERPRISE_EDITION = "enterprise"

# Features available on Business and Enterprise licenses.
BUSINESS_FEATURE_KEYS: frozenset[str] = frozenset({
    "standalone_runbooks", "visual_runbook_builder", "ps_module_management",
    "deputy_support", "scheduled_orders", "app_owner_approval", "reapproval_on_modify",
    "email_template_editor", "app_branding", "eligible_requestors", "global_variables",
    "audit_log_viewer", "change_log_viewer", "api_token_management", "certifications",
})

# Features that require an Enterprise license; hard-blocked on Business.
ENTERPRISE_ONLY_FEATURE_KEYS: frozenset[str] = frozenset({
    "servicenow_webhook", "hr_webhook", "hr_leaver_events", "scim",
    "vsphere_integration", "xenserver_integration", "sccm_integration",
    "audit_retention", "advanced_maintenance", "custom_deprovision",
    "rbac_asset_type_grants", "rbac_token_role_binding", "rbac_sod_enforcement",
    "password_policy",
})


class LicenseInfo(BaseModel):
    """Effective license state for the running process."""

    model_config = ConfigDict(frozen=True)

    license_id: str = "community"
    licensee: str = "Community Edition"
    edition: Literal["community", "business", "enterprise"] = "community"
    max_users: int = 0
    max_asset_types: int = 0
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    features: list[str] = []
    install_uuid: str | None = None  # set when the license is install-bound
    valid: bool = True
    message: str = ""


_COMMUNITY_FALLBACK = LicenseInfo()
_CACHED_INFO: LicenseInfo | None = None
_CACHED_MTIME: float | None = None  # mtime of the license file at cache time (None = no file)

# Per-install identifier registered by application bootstrap (api lifespan or
# worker task init). When set, the verifier enforces install-bound licenses;
# when None, install-bound licenses fail closed (treated as Community) so a
# bootstrap-order race can't accidentally grant Enterprise without a binding
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
    """Ed25519 signature verification over canonically-serialized payload bytes."""
    if not PUBLIC_KEY_HEX:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        logger.warning("cryptography library not available; cannot verify license")
        return False

    try:
        public_key_bytes = bytes.fromhex(PUBLIC_KEY_HEX)
        if len(public_key_bytes) != 32:
            return False
        key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        signature = base64.b64decode(signature_b64)
        message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        key.verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


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
    if not _verify_signature(data, signature_b64):
        logger.warning("License signature verification failed")
        _CACHED_INFO = _community("License signature invalid")
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
    # Community than grant Enterprise to an install we can't verify.
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
    if edition not in (COMMUNITY_EDITION, BUSINESS_EDITION, ENTERPRISE_EDITION):
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


def is_enterprise() -> bool:
    """True iff the instance runs with a valid Enterprise license."""
    info = get_license_info()
    return info.edition == ENTERPRISE_EDITION and info.valid


def is_business() -> bool:
    """True iff the instance runs with a valid Business or Enterprise license.

    Enterprise is a strict superset of Business, so Enterprise licenses
    also satisfy Business-tier gates.
    """
    info = get_license_info()
    return info.edition in (BUSINESS_EDITION, ENTERPRISE_EDITION) and info.valid


def is_feature_enabled(feature: str) -> bool:
    """True iff the feature is available under the current license.

    Tier hierarchy: Enterprise ⊇ Business ⊇ Community.
    - Enterprise with features=["all"] or empty list → all features (legacy behaviour preserved).
    - Enterprise with explicit list → honour the list.
    - Business → enables BUSINESS_FEATURE_KEYS; ENTERPRISE_ONLY_FEATURE_KEYS always blocked.
    - Community → all gated features disabled.
    """
    info = get_license_info()
    if not info.valid:
        return False
    if info.edition == ENTERPRISE_EDITION:
        if "all" in info.features or not info.features:
            return True
        return feature in info.features
    if info.edition == BUSINESS_EDITION:
        if feature in ENTERPRISE_ONLY_FEATURE_KEYS:
            return False
        if "all" in info.features or not info.features:
            return feature in BUSINESS_FEATURE_KEYS
        return feature in info.features
    return False
