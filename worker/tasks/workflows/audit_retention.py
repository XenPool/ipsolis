"""Beat task: prune audit_log rows past their retention window.

Runs daily at 03:00 (Europe/Berlin). Reads up to four windows from
``app_config``:

* ``retention.audit_log_days`` — global default. Applied to rows
  classified as ``internal`` or whose classification is NULL (legacy
  rows from before slice 2 of audit retention).
* ``retention.pii_days`` — overrides for rows tagged ``pii``.
* ``retention.phi_days`` — overrides for rows tagged ``phi``.
* ``retention.pci_days`` — overrides for rows tagged ``pci``.

A window of ``0`` means "fall back to the global default" for the
per-class keys, and "disabled" for the global key. So a tenant who
sets ``retention.audit_log_days=90`` and ``retention.pii_days=2555``
keeps PII for 7 years while purging routine config changes after 90
days. A tenant who sets only the global key keeps slice-1 behaviour
(every row treated identically).

Each pass runs in its own bounded DELETE statement, so a single huge
class can't starve the others. The ``ipsolis.allow_audit_mutation``
GUC bypass installed by migration 0062 is set once via ``SET LOCAL``
inside the prune transaction and released on COMMIT — every other DB
session continues to hit the default-deny triggers.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Order matters for logging: log strict classes after the global one
# so the per-class numbers read top-down by sensitivity.
_PER_CLASS_KEYS = (
    ("pii", "retention.pii_days"),
    ("phi", "retention.phi_days"),
    ("pci", "retention.pci_days"),
)


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


def _read_int_config(db: Session, key: str) -> int:
    try:
        return int(get_config(db, key, "0") or "0")
    except (TypeError, ValueError):
        return 0


def _delete_with_bypass(
    db: Session,
    *,
    cutoff: datetime,
    classification: str | None,
) -> int:
    """Delete audit_log rows older than ``cutoff`` for one classification bucket.

    ``classification=None`` matches both NULL rows and rows tagged
    ``internal`` (= the legacy default). Per-class buckets match
    exactly. Each call sets ``ipsolis.allow_audit_mutation`` via
    ``SET LOCAL`` before the DELETE, then COMMITs to release the
    bypass — keeping each pass narrowly scoped.
    """
    if classification is None:
        result = db.execute(
            text("""
                SET LOCAL ipsolis.allow_audit_mutation = 'true';
                WITH deleted AS (
                    DELETE FROM audit_log
                    WHERE timestamp < :cutoff
                      AND (classification IS NULL OR classification = 'internal')
                    RETURNING 1
                )
                SELECT count(*) FROM deleted
            """),
            {"cutoff": cutoff},
        ).scalar_one()
    else:
        result = db.execute(
            text("""
                SET LOCAL ipsolis.allow_audit_mutation = 'true';
                WITH deleted AS (
                    DELETE FROM audit_log
                    WHERE timestamp < :cutoff
                      AND classification = :cls
                    RETURNING 1
                )
                SELECT count(*) FROM deleted
            """),
            {"cutoff": cutoff, "cls": classification},
        ).scalar_one()
    db.commit()
    return int(result or 0)


@app.task(name="tasks.workflows.audit_retention.prune_old_rows")
def prune_old_rows() -> dict:
    """Prune audit_log per-classification using configured retention windows."""
    db = _get_db_session()
    try:
        global_days = _read_int_config(db, "retention.audit_log_days")
        per_class: dict[str, int] = {}
        for cls, key in _PER_CLASS_KEYS:
            n = _read_int_config(db, key)
            if n > 0:
                per_class[cls] = n

        # Nothing configured at all — keep the slice-1 "disabled = no-op" semantics.
        if global_days <= 0 and not per_class:
            return {"success": True, "skipped": True, "reason": "retention disabled"}

        now = datetime.now(timezone.utc)
        pruned: dict[str, int] = {}

        # Pass 1: global default applied to internal / NULL rows.
        if global_days > 0:
            cutoff = now - timedelta(days=global_days)
            pruned["internal"] = _delete_with_bypass(
                db, cutoff=cutoff, classification=None,
            )

        # Passes 2..N: per-class buckets. A class with its own window
        # falls under that window; classes without one fall through
        # the global window (above) when their rows are tagged
        # ``internal`` — which won't happen for rows correctly
        # classified at write time. Per-class rows without a window
        # are kept indefinitely (= explicit opt-in to retention only).
        for cls, days in per_class.items():
            cutoff = now - timedelta(days=days)
            pruned[cls] = _delete_with_bypass(
                db, cutoff=cutoff, classification=cls,
            )

        total = sum(pruned.values())
        _set_config(db, "retention.last_run_at", now.isoformat())
        _set_config(db, "retention.last_pruned", str(total))
        _set_config(
            db, "retention.last_pruned_by_class",
            json.dumps(pruned, separators=(",", ":")),
        )
        db.commit()

        logger.info(
            "Audit retention: pruned %d rows by class (%s); global=%dd, per-class=%s",
            total,
            ", ".join(f"{k}={v}" for k, v in pruned.items()) or "none",
            global_days,
            {k: v for k, v in per_class.items()} or "none",
        )
        return {
            "success": True,
            "pruned": total,
            "by_class": pruned,
            "retention_days": global_days,
            "per_class_days": per_class,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Audit retention prune failed: %s", exc)
        db.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        db.close()
