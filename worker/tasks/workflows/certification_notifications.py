"""Celery tasks for sending certification campaign notifications.

Decouples the API's ``start_campaign`` endpoint from blocking on email +
Teams delivery — the endpoint enqueues these tasks and returns
immediately. The reminder Beat task (``certification_reminders``) calls
the same helpers directly since it's already running in the worker.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules import notifications as notif
from tasks.modules import teams_notify

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _get_db_session() -> Session:
    from tasks.modules.db import get_worker_session
    return get_worker_session()


@app.task(name="tasks.workflows.certification_notifications.send_kickoff_email")
def send_kickoff_email(
    reviewer_email: str,
    reviewer_name: str,
    campaign_name: str,
    campaign_id: int,
    review_count: int,
    due_date: str,
    review_url: str,
    teams_enabled: bool,
    teams_webhook: str,
    app_title: str,
) -> dict:
    """Send the kickoff email + (optional) Teams card to one reviewer.

    Best-effort: failures log at WARNING but don't raise — the campaign
    has already started and an email outage shouldn't surface as a
    Celery task failure that re-queues forever.

    Task name lives under ``dynamic_runner.*`` so the existing
    notifications-queue routing pattern picks it up without an extra
    routing entry.
    """
    db = _get_db_session()
    try:
        try:
            notif.send_certification_kickoff(
                db,
                reviewer_email=reviewer_email,
                reviewer_name=reviewer_name,
                campaign_name=campaign_name,
                campaign_id=campaign_id,
                review_count=review_count,
                due_date=due_date,
                review_url=review_url,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "certification kickoff email failed for %s (campaign %s): %s",
                reviewer_email, campaign_id, exc,
            )

        if teams_enabled and teams_webhook:
            try:
                card = teams_notify.build_certification_kickoff_card(
                    reviewer_name=reviewer_name,
                    reviewer_email=reviewer_email,
                    campaign_name=campaign_name,
                    review_count=review_count,
                    due_date=due_date,
                    review_url=review_url,
                    app_title=app_title,
                )
                ok, msg = teams_notify.post_adaptive_card(teams_webhook, card)
                if not ok:
                    logger.warning(
                        "certification kickoff Teams card failed for %s (campaign %s): %s",
                        reviewer_email, campaign_id, msg,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "certification kickoff Teams card raised for %s (campaign %s): %s",
                    reviewer_email, campaign_id, exc,
                )

        return {"success": True, "reviewer_email": reviewer_email}
    finally:
        db.close()
