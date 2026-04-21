"""Maintenance: backup scheduler + health alert config keys.

Adds app_config entries for:
  - Scheduled backups (cron expression + enabled flag)
  - Health probe email alerts (recipient, enabled flag, cooldown, state snapshot)

Revision ID: 0042
Revises: 0041
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret) VALUES
        ('backup.enabled',                 'false',      'Enable scheduled database backups via Celery Beat.', false),
        ('backup.schedule_cron',           '0 2 * * *',  'Cron expression (UTC) for scheduled backups. Default: daily 02:00.', false),
        ('health.alert_enabled',           'false',      'Send an email when a health probe flips to FAILED (or back to OK).', false),
        ('health.alert_email',             '',           'Recipient email address for Maintenance health alerts.', false),
        ('health.alert_cooldown_minutes',  '60',         'Suppress repeat failure alerts for this many minutes per service.', false),
        ('health.last_state',              '{}',         'Internal: JSON snapshot of last health probe result per service (maintained by Beat).', false)
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
            'backup.enabled',
            'backup.schedule_cron',
            'health.alert_enabled',
            'health.alert_email',
            'health.alert_cooldown_minutes',
            'health.last_state'
        )
    """)
