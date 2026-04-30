"""ORM for the cost_thresholds table — see migration 0079."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CostThreshold(Base):
    __tablename__ = "cost_thresholds"

    cost_center: Mapped[str] = mapped_column(String(100), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    recipients: Mapped[str] = mapped_column(Text, nullable=False)
    last_alerted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_alerted_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<CostThreshold {self.cost_center}/{self.currency} "
            f"limit={self.monthly_limit}>"
        )
