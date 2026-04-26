"""Add help_text (long-form markdown) to asset_types.

Lets admins write a paragraph beyond the one-line description that gets
surfaced to requesters in the portal at request time. Rendered safely
via markdown + bleach allowlist.

Revision ID: 0050
Revises: 0049
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_types",
        sa.Column("help_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("asset_types", "help_text")
