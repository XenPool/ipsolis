"""Seed hosting infrastructure config keys (vSphere, XenServer)

Revision ID: 0017
Revises: 0016
Create Date: 2026-03-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret) VALUES
        ('vsphere.host',       '', 'vCenter / ESXi hostname or IP',      false),
        ('vsphere.username',   '', 'vSphere admin service account',       false),
        ('vsphere.password',   '', 'vSphere admin password',              true),
        ('xenserver.host',     '', 'XCP-ng / XenServer hostname or IP',  false),
        ('xenserver.username', '', 'XenServer admin service account',     false),
        ('xenserver.password', '', 'XenServer admin password',            true)
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
            'vsphere.host', 'vsphere.username', 'vsphere.password',
            'xenserver.host', 'xenserver.username', 'xenserver.password'
        )
    """)
