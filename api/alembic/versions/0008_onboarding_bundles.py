"""Onboarding bundles + assignment rules + lightweight order groups.

Adds:
1. ``bundles`` + ``bundle_positions`` — a named set of AssetType positions.
2. ``assignment_rules`` — user-attribute condition (approval-rule format) → bundle.
3. ``order_groups`` — a lightweight *optional* multi-item header (bundles / future
   cart). NOT the full Order-model inversion — see the TASKS.md descope note.
4. ``orders.order_group_id`` — **nullable** FK; single orders stay NULL / untouched.

Revision ID: 0008
Revises: 0007
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bundles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("catalog_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_table(
        "bundle_positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bundle_id", sa.Integer(), sa.ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_type_id", sa.Integer(), sa.ForeignKey("asset_types.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("default_config", sa.dialects.postgresql.JSON(), nullable=True),
    )
    op.create_index("ix_bundle_positions_bundle", "bundle_positions", ["bundle_id"])

    op.create_table(
        "assignment_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("condition", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("bundle_id", sa.Integer(), sa.ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_assignment_rules_bundle", "assignment_rules", ["bundle_id"])

    op.create_table(
        "order_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("origin", sa.String(30), nullable=False, server_default="portal"),
        sa.Column("requester_email", sa.String(255), nullable=True),
        sa.Column("requester_name", sa.String(255), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=True),
        sa.Column("recipient_name", sa.String(255), nullable=True),
        sa.Column("bundle_id", sa.Integer(), sa.ForeignKey("bundles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bundle_name", sa.String(150), nullable=True),
        sa.Column("snapshot", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.add_column("orders", sa.Column("order_group_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_orders_order_group_id", "orders",
        "order_groups", ["order_group_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_orders_order_group_id", "orders", ["order_group_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_order_group_id", table_name="orders")
    op.drop_constraint("fk_orders_order_group_id", "orders", type_="foreignkey")
    op.drop_column("orders", "order_group_id")
    op.drop_table("order_groups")
    op.drop_index("ix_assignment_rules_bundle", table_name="assignment_rules")
    op.drop_table("assignment_rules")
    op.drop_index("ix_bundle_positions_bundle", table_name="bundle_positions")
    op.drop_table("bundle_positions")
    op.drop_table("bundles")
