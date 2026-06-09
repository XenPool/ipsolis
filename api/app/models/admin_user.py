"""Admin user accounts — RBAC slice 1.

Backs the per-user login flow (Settings → Users page in the Admin UI)
and the role-based dependency in ``app.utils.rbac``. Passwords are
hashed with PBKDF2-SHA256 (600k iterations, OWASP 2023 minimum) by the
``app.utils.password`` helpers so this model only stores the hash.

The legacy ``ADMIN_API_KEY`` continues to authenticate without a row
here — it's mapped to a virtual ``superadmin`` actor. Real users are
required for any non-superadmin role, since the legacy key has no
identity.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Normalised to lowercase at write time so the unique index is reliable.
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    # ── RBAC slice 4: password rotation + lockout-on-N-failed-attempts ───
    # NULL on rows pre-dating slice 4 is treated as "never expires" for
    # back-compat — the migration backfills with ``created_at``.
    password_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AdminUser id={self.id} username={self.username!r} "
            f"role={self.role!r} active={self.is_active}>"
        )
