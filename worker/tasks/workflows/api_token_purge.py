"""Beat task: hard-delete revoked / expired API tokens past their retention window.

Slice 1 of API tokens (migration ``0054_api_tokens``) soft-deletes via
``revoked_at`` and leaves rows in the database forever — the audit
trail of "we used to have a token X" stays intact. This task is the
opt-in counterpart for tenants whose record-retention policy mandates
the opposite: revoked / expired credentials must not linger past a
configured window.

Two delete conditions are evaluated against the same window
(``api_tokens.purge_after_days``):

1. ``revoked_at IS NOT NULL AND revoked_at < cutoff`` — admin-revoked
   tokens. Already not authenticating because the auth path checks
   ``revoked_at``.
2. ``expires_at IS NOT NULL AND expires_at < cutoff`` — naturally
   expired tokens that were never explicitly revoked. Already not
   authenticating because the auth path checks ``expires_at``.

Tokens with ``revoked_at IS NULL AND (expires_at IS NULL OR
expires_at > NOW())`` are *active* and never touched by this task.

A window of ``0`` (default) disables the purge entirely — the task
runs but is a no-op, so admins can leave the Beat slot installed and
flip the policy on/off without restarting Celery.

Each deletion writes one ``api_token / hard_deleted`` audit row
capturing the token name + prefix + the reason it qualified
(``revoked`` / ``expired``) and when. The hash isn't recorded —
once the row is gone there's no replay surface, and the audit
trail's role is to identify which integration the token belonged
to, not to reconstruct the credential.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app
from tasks.modules.audit_helper import waudit
from tasks.modules.config_reader import get_config

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _get_db_session() -> Session:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return Session(engine)


@app.task(name="tasks.workflows.api_token_purge.purge_old_tokens")
def purge_old_tokens() -> dict:
    """Hard-delete revoked / expired API tokens older than the configured window.

    Returns ``{success, deleted_revoked, deleted_expired, cutoff_iso}``
    so /health-style introspection can read the last-tick result. A
    disabled window (``0``) returns a ``skipped: True`` envelope so
    operators can distinguish "task ran but did nothing" from "task
    didn't run at all".
    """
    db = _get_db_session()
    try:
        try:
            window_days = int(get_config(db, "api_tokens.purge_after_days", "0") or "0")
        except (TypeError, ValueError):
            window_days = 0
        if window_days <= 0:
            return {"success": True, "skipped": True, "reason": "purge_after_days disabled"}

        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

        # Snapshot the rows we're about to delete so the audit trail
        # has the full row context (name, prefix, reason). We capture
        # both reasons in a single SELECT so a row that's both expired
        # AND revoked (admin revoked an already-expired token) only
        # produces one audit entry attributed to the stronger reason
        # (revocation — explicit operator intent vs. clock-driven).
        rows = db.execute(
            text("""
                SELECT id, name, token_prefix, revoked_at, expires_at, scopes,
                       CASE
                         WHEN revoked_at IS NOT NULL AND revoked_at < :cutoff THEN 'revoked'
                         WHEN expires_at IS NOT NULL AND expires_at < :cutoff THEN 'expired'
                       END AS reason
                FROM api_tokens
                WHERE (revoked_at IS NOT NULL AND revoked_at < :cutoff)
                   OR (expires_at IS NOT NULL AND expires_at < :cutoff)
                ORDER BY id
            """),
            {"cutoff": cutoff},
        ).fetchall()

        if not rows:
            logger.info(
                "api_token_purge: no tokens past cutoff %s (window=%dd)",
                cutoff.isoformat(), window_days,
            )
            return {
                "success": True, "deleted_revoked": 0, "deleted_expired": 0,
                "cutoff_iso": cutoff.isoformat(),
            }

        deleted_revoked = 0
        deleted_expired = 0
        for r in rows:
            waudit(
                db, "api_token", int(r.id), "hard_deleted",
                old={
                    "name": r.name,
                    "token_prefix": r.token_prefix,
                    "scopes": r.scopes,
                    "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                },
                new={
                    "reason": r.reason,
                    "purged_after_days": window_days,
                    "cutoff_iso": cutoff.isoformat(),
                },
                by="celery:api_token_purge",
            )
            if r.reason == "revoked":
                deleted_revoked += 1
            else:
                deleted_expired += 1

        # Single bulk delete after the audit trail is written — keeps
        # the audit row order deterministic against the deletion order
        # so a downstream SIEM consumer can replay them in id sequence.
        db.execute(
            text("""
                DELETE FROM api_tokens
                WHERE id = ANY(:ids)
            """),
            {"ids": [int(r.id) for r in rows]},
        )
        db.commit()

        logger.info(
            "api_token_purge: deleted %d tokens past cutoff %s (revoked=%d expired=%d, window=%dd)",
            len(rows), cutoff.isoformat(), deleted_revoked, deleted_expired, window_days,
        )
        return {
            "success": True,
            "deleted_revoked": deleted_revoked,
            "deleted_expired": deleted_expired,
            "cutoff_iso": cutoff.isoformat(),
        }
    finally:
        db.close()
