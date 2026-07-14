"""Attestation artifacts: handover + revocation certificates.

Adds:
1. ``asset_types.requires_handover_ack`` + ``emit_revocation_certificate``
   (bool, default false) — the per-asset-type opt-ins.
2. ``attestation_artifacts`` table — one row per emitted handover /
   revocation artifact (signed-token HTML, acknowledged or evidence-only).
3. Seeds the ``attestation.*`` config defaults (AUP text + overdue-reminder).

Revision ID: 0007
Revises: 0006
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels = None
depends_on = None


def _seed(conn, key: str, value: str, description: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) "
            "VALUES (:k, :v, :d, false, NOW(), NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"k": key, "v": value, "d": description},
    )


def upgrade() -> None:
    op.add_column(
        "asset_types",
        sa.Column("requires_handover_ack", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "asset_types",
        sa.Column("emit_revocation_certificate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "attestation_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False),  # handover | revocation
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asset_type_id", sa.Integer(), sa.ForeignKey("asset_types.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=True),
        sa.Column("recipient_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("snapshot", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_attestation_artifacts_status", "attestation_artifacts", ["kind", "status"])
    op.create_index("ix_attestation_artifacts_order", "attestation_artifacts", ["order_id"])

    conn = op.get_bind()
    _seed(conn, "attestation.aup_text", "",
          "Optional Acceptable-Use-Policy text shown on the handover acknowledgment page.")
    _seed(conn, "attestation.handover_reminder_enabled", "false",
          "Master switch for the overdue-handover-acknowledgment reminder Beat task (opt-in).")
    _seed(conn, "attestation.handover_reminder_days", "3",
          "Days after emit before an unacknowledged handover is re-reminded.")


def downgrade() -> None:
    op.drop_index("ix_attestation_artifacts_order", table_name="attestation_artifacts")
    op.drop_index("ix_attestation_artifacts_status", table_name="attestation_artifacts")
    op.drop_table("attestation_artifacts")
    op.drop_column("asset_types", "emit_revocation_certificate")
    op.drop_column("asset_types", "requires_handover_ack")
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM app_config WHERE key IN "
        "('attestation.aup_text', 'attestation.handover_reminder_enabled', "
        "'attestation.handover_reminder_days')"
    ))
