"""Order justification: requester free-text reason shown to the approver.

Adds an opt-in, optionally-required business-justification field:
- ``orders.justification`` (TEXT, nullable) — the requester's reason.
- ``asset_types.collect_justification`` (bool) — show the field on the order form.
- ``asset_types.justification_required`` (bool) — make it mandatory (only
  meaningful together with collect_justification).

All additive (new nullable column + two boolean columns with a ``false``
server_default); no backfill of existing rows.

Revision ID: 0013
Revises: 0012
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("justification", sa.Text(), nullable=True))
    op.add_column(
        "asset_types",
        sa.Column(
            "collect_justification",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "asset_types",
        sa.Column(
            "justification_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("asset_types", "justification_required")
    op.drop_column("asset_types", "collect_justification")
    op.drop_column("orders", "justification")
