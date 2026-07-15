"""DriftFinding — a detected divergence between provisioned state and AD.

One row per (group, principal, direction) divergence found by the drift
reconciliation Beat task:

* ``missing_access`` — ipSolis provisioned the grant (it's in an active
  order's change log) but the principal is NOT in the AD group.
* ``out_of_band``    — the principal IS in the AD group but ipSolis never
  granted it (added directly in AD, outside ipSolis).

``remediation`` records what the task did: ``detected`` (detect-only),
``re_granted`` / ``revoked`` (auto-remediate succeeded), ``failed``, or
``skipped``.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DriftFinding(Base):
    __tablename__ = "drift_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("asset_types.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="ad_group")
    identifier: Mapped[str] = mapped_column(Text, nullable=False)      # group DN
    principal: Mapped[str] = mapped_column(String(255), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # missing_access | out_of_band
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    remediation: Mapped[str] = mapped_column(String(30), nullable=False, default="detected")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DriftFinding id={self.id} {self.direction} {self.principal} "
            f"@ {self.identifier!r} status={self.status}>"
        )
