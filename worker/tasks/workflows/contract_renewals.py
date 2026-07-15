"""Celery Beat task: software-contract renewal reminders.

Runs daily. For every ``software_contracts`` row whose ``renewal_date`` has
entered its ``notice_period_days`` window, emails a reminder (once per window
entry — deduped via ``last_renewal_reminder_at``) and audit-logs it (which
the SIEM streamer forwards). Opt-in via ``contract.renewal_reminder_enabled``.

Auto-renew contracts are still reminded: the notice window is exactly when you
must act to *cancel* before it renews — arguably the more important case.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.audit_helper import waudit
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

_ACTOR = "beat:contract_renewals"


def _db() -> Session:
    from tasks.modules.db import get_worker_session
    return get_worker_session()


def _bool_cfg(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


@app.task(name="tasks.workflows.contract_renewals.check_contract_renewals")
def check_contract_renewals() -> dict:
    """Email a reminder for each contract inside its renewal notice window."""
    db = _db()
    try:
        if not _bool_cfg(get_config(db, "contract.renewal_reminder_enabled", "false")):
            return {"success": True, "skipped": "disabled"}

        to_addr = (
            (get_config(db, "contract.renewal_reminder_email", "") or "").strip()
            or (get_config(db, "health.alert_email", "") or "").strip()
        )

        # Due = renewal_date set, today is within [renewal - notice, renewal],
        # and we haven't already reminded for this window.
        today = date.today()
        rows = db.execute(text(
            """
            SELECT id, vendor, product, currency, contract_value, billing_interval,
                   licensed_seats, renewal_date, notice_period_days, auto_renew
            FROM software_contracts
            WHERE renewal_date IS NOT NULL
              AND last_renewal_reminder_at IS NULL
              AND (renewal_date - notice_period_days) <= :today
              AND renewal_date >= :today
            ORDER BY renewal_date
            """
        ), {"today": today}).mappings().all()

        if not rows:
            return {"success": True, "due": 0}

        reminded = 0
        for r in rows:
            days_left = (r["renewal_date"] - today).days
            _send_reminder(db, r, days_left, to_addr)
            db.execute(
                text("UPDATE software_contracts SET last_renewal_reminder_at = :now WHERE id = :i"),
                {"now": datetime.now(timezone.utc), "i": r["id"]},
            )
            waudit(
                db, "software_contract", r["id"], "renewal_reminder",
                new={"renewal_date": r["renewal_date"].isoformat(), "days_left": days_left,
                     "notified": bool(to_addr)},
                by=_ACTOR,
            )
            reminded += 1
        db.commit()
        logger.info("contract renewals: %d reminder(s) sent", reminded)
        return {"success": True, "due": len(rows), "reminded": reminded, "notified": bool(to_addr)}
    finally:
        db.close()


def _send_reminder(db, r, days_left: int, to_addr: str) -> None:
    """Best-effort renewal email. SIEM/audit covers the event regardless."""
    if not to_addr:
        return
    try:
        from tasks.modules.notifications import _production_send_html_email, MAIL_FROM
        renew = "auto-renews" if r["auto_renew"] else "expires"
        seats = f"{r['licensed_seats']} seats" if r["licensed_seats"] else "unlimited seats"
        subj = (
            f"[ipSolis] Contract renewal in {days_left}d: {r['vendor']} {r['product']}"
        )
        body = (
            "<p>A software contract is entering its renewal notice window:</p>"
            "<table style='font-size:13px;border-collapse:collapse'>"
            f"<tr><td style='padding:2px 8px'><b>Vendor / product</b></td><td>{r['vendor']} — {r['product']}</td></tr>"
            f"<tr><td style='padding:2px 8px'><b>Renewal date</b></td><td>{r['renewal_date'].isoformat()} "
            f"({days_left} day(s), {renew})</td></tr>"
            f"<tr><td style='padding:2px 8px'><b>Notice period</b></td><td>{r['notice_period_days']} day(s)</td></tr>"
            f"<tr><td style='padding:2px 8px'><b>Value</b></td><td>{float(r['contract_value']):.2f} "
            f"{r['currency']} / {r['billing_interval']}</td></tr>"
            f"<tr><td style='padding:2px 8px'><b>Seats</b></td><td>{seats}</td></tr>"
            "</table>"
            "<p>See <b>Licenses &amp; Contracts</b> in the admin UI.</p>"
        )
        _production_send_html_email(db, [to_addr], None, get_config(db, "email.from", MAIL_FROM), subj, body)
    except Exception as exc:  # noqa: BLE001 — delivery must never break the task
        logger.warning("contract renewal reminder email failed for %s: %s", r["id"], exc)
