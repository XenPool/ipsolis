"""Seed generic-webhook SIEM adapter config keys.

Adds four keys for the third SIEM format option, alongside Splunk HEC
(0053) and Sentinel (0065). The webhook adapter posts a JSON array of
audit events with an HMAC-SHA256 signature in a GitHub-compatible
``X-Hub-Signature-256: sha256=<hex>`` header by default — receivers
verify by recomputing HMAC over the raw body.

``siem.format`` description is updated to list all three adapters.

Revision ID: 0068
Revises: 0067
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('siem.webhook_url', '',
         'Webhook: HTTPS endpoint that accepts a signed JSON array of audit events.',
         false, NOW()),
        ('siem.webhook_secret', '',
         'Webhook: shared secret for HMAC-SHA256 signing. Receivers verify by recomputing HMAC over the raw body.',
         true, NOW()),
        ('siem.webhook_signature_header', 'X-Hub-Signature-256',
         'Webhook: header name carrying the sha256=<hex> signature. GitHub-compatible default; change to fit your receiver.',
         false, NOW()),
        ('siem.webhook_extra_headers', '',
         'Webhook: optional additional headers as a JSON object (e.g. {"Authorization":"Bearer …","DD-API-KEY":"…"}). The signature header is always written by ipSolis and overrides any extras with the same name.',
         false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute("""
        UPDATE app_config
        SET description = 'SIEM payload format. One of: splunk_hec, sentinel, webhook.'
        WHERE key = 'siem.format'
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'siem.webhook_url',
          'siem.webhook_secret',
          'siem.webhook_signature_header',
          'siem.webhook_extra_headers'
        )
    """)
