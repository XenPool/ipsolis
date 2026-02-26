"""Add runbook_definitions, runbook_steps tables; extend asset_types

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-24
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. asset_types erweitern
    op.add_column(
        "asset_types",
        sa.Column("asset_model", sa.String(20), nullable=False, server_default="named"),
    )
    op.add_column(
        "asset_types",
        sa.Column("pool_capacity", sa.Integer(), nullable=True),
    )

    # 2. runbook_definitions  (use raw SQL to avoid re-creating the existing order_action enum)
    op.execute("""
        CREATE TABLE runbook_definitions (
            id            SERIAL PRIMARY KEY,
            name          VARCHAR(255) NOT NULL,
            description   TEXT,
            asset_type_id INTEGER NOT NULL
                              REFERENCES asset_types(id) ON DELETE CASCADE,
            action        order_action NOT NULL,
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_runbook_asset_action UNIQUE (asset_type_id, action)
        )
    """)

    # 3. runbook_steps
    op.execute("""
        CREATE TABLE runbook_steps (
            id              SERIAL PRIMARY KEY,
            runbook_id      INTEGER NOT NULL
                                REFERENCES runbook_definitions(id) ON DELETE CASCADE,
            position        INTEGER NOT NULL,
            step_name       VARCHAR(255) NOT NULL,
            module_key      VARCHAR(255) NOT NULL,
            params_template JSONB,
            is_critical     BOOLEAN NOT NULL DEFAULT TRUE,
            retry_count     INTEGER NOT NULL DEFAULT 3,
            timeout_seconds INTEGER NOT NULL DEFAULT 120,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_runbook_step_position UNIQUE (runbook_id, position)
        )
    """)

    # 4. Seed: VDI Runbooks für Test VDI und Business VDI
    conn = op.get_bind()

    rows = conn.execute(sa.text("SELECT id, name FROM asset_types")).fetchall()
    type_ids = {row[1]: row[0] for row in rows}

    # Params-Templates (shared)
    _notif_confirm = {
        "user_email": "{{user_email}}",
        "user_name": "{{user_name}}",
        "owner_email": "{{owner_email}}",
        "owner_name": "{{owner_name}}",
        "asset_type_name": "{{asset_type_name}}",
        "asset_type_description": "{{asset_type_description}}",
        "requested_from": "{{requested_from}}",
        "expires_at": "{{expires_at}}",
        "snow_req": "{{snow_req}}",
        "snow_ritm": "{{snow_ritm}}",
    }
    _notif_provision = {
        "user_email": "{{user_email}}",
        "user_name": "{{user_name}}",
        "asset_name": "{{asset_name}}",
        "rdp_users": "{{rdp_users}}",
        "expires_at": "{{expires_at}}",
    }
    _notif_reclaim = {
        "user_email": "{{user_email}}",
        "user_name": "{{user_name}}",
        "asset_name": "{{asset_name}}",
    }

    _provision_steps = [
        (1, "Bestellbestätigung senden",    "notifications.send_confirmation",           _notif_confirm,  False),
        (2, "VM aus Pool reservieren",       "pool.reserve_asset",                        {"order_id": "{{order_id}}", "asset_type_id": "{{asset_type_id}}", "expires_at": "{{expires_at}}"}, True),
        (3, "RDP-Gruppe konfigurieren",      "active_roles.set_rdp_group",                {"asset_name": "{{asset_name}}", "rdp_users": "{{rdp_users}}"}, True),
        (4, "Admin-Gruppe konfigurieren",    "active_roles.set_admin_group",              {"asset_name": "{{asset_name}}", "admin_users": "{{admin_users}}"}, True),
        (5, "VMware Tools aktualisieren",    "vsphere.update_vmware_tools",               {"asset_name": "{{asset_name}}"}, True),
        (6, "VM rebooten",                   "vsphere.restart_vm",                        {"asset_name": "{{asset_name}}"}, True),
        (7, "Bereitstellungsmail senden",    "notifications.send_provision_confirmation", _notif_provision, False),
        (8, "Asset auf BUSY setzen",         "pool.set_asset_busy",                       {"asset_id": "{{asset_id}}", "order_id": "{{order_id}}", "expires_at": "{{expires_at}}"}, True),
    ]
    _modify_steps = [
        (1, "RDP-Gruppe aktualisieren",    "active_roles.set_rdp_group",                {"asset_name": "{{asset_name}}", "rdp_users": "{{rdp_users}}"}, True),
        (2, "Admin-Gruppe aktualisieren",  "active_roles.set_admin_group",              {"asset_name": "{{asset_name}}", "admin_users": "{{admin_users}}"}, True),
        (3, "Änderungsmail senden",        "notifications.send_provision_confirmation", _notif_provision, False),
    ]
    _extend_steps = [
        (1, "Ablaufzeit verlängern",       "pool.set_asset_busy",                       {"asset_id": "{{asset_id}}", "order_id": "{{order_id}}", "expires_at": "{{expires_at}}"}, True),
        (2, "Verlängerungsmail senden",    "notifications.send_provision_confirmation", _notif_provision, False),
    ]
    _delete_steps = [
        (1, "AD-Gruppen entfernen",         "active_roles.remove_all_groups",           {"asset_name": "{{asset_name}}"}, True),
        (2, "VM-Neuinstallation auslösen",  "sccm.trigger_reinstall",                  {"asset_name": "{{asset_name}}"}, True),
        (3, "Asset freigeben",              "pool.release_asset",                       {"asset_id": "{{asset_id}}"}, True),
        (4, "Rückforderungsmail senden",    "notifications.send_reclaim",               _notif_reclaim, False),
    ]

    _runbooks = [
        ("VDI Bereitstellen",       "Vollständige VDI-Bereitstellung: Pool reservieren, AD-Gruppen konfigurieren, VM vorbereiten", "provision", _provision_steps),
        ("VDI Zugang ändern",       "RDP- und Admin-Gruppen einer bestehenden VDI aktualisieren", "modify",    _modify_steps),
        ("VDI Laufzeit verlängern", "Ablaufzeit einer VDI-Bestellung verlängern",                 "extend",    _extend_steps),
        ("VDI Zurückfordern",       "VDI aus dem Betrieb nehmen, Zugänge entfernen, Pool freigeben", "delete", _delete_steps),
    ]

    for vdi_type_name in ["Test VDI", "Business VDI"]:
        asset_type_id = type_ids.get(vdi_type_name)
        if not asset_type_id:
            continue

        for rb_name, rb_desc, action, steps in _runbooks:
            full_name = f"{vdi_type_name} – {rb_name}"
            result = conn.execute(
                sa.text("""
                    INSERT INTO runbook_definitions
                        (name, description, asset_type_id, action, is_active, created_at, updated_at)
                    VALUES (:name, :desc, :at, CAST(:ac AS order_action), true, NOW(), NOW())
                    ON CONFLICT (asset_type_id, action) DO NOTHING
                    RETURNING id
                """),
                {"name": full_name, "desc": rb_desc, "at": asset_type_id, "ac": action},
            )
            row = result.fetchone()
            if not row:
                row = conn.execute(
                    sa.text(
                        "SELECT id FROM runbook_definitions "
                        "WHERE asset_type_id = :at AND action = CAST(:ac AS order_action)"
                    ),
                    {"at": asset_type_id, "ac": action},
                ).fetchone()
            if not row:
                continue
            runbook_id = row[0]

            for pos, sname, mkey, params, critical in steps:
                conn.execute(
                    sa.text("""
                        INSERT INTO runbook_steps
                            (runbook_id, position, step_name, module_key, params_template,
                             is_critical, retry_count, timeout_seconds, created_at)
                        VALUES (:rid, :pos, :sname, :mkey, CAST(:ptpl AS jsonb),
                                :critical, 3, 120, NOW())
                        ON CONFLICT (runbook_id, position) DO NOTHING
                    """),
                    {
                        "rid": runbook_id,
                        "pos": pos,
                        "sname": sname,
                        "mkey": mkey,
                        "ptpl": json.dumps(params),
                        "critical": critical,
                    },
                )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS runbook_steps")
    op.execute("DROP TABLE IF EXISTS runbook_definitions")
    op.drop_column("asset_types", "pool_capacity")
    op.drop_column("asset_types", "asset_model")
