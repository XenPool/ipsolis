"""Junction table — admin user ↔ asset type ACL grants (RBAC slice 2)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdminUserAssetTypeGrant(Base):
    """A grant that scopes an admin user to a specific asset type.

    Visibility semantics live in ``app.utils.rbac_grants.visible_asset_type_ids``:
    a user with zero grants is treated as "see all" (back-compat for
    single-team installs); attaching even one grant flips the user
    into "see only granted types" mode.
    """

    __tablename__ = "admin_user_asset_type_grants"

    admin_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_users.id", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
    asset_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("asset_types.id", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AdminUserAssetTypeGrant user={self.admin_user_id} "
            f"type={self.asset_type_id}>"
        )
