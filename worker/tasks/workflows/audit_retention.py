"""Beat task: prune audit_log rows past their retention window.

Runs daily at 03:00 (Europe/Berlin). Reads ``retention.audit_log_days``
from ``app_config`` and deletes rows older than that age, using the
``ipsolis.allow_audit_mutation`` GUC bypass installed by migration
0062. The bypass is scoped via ``SET LOCAL`` so it only applies to
the prune transaction — every other DB connection still hits the
default-deny triggers.

When the retention window is 0, the task is a no-op (legitimate
default for compliance-driven tenants who want to keep everything).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _get_db_session() -> Session:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return Session(engine)


def _set_config(db: Session, key: str, value: str) -> None:
    db.execute(
        text("""
            INSERT INTO app_config (key, value, description, is_secret, updated_at)
            VALUES (:k, :v, '', false, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
        """),
        {"k": key, "v": value or ""},
    )


@app.task(name="tasks.workflows.audit_retention.prune_old_rows")
def prune_old_rows() -> dict:
    """Delete audit_log rows older than ``retention.audit_log_days``."""
    db = _get_db_session()
    try:
        try:
            days = int(get_config(db, "retention.audit_log_days", "0") or "0")
        except (TypeError, ValueError):
            days = 0
        if days <= 0:
            return {"success": True, "skipped": True, "reason": "retention disabled"}

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Two-step transaction: opt into the audit-log mutation bypass via
        # SET LOCAL, then DELETE. Both must run in the same transaction —
        # SET LOCAL is scoped to it. ``COMMIT`` releases the bypass.
        result = db.execute(
            text("""
                SET LOCAL ipsolis.allow_audit_mutation = 'true';
                WITH deleted AS (
                    DELETE FROM audit_log
                    WHERE timestamp < :cutoff
                    RETURNING 1
                )
                SELECT count(*) FROM deleted
            """),
            {"cutoff": cutoff},
        ).scalar_one()
        deleted_count = int(result or 0)

        _set_config(db, "retention.last_run_at",
                    datetime.now(timezone.utc).isoformat())
        _set_config(db, "retention.last_pruned", str(deleted_count))
        db.commit()

        if deleted_count == 0:
            logger.info(
                "Audit retention: nothing past %d-day window (cutoff=%s)",
                days, cutoff.isoformat(),
            )
        else:
            logger.info(
                "Audit retention: pruned %d rows older than %d days (cutoff=%s)",
                deleted_count, days, cutoff.isoformat(),
            )
        return {
            "success": True,
            "pruned": deleted_count,
            "retention_days": days,
            "cutoff": cutoff.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Audit retention prune failed: %s", exc)
        db.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        db.close()
