"""Daily update notifier — opt-in.

Reads ``updates.check_enabled`` and short-circuits when off, so an
air-gapped install (or any tenant that hasn't flipped the toggle) never
makes outbound calls. When on, polls the configured GitHub releases
endpoint, parses the latest tag (``tag_name``), and stores the result
in ``app_config``. The api side hydrates the corresponding Jinja
globals on the next request via the existing
``refresh_app_config_if_stale`` middleware, so the banner partial
shows up without restarts.

The Beat schedule is daily (registered in ``worker/tasks/__init__.py``)
so we don't pound GitHub's API even on a noisy fleet. The endpoint is
unauthenticated by default — GitHub allows 60 unauth requests per hour
per IP, more than enough for once-daily polls. Operators who hit the
limit (e.g. NAT'd behind a shared egress IP) can repoint
``updates.repo_url`` at an internal mirror.

This task lives in the worker so a slow GitHub API doesn't block the
api event loop, and so the request lifecycle isn't on the hook for an
external network call.
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


def _fetch_latest_release(repo_url: str, token: str | None = None) -> dict:
    """GET ``<repo_url>/releases/latest`` and return a parsed dict.

    ``repo_url`` is expected to be the GitHub API root for a repo, e.g.
    ``https://api.github.com/repos/XenPool/ipsolis``. The trailing
    ``/releases/latest`` is appended here so operators don't have to
    encode that boilerplate in config.

    ``token`` is an optional Personal Access Token (classic or
    fine-grained) — required for private repos. Sent as
    ``Authorization: Bearer <token>`` per the GitHub REST API spec.
    Public repos still work without it (60 unauth req/h/IP).
    """
    url = repo_url.rstrip("/") + "/releases/latest"
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 — fixed scheme, validated input
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
        cfg = _read_keys(db, [
            "updates.check_enabled",
            "updates.repo_url",
            "updates.github_token",
            "updates.latest_version",
        ])
        enabled = (cfg.get("updates.check_enabled") or "false").strip().lower() in (
            "true", "1", "yes", "on", "enabled",
        )
        if not enabled:
            return {"status": "skipped", "reason": "disabled"}

        repo_url = (cfg.get("updates.repo_url") or "").strip()
        if not repo_url.startswith(("http://", "https://")):
            _write_kv(db, "updates.check_error", "updates.repo_url is not a valid http(s) URL")
            db.commit()
            return {"status": "error", "reason": "bad_repo_url"}

        token = (cfg.get("updates.github_token") or "").strip() or None
        try:
            release = _fetch_latest_release(repo_url, token=token)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
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
