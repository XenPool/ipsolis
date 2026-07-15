"""Entra group provisioning via Microsoft Graph — graph.* config.

Seeds the app-only (client-credentials) credentials used by the ``entra_group``
access-target handlers. A dedicated app registration with Application
permissions GroupMember.ReadWrite.All + User.Read.All (admin-consented).

Revision ID: 0011
Revises: 0010
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES "
        "('graph.tenant_id', '', 'Entra tenant id for Microsoft Graph group provisioning.', false, NOW(), NOW()), "
        "('graph.client_id', '', 'App registration (client) id for Graph group provisioning.', false, NOW(), NOW()), "
        "('graph.client_secret', '', 'App registration client secret (Graph provisioning).', true, NOW(), NOW()) "
        "ON CONFLICT (key) DO NOTHING"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM app_config WHERE key IN "
        "('graph.tenant_id', 'graph.client_id', 'graph.client_secret')"
    ))
