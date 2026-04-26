"""Seed Microsoft Teams notification config keys.

Empty defaults — admin sets the workflow webhook URL via Settings → E-Mail
→ Microsoft Teams. Until ``teams.mode = enabled`` and ``teams.webhook_url``
is non-empty the worker silently skips Teams delivery.

Revision ID: 0051
Revises: 0050
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KEYS = [
    ("teams.mode", "disabled",
     "Microsoft Teams approval notifications: 'disabled' or 'enabled'.", False),
    ("teams.webhook_url", "",
     "Teams Workflows webhook URL (trigger: 'When a webhook request is received').", True),
]


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('teams.mode',        'disabled', 'Microsoft Teams approval notifications: disabled or enabled.', false, NOW()),
        ('teams.webhook_url', '',         'Teams Workflows webhook URL (trigger: When a webhook request is received).', true,  NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key IN ('teams.mode', 'teams.webhook_url')")
