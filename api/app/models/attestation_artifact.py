"""ORM for attestation_artifacts — signed handover + revocation evidence.

Two ISO-27001-relevant artifacts sharing one mechanism (the HMAC signed-token
URL, like the certification review link):

* **handover** (Übergabeprotokoll) — created when an order reaches
  ``provisioned`` for asset types with ``requires_handover_ack``. The recipient
  gets a signed link, sees the asset + config snapshot + optional AUP, and
  acknowledges (``pending`` → ``acknowledged``). Nothing is blocked.
* **revocation** — created when an order is ``revoked`` / ``expired`` for asset
  types with ``emit_revocation_certificate``. A signed HTML attestation of what
  was removed (from the order change log), emitted immediately (no ack needed);
  archival is via browser print. Status stays ``emitted``.

Not a PDF — signed HTML only (there is no PDF library in the repo). ``snapshot``
freezes the human-readable facts at emit time so the page renders identically
even after the order / asset type later changes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

KIND_HANDOVER = "handover"
KIND_REVOCATION = "revocation"


class AttestationArtifact(Base):
    __tablename__ = "attestation_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # handover | revocation
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    asset_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("asset_types.id", ondelete="SET NULL"), nullable=True
    )
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # handover: pending | acknowledged   ·   revocation: emitted
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    # Frozen human-readable facts (asset name, config, dates, removed grants, AUP).
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Dedup guard for the overdue-handover reminder Beat task.
    last_reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AttestationArtifact id={self.id} kind={self.kind} status={self.status}>"
