"""Add deprovision_policy, personal_provisioning_strategy, naming_pattern, max_per_user

Revision ID: 0008
Revises: 0007
Create Date: 2026-02-25

Changes:
- asset_types: add deprovision_policy (access_only/return_to_pool/deallocate_instance/
  delete_instance/custom_runbook), personal_provisioning_strategy, naming_pattern, max_per_user
- Data migration: smart defaults based on assignment_model
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. asset_types: neue Spalten hinzufügen ───────────────────────────────
    op.add_column("asset_types", sa.Column(
        "deprovision_policy", sa.String(30), nullable=False, server_default="access_only"
    ))
    op.add_column("asset_types", sa.Column(
        "personal_provisioning_strategy", sa.String(30), nullable=True
    ))
    op.add_column("asset_types", sa.Column(
        "naming_pattern", sa.String(100), nullable=True
    ))
    op.add_column("asset_types", sa.Column(
        "max_per_user", sa.Integer(), nullable=False, server_default="1"
    ))

    # ── 2. Datenmigration: sinnvolle Defaults je assignment_model ─────────────
    # capacity_pooled → return_to_pool (keine individuelle Instanz)
    conn.execute(sa.text(
        "UPDATE asset_types SET deprovision_policy = 'return_to_pool'"
        " WHERE assignment_model = 'capacity_pooled'"
    ))
    # dedicated_shared → access_only (Instanz bleibt, nur Zugriff entziehen)
    conn.execute(sa.text(
        "UPDATE asset_types SET deprovision_policy = 'access_only'"
        " WHERE assignment_model = 'dedicated_shared'"
    ))
    # assigned_personal → deallocate_instance (persönliche VM stoppen)
    conn.execute(sa.text(
        "UPDATE asset_types SET deprovision_policy = 'deallocate_instance'"
        " WHERE assignment_model = 'assigned_personal'"
    ))
    # Alle: Standardstrategie für persönliche Zuweisung
    conn.execute(sa.text(
        "UPDATE asset_types SET personal_provisioning_strategy = 'assign_existing_free'"
    ))


def downgrade() -> None:
    op.drop_column("asset_types", "max_per_user")
    op.drop_column("asset_types", "naming_pattern")
    op.drop_column("asset_types", "personal_provisioning_strategy")
    op.drop_column("asset_types", "deprovision_policy")
