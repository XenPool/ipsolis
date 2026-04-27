"""Password rotation + lockout policy — RBAC slice 4.

Both knobs are governed by ``app_config`` rows so they're tunable at
runtime via the Settings UI:

* ``rbac.password_rotation_days`` — force admins to change their
  password every N days. ``0`` (default) disables rotation.
* ``rbac.lockout_threshold`` — lock the account after N consecutive
  bad passwords. ``0`` (default) disables lockout.
* ``rbac.lockout_duration_minutes`` — how long an account stays
  locked before the next login attempt is allowed to start with a
  fresh counter. Default ``30`` minutes.

Enforcement is itself an Enterprise feature; on a community license
the helpers below short-circuit so the UX remains identical to
slice-3 (no rotation, no lockout). Operators can still configure the
values without an Enterprise license — they just won't be applied
until a license is uploaded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import AdminUser
from app.models.config import AppConfig
from app.utils.license import is_feature_enabled


@dataclass(frozen=True)
class PasswordPolicy:
    """Effective policy for the current request.

    Values come straight from ``app_config``; ``enforced`` reflects whether
    the Enterprise feature flag is set. When ``enforced`` is False the
    other fields are still populated (so the Settings UI can echo them)
    but the helpers below treat the policy as inert.
    """

    rotation_days: int       # ``0`` = no rotation
    lockout_threshold: int   # ``0`` = no lockout
    lockout_minutes: int     # auto-unlock window
    enforced: bool


_DEFAULT_LOCKOUT_MINUTES = 30


async def read_policy(db: AsyncSession) -> PasswordPolicy:
    """Return the policy as configured + whether Enterprise enforces it."""
    rows = await db.execute(
        select(AppConfig).where(AppConfig.key.in_([
            "rbac.password_rotation_days",
            "rbac.lockout_threshold",
            "rbac.lockout_duration_minutes",
        ]))
    )
    cfg: dict[str, str] = {r.key: (r.value or "") for r in rows.scalars().all()}

    def _coerce_int(raw: str, default: int, lo: int = 0, hi: int = 10**9) -> int:
        try:
            v = int((raw or "").strip() or default)
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    return PasswordPolicy(
        rotation_days=_coerce_int(cfg.get("rbac.password_rotation_days", "0"), 0),
        lockout_threshold=_coerce_int(cfg.get("rbac.lockout_threshold", "0"), 0),
        lockout_minutes=_coerce_int(
            cfg.get("rbac.lockout_duration_minutes", str(_DEFAULT_LOCKOUT_MINUTES)),
            _DEFAULT_LOCKOUT_MINUTES,
        ),
        enforced=is_feature_enabled("password_policy"),
    )


def is_locked(
    user: AdminUser,
    policy: PasswordPolicy,
    now: datetime,
) -> tuple[bool, datetime | None]:
    """Return ``(locked, unlock_at)``.

    A user is locked when ``locked_at`` is set and the configured
    auto-unlock window has not elapsed. The caller is expected to
    auto-clear the row on next successful login (``record_successful_login``)
    or on the next failed attempt that crosses the unlock boundary
    (``record_failed_login`` resets the counter when ``unlock_at`` has
    passed).
    """
    if not policy.enforced:
        return False, None
    if user.locked_at is None:
        return False, None
    unlock_at = user.locked_at + timedelta(minutes=policy.lockout_minutes)
    if now < unlock_at:
        return True, unlock_at
    return False, None


def password_must_be_changed(
    user: AdminUser,
    policy: PasswordPolicy,
    now: datetime,
) -> bool:
    """True iff ``rotation_days`` is set, enforced, and the password is older."""
    if not policy.enforced or policy.rotation_days <= 0:
        return False
    set_at = user.password_set_at or user.created_at
    if set_at is None:
        return False
    return (now - set_at) > timedelta(days=policy.rotation_days)


async def record_failed_login(
    db: AsyncSession,
    user: AdminUser,
    policy: PasswordPolicy,
    now: datetime,
) -> bool:
    """Increment the bad-password counter; lock when threshold is crossed.

    Returns True iff this attempt put the account into the locked state.
    On community / unenforced policy this is a no-op (returns False) so
    the helper is safe to call unconditionally from the login flow.

    If the existing ``locked_at`` is older than the auto-unlock window
    the counter is reset before the increment — this matches the
    "burst of bad attempts auto-clears" semantics described in
    ``read_policy``.
    """
    if not policy.enforced or policy.lockout_threshold <= 0:
        return False

    # Auto-recover from a stale lockout before counting the new attempt.
    if user.locked_at is not None:
        unlock_at = user.locked_at + timedelta(minutes=policy.lockout_minutes)
        if now >= unlock_at:
            user.locked_at = None
            user.failed_login_count = 0

    user.failed_login_count = (user.failed_login_count or 0) + 1
    just_locked = user.failed_login_count >= policy.lockout_threshold
    if just_locked:
        user.locked_at = now
    await db.commit()
    return just_locked


async def record_successful_login(
    db: AsyncSession,
    user: AdminUser,
    now: datetime,
) -> None:
    """Clear the bad-password counter / lockout and stamp ``last_login_at``."""
    user.last_login_at = now
    if user.failed_login_count:
        user.failed_login_count = 0
    if user.locked_at is not None:
        user.locked_at = None
    await db.commit()


async def record_password_change(
    db: AsyncSession,
    user: AdminUser,
    now: datetime,
) -> None:
    """Reset rotation timer + lockout state after a password write.

    Called from both the self-service change-password path
    (``admin_self.py``) and the superadmin-driven reset path
    (``admin_users.py``) so a freshly-rotated password always starts a
    new rotation window with a clean counter.
    """
    user.password_set_at = now
    if user.failed_login_count:
        user.failed_login_count = 0
    if user.locked_at is not None:
        user.locked_at = None
    # Caller owns the surrounding transaction (so the password write and
    # the counter reset land atomically). No commit here.
