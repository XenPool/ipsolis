"""SCIM mover reconciliation config.

Seeds ``scim.mover_mode`` — the safety ramp for mover reconciliation, mirroring
drift's detect_only/auto_remediate:

* ``disabled``        — attribute changes do nothing (default).
* ``additions_only``  — re-evaluate rules and order newly-entitled bundles;
                        never revoke.
* ``reconcile``       — additions **plus** revoke rule-provisioned entitlements
                        the user is no longer entitled to.

Revision ID: 0010
Revises: 0009
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
        "('scim.mover_mode', 'disabled', "
        "'SCIM mover reconciliation on attribute change: disabled | additions_only | reconcile.', "
        "false, NOW(), NOW()) ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM app_config WHERE key = 'scim.mover_mode'"))
