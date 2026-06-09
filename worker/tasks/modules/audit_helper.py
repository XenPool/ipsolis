"""Sync audit helper for Celery worker.

Writes audit entries via raw SQL (no ORM import from api/).
The caller is responsible for the commit.
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


_VALID_CLASSIFICATIONS = ("internal", "pii", "phi", "pci")


def waudit(
    db: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    *,
    old: dict | None = None,
    new: dict | None = None,
    by: str,
    ctx: str | None = None,
    classification: str | None = None,
) -> None:
    """Writes an audit log entry (sync, no commit).

    Args:
        db:             Active SQLAlchemy Session (psycopg2)
        entity_type:    "order" | "asset" | "asset_type" | "app_config"
        entity_id:      PK of the changed record
        action:         "created" | "status_changed" | "updated" | "deleted"
        old:            Snapshot before the change
        new:            Snapshot after the change
        by:             Trigger, e.g. "celery:vdi_provision"
        ctx:             Optional context (celery_task_id, etc.)
        classification: One of ``internal`` / ``pii`` / ``phi`` / ``pci``
                        for per-class retention windows. Defaults to
                        ``internal``. Pass the strictest class of any
                        attribute on the touched asset type.
    """
    cls = classification if classification in _VALID_CLASSIFICATIONS else "internal"
    try:
        db.execute(
            text("""
                INSERT INTO audit_log
                  (entity_type, entity_id, action, old_value, new_value,
                   triggered_by, context, classification)
                VALUES
                  (:et, :eid, :act, CAST(:old AS JSON), CAST(:new AS JSON),
                   :by, :ctx, :cls)
            """),
            {
                "et": entity_type,
                "eid": entity_id,
                "act": action,
                "old": json.dumps(old) if old is not None else None,
                "new": json.dumps(new) if new is not None else None,
                "by": by,
                "ctx": ctx,
                "cls": cls,
            },
        )
    except Exception as e:
        # Audit errors must not interrupt the main runbook
        logger.error("waudit failed (non-critical): entity=%s:%s action=%s error=%s",
                     entity_type, entity_id, action, e)


def classify_asset_type_config(config: list[dict] | None) -> str:
    """Strictest classification declared on any attribute in ``config``.

    Mirror of ``app.utils.audit.classify_asset_type`` for the worker
    side. Kept duplicated to keep the worker free of api package
    imports (the boundary the rest of audit_helper observes).
    """
    rank = {"internal": 0, "pii": 1, "phi": 2, "pci": 3}
    best = "internal"
    best_rank = 0
    for attr in config or ():
        if not isinstance(attr, dict):
            continue
        cls = (attr.get("classification") or "").lower()
        r = rank.get(cls, 0)
        if r > best_rank:
            best_rank = r
            best = cls
    return best
