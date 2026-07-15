"""Slack approval delivery: seed slack.* config defaults.

Mirrors the ``teams.*`` config pair (seeded in the initial schema): a mode
switch and an is_secret webhook URL. Delivery logic lives in the worker
(``deliver_approval_notification`` gained a Slack branch); this migration only
makes the two config keys exist so the Settings UI renders them and the
webhook URL is stored with ``is_secret = true`` (secret-store-reference aware).

Revision ID: 0006
Revises: 0005
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
        "('slack.mode', 'disabled', 'Slack approval notifications: disabled or enabled.', false, NOW(), NOW()), "
        "('slack.webhook_url', '', 'Slack incoming-webhook URL.', true, NOW(), NOW()) "
        "ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM app_config WHERE key IN ('slack.mode', 'slack.webhook_url')"
    ))
