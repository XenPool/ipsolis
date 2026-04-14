"""Add rds_gateway_url column to asset_types

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("asset_types", sa.Column("rds_gateway_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("asset_types", "rds_gateway_url")
