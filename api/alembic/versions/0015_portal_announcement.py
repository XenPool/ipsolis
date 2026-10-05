"""Portal announcement banner config.

Seeds ``portal.announcement`` (free text, empty = no banner) and
``portal.announcement_level`` (info / warning) — an operator-set notice shown
at the top of every portal page (e.g. a maintenance window).

Revision ID: 0015
Revises: 0014
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
        "('portal.announcement', '', "
        "'Notice shown at the top of every portal page. Empty = no banner.', "
        "false, NOW(), NOW()), "
        "('portal.announcement_level', 'info', "
        "'Portal announcement style: info or warning.', "
        "false, NOW(), NOW()) "
        "ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM app_config WHERE key IN ('portal.announcement', 'portal.announcement_level')"
    ))
