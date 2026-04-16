"""Add lifecycle_reminder_days to asset_types and expiry_reminder_sent_at to orders

Revision ID: 0032
Revises: 0031
Create Date: 2026-04-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_types",
        sa.Column("lifecycle_reminder_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("expiry_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "expiry_reminder_sent_at")
    op.drop_column("asset_types", "lifecycle_reminder_days")
