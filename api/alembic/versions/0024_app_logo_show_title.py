"""Seed app.logo_show_title config key

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES (
            'app.logo_show_title',
            'true',
            'Show the application title below the portal logo (true | false)',
            false,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'app.logo_show_title'")
