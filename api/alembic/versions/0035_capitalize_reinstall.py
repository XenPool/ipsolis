"""Rename asset_status value 'reinstall' -> 'Reinstall'

Revision ID: 0035
Revises: 0034
Create Date: 2026-04-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_status RENAME VALUE 'reinstall' TO 'Reinstall'")


def downgrade() -> None:
    op.execute("ALTER TYPE asset_status RENAME VALUE 'Reinstall' TO 'reinstall'")
