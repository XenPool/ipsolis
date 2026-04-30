"""Daily snapshot of the cost report into ``cost_report_snapshots``.

Captures three views — provider, consumer cost center, consumer department —
once per day so the cost report can render at any past date by reading the
snapshot rather than re-querying live state. ``cost.snapshot_retention_days``
caps storage growth (default 365 days; 0 = keep forever).

Idempotent within a day: rerunning the task overwrites the same
``snapshot_date`` rows. The daily cadence is at 02:00 Europe/Berlin so the
day's final state is captured before the audit-retention prune (03:00) and
the threshold alerter (04:00) run.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

_ACTIVE_ORDER_STATUSES = (
    "pending", "pending_approval", "scheduled",
    "processing", "provisioning", "provisioned", "delivered",
)

# View → SQL grouping column mapping. Mirrors the API report's three views.
_VIEW_SOURCES = {
    "provider": (
        "COALESCE(NULLIF(at.cost_center, ''), '(unassigned)')",
        "at.currency",
    ),
    "consumer_cc": (
        "COALESCE(NULLIF(o.requester_cost_center, ''), '(unassigned)')",
        "at.currency",
    ),
    "consumer_dept": (
        "COALESCE(NULLIF(o.requester_department, ''), '(unassigned)')",
        "at.currency",
    ),
}


def _get_db_session() -> Session:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return Session(engine)


@app.task(name="tasks.workflows.cost_report_snapshot.capture_daily_snapshot")
def capture_daily_snapshot() -> dict:
    """Capture all three cost-report views for today, prune old snapshots."""
    db = _get_db_session()
    try:
        today = date.today()
        captured_at = datetime.now(timezone.utc)

        # Wipe today's rows first so we're idempotent if the task runs
        # twice in one day (manual re-trigger, Beat HA edge cases).
        db.execute(
            text("DELETE FROM cost_report_snapshots WHERE snapshot_date = :d"),
            {"d": today},
        )

        rows_written = 0
        per_view: dict[str, int] = {}
        for view, (key_expr, currency_expr) in _VIEW_SOURCES.items():
            sql = f"""
                INSERT INTO cost_report_snapshots
                  (snapshot_date, view, dimension_key, currency,
                   projected_monthly_total, active_orders, asset_types, captured_at)
                SELECT
                    :snapshot_date,
                    :view,
                    {key_expr}                          AS dimension_key,
                    {currency_expr}                     AS currency,
                    SUM(at.monthly_cost)                AS projected_monthly_total,
                    COUNT(o.id)                         AS active_orders,
                    COUNT(DISTINCT at.id)               AS asset_types,
                    :captured_at
                FROM orders o
                JOIN asset_types at ON at.id = o.asset_type_id
                WHERE at.monthly_cost IS NOT NULL
                  AND o.status::text = ANY(:active_statuses)
                GROUP BY {key_expr}, {currency_expr}
            """
            result = db.execute(
                text(sql),
                {
                    "snapshot_date": today,
                    "view": view,
                    "captured_at": captured_at,
                    "active_statuses": list(_ACTIVE_ORDER_STATUSES),
                },
            )
            per_view[view] = result.rowcount or 0
            rows_written += per_view[view]

        # Retention prune.
        try:
            retention_days = max(0, int(get_config(db, "cost.snapshot_retention_days", "365") or "365"))
        except (TypeError, ValueError):
            retention_days = 365
        pruned = 0
        if retention_days > 0:
            cutoff = today - timedelta(days=retention_days)
            res = db.execute(
                text("DELETE FROM cost_report_snapshots WHERE snapshot_date < :cutoff"),
                {"cutoff": cutoff},
            )
            pruned = res.rowcount or 0

        db.commit()

        logger.info(
            "Cost-report snapshot %s captured: %d rows (provider=%d, consumer_cc=%d, consumer_dept=%d), pruned=%d (retention=%d days)",
            today, rows_written, per_view["provider"], per_view["consumer_cc"], per_view["consumer_dept"],
            pruned, retention_days,
        )
        return {
            "success": True,
            "snapshot_date": today.isoformat(),
            "rows_written": rows_written,
            "per_view": per_view,
            "pruned": pruned,
            "retention_days": retention_days,
        }
    finally:
        db.close()
