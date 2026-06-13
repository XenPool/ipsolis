"""Daily update notifier — opt-in.

Reads ``updates.check_enabled`` and short-circuits when off, so an
air-gapped install never makes outbound calls without explicit consent.
When on, polls the public ip·Solis GitHub releases endpoint
(unauthenticated — no token needed since the repo is public), parses
the latest tag, and stores the result in ``app_config``. The api side
hydrates the Jinja globals on the next request via
``refresh_app_config_if_stale``, so the banner appears without restarts.

Beat schedule is daily (registered in ``worker/tasks/__init__.py``).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import text

from tasks.modules.maintenance import _db

logger = logging.getLogger(__name__)

_USER_AGENT = "ipsolis-update-notifier"
_TIMEOUT_SECONDS = 5
_MAX_BODY_BYTES = 256 * 1024  # GitHub release JSON is ~few kB; cap defensively.
_RELEASES_URL = "https://api.github.com/repos/xenpool/ipsolis/releases/latest"


def _read_keys(db, keys: list[str]) -> dict[str, str]:
    rows = db.execute(
        text("SELECT key, value FROM app_config WHERE key = ANY(:keys)"),
        {"keys": keys},
    ).all()
    return {r[0]: (r[1] or "") for r in rows}


def _write_kv(db, key: str, value: str) -> None:
    db.execute(
        text(
            """
            UPDATE app_config
               SET value = :value, updated_at = now()
             WHERE key = :key
            """
        ),
        {"key": key, "value": value},
    )


def _fetch_latest_release() -> dict:
    """GET the public ip·Solis releases/latest endpoint and return a parsed dict."""
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    req = urllib.request.Request(_RELEASES_URL, headers=headers)  # noqa: S310
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
        body = resp.read(_MAX_BODY_BYTES + 1)
    if len(body) > _MAX_BODY_BYTES:
        raise ValueError(f"Release JSON exceeds {_MAX_BODY_BYTES} bytes")
    return json.loads(body.decode("utf-8"))


@shared_task(name="tasks.workflows.update_checker.check_for_updates", bind=True)
def check_for_updates(self) -> dict:
    """Daily check. Returns a small summary dict; logs on failure."""
    db = _db()
    try:
        cfg = _read_keys(db, ["updates.check_enabled"])
        enabled = (cfg.get("updates.check_enabled") or "false").strip().lower() in (
            "true", "1", "yes", "on", "enabled",
        )
        if not enabled:
            return {"status": "skipped", "reason": "disabled"}

        try:
            release = _fetch_latest_release()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Repo exists but has no published releases yet — not an error.
                _write_kv(db, "updates.check_error", "")
                _write_kv(db, "updates.checked_at", datetime.now(timezone.utc).isoformat())
                _write_kv(db, "updates.latest_version", "")
                db.commit()
                logger.info("update_checker: no releases published yet (404)")
                return {"status": "ok", "tag": None, "note": "no_releases_yet"}
            msg = f"HTTPError {exc.code}: {exc.reason}"
            logger.warning("update_checker: poll failed: %s", msg)
            _write_kv(db, "updates.check_error", msg[:500])
            _write_kv(db, "updates.checked_at", datetime.now(timezone.utc).isoformat())
            db.commit()
            return {"status": "error", "reason": "fetch_failed", "message": msg}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            logger.warning("update_checker: poll failed: %s", msg)
            _write_kv(db, "updates.check_error", msg[:500])
            _write_kv(db, "updates.checked_at", datetime.now(timezone.utc).isoformat())
            db.commit()
            return {"status": "error", "reason": "fetch_failed", "message": msg}

        tag = (release.get("tag_name") or "").strip()
        html_url = (release.get("html_url") or "").strip()
        published_at = (release.get("published_at") or "").strip()

        # Normalise the tag for storage. We intentionally keep the leading
        # ``v`` if present — the banner reads ``app_version`` (which has no
        # ``v``) and uses ``_normalise_tag`` for comparison. Storing the
        # display form keeps the release URL ↔ banner text aligned.
        _write_kv(db, "updates.latest_version", tag)
        _write_kv(db, "updates.latest_url", html_url)
        _write_kv(db, "updates.latest_published_at", published_at)
        _write_kv(db, "updates.checked_at", datetime.now(timezone.utc).isoformat())
        _write_kv(db, "updates.check_error", "")
        db.commit()

        logger.info(
            "update_checker: latest=%s published=%s url=%s",
            tag, published_at, html_url,
        )
        return {"status": "ok", "tag": tag, "url": html_url}
    finally:
        db.close()
