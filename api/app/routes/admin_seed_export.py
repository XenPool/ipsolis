"""Admin API: export current DB scripts + standalone runbooks to disk.

The DB (``script_modules`` + ``standalone_runbooks``) is the runtime source of
truth. This endpoint snapshots the current state to ``/app/scripts/modules/``
and ``/app/scripts/runbooks/`` so the set can be committed to git and used as
seed material for fresh deployments (see migration 0046).

On-disk conventions:
- Script files are organised by category derived from the DB name prefix
  (``"SCCM - Delete Device"`` → ``scripts/modules/sccm/SCCM_-_Delete_Device.ps1``).
- The first two comment lines carry the exact DB name and description so
  the seed importer can round-trip without relying on filename parsing:

      # NAME: SCCM - Delete Device
      # DESC: Removes the device record from SCCM prior to re-import.
      <rest of the PowerShell source as authored in the Admin UI>

- Runbooks are written as JSON with their steps referenced by script *name*
  (not id), so a seed import in a new environment resolves them against
  whatever ids the freshly-seeded script_modules happen to have.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.script_module import ScriptModule
from app.models.standalone_runbook import StandaloneRunbook, StandaloneRunbookStep
from app.utils.auth import require_admin_key
from app.utils.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/seed",
    tags=["admin-seed"],
    # Seed export writes scripts/runbooks to disk for git commit —
    # superadmin only since it touches the seed material that
    # ships in the docker image.
    dependencies=[Depends(require_admin_key), require_role("superadmin")],
)

SCRIPTS_ROOT = Path("/app/scripts")
MODULES_DIR = SCRIPTS_ROOT / "modules"
RUNBOOKS_DIR = SCRIPTS_ROOT / "runbooks"

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _category_from_name(name: str) -> str:
    """Derive category from the "<CAT> - ..." convention used in DB names."""
    if " - " in name:
        cat = name.split(" - ", 1)[0].strip().lower()
        if cat:
            return cat
    return "misc"


def _slugify(name: str) -> str:
    """Filesystem-safe slug that preserves readability."""
    slug = _SAFE.sub("_", name.strip())
    return slug.strip("._") or "unnamed"


_EXT_BY_TYPE = {
    "powershell": ".ps1",
    "python": ".py",
    "bash": ".sh",
}


def _script_extension(script_type: str) -> str:
    return _EXT_BY_TYPE.get(script_type, ".txt")


def _render_script_file(row: ScriptModule) -> str:
    """Produce the on-disk content for one script_module row.

    The comment prefix uses PowerShell / shell-compatible ``#``; for python the
    ``#`` is also valid. We intentionally use the same two-line header format
    for every language so the importer is language-agnostic.
    """
    name = row.name or ""
    desc = (row.description or "").replace("\r", "").replace("\n", " ").strip()
    header = [f"# NAME: {name}"]
    if desc:
        header.append(f"# DESC: {desc}")
    header.append("")
    body = row.script_content or ""
    # Avoid duplicating headers if the script already carries them from a
    # previous export — strip any leading NAME:/DESC: comment block.
    lines = body.splitlines()
    i = 0
    while i < len(lines) and (
        lines[i].startswith("# NAME:") or lines[i].startswith("# DESC:") or lines[i].strip() == ""
    ):
        # Only consume at most a two-line header + one blank
        if lines[i].startswith("# NAME:") or lines[i].startswith("# DESC:"):
            i += 1
            continue
        break
    cleaned = "\n".join(lines[i:])
    return "\n".join(header) + cleaned.rstrip() + "\n"


async def _export_scripts(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(ScriptModule).order_by(ScriptModule.name))
    rows = list(result.scalars().all())
    written: list[dict[str, Any]] = []
    for row in rows:
        category = _category_from_name(row.name or "")
        folder = MODULES_DIR / category
        folder.mkdir(parents=True, exist_ok=True)
        filename = _slugify(row.name or f"script-{row.id}") + _script_extension(row.script_type)
        path = folder / filename
        path.write_text(_render_script_file(row), encoding="utf-8", newline="\n")
        written.append({"id": row.id, "name": row.name, "path": str(path.relative_to(SCRIPTS_ROOT.parent))})
    return written


async def _export_runbooks(db: AsyncSession) -> list[dict[str, Any]]:
    RUNBOOKS_DIR.mkdir(parents=True, exist_ok=True)

    # Map script_module_id -> name so we can reference scripts symbolically.
    id_to_name: dict[int, str] = dict(
        (
            await db.execute(text("SELECT id, name FROM script_modules"))
        ).fetchall()
    )

    result = await db.execute(select(StandaloneRunbook).order_by(StandaloneRunbook.name))
    runbooks = list(result.scalars().all())
    written: list[dict[str, Any]] = []
    for rb in runbooks:
        step_result = await db.execute(
            select(StandaloneRunbookStep)
            .where(StandaloneRunbookStep.runbook_id == rb.id)
            .order_by(StandaloneRunbookStep.position)
        )
        steps = list(step_result.scalars().all())

        payload = {
            "name": rb.name,
            "description": rb.description or "",
            "is_active": bool(rb.is_active),
            "cron_enabled": bool(rb.cron_enabled),
            "cron_expression": rb.cron_expression,
            "skip_if_running": bool(rb.skip_if_running),
            "steps": [
                {
                    "position": s.position,
                    "step_name": s.step_name,
                    "script_module_name": id_to_name.get(s.script_module_id) if s.script_module_id else None,
                    "params_template": s.params_template or {},
                    "is_critical": bool(s.is_critical),
                    "retry_count": int(s.retry_count or 1),
                    "timeout_seconds": int(s.timeout_seconds or 120),
                    "always_run": bool(s.always_run),
                }
                for s in steps
            ],
        }

        slug = _slugify(rb.name or f"runbook-{rb.id}")
        path = RUNBOOKS_DIR / f"{slug}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        written.append({"id": rb.id, "name": rb.name, "path": str(path.relative_to(SCRIPTS_ROOT.parent))})
    return written


@router.post("/export")
async def export_seed(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Snapshot all script_modules + standalone_runbooks to disk.

    Overwrites existing files. Intended to be followed by `git add scripts/ && git commit`.
    """
    try:
        MODULES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cannot create {MODULES_DIR}: {exc}. "
                   "Make sure ./scripts is bind-mounted read-write for the api container.",
        )

    try:
        scripts = await _export_scripts(db)
        runbooks = await _export_runbooks(db)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Filesystem error during export: {exc}",
        )

    logger.info(
        "admin: seed export wrote %d script(s) and %d runbook(s) to %s",
        len(scripts), len(runbooks), SCRIPTS_ROOT,
    )
    return {
        "scripts": len(scripts),
        "runbooks": len(runbooks),
        "modules_dir": str(MODULES_DIR),
        "runbooks_dir": str(RUNBOOKS_DIR),
        "written": {"scripts": scripts, "runbooks": runbooks},
    }
