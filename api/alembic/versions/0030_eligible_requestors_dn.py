"""Add eligible_requestors_dn to asset_types

Revision ID: 0030
Revises: 0029
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_types",
        sa.Column("eligible_requestors_dn", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("asset_types", "eligible_requestors_dn")
