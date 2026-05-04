"""Drop any 'ip_address' key from asset_pool.metadata JSON blobs.

IP address was stored as an informal key in the asset_pool.metadata JSON
column (never a top-level DB column). The UI used to prompt for it on
create/edit/import, but the value wasn't consumed by any runtime workflow
— IP discovery happens on-the-fly via the hypervisor when a runbook needs
it. Removing the field from the schema and scrubbing any residual JSON
keys so exported or restored DBs don't drag stale data forward.

Revision ID: 0048
Revises: 0047
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # asset_pool.metadata is a `json` column (not jsonb). Cast to jsonb for
    # the `- 'key'` operator, then back to json to write.
    op.execute(
        "UPDATE asset_pool "
        "SET metadata = (CAST(metadata AS jsonb) - 'ip_address')::json "
        "WHERE metadata IS NOT NULL "
        "  AND CAST(metadata AS jsonb) ? 'ip_address'"
    )


def downgrade() -> None:
    # Data is gone — can't reconstruct IP addresses we never stored elsewhere.
    pass
