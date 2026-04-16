"""Add logo column to asset_types

Revision ID: 0036
Revises: 0035
Create Date: 2026-04-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("asset_types", sa.Column("logo", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("asset_types", "logo")
