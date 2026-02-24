"""Modul: Config Reader – Liest Konfigurationswerte aus der app_config-Tabelle.

Ersetzt Env-Var-basierte Config für SMTP, AD und andere Laufzeiteinstellungen.
"""

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_config(db: Session, key: str, default: str | None = None) -> str | None:
    """
    Liest einen Wert aus der app_config-Tabelle.

    Args:
        db:      Sync SQLAlchemy Session
        key:     Konfigurationsschlüssel (z.B. "ad.server")
        default: Rückgabewert wenn Schlüssel nicht existiert

    Returns:
        Der Wert als str, oder default wenn nicht gefunden.
    """
    row = db.execute(
        text("SELECT value FROM app_config WHERE key = :key"),
        {"key": key},
    ).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def get_config_int(db: Session, key: str, default: int = 0) -> int:
    """Liest einen Integer-Konfigurationswert."""
    value = get_config(db, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("app_config key=%r value=%r is not an integer, using default=%s", key, value, default)
        return default
