"""Seed SIEM (audit-log streaming) config keys.

Defaults to disabled. Operators set ``siem.endpoint_url`` + ``siem.token``,
flip ``siem.enabled`` to ``true``, and the worker's Beat task starts
forwarding new audit_log rows to the configured Splunk HEC endpoint.

Revision ID: 0053
Revises: 0052
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('siem.enabled',      'false',       'SIEM audit-log streaming master switch (true/false).', false, NOW()),
        ('siem.format',       'splunk_hec',  'SIEM payload format. Currently only splunk_hec is supported.', false, NOW()),
        ('siem.endpoint_url', '',            'SIEM ingestion endpoint, e.g. https://splunk:8088/services/collector/event', false, NOW()),
        ('siem.token',        '',            'Splunk HEC token (sent as Authorization: Splunk <token>).', true,  NOW()),
        ('siem.batch_size',   '200',         'Maximum audit_log rows forwarded per Beat tick.', false, NOW()),
        ('siem.verify_tls',   'true',        'Verify SIEM endpoint TLS certificate. Set to false only for self-signed labs.', false, NOW()),
        ('siem.last_id',      '0',           'Auto-managed cursor — last audit_log id successfully forwarded.', false, NOW()),
        ('siem.last_error',   '',            'Auto-managed — most recent streaming failure (empty on success).', false, NOW()),
        ('siem.last_success_at', '',         'Auto-managed — ISO timestamp of the last successful batch.', false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'siem.enabled', 'siem.format', 'siem.endpoint_url', 'siem.token',
          'siem.batch_size', 'siem.verify_tls', 'siem.last_id',
          'siem.last_error', 'siem.last_success_at'
        )
    """)
