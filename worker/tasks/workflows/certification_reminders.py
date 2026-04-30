"""Daily Beat task: certification campaign reminders + escalation + auto-revoke.

For each ``running`` campaign:

1. **Reminders** — for every reviewer with pending rows, send one email
   per configured day-offset before due_at (default 7d, 1d). Each
   ``(campaign, reviewer, days_left)`` tuple sends at most once — we
   key dedup off audit log rows the email helper writes, so no extra
   schema is needed.

2. **Overdue email** — once past ``due_at``, send one nag email per
   reviewer with pending rows. Same once-per-(campaign, reviewer)
   semantics as reminders.

3. **Escalation** — once past due_at, send one summary to the
   configured ``certification.escalation_email`` listing every
   reviewer who still has pending rows. At most once per campaign.

4. **Auto-revoke** — once past due_at AND
   ``certification.auto_revoke_on_overdue=true``, transition every
   pending review row to ``auto_revoked`` and dispatch the
   deprovision runbook. Closes the cycle without operator
   intervention.

Closed / cancelled campaigns are skipped — no notifications, no
auto-revoke. Re-opening a campaign isn't supported in slice 1, so this
is sufficient.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from celery import Celery
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules import notifications as notif
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")


def _get_db_session() -> Session:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return Session(engine)


def _truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in ("true", "1", "yes", "on", "enabled")


def _parse_offsets(raw: str) -> list[int]:
    """Parse '7,1' → [7, 1]. Empty / malformed → []."""
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        try:
            n = int(part.strip())
            if n > 0:
                out.append(n)
        except (TypeError, ValueError):
            continue
    return sorted(set(out), reverse=True)


def _days_to_due(due_at: datetime, today: date) -> int:
    """Calendar days from ``today`` to the campaign's due date.

    Returns 0 on the due day itself, negative once overdue. Uses the
    campaign's local date (not its full timestamp) — campaign reminders
    fire daily and 'today is 7 days before due_at' means 'the date
    component' here, not '7×24h to the second'.
    """
    return (due_at.date() - today).days


def _audit_action_for(days_left: int, kind: str) -> str:
    """Build a stable audit-action string the dedup query keys off.

    Reminders use ``reminder_<n>d``; overdue uses ``overdue``;
    escalation uses ``escalation``. Format is grep-friendly and
    survives row-level edits — we only need a unique action per
    notification kind per campaign.
    """
    if kind == "reminder":
        return f"reminder_{days_left}d"
    return kind


def _audit_already_sent(
    db: Session,
    *,
    campaign_id: int,
    reviewer_email: str | None,
    action: str,
) -> bool:
    """Has this exact (campaign, reviewer, action) notification already
    been audited? Reviewer-less notifications (escalation) pass
    ``reviewer_email=None``."""
    if reviewer_email is None:
        rows = db.execute(
            text("""
                SELECT 1 FROM audit_log
                WHERE entity_type = 'certification_campaign'
                  AND entity_id = :cid
                  AND action = :act
                LIMIT 1
            """),
            {"cid": campaign_id, "act": action},
        ).first()
    else:
        # Match on the JSON 'reviewer_email' field inside new_value.
        rows = db.execute(
            text("""
                SELECT 1 FROM audit_log
                WHERE entity_type = 'certification_campaign'
                  AND entity_id = :cid
                  AND action = :act
                  AND new_value->>'reviewer_email' = :email
                LIMIT 1
            """),
            {"cid": campaign_id, "act": action, "email": reviewer_email},
        ).first()
    return rows is not None


def _record_audit(
    db: Session,
    *,
    campaign_id: int,
    action: str,
    payload: dict,
) -> None:
    """Insert one audit row keyed off the certification_campaign entity.

    We attach the per-reviewer notifications here (rather than to the
    review row) because the dedup query keys off campaign+action. If
    we wrote to ``certification_review`` rows instead, restarting a
    campaign with new reviews would re-fire old notifications.
    """
    db.execute(
        text("""
            INSERT INTO audit_log
              (entity_type, entity_id, action, new_value,
               triggered_by, classification)
            VALUES
              ('certification_campaign', :cid, :act, CAST(:val AS JSON),
               'system:certification_reminders', 'internal')
        """),
        {"cid": campaign_id, "act": action, "val": __import__("json").dumps(payload)},
    )


@app.task(name="tasks.workflows.certification_reminders.scan_and_remind")
def scan_and_remind() -> dict:
    """Drive reminder + overdue + escalation + auto-revoke for every running campaign."""
    db = _get_db_session()
    try:
        portal_base = (get_config(db, "portal.base_url", "http://localhost:8000") or "").rstrip("/")
        offsets = _parse_offsets(get_config(db, "certification.reminder_days", "7,1") or "")
        overdue_enabled = _truthy(get_config(db, "certification.overdue_reminder_enabled", "true"))
        auto_revoke = _truthy(get_config(db, "certification.auto_revoke_on_overdue", "false"))
        escalation_emails_raw = (get_config(db, "certification.escalation_email") or "").strip()
        escalation_emails = [a.strip() for a in escalation_emails_raw.split(",") if a.strip()]
        # Worker side doesn't need the api token signer — we mirror it
        # in tasks.modules.teams_notify so review URLs stay self-contained.
        from tasks.modules.teams_notify import make_review_token

        today = date.today()
        # Pull running campaigns + their pending review rows in two queries.
        running = db.execute(
            text("""
                SELECT id, name, due_at
                FROM certification_campaigns
                WHERE status = 'running'
                ORDER BY id
            """)
        ).mappings().all()
        if not running:
            return {"success": True, "checked": 0, "reason": "no running campaigns"}

        total_reminders = 0
        total_overdue = 0
        total_escalations = 0
        total_auto_revoked = 0

        celery_app = Celery(broker=BROKER_URL)

        for camp in running:
            campaign_id = camp["id"]
            campaign_name = camp["name"]
            due_at = camp["due_at"]
            days_left = _days_to_due(due_at, today)
            due_date_str = due_at.strftime("%Y-%m-%d")

            # Pending reviews per (reviewer_email, reviewer_name).
            pending_rows = db.execute(
                text("""
                    SELECT id, reviewer_email, reviewer_name
                    FROM certification_reviews
                    WHERE campaign_id = :cid AND status = 'pending'
                    ORDER BY reviewer_email, id
                """),
                {"cid": campaign_id},
            ).mappings().all()
            if not pending_rows:
                continue

            by_reviewer: dict[str, dict] = {}
            for r in pending_rows:
                bucket = by_reviewer.setdefault(
                    r["reviewer_email"],
                    {"name": r["reviewer_name"], "first_review_id": r["id"], "count": 0},
                )
                bucket["count"] += 1

            # ── Reminders (T-N days before due) ───────────────────────────
            if days_left > 0 and offsets:
                # Fire any offset where days_left <= offset and not yet
                # sent for this (campaign, reviewer, offset). We dedup on
                # the offset itself rather than days_left, so a daily Beat
                # tick that misses a day still fires the latest applicable
                # reminder once.
                for offset in offsets:
                    if days_left > offset:
                        continue
                    action = _audit_action_for(offset, "reminder")
                    for reviewer_email, info in by_reviewer.items():
                        if _audit_already_sent(
                            db, campaign_id=campaign_id,
                            reviewer_email=reviewer_email, action=action,
                        ):
                            continue
                        token = make_review_token(info["first_review_id"])
                        review_url = f"{portal_base}/review-queue/{token}"
                        try:
                            notif.send_certification_reminder(
                                db,
                                reviewer_email=reviewer_email,
                                reviewer_name=info["name"],
                                campaign_name=campaign_name,
                                campaign_id=campaign_id,
                                pending_count=info["count"],
                                days_left=days_left,
                                due_date=due_date_str,
                                review_url=review_url,
                            )
                            _record_audit(
                                db, campaign_id=campaign_id, action=action,
                                payload={
                                    "reviewer_email": reviewer_email,
                                    "pending_count": info["count"],
                                    "days_left": days_left,
                                    "offset": offset,
                                },
                            )
                            total_reminders += 1
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "certification reminder failed for %s (campaign %s): %s",
                                reviewer_email, campaign_id, exc,
                            )
                    # We only fire one offset per (campaign, reviewer) per
                    # day — once an offset matches, break to avoid sending
                    # both the 7d and 1d nudges in the same tick.
                    break

            # ── Overdue email (one per reviewer, once per campaign) ───────
            if days_left < 0 and overdue_enabled:
                for reviewer_email, info in by_reviewer.items():
                    if _audit_already_sent(
                        db, campaign_id=campaign_id,
                        reviewer_email=reviewer_email, action="overdue",
                    ):
                        continue
                    token = make_review_token(info["first_review_id"])
                    review_url = f"{portal_base}/review-queue/{token}"
                    try:
                        notif.send_certification_overdue(
                            db,
                            reviewer_email=reviewer_email,
                            reviewer_name=info["name"],
                            campaign_name=campaign_name,
                            campaign_id=campaign_id,
                            pending_count=info["count"],
                            due_date=due_date_str,
                            review_url=review_url,
                            auto_revoke_enabled=auto_revoke,
                        )
                        _record_audit(
                            db, campaign_id=campaign_id, action="overdue",
                            payload={
                                "reviewer_email": reviewer_email,
                                "pending_count": info["count"],
                            },
                        )
                        total_overdue += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "certification overdue email failed for %s (campaign %s): %s",
                            reviewer_email, campaign_id, exc,
                        )

            # ── Escalation (once per campaign, after due) ─────────────────
            if days_left < 0 and escalation_emails and not _audit_already_sent(
                db, campaign_id=campaign_id, reviewer_email=None, action="escalation",
            ):
                summary_lines = []
                for reviewer_email, info in by_reviewer.items():
                    summary_lines.append(
                        f"  - {info['name'] or reviewer_email} <{reviewer_email}>: "
                        f"{info['count']} pending"
                    )
                summary = "\n".join(summary_lines) or "  (none)"
                if auto_revoke:
                    auto_status = (
                        "<p><strong>Auto-revoke is enabled</strong> — the next Beat tick "
                        "will revoke any remaining pending reviews automatically.</p>"
                    )
                else:
                    auto_status = (
                        "<p>Auto-revoke is not enabled — pending reviews will sit until "
                        "manually decided or the campaign is closed.</p>"
                    )
                try:
                    notif.send_certification_escalation(
                        db,
                        escalation_emails=escalation_emails,
                        campaign_name=campaign_name,
                        campaign_id=campaign_id,
                        due_date=due_date_str,
                        pending_count=sum(info["count"] for info in by_reviewer.values()),
                        reviewer_count=len(by_reviewer),
                        reviewer_summary=summary,
                        campaign_url=f"{portal_base}/ui/certifications",
                        auto_revoke_status=auto_status,
                    )
                    _record_audit(
                        db, campaign_id=campaign_id, action="escalation",
                        payload={
                            "recipients": escalation_emails,
                            "reviewer_count": len(by_reviewer),
                            "pending_count": sum(info["count"] for info in by_reviewer.values()),
                        },
                    )
                    total_escalations += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "certification escalation failed (campaign %s): %s",
                        campaign_id, exc,
                    )

            # ── Auto-revoke pending rows past due ─────────────────────────
            if days_left < 0 and auto_revoke:
                # Mark the pending rows on the certification side and
                # dispatch the deprovision runbook for each underlying
                # order. Done in one tick per campaign to keep audit
                # ordering clean; a campaign with thousands of pending
                # rows on a single day would be unusual but if it happens
                # the next tick will pick up any leftovers.
                rows_to_revoke = db.execute(
                    text("""
                        SELECT id, order_id, reviewer_email
                        FROM certification_reviews
                        WHERE campaign_id = :cid AND status = 'pending'
                    """),
                    {"cid": campaign_id},
                ).mappings().all()
                now = datetime.now(timezone.utc)
                for row in rows_to_revoke:
                    db.execute(
                        text("""
                            UPDATE certification_reviews
                            SET status = 'auto_revoked',
                                decided_at = :now,
                                decided_by = 'system:certification_auto_revoke',
                                comment = 'Auto-revoked: pending past due_at'
                            WHERE id = :id AND status = 'pending'
                        """),
                        {"now": now, "id": row["id"]},
                    )
                    # Dispatch deprovision via the existing dynamic_runner
                    # path. Set the order to REVOKING + DELETE action so
                    # downstream code matches what the API-side decide
                    # endpoint does.
                    db.execute(
                        text("""
                            UPDATE orders
                            SET status = 'revoking',
                                action = 'delete',
                                error_message = :reason
                            WHERE id = :oid
                              AND status::text NOT IN ('rejected','cancelled','revoked')
                        """),
                        {
                            "reason": f"Auto-revoked by certification campaign #{campaign_id}",
                            "oid": row["order_id"],
                        },
                    )
                    celery_app.send_task(
                        "tasks.workflows.dynamic_runner.run",
                        args=[row["order_id"]],
                        queue="reclaim",
                    )
                    _record_audit(
                        db, campaign_id=campaign_id, action="auto_revoke_review",
                        payload={
                            "review_id": row["id"],
                            "order_id": row["order_id"],
                            "reviewer_email": row["reviewer_email"],
                        },
                    )
                    total_auto_revoked += 1

        db.commit()

        if (total_reminders + total_overdue + total_escalations + total_auto_revoked) > 0:
            logger.info(
                "certification scan: reminders=%d overdue=%d escalations=%d auto_revoked=%d",
                total_reminders, total_overdue, total_escalations, total_auto_revoked,
            )

        return {
            "success": True,
            "campaigns_running": len(running),
            "reminders_sent": total_reminders,
            "overdue_emails": total_overdue,
            "escalations": total_escalations,
            "auto_revoked": total_auto_revoked,
        }
    finally:
        db.close()
