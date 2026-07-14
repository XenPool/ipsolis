"""Onboarding first-login trigger config.

Seeds ``onboarding.eval_on_first_login`` — opt-in flag; when on, a user's first
portal login evaluates assignment rules and orders the matched bundles.

Revision ID: 0012
Revises: 0011
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
        "('onboarding.eval_on_first_login', 'false', "
        "'When on, a user''s first portal login evaluates assignment rules and orders matched bundles.', "
        "false, NOW(), NOW()) ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM app_config WHERE key = 'onboarding.eval_on_first_login'"))
