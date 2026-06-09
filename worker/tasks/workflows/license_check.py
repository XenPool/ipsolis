"""Celery Beat task: daily license expiry check.

Runs once per day (see ``beat_schedule`` in ``worker/tasks/__init__.py``).
Checks the license state and sends email alerts at key milestones:

  - 30 / 14 / 7 days before expiry   → warning email
  - Day 1 of grace period through day 30 → daily warning (grace period active)
  - After grace period ends            → error email (running Community edition)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import text

from tasks.modules.maintenance import _db
from tasks.utils.license import GRACE_PERIOD_DAYS, load_license

logger = logging.getLogger(__name__)

_WARN_THRESHOLDS_DAYS = (30, 14, 7)


@shared_task(name="tasks.workflows.license_check.check_license_expiry", bind=True)
def check_license_expiry(self) -> dict:
    """Daily check. Returns a summary dict; logs/emails side-effects."""
    info = load_license(force_reload=True)

    if info.expires_at is None:
        # Community install or perpetual license — nothing to do.
        return {"status": "skipped", "reason": "no-expiry"}

    now = datetime.now(timezone.utc)
    days_until_expiry = (info.expires_at - now).days
    grace_deadline = info.expires_at + timedelta(days=GRACE_PERIOD_DAYS)

    # ── Case 1: Grace period exhausted — running Community edition ────────────
    if not info.valid:
        days_past_grace = (now - grace_deadline).days
        level_msg = (
            f"License expired {abs(days_until_expiry)} day(s) ago. "
            f"Grace period ended {grace_deadline.date().isoformat()} "
            f"({days_past_grace} day(s) ago). Instance is now Community edition."
        )
        logger.error(level_msg)
        _maybe_send_alert(
            subject="[ip·Solis] License expired — now running Community edition",
            html_body=(
                f"<p>The ip·Solis Pro license has <strong>expired</strong> and "
                f"the 30-day grace period has ended.</p>"
                f"<p>Licensee: {info.licensee}<br>"
                f"Expired on: {info.expires_at.date().isoformat()}<br>"
                f"Grace period ended: {grace_deadline.date().isoformat()}</p>"
                f"<p>The instance is now running <strong>Community edition</strong>. "
                f"Pro features are disabled. Contact "
                f"<a href='mailto:sales@xenpool.de'>sales@xenpool.de</a> to renew.</p>"
            ),
        )
        return {"status": "expired_community", "days_since_expiry": abs(days_until_expiry)}

    # ── Case 2: In grace period ───────────────────────────────────────────────
    if info.in_grace_period:
        grace_days_left = (grace_deadline - now).days
        logger.warning(
            "License expired %d day(s) ago — grace period active, %d day(s) until Community fallback (licensee: %s)",
            abs(days_until_expiry), grace_days_left, info.licensee,
        )
        _maybe_send_alert(
            subject=f"[ip·Solis] License expired — {grace_days_left} day(s) of grace period remaining",
            html_body=(
                f"<p>The ip·Solis Pro license has <strong>expired</strong>, but Pro features "
                f"remain active during the 30-day grace period.</p>"
                f"<p>Licensee: {info.licensee}<br>"
                f"Expired on: {info.expires_at.date().isoformat()}<br>"
                f"Grace period ends: {grace_deadline.date().isoformat()} "
                f"({grace_days_left} day(s) remaining)</p>"
                f"<p>After the grace period, the instance will fall back to Community edition. "
                f"Contact <a href='mailto:sales@xenpool.de'>sales@xenpool.de</a> to renew.</p>"
            ),
        )
        return {
            "status": "grace_period",
            "days_since_expiry": abs(days_until_expiry),
            "grace_days_left": grace_days_left,
            "licensee": info.licensee,
        }

    # ── Case 3: Pre-expiry warnings ───────────────────────────────────────────
    matched = next((d for d in _WARN_THRESHOLDS_DAYS if days_until_expiry <= d), None)
    if matched is None:
        return {"status": "ok", "days": days_until_expiry}

    msg = (
        f"ip·Solis license expires in {days_until_expiry} day(s) on "
        f"{info.expires_at.date().isoformat()} (licensee: {info.licensee})"
    )
    logger.warning(msg)
    _maybe_send_alert(
        subject=f"[ip·Solis] License expires in {days_until_expiry} days",
        html_body=(
            f"<p>The ip·Solis Pro license will expire in "
            f"<strong>{days_until_expiry} day(s)</strong>.</p>"
            f"<p>Licensee: {info.licensee}<br>"
            f"Expires on: {info.expires_at.date().isoformat()}</p>"
            f"<p>After expiry there is a 30-day grace period during which Pro features "
            f"remain active. Renew before expiry to avoid any interruption.<br>"
            f"Contact <a href='mailto:sales@xenpool.de'>sales@xenpool.de</a> to renew.</p>"
        ),
    )
    return {"status": "warning", "days": days_until_expiry, "threshold": matched}


def _maybe_send_alert(subject: str, html_body: str) -> None:
    """Send an email alert if health-alert email is configured. Never raises."""
    try:
        db = _db()
        try:
            enabled_row = db.execute(
                text("SELECT value FROM app_config WHERE key = 'health.alert_enabled'")
            ).first()
            if not enabled_row or (enabled_row[0] or "").strip().lower() not in ("true", "1", "yes"):
                return

            email_row = db.execute(
                text("SELECT value FROM app_config WHERE key = 'health.alert_email'")
            ).first()
            to_addr = (email_row[0] if email_row else "") or ""
            if not to_addr.strip():
                return

            from tasks.modules.maintenance import _send_health_email
            _send_health_email(db, to_addr.strip(), subject, html_body)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — alert delivery must never break the task
        logger.warning("license-expiry alert email failed: %s", exc)
