"""Beat task that alerts when projected monthly spend crosses a threshold.

Per-(cost_center, currency) limits live in the ``cost_thresholds`` table.
Each tick this task:

1. Computes the current projected monthly spend per (cost_center, currency)
   from active orders, mirroring the API-side cost report's "by provider"
   aggregation.
2. Joins against ``cost_thresholds``; for each row whose ``monthly_limit``
   is exceeded, sends an email to the configured recipients.
3. Suppresses repeat alerts via ``last_alerted_at`` and the
   ``cost.threshold_alert_quiet_hours`` config key (default 24h) — a
   spend hovering near the limit doesn't spam.

"Active" matches the API-side definition (capacity / quota set):
``pending``, ``pending_approval``, ``scheduled``, ``processing``,
``provisioning``, ``provisioned``, ``delivered``.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules import notifications as notif
from tasks.modules import teams_notify
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

_ACTIVE_ORDER_STATUSES = (
    "pending", "pending_approval", "scheduled",
    "processing", "provisioning", "provisioned", "delivered",
)


def _get_db_session() -> Session:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return Session(engine)


@app.task(name="tasks.workflows.cost_threshold_alerter.scan_and_alert")
def scan_and_alert() -> dict:
    """Compute provider-side projections, alert on breached thresholds."""
    db = _get_db_session()
    try:
        try:
            quiet_hours = max(0, int(get_config(db, "cost.threshold_alert_quiet_hours", "24") or "24"))
        except (TypeError, ValueError):
            quiet_hours = 24
        portal_base = get_config(db, "portal.base_url", "http://localhost:8000")
        cost_report_url = portal_base.rstrip("/") + "/ui/cost-report"

        # Optional Teams card alongside email — same channel that delivers
        # approval cards. Disabled when teams.mode != enabled or the webhook
        # URL isn't configured. Per-row card delivery is best-effort: we
        # send AND email, and stamp last_alerted_at regardless of either's
        # outcome, so a Teams outage doesn't keep emails firing on the
        # same breach forever.
        teams_mode = (get_config(db, "teams.mode", "disabled") or "disabled").strip()
        teams_webhook = (get_config(db, "teams.webhook_url") or "").strip()
        app_title = get_config(db, "app.title", "ip·Solis") or "ip·Solis"
        teams_enabled = teams_mode == "enabled" and bool(teams_webhook)

        # Aggregate provider-side projection: same shape as the API report.
        rows = db.execute(
            text(
                """
                SELECT
                  COALESCE(NULLIF(at.cost_center, ''), '(unassigned)') AS cost_center,
                  at.currency                                          AS currency,
                  SUM(at.monthly_cost)                                  AS projected_total,
                  COUNT(o.id)                                           AS active_orders,
                  COUNT(DISTINCT at.id)                                 AS asset_types
                FROM orders o
                JOIN asset_types at ON at.id = o.asset_type_id
                WHERE at.monthly_cost IS NOT NULL
                  AND o.status::text = ANY(:active_statuses)
                GROUP BY 1, 2
                """
            ),
            {"active_statuses": list(_ACTIVE_ORDER_STATUSES)},
        ).mappings().all()

        # Index projections so the threshold loop is O(thresholds).
        projections: dict[tuple[str, str], dict] = {}
        for r in rows:
            projections[(r["cost_center"], r["currency"] or "")] = {
                "projected_total": float(r["projected_total"] or 0),
                "active_orders":   int(r["active_orders"] or 0),
                "asset_types":     int(r["asset_types"] or 0),
            }

        thresholds = db.execute(
            text(
                """
                SELECT cost_center, currency, monthly_limit, recipients,
                       last_alerted_at, last_alerted_amount
                FROM cost_thresholds
                ORDER BY cost_center, currency
                """
            )
        ).mappings().all()

        if not thresholds:
            return {"success": True, "checked": 0, "alerted": 0, "reason": "no thresholds configured"}

        now = datetime.now(timezone.utc)
        alerted = 0
        teams_sent = 0
        skipped_quiet = 0

        for t in thresholds:
            key = (t["cost_center"], t["currency"])
            proj = projections.get(key)
            if not proj:
                # No active orders for this (cost_center, currency) — nothing to breach.
                continue
            limit = float(t["monthly_limit"])
            if proj["projected_total"] <= limit:
                continue

            # Quiet-window check: skip if we already alerted within the window.
            last = t["last_alerted_at"]
            if last and quiet_hours > 0:
                if now - last < timedelta(hours=quiet_hours):
                    skipped_quiet += 1
                    continue

            recipients = [a.strip() for a in (t["recipients"] or "").split(",") if a.strip()]

            try:
                notif.send_cost_threshold_breach(
                    db,
                    recipients=recipients,
                    cost_center=t["cost_center"],
                    currency=t["currency"],
                    monthly_limit=limit,
                    projected_total=proj["projected_total"],
                    active_orders=proj["active_orders"],
                    asset_types=proj["asset_types"],
                    quiet_hours=quiet_hours,
                    cost_report_url=cost_report_url,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Cost-threshold alert email failed for %s/%s: %s",
                    t["cost_center"], t["currency"], exc,
                )
                # Fall through — still record the attempt so a flaky SMTP
                # relay doesn't lock us into a re-fire loop. Operators see
                # the warning in the worker log and can re-trigger by
                # editing the threshold (which clears last_alerted_at).

            # Optional Teams card — also best-effort; failures don't roll
            # back the email or keep us from stamping last_alerted_at.
            if teams_enabled:
                try:
                    card = teams_notify.build_cost_threshold_breach_card(
                        cost_center=t["cost_center"],
                        currency=t["currency"],
                        monthly_limit=limit,
                        projected_total=proj["projected_total"],
                        active_orders=proj["active_orders"],
                        asset_types=proj["asset_types"],
                        quiet_hours=quiet_hours,
                        cost_report_url=cost_report_url,
                        app_title=app_title,
                    )
                    ok, msg = teams_notify.post_adaptive_card(teams_webhook, card)
                    if ok:
                        teams_sent += 1
                    else:
                        logger.warning(
                            "Teams card failed for cost threshold %s/%s: %s",
                            t["cost_center"], t["currency"], msg,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Teams card raised for cost threshold %s/%s: %s",
                        t["cost_center"], t["currency"], exc,
                    )

            db.execute(
                text(
                    """
                    UPDATE cost_thresholds
                    SET last_alerted_at = :now,
                        last_alerted_amount = :amt
                    WHERE cost_center = :cc AND currency = :cur
                    """
                ),
                {
                    "now": now,
                    "amt": Decimal(str(round(proj["projected_total"], 2))),
                    "cc": t["cost_center"],
                    "cur": t["currency"],
                },
            )

            alerted += 1
            logger.info(
                "Cost-threshold breach alerted: %s/%s — projected %.2f > limit %.2f (recipients=%d)",
                t["cost_center"], t["currency"], proj["projected_total"], limit, len(recipients),
            )

        db.commit()

        return {
            "success": True,
            "checked": len(thresholds),
            "alerted": alerted,
            "teams_sent": teams_sent,
            "skipped_quiet": skipped_quiet,
            "quiet_hours": quiet_hours,
        }
    finally:
        db.close()
