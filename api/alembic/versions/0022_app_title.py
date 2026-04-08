"""Seed app.title config key (configurable application title)

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES (
            'app.title',
            'IT Selfservice',
            'Application title shown in the navigation bar and browser tab',
            false,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'app.title'")
