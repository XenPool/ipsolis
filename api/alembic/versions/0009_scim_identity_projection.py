"""SCIM identity projection (joiner/mover foundation).

Adds ``scim_identities`` — the last-seen SCIM attribute snapshot per user, so
the provisioning service can distinguish joiner (new/reactivated) from mover
(changed attributes) and diff for reconciliation. Not an authoritative user
store (AD/Entra remain source of truth). Seeds ``scim.joiner_enabled`` (opt-in).

Revision ID: 0009
Revises: 0008
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_identities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(255), nullable=False, unique=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("attributes", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("raw", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_scim_identities_email", "scim_identities", ["user_email"], unique=True)
    op.create_index("ix_scim_identities_external", "scim_identities", ["external_id"])

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
        "('scim.joiner_enabled', 'false', "
        "'When on, a SCIM Create (joiner) evaluates assignment rules and orders matched bundles.', "
        "false, NOW(), NOW()) ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    op.drop_index("ix_scim_identities_external", table_name="scim_identities")
    op.drop_index("ix_scim_identities_email", table_name="scim_identities")
    op.drop_table("scim_identities")
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM app_config WHERE key = 'scim.joiner_enabled'"))
