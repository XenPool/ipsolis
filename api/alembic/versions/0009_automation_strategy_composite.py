"""Add automation_strategy and composite_steps to asset_types

Revision ID: 0009
Revises: 0008
Create Date: 2026-02-25

Changes:
- asset_types: add automation_strategy (group_only/runbook_only/composite)
- asset_types: add composite_steps JSONB (nullable)
- Data migration: targets_only → group_only, runbook → runbook_only
- automation_mode bleibt als deprecated-Fallback (kein DROP im MVP)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. asset_types: neue Spalten hinzufügen ───────────────────────────────
    op.add_column("asset_types", sa.Column(
        "automation_strategy", sa.String(20), nullable=False, server_default="runbook_only"
    ))
    op.add_column("asset_types", sa.Column(
        "composite_steps", JSONB(), nullable=True
    ))

    # ── 2. Datenmigration: automation_mode → automation_strategy ─────────────
    conn.execute(sa.text(
        "UPDATE asset_types SET automation_strategy = 'group_only'"
        " WHERE automation_mode = 'targets_only'"
    ))
    conn.execute(sa.text(
        "UPDATE asset_types SET automation_strategy = 'runbook_only'"
        " WHERE automation_mode = 'runbook'"
    ))


def downgrade() -> None:
    op.drop_column("asset_types", "composite_steps")
    op.drop_column("asset_types", "automation_strategy")
