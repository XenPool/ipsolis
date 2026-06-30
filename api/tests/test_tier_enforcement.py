"""Tests for free-tier / commercial-band user-count enforcement.

Covers the pure decision surface exhaustively (DB-free):
- ``effective_user_limit`` for each license state (no license / commercial /
  unlimited / expired-past-grace / evaluation).
- ``is_new_user_blocked`` for the spec edge cases: 25/26 without a license;
  max_users-1 / max_users / max_users+1 with a commercial band; existing user;
  unlimited.
"""
from __future__ import annotations

from app.utils.license import (
    FREE_TIER_MAX_USERS,
    LicenseInfo,
    effective_user_limit,
)
from app.utils.tier import is_new_user_blocked


# ── effective_user_limit ─────────────────────────────────────────────────────

def test_no_license_is_free_tier():
    # Community fallback: valid=True, is_commercial=False.
    assert effective_user_limit(LicenseInfo()) == FREE_TIER_MAX_USERS == 25


def test_evaluation_license_does_not_raise_limit():
    info = LicenseInfo(is_evaluation=True, is_commercial=False, valid=True, max_users=1000)
    assert effective_user_limit(info) == 25


def test_commercial_band_uses_max_users():
    info = LicenseInfo(is_commercial=True, valid=True, max_users=75)
    assert effective_user_limit(info) == 75


def test_commercial_unlimited_when_max_users_zero():
    info = LicenseInfo(is_commercial=True, valid=True, max_users=0)
    assert effective_user_limit(info) is None


def test_commercial_in_grace_still_uses_band():
    # During the 30-day expiry grace, valid stays True → band still applies.
    info = LicenseInfo(is_commercial=True, valid=True, in_grace_period=True, max_users=250)
    assert effective_user_limit(info) == 250


def test_commercial_expired_past_grace_reverts_to_free():
    # After grace, load_license reverts to community: is_commercial=False, valid=False.
    info = LicenseInfo(is_commercial=False, valid=False, edition="community", max_users=250)
    assert effective_user_limit(info) == 25


# ── is_new_user_blocked — free tier (limit 25): the 25/26 boundary ───────────

def test_free_tier_24_new_user_allowed():
    # 24 active, a new user would make 25 — allowed.
    assert is_new_user_blocked(24, is_existing=False, limit=25) is False


def test_free_tier_25_new_user_blocked():
    # 25 active, a new (26th) user — blocked.
    assert is_new_user_blocked(25, is_existing=False, limit=25) is True


def test_free_tier_25_existing_user_allowed():
    # Existing active user ordering again never blocks (count unchanged).
    assert is_new_user_blocked(25, is_existing=True, limit=25) is False


def test_free_tier_over_limit_new_user_blocked():
    assert is_new_user_blocked(30, is_existing=False, limit=25) is True


# ── is_new_user_blocked — commercial band: max_users -1 / =0 / +1 ────────────

def test_band_below_limit_new_user_allowed():
    assert is_new_user_blocked(74, is_existing=False, limit=75) is False  # max_users-1


def test_band_at_limit_new_user_blocked():
    assert is_new_user_blocked(75, is_existing=False, limit=75) is True   # == max_users


def test_band_above_limit_new_user_blocked():
    assert is_new_user_blocked(76, is_existing=False, limit=75) is True   # max_users+1


def test_band_at_limit_existing_user_allowed():
    assert is_new_user_blocked(75, is_existing=True, limit=75) is False


# ── is_new_user_blocked — unlimited ──────────────────────────────────────────

def test_unlimited_never_blocks():
    assert is_new_user_blocked(10_000, is_existing=False, limit=None) is False
