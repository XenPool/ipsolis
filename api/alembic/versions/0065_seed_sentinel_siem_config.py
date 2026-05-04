"""Seed Microsoft Sentinel SIEM adapter config keys.

Adds three new keys for the Azure Monitor / Sentinel HTTP Data Collector
path. ``siem.format`` was previously documented as ``splunk_hec`` only;
the description is updated in-place. Existing values on installs that
already saved siem.* config are not touched.

Revision ID: 0065
Revises: 0064
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('siem.workspace_id', '',             'Sentinel: Log Analytics workspace GUID.', false, NOW()),
        ('siem.shared_key',   '',             'Sentinel: workspace shared key, base64-encoded. Used to HMAC-SHA256 sign each batch.', true, NOW()),
        ('siem.log_type',     'IpsolisAudit', 'Sentinel: custom log table name (the _CL suffix is appended automatically).', false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute("""
        UPDATE app_config
        SET description = 'SIEM payload format. One of: splunk_hec, sentinel.'
        WHERE key = 'siem.format'
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config
        WHERE key IN ('siem.workspace_id', 'siem.shared_key', 'siem.log_type')
    """)
