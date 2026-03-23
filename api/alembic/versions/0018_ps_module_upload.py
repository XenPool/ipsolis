"""Add source_type and upload_data to ps_modules

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ps_modules ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'gallery'"
    )
    op.execute(
        "ALTER TABLE ps_modules ADD COLUMN upload_data BYTEA"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ps_modules DROP COLUMN upload_data")
    op.execute("ALTER TABLE ps_modules DROP COLUMN source_type")
