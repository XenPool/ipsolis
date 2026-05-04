"""Add is_active flag to asset_types so admins can deprecate without deleting.

When false, the type is hidden from the portal catalog (`/portal/orders/new`)
but stays visible in the admin UI with an "Inactive" badge so historical
orders, audit log, and runbook configurations remain coherent. Existing
rows default to active.

Revision ID: 0049
Revises: 0048
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_types",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("asset_types", "is_active")
