"""Lightweight optional order header for multi-item requests (bundles / cart).

**Deliberately not** the full "invert the Order model" design from the audit —
see the descope note in TASKS.md. Single orders never get a group
(``Order.order_group_id`` stays NULL and they behave exactly as before). An
``OrderGroup`` is created **only** when several orders are placed together (a
bundle today; a cart later), purely to tie the sibling orders together for a
grouped view and to freeze the bundle provenance.

Approval stays per-``Order`` (already item-scoped); group status is **derived**
from the member orders' statuses on read (``derive_status`` below), not stored.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Real sources of a multi-item request.
ORIGINS = ("portal", "servicenow", "api", "rule_based", "bundle_catalog")


class OrderGroup(Base):
    __tablename__ = "order_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin: Mapped[str] = mapped_column(String(30), nullable=False, server_default="portal")
    requester_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requester_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Recipient / owner the items are for (may differ from the requester).
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Bundle provenance (nullable — a future cart group has no bundle).
    bundle_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bundles.id", ondelete="SET NULL"), nullable=True
    )
    bundle_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # Frozen snapshot of the resolved bundle at order time (positions,
    # required flags, config) for auditability — the bundle itself is live.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    orders: Mapped[list["Order"]] = relationship(  # noqa: F821
        "Order", back_populates="order_group"
    )

    def __repr__(self) -> str:
        return f"<OrderGroup id={self.id} origin={self.origin} bundle={self.bundle_id}>"


# Status precedence for deriving a group status from member order statuses.
# A group is only as "done" as its least-progressed still-active item.
def derive_status(order_statuses: list[str]) -> str:
    """Derive a group status from its member orders' statuses.

    Computed on read (never stored): terminal-all → completed/rejected;
    any pending approval → pending_approval (or partially_approved if some
    items already moved on); otherwise in_progress.
    """
    if not order_statuses:
        return "empty"
    s = set(order_statuses)
    active = {"pending", "pending_approval", "scheduled", "processing", "provisioning", "revoking"}
    done_ok = {"provisioned", "delivered", "revoked", "expired"}
    dead = {"rejected", "cancelled", "failed"}

    if s <= dead:
        return "rejected" if "rejected" in s else "cancelled"
    if s <= (done_ok | dead):
        return "completed"
    if "pending_approval" in s:
        # Some items already progressed past approval → partial.
        return "partially_approved" if (s & (done_ok | {"scheduled", "processing", "provisioning"})) else "pending_approval"
    return "in_progress"
