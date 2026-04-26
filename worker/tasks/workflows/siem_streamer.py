"""Beat task that streams new ``audit_log`` rows to the configured SIEM.

Runs every minute. Queries audit_log rows with id greater than the
persisted cursor (``siem.last_id`` in ``app_config``), batches them up
to ``siem.batch_size``, POSTs to the SIEM endpoint, and advances the
cursor only on success.

Failure modes:

* HEC rejects the batch → cursor stays put, retry next tick. Repeated
  failures are surfaced via ``siem.last_error`` so the Settings UI can
  show "n batches behind, last error: …".
* Worker crashes mid-flight → cursor stayed at the last known-good id;
  on restart we re-fetch and re-deliver the in-flight batch. SIEM
  endpoints are expected to dedupe by event id (Splunk HEC accepts a
  duplicate stream gracefully — at-least-once is the contract).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

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
    """Upsert an ``app_config`` value. Keeps ``updated_at`` fresh."""
    db.execute(
        text("""
            INSERT INTO app_config (key, value, description, is_secret, updated_at)
            VALUES (:k, :v, '', false, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
        """),
        {"k": key, "v": value or ""},
    )


@app.task(name="tasks.workflows.siem_streamer.stream_audit_log")
def stream_audit_log() -> dict:
    """Forward outstanding audit_log rows to the configured SIEM endpoint."""
    db = _get_db_session()
    try:
        enabled = (get_config(db, "siem.enabled", "false") or "false").strip().lower()
        if enabled not in ("true", "1", "yes", "on", "enabled"):
            return {"success": True, "skipped": True, "reason": "siem.enabled is false"}

        endpoint = (get_config(db, "siem.endpoint_url", "") or "").strip()
        token = (get_config(db, "siem.token", "") or "").strip()
        workspace_id = (get_config(db, "siem.workspace_id", "") or "").strip()
        shared_key = (get_config(db, "siem.shared_key", "") or "").strip()
        log_type = (get_config(db, "siem.log_type", "IpsolisAudit") or "IpsolisAudit").strip() or "IpsolisAudit"
        fmt = (get_config(db, "siem.format", "splunk_hec") or "splunk_hec").strip()
        try:
            batch_size = max(1, min(1000, int(get_config(db, "siem.batch_size", "200") or "200")))
        except (TypeError, ValueError):
            batch_size = 200
        verify_tls_raw = (get_config(db, "siem.verify_tls", "true") or "true").strip().lower()
        verify_tls = verify_tls_raw not in ("false", "0", "no", "off")
        try:
            last_id = int(get_config(db, "siem.last_id", "0") or "0")
        except (TypeError, ValueError):
            last_id = 0

        if fmt == "splunk_hec" and (not endpoint or not token):
            _set_config(db, "siem.last_error",
                        f"Missing endpoint or token at {datetime.now(timezone.utc).isoformat()}")
            db.commit()
            return {"success": False, "reason": "missing endpoint or token"}
        if fmt == "sentinel" and (not workspace_id or not shared_key):
            _set_config(db, "siem.last_error",
                        f"Missing workspace_id or shared_key at {datetime.now(timezone.utc).isoformat()}")
            db.commit()
            return {"success": False, "reason": "missing workspace_id or shared_key"}

        rows = db.execute(
            text("""
                SELECT id, entity_type, entity_id, action,
                       old_value, new_value, triggered_by, context, timestamp
                FROM audit_log
                WHERE id > :last
                ORDER BY id ASC
                LIMIT :limit
            """),
            {"last": last_id, "limit": batch_size},
        ).fetchall()

        if not rows:
            return {"success": True, "forwarded": 0, "last_id": last_id}

        from tasks.modules.siem_export import (
            _row_to_event,
            build_sentinel_payload,
            build_splunk_hec_payload,
            post_sentinel,
            post_splunk_hec,
        )

        events = [_row_to_event(r) for r in rows]
        host = (get_config(db, "app.title", "ipsolis") or "ipsolis").strip().replace(" ", "_").lower()

        if fmt == "splunk_hec":
            payload = build_splunk_hec_payload(events, host=host)
            ok, msg = post_splunk_hec(endpoint, token, payload, verify_tls=verify_tls)
        elif fmt == "sentinel":
            payload = build_sentinel_payload(events)
            ok, msg = post_sentinel(
                workspace_id, shared_key, payload,
                log_type=log_type, verify_tls=verify_tls,
            )
        else:
            ok, msg = False, f"Unknown SIEM format: {fmt!r}"

        if not ok:
            _set_config(db, "siem.last_error",
                        f"{msg} (at {datetime.now(timezone.utc).isoformat()})")
            db.commit()
            logger.warning("SIEM stream failed (%d events pending from id>%d): %s",
                           len(events), last_id, msg)
            return {"success": False, "forwarded": 0, "pending": len(events), "error": msg}

        new_last = events[-1]["id"]
        _set_config(db, "siem.last_id", str(new_last))
        _set_config(db, "siem.last_error", "")
        _set_config(db, "siem.last_success_at", datetime.now(timezone.utc).isoformat())
        db.commit()
        logger.info("SIEM stream forwarded %d events (id %d → %d)",
                    len(events), last_id + 1, new_last)
        return {"success": True, "forwarded": len(events), "last_id": new_last}
    finally:
        db.close()
