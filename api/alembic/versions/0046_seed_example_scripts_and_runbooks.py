"""Seed example scripts and the Virtual Machine Recycler runbook.

Fresh deployments need at least one working runbook + its constituent modules
so admins have a live example of the ``360° VM lifecycle`` pitch to inspect
and adapt. This migration reads disk content and INSERTs it — existing rows
(matched by ``name``) are left untouched so running this against a populated
DB is a safe no-op.

On-disk layout (produced by ``POST /admin/seed/export``):
    /app/scripts/modules/<category>/<slug>.<ext>    — script_modules rows
    /app/scripts/runbooks/<slug>.json               — standalone_runbooks rows

Each script file starts with optional metadata comments:
    # NAME: <exact DB name>
    # DESC: <single-line description>

Runbook JSON references its steps' scripts by ``script_module_name``, so the
seed works regardless of what script ids end up in the fresh DB.

Revision ID: 0046
Revises: 0045
Create Date: 2026-04-24
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.migration.0046")

SCRIPTS_ROOT = Path("/app/scripts")
MODULES_DIR = SCRIPTS_ROOT / "modules"
RUNBOOKS_DIR = SCRIPTS_ROOT / "runbooks"

_EXT_TO_TYPE = {
    ".ps1": "powershell",
    ".py": "python",
    ".sh": "bash",
}


def _parse_script_file(path: Path) -> tuple[str, str, str, str]:
    """Return (name, description, script_type, script_content) for a seed file."""
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    name = ""
    desc = ""
    body_start = 0
    # Look for NAME/DESC in the first few lines (tolerant to blank-line shuffles)
    for i, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped.startswith("# NAME:") and not name:
            name = stripped[len("# NAME:"):].strip()
            body_start = i + 1
        elif stripped.startswith("# DESC:"):
            desc = stripped[len("# DESC:"):].strip()
            body_start = i + 1
        elif stripped == "" and body_start == i:
            body_start = i + 1
    if not name:
        # Fall back to the filename stem. Underscores to spaces.
        name = path.stem.replace("_", " ")
    script_type = _EXT_TO_TYPE.get(path.suffix.lower(), "powershell")
    body = "\n".join(lines[body_start:]).rstrip() + "\n"
    return name, desc, script_type, body


def _seed_scripts(conn) -> int:
    if not MODULES_DIR.is_dir():
        logger.info("seed: modules dir %s not present — skipping scripts seed", MODULES_DIR)
        return 0
    files = sorted(MODULES_DIR.rglob("*"))
    files = [f for f in files if f.is_file() and f.suffix.lower() in _EXT_TO_TYPE]
    inserted = 0
    for path in files:
        name, desc, script_type, content = _parse_script_file(path)
        # Skip if a row with this name already exists — never clobber.
        existing = conn.execute(
            _sql("SELECT 1 FROM script_modules WHERE name = :n"),
            {"n": name},
        ).scalar()
        if existing:
            continue
        conn.execute(
            _sql(
                "INSERT INTO script_modules (name, description, script_content, script_type, is_active) "
                "VALUES (:n, :d, :c, :t, true)"
            ),
            {"n": name, "d": desc or None, "c": content, "t": script_type},
        )
        inserted += 1
        logger.info("seed: inserted script_modules row %r from %s", name, path.name)
    return inserted


def _seed_runbooks(conn) -> int:
    if not RUNBOOKS_DIR.is_dir():
        logger.info("seed: runbooks dir %s not present — skipping runbooks seed", RUNBOOKS_DIR)
        return 0

    # Build name -> id map for script_modules so we can resolve step references.
    rows = conn.execute(_sql("SELECT id, name FROM script_modules")).fetchall()
    name_to_id = {r[1]: r[0] for r in rows}

    inserted = 0
    for path in sorted(RUNBOOKS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("seed: could not read %s: %s", path, exc)
            continue
        if not isinstance(payload, dict) or not payload.get("name"):
            logger.warning("seed: %s has no 'name' field — skipping", path.name)
            continue

        rb_name = str(payload["name"])
        existing = conn.execute(
            _sql("SELECT 1 FROM standalone_runbooks WHERE name = :n"),
            {"n": rb_name},
        ).scalar()
        if existing:
            continue

        rb_id = conn.execute(
            _sql(
                "INSERT INTO standalone_runbooks "
                "(name, description, is_active, cron_expression, cron_enabled, skip_if_running) "
                "VALUES (:n, :d, :a, :ce, :cen, :sir) RETURNING id"
            ),
            {
                "n": rb_name,
                "d": payload.get("description") or None,
                "a": bool(payload.get("is_active", True)),
                "ce": payload.get("cron_expression") or None,
                "cen": bool(payload.get("cron_enabled", False)),
                "sir": bool(payload.get("skip_if_running", True)),
            },
        ).scalar_one()

        for step in payload.get("steps", []):
            script_name = step.get("script_module_name")
            script_id = name_to_id.get(script_name) if script_name else None
            conn.execute(
                _sql(
                    "INSERT INTO standalone_runbook_steps "
                    "(runbook_id, position, step_name, script_module_id, params_template, "
                    " is_critical, retry_count, timeout_seconds, always_run) "
                    "VALUES (:rid, :pos, :sn, :smid, CAST(:pt AS json), :ic, :rc, :ts, :ar)"
                ),
                {
                    "rid": rb_id,
                    "pos": int(step.get("position", 0)),
                    "sn": step.get("step_name") or "",
                    "smid": script_id,
                    "pt": json.dumps(step.get("params_template") or {}),
                    "ic": bool(step.get("is_critical", True)),
                    "rc": int(step.get("retry_count") or 3),
                    "ts": int(step.get("timeout_seconds") or 120),
                    "ar": bool(step.get("always_run", False)),
                },
            )
        inserted += 1
        logger.info(
            "seed: inserted standalone_runbook %r (%d step(s)) from %s",
            rb_name, len(payload.get("steps", [])), path.name,
        )
    return inserted


def _sql(query: str):
    from sqlalchemy import text as sa_text
    return sa_text(query)


def upgrade() -> None:
    conn = op.get_bind()

    # ── Schema backfill ─────────────────────────────────────────────────────
    # These two columns were added manually on dev DBs but never got a
    # migration of their own. Running code depends on them; the seed below
    # writes to ``always_run``. Idempotent — harmless on DBs where the column
    # already exists.
    conn.execute(
        _sql(
            "ALTER TABLE standalone_runbook_steps "
            "ADD COLUMN IF NOT EXISTS always_run BOOLEAN NOT NULL DEFAULT false"
        )
    )
    conn.execute(
        _sql("ALTER TABLE standalone_runbook_runs ADD COLUMN IF NOT EXISTS notes TEXT")
    )

    # ── Seed ────────────────────────────────────────────────────────────────
    script_count = _seed_scripts(conn)
    runbook_count = _seed_runbooks(conn)
    logger.info(
        "seed: inserted %d script module(s) and %d runbook(s)",
        script_count, runbook_count,
    )


def downgrade() -> None:
    # Intentionally no-op: we don't know which of these rows were user-edited
    # vs seeded. Operators can delete rows manually from the admin UI.
    pass
