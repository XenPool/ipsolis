"""Admin-API: Maintenance (backups, cleanup, health, queue inspection).

All endpoints require X-Admin-Key or an authenticated admin session.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.db_backup import DbBackup
from app.utils.auth import require_admin_key
from app.utils.features import require_enterprise

_ENT = require_enterprise("advanced_maintenance")

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/maintenance",
    tags=["admin-maintenance"],
    dependencies=[Depends(require_admin_key)],
)

BACKUP_DIR = Path("/app/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

_SAFE_NAME = re.compile(r"^xp_backup_\d{8}_\d{6}\.sql\.gz$")


def _get_celery():
    from celery import Celery
    return Celery(broker=settings.CELERY_BROKER_URL)


# ── Backups ───────────────────────────────────────────────────────────────────


class BackupCreate(BaseModel):
    note: str | None = None


def _session_user(request: Request) -> str | None:
    s = request.session
    return s.get("admin_email") or s.get("admin_user") or "admin"


@router.get("/backups")
async def list_backups(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(
        select(DbBackup).order_by(DbBackup.created_at.desc()).limit(200)
    )
    rows = result.scalars().all()
    out = []
    for b in rows:
        out.append({
            "id":          b.id,
            "filename":    b.filename,
            "size_bytes":  b.size_bytes,
            "status":      b.status,
            "trigger":     b.trigger,
            "created_by":  b.created_by,
            "note":        b.note,
            "error":       b.error,
            "created_at":  b.created_at.isoformat() if b.created_at else None,
            "finished_at": b.finished_at.isoformat() if b.finished_at else None,
        })
    return out


@router.post("/backups", dependencies=[_ENT])
async def create_backup(
    request: Request,
    payload: BackupCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Creates a pending db_backups row and enqueues the worker task."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"xp_backup_{ts}.sql.gz"

    created_by = _session_user(request)
    backup = DbBackup(
        filename=filename,
        status="pending",
        trigger="manual",
        created_by=created_by,
        note=(payload.note or None),
    )
    db.add(backup)
    await db.commit()
    await db.refresh(backup)

    celery = _get_celery()
    task = celery.send_task(
        "tasks.modules.maintenance.run_backup",
        args=[backup.id, "manual"],
        queue="default",
    )
    logger.info("Enqueued backup id=%s task=%s", backup.id, task.id)
    return {
        "id":          backup.id,
        "filename":    backup.filename,
        "status":      backup.status,
        "task_id":     task.id,
    }


@router.get("/backups/{backup_id}/download", dependencies=[_ENT])
async def download_backup(
    backup_id: int, db: AsyncSession = Depends(get_db)
) -> FileResponse:
    backup = await db.get(DbBackup, backup_id)
    if not backup:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup not found")
    if backup.status != "success":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Backup is in status '{backup.status}' — cannot download",
        )
    if not _SAFE_NAME.match(backup.filename):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unexpected filename format")
    path = BACKUP_DIR / backup.filename
    if not path.exists():
        raise HTTPException(
            status.HTTP_410_GONE,
            "Backup file is missing on disk (was it deleted manually?)",
        )
    return FileResponse(
        path=str(path),
        media_type="application/gzip",
        filename=backup.filename,
    )


@router.delete("/backups/{backup_id}", dependencies=[_ENT])
async def delete_backup(
    backup_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    backup = await db.get(DbBackup, backup_id)
    if not backup:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup not found")
    if _SAFE_NAME.match(backup.filename):
        path = BACKUP_DIR / backup.filename
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not delete backup file %s: %s", path, exc)
    await db.delete(backup)
    await db.commit()
    return {"success": True, "id": backup_id}


# ── Retention ─────────────────────────────────────────────────────────────────


_RETENTION_KEYS = [
    # (table, config_key, timestamp_column)
    ("orders",                  "retention.orders_days",          "created_at"),
    ("audit_log",               "retention.audit_log_days",       "timestamp"),
    ("standalone_runbook_runs", "retention.standalone_runs_days", "created_at"),
]


class RetentionUpdate(BaseModel):
    orders_days: int | None = None
    audit_log_days: int | None = None
    standalone_runs_days: int | None = None
    keep_last_n_backups: int | None = None


@router.get("/retention")
async def get_retention(db: AsyncSession = Depends(get_db)) -> dict:
    rows = await db.execute(text(
        "SELECT key, value FROM app_config WHERE key IN ("
        "'retention.orders_days', 'retention.audit_log_days', "
        "'retention.standalone_runs_days', 'backup.keep_last_n')"
    ))
    cfg = {k: v for k, v in rows.all()}
    out = {
        "orders_days":          int(cfg.get("retention.orders_days") or 0),
        "audit_log_days":       int(cfg.get("retention.audit_log_days") or 0),
        "standalone_runs_days": int(cfg.get("retention.standalone_runs_days") or 0),
        "keep_last_n_backups":  int(cfg.get("backup.keep_last_n") or 0),
        "tables": [],
    }
    # Row counts + oldest/newest for each managed table
    for table, _key, ts_col in _RETENTION_KEYS:
        row = await db.execute(text(
            f"SELECT COUNT(*), MIN({ts_col}), MAX({ts_col}) FROM {table}"
        ))
        n, oldest, newest = row.first()
        out["tables"].append({
            "table":  table,
            "count":  int(n or 0),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
        })
    return out


@router.put("/retention", dependencies=[_ENT])
async def set_retention(
    payload: RetentionUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    updates = {
        "retention.orders_days":          payload.orders_days,
        "retention.audit_log_days":       payload.audit_log_days,
        "retention.standalone_runs_days": payload.standalone_runs_days,
        "backup.keep_last_n":             payload.keep_last_n_backups,
    }
    for key, value in updates.items():
        if value is None:
            continue
        if value < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{key} must be >= 0")
        await db.execute(
            text(
                "INSERT INTO app_config (key, value, description, is_secret) "
                "VALUES (:k, :v, NULL, false) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
            ),
            {"k": key, "v": str(value)},
        )
    await db.commit()
    return {"success": True}


@router.post("/cleanup")
async def run_cleanup(
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Preview or execute retention cleanup, inline.

    Both dry-run and the actual delete run directly in the API's async
    session. The queries are simple COUNT/DELETE statements against indexed
    timestamp columns — fast enough that a Celery round-trip would only add
    failure modes (tasks stuck in a backlogged queue).
    """
    cfg_rows = (
        await db.execute(
            text(
                "SELECT key, value FROM app_config WHERE key IN "
                "('retention.orders_days', 'retention.audit_log_days', "
                "'retention.standalone_runs_days')"
            )
        )
    ).fetchall()
    cfg = {r[0]: r[1] for r in cfg_rows}
    summary: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for table, key, col in _RETENTION_KEYS:
        raw = (cfg.get(key) or "").strip()
        days = int(raw) if raw.isdigit() else 0
        if days <= 0:
            summary[table] = {"days": days, "skipped": True}
            continue
        cutoff = now - timedelta(days=days)
        count_row = (
            await db.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {col} < :c"),  # noqa: S608 — table is from the fixed _RETENTION_KEYS list, not user input
                {"c": cutoff},
            )
        ).first()
        n = int(count_row[0]) if count_row else 0
        if dry_run:
            summary[table] = {"days": days, "would_delete": n}
        else:
            await db.execute(
                text(f"DELETE FROM {table} WHERE {col} < :c"),  # noqa: S608 — table is from the fixed _RETENTION_KEYS list, not user input
                {"c": cutoff},
            )
            summary[table] = {"days": days, "deleted": n}
    if not dry_run:
        await db.commit()
        logger.info("admin: retention cleanup deleted rows per table: %s", summary)
    return {"enqueued": False, "success": True, "dry_run": dry_run, "summary": summary}


# ── Health probes ─────────────────────────────────────────────────────────────


async def _probe_db(db: AsyncSession) -> dict:
    try:
        res = await db.execute(text("SELECT version()"))
        version = (res.first() or ("?",))[0]
        return {"ok": True, "detail": str(version)[:120]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


def _probe_redis() -> dict:
    try:
        import redis  # type: ignore[import-not-found]
    except Exception as exc:
        return {"ok": False, "detail": f"redis package missing: {exc}"}
    try:
        url = settings.CELERY_BROKER_URL
        r = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        r.ping()
        info = r.info(section="server")
        return {"ok": True, "detail": f"redis {info.get('redis_version', '?')}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


async def _probe_entra(db: AsyncSession) -> dict:
    row = await db.execute(
        text("SELECT value FROM app_config WHERE key = 'entra.mode'")
    )
    mode = (row.first() or (None,))[0]
    if (mode or "disabled") == "disabled":
        return {"ok": None, "detail": "disabled"}
    try:
        from app.utils.entra import _get_entra_config, get_msal_app
        cfg = await _get_entra_config(db)
        msal_app = get_msal_app(cfg)
        if msal_app is None:
            return {"ok": False, "detail": "Missing tenant_id, client_id, or client_secret"}
        result = msal_app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" in result:
            return {"ok": True, "detail": f"token acquired (mode={mode})"}
        err = result.get("error_description") or result.get("error") or "unknown error"
        return {"ok": False, "detail": str(err)[:200]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


async def _probe_sccm(db: AsyncSession) -> dict:
    row = await db.execute(text(
        "SELECT key, value FROM app_config WHERE key IN "
        "('sccm.base_url', 'sccm.username', 'sccm.realm', 'sccm.kdc')"
    ))
    cfg = {k: v for k, v in row.all()}
    base = (cfg.get("sccm.base_url") or "").strip()
    if not base:
        return {"ok": None, "detail": "not configured"}
    # Enqueue the existing sccm_probe (pwsh+Kerberos) task and wait briefly
    try:
        import asyncio
        from celery import Celery
        client = Celery(broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND)
        def _probe() -> dict:
            try:
                ar = client.send_task("tasks.workflows.sccm_probe.probe", queue="provision")
                return ar.get(timeout=20)
            except Exception as exc:
                return {"ok": False, "message": str(exc)}
        result = await asyncio.get_running_loop().run_in_executor(None, _probe)
        ok = result.get("ok")
        detail = result.get("message") or base
        return {"ok": bool(ok) if ok is not None else None, "detail": str(detail)[:200]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


async def _probe_smtp(db: AsyncSession) -> dict:
    import smtplib
    import socket as _socket
    row = await db.execute(text(
        "SELECT key, value FROM app_config "
        "WHERE key IN ('email.smtp_server', 'email.smtp_port')"
    ))
    cfg = {k: v for k, v in row.all()}
    host = (cfg.get("email.smtp_server") or "").strip()
    port_s = (cfg.get("email.smtp_port") or "25").strip()
    if not host:
        return {"ok": None, "detail": "not configured"}
    try:
        port = int(port_s) if port_s.isdigit() else 25
    except Exception:
        port = 25
    try:
        with smtplib.SMTP(host, port, timeout=4) as s:
            s.ehlo()
        return {"ok": True, "detail": f"connected to {host}:{port}"}
    except (OSError, _socket.timeout, smtplib.SMTPException) as exc:
        return {"ok": False, "detail": str(exc)[:200]}


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    return {
        "database": await _probe_db(db),
        "redis":    _probe_redis(),
        "entra":    await _probe_entra(db),
        "sccm":     await _probe_sccm(db),
        "smtp":     await _probe_smtp(db),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Queue inspection ──────────────────────────────────────────────────────────


_KNOWN_QUEUES = ("default", "provision", "reclaim", "notifications")


@router.get("/queue")
async def queue_status() -> dict:
    """Returns queue depth (Redis LLEN) + worker-side active/reserved task counts."""
    # Queue depth via redis
    depths: dict[str, int | str] = {}
    try:
        import redis  # type: ignore[import-not-found]
        r = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2)
        for q in _KNOWN_QUEUES:
            try:
                depths[q] = int(r.llen(q))
            except Exception as exc:
                depths[q] = f"err: {exc}"
    except Exception as exc:
        depths = {"error": str(exc)}

    # Worker activity via celery control
    workers: dict[str, dict] = {}
    try:
        celery = _get_celery()
        insp = celery.control.inspect(timeout=2.0)
        active = insp.active() or {}
        reserved = insp.reserved() or {}
        ping = insp.ping() or {}
        for name, status_dict in ping.items():
            workers[name] = {
                "ok":       (status_dict or {}).get("ok") == "pong",
                "active":   len(active.get(name, [])),
                "reserved": len(reserved.get(name, [])),
            }
    except Exception as exc:
        workers = {"error": str(exc)}

    return {"queues": depths, "workers": workers}


class QueuePurge(BaseModel):
    queue: str


@router.post("/queue/purge", dependencies=[_ENT])
async def purge_queue(payload: QueuePurge) -> dict:
    if payload.queue not in _KNOWN_QUEUES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown queue: {payload.queue}")
    try:
        import redis  # type: ignore[import-not-found]
        r = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2)
        n = int(r.llen(payload.queue))
        r.delete(payload.queue)
        return {"success": True, "queue": payload.queue, "removed": n}
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc


# ── Backup schedule ───────────────────────────────────────────────────────────


class ScheduleUpdate(BaseModel):
    enabled: bool | None = None
    cron: str | None = None


def _validate_cron(expr: str) -> None:
    try:
        from croniter import croniter  # type: ignore[import-not-found]
    except Exception:
        # croniter is only in the worker image; accept unvalidated in api if missing
        return
    if not croniter.is_valid(expr):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid cron expression: {expr!r}")


async def _upsert_cfg(db: AsyncSession, key: str, value: str) -> None:
    await db.execute(
        text(
            "INSERT INTO app_config (key, value, description, is_secret) "
            "VALUES (:k, :v, NULL, false) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
        ),
        {"k": key, "v": value},
    )


@router.get("/schedule", dependencies=[_ENT])
async def get_schedule(db: AsyncSession = Depends(get_db)) -> dict:
    rows = await db.execute(text(
        "SELECT key, value FROM app_config "
        "WHERE key IN ('backup.enabled', 'backup.schedule_cron')"
    ))
    cfg = {k: v for k, v in rows.all()}
    return {
        "enabled": (cfg.get("backup.enabled") or "false").lower() in ("1", "true", "yes", "on"),
        "cron":    cfg.get("backup.schedule_cron") or "0 2 * * *",
    }


@router.put("/schedule", dependencies=[_ENT])
async def set_schedule(
    payload: ScheduleUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    if payload.cron is not None:
        cron = payload.cron.strip()
        if not cron:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "cron must not be empty")
        _validate_cron(cron)
        await _upsert_cfg(db, "backup.schedule_cron", cron)
    if payload.enabled is not None:
        await _upsert_cfg(db, "backup.enabled", "true" if payload.enabled else "false")
    await db.commit()
    return {"success": True}


# ── Health alerts ─────────────────────────────────────────────────────────────


class AlertUpdate(BaseModel):
    enabled: bool | None = None
    email: str | None = None
    cooldown_minutes: int | None = None


@router.get("/alerts", dependencies=[_ENT])
async def get_alerts(db: AsyncSession = Depends(get_db)) -> dict:
    rows = await db.execute(text(
        "SELECT key, value FROM app_config WHERE key IN ("
        "'health.alert_enabled', 'health.alert_email', 'health.alert_cooldown_minutes')"
    ))
    cfg = {k: v for k, v in rows.all()}
    cooldown = cfg.get("health.alert_cooldown_minutes") or "60"
    return {
        "enabled":          (cfg.get("health.alert_enabled") or "false").lower() in ("1", "true", "yes", "on"),
        "email":            cfg.get("health.alert_email") or "",
        "cooldown_minutes": int(cooldown) if cooldown.isdigit() else 60,
    }


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.put("/alerts", dependencies=[_ENT])
async def set_alerts(
    payload: AlertUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    if payload.email is not None:
        email = payload.email.strip()
        if email and not _EMAIL_RE.match(email):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid email: {email!r}")
        await _upsert_cfg(db, "health.alert_email", email)
    if payload.cooldown_minutes is not None:
        if payload.cooldown_minutes < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "cooldown_minutes must be >= 0")
        await _upsert_cfg(db, "health.alert_cooldown_minutes", str(payload.cooldown_minutes))
    if payload.enabled is not None:
        await _upsert_cfg(db, "health.alert_enabled", "true" if payload.enabled else "false")
    await db.commit()
    return {"success": True}


@router.post("/alerts/test", dependencies=[_ENT])
async def test_alert(db: AsyncSession = Depends(get_db)) -> dict:
    """Sends a test email to the configured alert recipient."""
    row = await db.execute(
        text("SELECT value FROM app_config WHERE key = 'health.alert_email'")
    )
    to_addr = ((row.first() or ("",))[0] or "").strip()
    if not to_addr:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No alert recipient configured")
    celery = _get_celery()
    result = celery.send_task(
        "tasks.modules.maintenance.send_test_alert_email",
        queue="default",
    )
    return {"enqueued": True, "task_id": result.id, "recipient": to_addr}
