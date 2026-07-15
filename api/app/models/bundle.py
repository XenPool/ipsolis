"""Onboarding bundles — a named set of asset-type positions ordered as a unit.

A ``Bundle`` defines **no new assets**; each ``BundlePosition`` references an
existing ``AssetType`` (the single source of truth) with a required/optional
flag and an optional default attribute pre-fill for ``Order.config``. Ordering
a bundle produces one ``OrderGroup`` with one ``Order`` line item per position,
through the existing order/approval/execution paths (no quantity — one item per
unit). Bundles are the target that assignment rules and (later) SCIM joiner
events trigger, and are also directly orderable from the self-service catalog.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Bundle(Base):
    __tablename__ = "bundles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # When true, the bundle is offered in the self-service catalog as an
    # "order package". Rule-based / admin triggers work regardless.
    catalog_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    positions: Mapped[list["BundlePosition"]] = relationship(
        "BundlePosition",
        back_populates="bundle",
        cascade="all, delete-orphan",
        order_by="BundlePosition.sort_order",
    )

    def __repr__(self) -> str:
        return f"<Bundle id={self.id} name={self.name!r}>"


class BundlePosition(Base):
    __tablename__ = "bundle_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bundle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("asset_types.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Optional positions can be struck by the approver / deselected in the
    # catalog; required positions are always ordered.
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Pre-fill for Order.config drawn from AssetType.config (attribute selection).
    default_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    bundle: Mapped["Bundle"] = relationship("Bundle", back_populates="positions")

    def __repr__(self) -> str:
        return f"<BundlePosition id={self.id} bundle={self.bundle_id} asset_type={self.asset_type_id}>"
