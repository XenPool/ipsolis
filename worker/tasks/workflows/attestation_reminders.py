"""Celery Beat task: overdue handover-acknowledgment reminders.

Runs daily. Re-emails the signed handover link for every ``handover`` artifact
still ``pending`` past ``attestation.handover_reminder_days`` since it was
emitted (or last reminded). Opt-in via ``attestation.handover_reminder_enabled``.
Deduped so a given artifact is nudged at most once per reminder window
(``last_reminder_at``). Revocation certificates are evidence-only — never
reminded. Nothing is blocked.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.audit_helper import waudit
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

_ACTOR = "beat:attestation_reminders"


def _db() -> Session:
    from tasks.modules.db import get_worker_session
    return get_worker_session()


def _bool_cfg(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


@app.task(name="tasks.workflows.attestation_reminders.check_overdue_handovers")
def check_overdue_handovers() -> dict:
    """Nudge recipients who haven't acknowledged their handover."""
    db = _db()
    try:
        if not _bool_cfg(get_config(db, "attestation.handover_reminder_enabled", "false")):
            return {"success": True, "skipped": "disabled"}

        try:
            days = int((get_config(db, "attestation.handover_reminder_days", "3") or "3").strip())
        except ValueError:
            days = 3
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))

        rows = db.execute(text(
            """
            SELECT id, order_id, recipient_email, recipient_name, snapshot
            FROM attestation_artifacts
            WHERE kind = 'handover' AND status = 'pending'
              AND COALESCE(last_reminder_at, created_at) < :cutoff
            ORDER BY created_at ASC
            LIMIT 200
            """
        ), {"cutoff": cutoff}).mappings().all()

        if not rows:
            return {"success": True, "due": 0}

        reminded = 0
        for r in rows:
            _remind(db, r)
            db.execute(
                text("UPDATE attestation_artifacts SET last_reminder_at = :now WHERE id = :i"),
                {"now": datetime.now(timezone.utc), "i": r["id"]},
            )
            waudit(db, "attestation_artifact", r["id"], "reminder_sent",
                   new={"order_id": r["order_id"], "recipient_email": r["recipient_email"]},
                   by=_ACTOR)
            reminded += 1
        db.commit()
        logger.info("attestation reminders: %d overdue handover(s) nudged", reminded)
        return {"success": True, "due": len(rows), "reminded": reminded}
    finally:
        db.close()


def _remind(db, r) -> None:
    """Best-effort re-send of the signed handover link."""
    to_email = r["recipient_email"]
    if not to_email:
        return
    try:
        from tasks.modules.config_reader import get_config
        from tasks.modules.teams_notify import make_attestation_token
        portal_base = (get_config(db, "portal.base_url", "http://localhost:8000") or "").rstrip("/")
        app_title = get_config(db, "app.title", "ip·Solis") or "ip·Solis"
        snap = r["snapshot"] or {}
        asset = snap.get("asset_type_name") if isinstance(snap, dict) else None
        url = f"{portal_base}/attestation/{make_attestation_token(r['id'])}"
        subj = f"[{app_title}] Reminder — please acknowledge receipt: {asset or 'your access'}"
        body = (
            f"<p>Hi {r['recipient_name'] or ''},</p>"
            f"<p>This is a reminder to acknowledge the handover of your access"
            f"{(' to <b>' + asset + '</b>') if asset else ''}:</p>"
            f"<p><a href='{url}'>Open handover acknowledgment &rarr;</a></p>"
            "<p>The link is signed and works without a portal login.</p>"
        )
        from tasks.modules.notifications import _production_send_html_email, MAIL_FROM
        _production_send_html_email(db, [to_email], None, get_config(db, "email.from", MAIL_FROM), subj, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attestation reminder email failed for %s: %s", r["id"], exc)
