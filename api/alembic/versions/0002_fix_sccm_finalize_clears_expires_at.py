"""Fix SCCM finalize script: clear expires_at when asset returns to Free/Failed.

Updates the script_modules body for
"SCCM - Verify Task Sequence Completion and Finalize Asset" so that
Update-AssetStatus also sets expires_at = NULL. This prevents a previous
order's expiry date from lingering on an asset that is no longer assigned.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCRIPT_NAME = "SCCM - Verify Task Sequence Completion and Finalize Asset"

_OLD_SQL = "UPDATE asset_pool SET status = %s::asset_status, updated_at = NOW() WHERE name = %s"
_NEW_SQL = "UPDATE asset_pool SET status = %s::asset_status, expires_at = NULL, updated_at = NOW() WHERE name = %s"


def upgrade() -> None:
    op.execute(f"""
        UPDATE script_modules
        SET    script_content = REPLACE(script_content, '{_OLD_SQL}', '{_NEW_SQL}'),
               updated_at     = NOW()
        WHERE  name = '{_SCRIPT_NAME}'
          AND  script_content LIKE '%{_OLD_SQL}%'
    """)


def downgrade() -> None:
    op.execute(f"""
        UPDATE script_modules
        SET    script_content = REPLACE(script_content, '{_NEW_SQL}', '{_OLD_SQL}'),
               updated_at     = NOW()
        WHERE  name = '{_SCRIPT_NAME}'
          AND  script_content LIKE '%{_NEW_SQL}%'
    """)
