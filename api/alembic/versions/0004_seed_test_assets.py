"""Seed asset_types and asset_pool with test data

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-24
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ASSET_TYPES = [
    # (name, description, category, config)
    (
        "Test VDI",
        "Leichtgewichtige VDI für Testzwecke und kurzfristige Projekte",
        "vdi",
        {"cpu": 2, "ram_gb": 4, "disk_gb": 60, "os": "Windows 10 22H2"},
    ),
    (
        "Business VDI",
        "Standard-VDI für produktiven Einsatz",
        "vdi",
        {"cpu": 4, "ram_gb": 8, "disk_gb": 100, "os": "Windows 11 23H2"},
    ),
]

# (name, type_name, status, metadata)
_ASSETS = [
    # ── Test VDIs ──────────────────────────────────────────────────────────────
    ("XP-TVDI-001", "Test VDI", "free",        {"hostname": "xp-tvdi-001", "ip": "10.10.1.1", "vsphere_vm_id": "vm-1001"}),
    ("XP-TVDI-002", "Test VDI", "free",        {"hostname": "xp-tvdi-002", "ip": "10.10.1.2", "vsphere_vm_id": "vm-1002"}),
    ("XP-TVDI-003", "Test VDI", "free",        {"hostname": "xp-tvdi-003", "ip": "10.10.1.3", "vsphere_vm_id": "vm-1003"}),
    ("XP-TVDI-004", "Test VDI", "busy",        {"hostname": "xp-tvdi-004", "ip": "10.10.1.4", "vsphere_vm_id": "vm-1004"}),
    ("XP-TVDI-005", "Test VDI", "busy",        {"hostname": "xp-tvdi-005", "ip": "10.10.1.5", "vsphere_vm_id": "vm-1005"}),
    ("XP-TVDI-006", "Test VDI", "maintenance", {"hostname": "xp-tvdi-006", "ip": "10.10.1.6", "vsphere_vm_id": "vm-1006"}),
    # ── Business VDIs ──────────────────────────────────────────────────────────
    ("XP-BVDI-001", "Business VDI", "free",        {"hostname": "xp-bvdi-001", "ip": "10.10.2.1", "vsphere_vm_id": "vm-2001"}),
    ("XP-BVDI-002", "Business VDI", "free",        {"hostname": "xp-bvdi-002", "ip": "10.10.2.2", "vsphere_vm_id": "vm-2002"}),
    ("XP-BVDI-003", "Business VDI", "busy",        {"hostname": "xp-bvdi-003", "ip": "10.10.2.3", "vsphere_vm_id": "vm-2003"}),
    ("XP-BVDI-004", "Business VDI", "busy",        {"hostname": "xp-bvdi-004", "ip": "10.10.2.4", "vsphere_vm_id": "vm-2004"}),
    ("XP-BVDI-005", "Business VDI", "reclaiming",  {"hostname": "xp-bvdi-005", "ip": "10.10.2.5", "vsphere_vm_id": "vm-2005"}),
    ("XP-BVDI-006", "Business VDI", "maintenance", {"hostname": "xp-bvdi-006", "ip": "10.10.2.6", "vsphere_vm_id": "vm-2006"}),
]


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Asset Types anlegen
    for name, description, category, config in _ASSET_TYPES:
        conn.execute(
            sa.text("""
                INSERT INTO asset_types (name, description, category, config, created_at, updated_at)
                VALUES (:name, :description, CAST(:category AS asset_category), CAST(:config AS jsonb), NOW(), NOW())
                ON CONFLICT (name) DO NOTHING
            """),
            {"name": name, "description": description, "category": category, "config": json.dumps(config)},
        )

    # Type-IDs abfragen (ON CONFLICT DO NOTHING → kein RETURNING nutzbar)
    type_id_rows = conn.execute(
        sa.text("SELECT id, name FROM asset_types WHERE name = ANY(:names)"),
        {"names": [t[0] for t in _ASSET_TYPES]},
    ).fetchall()
    type_ids = {row[1]: row[0] for row in type_id_rows}

    # 2. Assets anlegen
    for asset_name, type_name, status, metadata in _ASSETS:
        asset_type_id = type_ids.get(type_name)
        if asset_type_id is None:
            continue
        conn.execute(
            sa.text("""
                INSERT INTO asset_pool (name, asset_type_id, status, metadata, created_at, updated_at)
                VALUES (:name, :asset_type_id, CAST(:status AS asset_status), CAST(:metadata AS jsonb), NOW(), NOW())
                ON CONFLICT (name) DO NOTHING
            """),
            {
                "name": asset_name,
                "asset_type_id": asset_type_id,
                "status": status,
                "metadata": json.dumps(metadata),
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM asset_pool WHERE name = ANY(:names)"),
        {"names": [a[0] for a in _ASSETS]},
    )
    conn.execute(
        sa.text("DELETE FROM asset_types WHERE name = ANY(:names)"),
        {"names": [t[0] for t in _ASSET_TYPES]},
    )
