"""Drop dedicated_shared assignment model.

Deletes all asset types (and their FK dependents) that use
assignment_model = 'dedicated_shared'. This is test/pre-live data only —
no migration to another model is needed.

Revision ID: 0095
Revises: 0094
Create Date: 2026-05-05
"""
from alembic import op
from sqlalchemy import text

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id FROM asset_types WHERE assignment_model = 'dedicated_shared'")
    ).fetchall()
    type_ids = [r[0] for r in rows]

    if not type_ids:
        return

    ids_sql = ", ".join(str(i) for i in type_ids)

    # Asset-type-bound runbooks
    conn.execute(text(f"DELETE FROM runbook_steps WHERE runbook_id IN (SELECT id FROM runbook_definitions WHERE asset_type_id IN ({ids_sql}))"))
    conn.execute(text(f"DELETE FROM runbook_definitions WHERE asset_type_id IN ({ids_sql})"))

    # Orders and their dependents
    conn.execute(text(f"DELETE FROM order_steps WHERE order_id IN (SELECT id FROM orders WHERE asset_type_id IN ({ids_sql}))"))
    conn.execute(text(f"DELETE FROM order_approvals WHERE order_id IN (SELECT id FROM orders WHERE asset_type_id IN ({ids_sql}))"))
    conn.execute(text(f"DELETE FROM order_change_log WHERE order_id IN (SELECT id FROM orders WHERE asset_type_id IN ({ids_sql}))"))
    conn.execute(text(f"UPDATE asset_pool SET current_order_id = NULL WHERE asset_type_id IN ({ids_sql})"))
    conn.execute(text(f"DELETE FROM orders WHERE asset_type_id IN ({ids_sql})"))

    # Pool entries and the asset types themselves
    conn.execute(text(f"DELETE FROM asset_pool WHERE asset_type_id IN ({ids_sql})"))
    conn.execute(text(f"DELETE FROM asset_types WHERE id IN ({ids_sql})"))


def downgrade() -> None:
    pass  # data deletion is not reversible
