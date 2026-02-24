"""Add owner_email, owner_name, snow_req to orders

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("owner_email", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("owner_name", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("snow_req", sa.String(50), nullable=True))
    op.create_index("ix_orders_snow_req", "orders", ["snow_req"])


def downgrade() -> None:
    op.drop_index("ix_orders_snow_req", "orders")
    op.drop_column("orders", "snow_req")
    op.drop_column("orders", "owner_name")
    op.drop_column("orders", "owner_email")
