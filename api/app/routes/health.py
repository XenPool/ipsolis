import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(tags=["health"])


def _resolve_version() -> str:
    """Read the running version. ``/app/VERSION`` (bind-mount) wins so
    bumping the file alone is enough — no image rebuild required.

    Tolerant of the encoding the file lands in. Windows PowerShell
    5.1's ``>`` redirection writes UTF-16 LE with BOM by default, so
    operators who run ``echo "0.5.0" > VERSION`` will produce a file
    that a naive utf-8 decoder rejects. We try utf-8-sig (handles
    UTF-8 + BOM), then utf-16 (LE/BE auto-detect), then plain utf-8;
    the first that yields a non-empty stripped string wins. A
    completely unparseable file falls through to ``APP_VERSION`` env
    rather than 500'ing the module import.
    """
    try:
        with open("/app/VERSION", "rb") as f:
            raw = f.read()
    except OSError:
        raw = b""
    for codec in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            v = raw.decode(codec).strip()
        except (UnicodeDecodeError, UnicodeError):
            continue
        if v:
            return v
    return (os.environ.get("APP_VERSION") or "0.0.0").strip() or "0.0.0"


_VERSION = _resolve_version()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> dict:
    """Liveness + Readiness Check."""
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "version": _VERSION,
        "service": "xp-api",
    }
