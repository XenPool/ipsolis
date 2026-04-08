"""Split allow_user_lists into allow_rdp_users + allow_admin_users

Revision ID: 0021
Revises: 0020
Create Date: 2026-03-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE asset_types
        ADD COLUMN allow_rdp_users   BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN allow_admin_users BOOLEAN NOT NULL DEFAULT FALSE
    """))
    # Migrate existing flag: if allow_user_lists was true, enable both
    op.execute(sa.text("""
        UPDATE asset_types
        SET allow_rdp_users = allow_user_lists,
            allow_admin_users = allow_user_lists
    """))
    op.execute(sa.text("""
        ALTER TABLE asset_types DROP COLUMN allow_user_lists
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE asset_types
        ADD COLUMN allow_user_lists BOOLEAN NOT NULL DEFAULT FALSE
    """))
    op.execute(sa.text("""
        UPDATE asset_types
        SET allow_user_lists = (allow_rdp_users OR allow_admin_users)
    """))
    op.execute(sa.text("""
        ALTER TABLE asset_types
        DROP COLUMN allow_rdp_users,
        DROP COLUMN allow_admin_users
    """))
