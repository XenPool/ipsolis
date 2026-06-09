"""ORM for the cost_report_snapshots table — see migration 0080."""
from __future__ import annotations

from datetime import date as _date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CostReportSnapshot(Base):
    __tablename__ = "cost_report_snapshots"

    snapshot_date: Mapped[_date] = mapped_column(Date, primary_key=True)
    # 'provider' | 'consumer_cc' | 'consumer_dept'
    view: Mapped[str] = mapped_column(String(20), primary_key=True)
    # The aggregation key (cost center, department) — kept as text so
    # all three views fit one table without per-view columns.
    dimension_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    projected_monthly_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    active_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_types: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<CostReportSnapshot {self.snapshot_date} {self.view} "
            f"{self.dimension_key}/{self.currency}={self.projected_monthly_total}>"
        )
