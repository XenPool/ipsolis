"""Add assignment_model, targets, automation_mode, lifecycle; add order_change_log

Revision ID: 0007
Revises: 0006
Create Date: 2026-02-25

Changes:
- asset_types: add assignment_model (replaces asset_model), targets, automation_mode,
  lifecycle_ttl_days, lifecycle_renewable; drop asset_model
- new table order_change_log (deterministic revoke state)
- data migration: named → assigned_personal, pooled → capacity_pooled
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. asset_types: neue Spalten hinzufügen ───────────────────────────────
    op.add_column("asset_types", sa.Column(
        "assignment_model", sa.String(30), nullable=False, server_default="assigned_personal"
    ))
    op.add_column("asset_types", sa.Column("targets", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("asset_types", sa.Column(
        "automation_mode", sa.String(20), nullable=False, server_default="runbook"
    ))
    op.add_column("asset_types", sa.Column("lifecycle_ttl_days", sa.Integer(), nullable=True))
    op.add_column("asset_types", sa.Column(
        "lifecycle_renewable", sa.Boolean(), nullable=False, server_default="true"
    ))

    # ── 2. asset_model → assignment_model datenmigration ─────────────────────
    conn.execute(sa.text(
        "UPDATE asset_types SET assignment_model = 'capacity_pooled' WHERE asset_model = 'pooled'"
    ))
    conn.execute(sa.text(
        "UPDATE asset_types SET assignment_model = 'assigned_personal' WHERE asset_model = 'named'"
    ))
    # Fallback für unbekannte Werte
    conn.execute(sa.text(
        "UPDATE asset_types SET assignment_model = 'assigned_personal'"
        " WHERE assignment_model = 'assigned_personal' AND asset_model NOT IN ('named', 'pooled')"
    ))

    # ── 3. asset_model Spalte entfernen ───────────────────────────────────────
    op.drop_column("asset_types", "asset_model")

    # ── 4. order_change_log Tabelle erstellen ─────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE order_change_log (
            id            SERIAL PRIMARY KEY,
            order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            target_type   VARCHAR(50) NOT NULL,
            identifier    TEXT NOT NULL,
            action        VARCHAR(20) NOT NULL,
            principal     VARCHAR(255) NOT NULL,
            state         VARCHAR(20) NOT NULL DEFAULT 'success',
            executed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata      JSONB
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX ix_order_change_log_order_id ON order_change_log(order_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # ── order_change_log entfernen ────────────────────────────────────────────
    op.drop_table("order_change_log")

    # ── asset_model Spalte wiederherstellen ───────────────────────────────────
    op.add_column("asset_types", sa.Column(
        "asset_model", sa.String(20), nullable=False, server_default="named"
    ))
    conn.execute(sa.text(
        "UPDATE asset_types SET asset_model = 'pooled' WHERE assignment_model = 'capacity_pooled'"
    ))
    conn.execute(sa.text(
        "UPDATE asset_types SET asset_model = 'named'"
        " WHERE assignment_model IN ('assigned_personal', 'dedicated_shared')"
    ))

    # ── neue Spalten entfernen ────────────────────────────────────────────────
    op.drop_column("asset_types", "lifecycle_renewable")
    op.drop_column("asset_types", "lifecycle_ttl_days")
    op.drop_column("asset_types", "automation_mode")
    op.drop_column("asset_types", "targets")
    op.drop_column("asset_types", "assignment_model")
