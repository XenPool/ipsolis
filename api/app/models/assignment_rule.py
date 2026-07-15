"""Attribute-based assignment rules — condition on user attributes → bundle.

Reuses the existing conditional-approval-rule **condition format** (the same
AND/OR/NOT tree over attribute fields that ``AssetType.approval_rules`` uses)
rather than inventing a new rule syntax. Because ip·Solis has no local user
store, evaluation is a pure function over a user-attribute dict (AD-resolved at
trigger time), not a hook on a user entity. A matching rule targets a
``Bundle`` which is then ordered as one ``OrderGroup``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssignmentRule(Base):
    __tablename__ = "assignment_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Condition tree in the same JSON shape as AssetType.approval_rules[*].condition.
    condition: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    bundle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Lower runs first; all matching rules contribute their bundle (deduped).
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    bundle: Mapped["Bundle"] = relationship("Bundle")  # noqa: F821

    def __repr__(self) -> str:
        return f"<AssignmentRule id={self.id} name={self.name!r} bundle={self.bundle_id}>"
