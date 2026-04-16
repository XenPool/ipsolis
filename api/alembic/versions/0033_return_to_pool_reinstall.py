"""Add pending_reinstall value to asset_status enum

Revision ID: 0033
Revises: 0032
Create Date: 2026-04-15

deprovision_policy is a varchar, not a PG enum – no DDL needed for the new
'return_to_pool_reinstall' value; only Python/Pydantic validation changes.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    op.execute("COMMIT")
    op.execute("ALTER TYPE asset_status ADD VALUE IF NOT EXISTS 'pending_reinstall'")
    op.execute("BEGIN")


def downgrade() -> None:
    # Postgres does not support removing enum values without recreating the type.
    pass
