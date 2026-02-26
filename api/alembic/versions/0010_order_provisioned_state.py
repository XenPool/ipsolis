"""0010_order_provisioned_state

Revision ID: 0010
Revises: 0009
Create Date: 2026-02-26

Ergänzungen für deterministisches Provisioning & Revoke:
  - orders.provisioned_state    JSONB  – Snapshot nach Provision
  - order_change_log.idempotency_key   VARCHAR(255) – Duplikat-Schutz für Grants
  - order_change_log.resolved_object_id VARCHAR(255) – Gruppen-ObjectId bei Bedarf
  - order_status Enum: PROVISIONING / PROVISIONED / REVOKING / REVOKED (neue Werte)

Hinweis: ALTER TYPE ... ADD VALUE IF NOT EXISTS benötigt PostgreSQL 12+.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. OrderStatus Enum erweitern ──────────────────────────────────────────
    # ALTER TYPE ADD VALUE kann in PostgreSQL 12+ in einer Transaktion stehen
    # (solange der neue Wert in derselben Transaktion nicht verwendet wird).
    for val in ("provisioning", "provisioned", "revoking", "revoked"):
        op.execute(f"ALTER TYPE order_status ADD VALUE IF NOT EXISTS '{val}'")

    # ── 2. orders.provisioned_state ────────────────────────────────────────────
    op.add_column(
        "orders",
        sa.Column("provisioned_state", JSONB(), nullable=True),
    )

    # ── 3. order_change_log: idempotency_key + resolved_object_id ──────────────
    op.add_column(
        "order_change_log",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "order_change_log",
        sa.Column("resolved_object_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_ocl_idempotency_key",
        "order_change_log",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_ocl_idempotency_key", table_name="order_change_log")
    op.drop_column("order_change_log", "resolved_object_id")
    op.drop_column("order_change_log", "idempotency_key")
    op.drop_column("orders", "provisioned_state")
    # HINWEIS: PostgreSQL unterstützt kein "DROP VALUE" für Enums.
    # Die neuen Status-Werte (provisioning/provisioned/revoking/revoked) bleiben im Typ erhalten.
