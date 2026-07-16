"""Portal step visibility per asset type.

Adds ``asset_types.portal_step_visibility`` controlling how much of an order's
execution steps the self-service portal shows the end user:
- ``off`` (default) — no step list, just the overall order status;
- ``detailed`` — step names + status + timing (generic failure message);
- ``debug`` — the above plus raw step log_output + error text.

Additive (one non-null column with an ``off`` server_default); no backfill.

Revision ID: 0014
Revises: 0013
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asset_types",
        sa.Column(
            "portal_step_visibility",
            sa.String(length=20),
            nullable=False,
            server_default="off",
        ),
    )


def downgrade() -> None:
    op.drop_column("asset_types", "portal_step_visibility")
