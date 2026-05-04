"""Per-asset-type ACL grants for admin users — RBAC slice 2.

Junction table that scopes an ``admin`` user to a subset of asset
types. The visibility model:

* ``superadmin`` always sees everything (back-compat + escape hatch).
* ``admin`` with **no** rows here sees everything (back-compat — single-
  team installs aren't surprised by the new feature).
* ``admin`` with **at least one** row here sees only the granted
  types — opt-in scoping for multi-team enterprises.
* ``approver``, ``auditor``, ``helpdesk`` see all (slice 2 doesn't
  scope read-only roles; their privileges are bounded by role gates
  alone).

The grant is keyed by ``(admin_user_id, asset_type_id)`` and cascades
on parent delete so dropping a user or asset type cleans the
junction automatically.

Revision ID: 0070
Revises: 0069
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: Union[str, None] = "0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_user_asset_type_grants",
        sa.Column(
            "admin_user_id", sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            primary_key=True, nullable=False,
        ),
        sa.Column(
            "asset_type_id", sa.Integer(),
            sa.ForeignKey("asset_types.id", ondelete="CASCADE"),
            primary_key=True, nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
    )
    # Reverse-lookup index so listing "which users see this type" is fast
    # — needed for the slice-2 admin-users UI grant editor and any
    # future SoD enforcement that walks back from a type to its
    # configurers.
    op.create_index(
        "ix_admin_user_asset_type_grants_type",
        "admin_user_asset_type_grants",
        ["asset_type_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_user_asset_type_grants_type",
        table_name="admin_user_asset_type_grants",
    )
    op.drop_table("admin_user_asset_type_grants")
