"""Rename asset_status value 'pending_reinstall' -> 'reinstall'

Revision ID: 0034
Revises: 0033
Create Date: 2026-04-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_status RENAME VALUE 'pending_reinstall' TO 'reinstall'")


def downgrade() -> None:
    op.execute("ALTER TYPE asset_status RENAME VALUE 'reinstall' TO 'pending_reinstall'")
