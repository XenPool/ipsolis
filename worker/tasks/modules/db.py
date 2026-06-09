"""Shared DB session factory for Celery worker tasks.

Uses NullPool so every task opens exactly one connection and closes it when
the session is disposed — no idle connections accumulate between tasks even
when many workers run concurrently.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

_DATABASE_URL = (
    os.getenv("DATABASE_URL", "postgresql+psycopg2://xpuser:changeme@localhost:5432/ipsolis")
    .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
)


def get_worker_session() -> Session:
    """Return a new SQLAlchemy Session backed by a NullPool engine.

    Callers are responsible for closing the session (and thereby the
    underlying connection) when done.
    """
    engine = create_engine(_DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)
    return Session(engine)
