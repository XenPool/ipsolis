"""ORM for the hr_leaver_events table — see migration 0083."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HrLeaverEvent(Base):
    __tablename__ = "hr_leaver_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    user_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    orders_revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approvals_superseded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviews_superseded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(255), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<HrLeaverEvent id={self.id} source={self.source} "
            f"email={self.user_email} status={self.status}>"
        )
