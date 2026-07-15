"""ORM for the software_contracts table — vendor software licenses / contracts.

**Naming:** this is the *customer's* software-vendor contract (Adobe CC,
Microsoft 365, …) that backs one or more asset types — NOT the commercial
ip·Solis product `.lic` license (that lives in ``admin_license.py`` /
``license_check.py`` and is config-driven, not a table). The UI labels this
"License / Contract"; the code says ``SoftwareContract`` to keep the two
apart.

Cardinality: **1 contract : N asset types** — the FK (``contract_id``) lives
on ``asset_types`` (0..1 per type). Seat consumption is the sum of active
orders across every bound type (derived, never stored). Cost allocation is
**Model A** (actual consumption × per-seat price); unused seats surface as
shelfware, they are not charged. See ``admin_cost_report.py``.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Billing intervals we normalise to a monthly figure for the cost report.
BILLING_INTERVALS = ("monthly", "quarterly", "annual")
# Divisor INTO a monthly value: contract_value / N = monthly value.
BILLING_TO_MONTHLY_DIVISOR = {"monthly": 1, "quarterly": 3, "annual": 12}


class SoftwareContract(Base):
    __tablename__ = "software_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    product: Mapped[str] = mapped_column(String(200), nullable=False)
    # Contract value per ``billing_interval`` (e.g. annual list price).
    contract_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="EUR")
    billing_interval: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="annual"
    )
    # NULL = unlimited seats (site license): no per-seat price / utilisation.
    licensed_seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Days before ``renewal_date`` the renewal-reminder Beat task fires.
    notice_period_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    auto_renew: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Provider cost center that owns / pays for the contract.
    cost_center: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dedup guard for the renewal-reminder Beat task (mirrors
    # CostThreshold.last_alerted_at) — set to the renewal date it fired for.
    last_renewal_reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SoftwareContract id={self.id} {self.vendor!r}/{self.product!r}>"
