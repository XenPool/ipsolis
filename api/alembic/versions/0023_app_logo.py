"""Seed app.logo, app.logo_position and app.logo_size config keys

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES
          ('app.logo',          '',     'Portal logo stored as base64 data URL (SVG/PNG/JPG, max 1 MB)', false, NOW(), NOW()),
          ('app.logo_position', 'left', 'Logo alignment in the portal sidebar: left | center | right',  false, NOW(), NOW()),
          ('app.logo_size',     '80',   'Logo width as a percentage of the sidebar width (20–100)',     false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config
        WHERE key IN ('app.logo', 'app.logo_position', 'app.logo_size')
    """)
