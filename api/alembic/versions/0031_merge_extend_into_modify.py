"""Merge extend runbooks and orders into modify

Revision ID: 0031
Revises: 0030
Create Date: 2026-04-15

Consolidates the deprecated 'extend' runbook action into 'modify'. The Postgres
enum value 'extend' is intentionally retained to avoid a full enum rebuild.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text as sa_text

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. For each asset_type with an extend runbook, merge into the modify runbook.
    extend_rows = conn.execute(
        sa_text(
            "SELECT id, asset_type_id FROM runbook_definitions "
            "WHERE action = CAST('extend' AS order_action)"
        )
    ).fetchall()

    for extend_id, asset_type_id in extend_rows:
        modify_row = conn.execute(
            sa_text(
                "SELECT id FROM runbook_definitions "
                "WHERE asset_type_id = :at AND action = CAST('modify' AS order_action) "
                "LIMIT 1"
            ),
            {"at": asset_type_id},
        ).fetchone()

        if modify_row:
            modify_id = modify_row[0]
            # Append extend steps at the end of the modify runbook's ordering.
            max_pos = conn.execute(
                sa_text(
                    "SELECT COALESCE(MAX(position), 0) FROM runbook_steps WHERE runbook_id = :rid"
                ),
                {"rid": modify_id},
            ).scalar()
            conn.execute(
                sa_text(
                    "UPDATE runbook_steps "
                    "SET runbook_id = :modify_id, position = position + :offset "
                    "WHERE runbook_id = :extend_id"
                ),
                {"modify_id": modify_id, "offset": max_pos, "extend_id": extend_id},
            )
            conn.execute(
                sa_text("DELETE FROM runbook_definitions WHERE id = :rid"),
                {"rid": extend_id},
            )
        else:
            # No modify runbook yet — rename the extend row in place.
            conn.execute(
                sa_text(
                    "UPDATE runbook_definitions "
                    "SET action = CAST('modify' AS order_action) "
                    "WHERE id = :rid"
                ),
                {"rid": extend_id},
            )

    # 2. Rewrite any legacy order rows still tagged as extend.
    conn.execute(
        sa_text(
            "UPDATE orders SET action = CAST('modify' AS order_action) "
            "WHERE action = CAST('extend' AS order_action)"
        )
    )


def downgrade() -> None:
    # One-way merge: cannot reliably reconstruct the original extend/modify split.
    pass
