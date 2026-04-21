"""ORM model for the db_backups table (Maintenance → Backups)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DbBackup(Base):
    __tablename__ = "db_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # pending | running | success | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    # manual | scheduled
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual", server_default="manual")
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<DbBackup id={self.id} file={self.filename!r} status={self.status!r}>"
