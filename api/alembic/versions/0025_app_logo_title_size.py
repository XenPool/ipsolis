"""Seed app.logo_title_size config key

Revision ID: 0025
Revises: 0024
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES (
            'app.logo_title_size',
            '12',
            'Font size (px) of the application title shown below the portal logo (8–24)',
            false,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'app.logo_title_size'")
