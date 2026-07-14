"""Minimal identity projection for SCIM joiner/mover.

ip·Solis has no local user store — portal users are session-only (OIDC/LDAP)
and only become "real" when they place an order. SCIM joiner/mover need a
persistent last-seen projection so an attribute change (**mover**) can be
diffed against the previous state and reconciled. This table is exactly that:
the last SCIM-provided attribute snapshot per user, keyed by email.

It is **not** an authoritative user store — AD/Entra remain the source of
truth. It only records what the upstream IdP last told us via SCIM, so the
provisioning service can decide joiner (new/reactivated) vs mover (changed).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScimIdentity(Base):
    __tablename__ = "scim_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Join key to orders.user_email (lowercased). Unique per user.
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # The IdP's stable external id (SCIM ``externalId``), when supplied.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Last-seen mapped attributes used for rule evaluation + mover diffing:
    # {department, cost_center, title, company, employee_id}.
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Last raw SCIM payload (debugging / audit).
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ScimIdentity id={self.id} email={self.user_email!r} active={self.active}>"
