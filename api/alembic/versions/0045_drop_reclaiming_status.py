"""Drop 'reclaiming' value from asset_status enum.

The 'reclaiming' status was declared in the initial schema but no workflow
ever transitioned an asset into it — releases go directly busy -> Free.
Operators who had set assets to 'reclaiming' manually are migrated to
'Failed' so the dashboard surfaces them for manual attention.

PostgreSQL doesn't support ALTER TYPE ... DROP VALUE, so we create a new
enum, migrate the column through a text cast, and swap.

Revision ID: 0045
Revises: 0044
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Remap any residual 'reclaiming' rows to 'Failed'.
    op.execute("UPDATE asset_pool SET status = 'Failed' WHERE status = 'reclaiming'")

    # 2. Create replacement enum without 'reclaiming'.
    op.execute(
        "CREATE TYPE asset_status_new AS ENUM "
        "('Free', 'reserved', 'busy', 'maintenance', "
        "'Reinstall', 'Reinstalling', 'Failed')"
    )

    # 3. Drop the default so ALTER COLUMN TYPE doesn't choke on the old-type cast.
    op.execute("ALTER TABLE asset_pool ALTER COLUMN status DROP DEFAULT")

    # 4. Swap the column type.
    op.execute(
        "ALTER TABLE asset_pool "
        "ALTER COLUMN status TYPE asset_status_new "
        "USING status::text::asset_status_new"
    )

    # 5. Drop the old type and rename the replacement.
    op.execute("DROP TYPE asset_status")
    op.execute("ALTER TYPE asset_status_new RENAME TO asset_status")

    # 6. Restore the default.
    op.execute("ALTER TABLE asset_pool ALTER COLUMN status SET DEFAULT 'Free'::asset_status")


def downgrade() -> None:
    op.execute("ALTER TYPE asset_status ADD VALUE IF NOT EXISTS 'reclaiming'")
