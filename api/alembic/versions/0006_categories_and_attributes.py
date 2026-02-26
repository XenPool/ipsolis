"""Replace asset_category enum values; migrate config from dict to list

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-25

Changes:
- Old categories (vdi, server, workstation, other) → new categories
  (application_access, platform_access, data_access, device_access, infrastructure_access)
- Migration mapping: vdi→platform_access, server→platform_access,
  workstation→device_access, other→application_access
- config column: flat dict {"cpu": "4"} → structured list
  [{"key": "cpu", "label": "cpu", "options": ["4"]}]
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATEGORY_MAP = {
    "vdi":         "platform_access",
    "server":      "platform_access",
    "workstation": "device_access",
    "other":       "application_access",
}

_NEW_VALUES = [
    "application_access",
    "platform_access",
    "data_access",
    "device_access",
    "infrastructure_access",
]

_OLD_VALUES = ["vdi", "server", "workstation", "other"]

_REVERSE_MAP = {
    "platform_access":       "vdi",
    "application_access":    "other",
    "data_access":           "other",
    "device_access":         "workstation",
    "infrastructure_access": "server",
}


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: Create new enum type ─────────────────────────────────────────
    new_values_sql = ", ".join(f"'{v}'" for v in _NEW_VALUES)
    conn.execute(sa.text(f"CREATE TYPE asset_category_new AS ENUM ({new_values_sql})"))

    # ── Step 2: Add temporary column with new enum type ───────────────────────
    conn.execute(sa.text(
        "ALTER TABLE asset_types ADD COLUMN category_new asset_category_new"
    ))

    # ── Step 3: Migrate existing category values ──────────────────────────────
    for old_val, new_val in _CATEGORY_MAP.items():
        conn.execute(sa.text(
            "UPDATE asset_types "
            "SET category_new = CAST(:new_val AS asset_category_new) "
            "WHERE category = CAST(:old_val AS asset_category)"
        ), {"new_val": new_val, "old_val": old_val})

    # ── Step 4: Drop old column and old enum type ─────────────────────────────
    conn.execute(sa.text("ALTER TABLE asset_types DROP COLUMN category"))
    conn.execute(sa.text("DROP TYPE asset_category"))

    # ── Step 5: Rename new column and new type ────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE asset_types RENAME COLUMN category_new TO category"
    ))
    conn.execute(sa.text(
        "ALTER TYPE asset_category_new RENAME TO asset_category"
    ))

    # ── Step 6: Apply NOT NULL + default ─────────────────────────────────────
    conn.execute(sa.text(
        "UPDATE asset_types SET category = 'platform_access' WHERE category IS NULL"
    ))
    conn.execute(sa.text(
        "ALTER TABLE asset_types ALTER COLUMN category SET NOT NULL"
    ))
    conn.execute(sa.text(
        "ALTER TABLE asset_types ALTER COLUMN category SET DEFAULT 'platform_access'"
    ))

    # ── Step 7: Migrate config from flat dict to structured attribute list ────
    rows = conn.execute(
        sa.text("SELECT id, config FROM asset_types WHERE config IS NOT NULL")
    ).fetchall()

    for row_id, config_raw in rows:
        if config_raw is None:
            continue
        if isinstance(config_raw, dict):
            new_config = [
                {"key": k, "label": k, "options": [str(v)]}
                for k, v in config_raw.items()
            ]
            conn.execute(
                sa.text(
                    "UPDATE asset_types SET config = CAST(:cfg AS jsonb) WHERE id = :id"
                ),
                {"cfg": json.dumps(new_config), "id": row_id},
            )
        # if already a list, leave as-is


def downgrade() -> None:
    conn = op.get_bind()

    # ── Restore old enum type ─────────────────────────────────────────────────
    old_values_sql = ", ".join(f"'{v}'" for v in _OLD_VALUES)
    conn.execute(sa.text(f"CREATE TYPE asset_category_old AS ENUM ({old_values_sql})"))
    conn.execute(sa.text(
        "ALTER TABLE asset_types ADD COLUMN category_old asset_category_old"
    ))
    for new_val, old_val in _REVERSE_MAP.items():
        conn.execute(sa.text(
            "UPDATE asset_types "
            "SET category_old = CAST(:old_val AS asset_category_old) "
            "WHERE category = CAST(:new_val AS asset_category)"
        ), {"old_val": old_val, "new_val": new_val})

    conn.execute(sa.text("ALTER TABLE asset_types DROP COLUMN category"))
    conn.execute(sa.text("DROP TYPE asset_category"))
    conn.execute(sa.text(
        "ALTER TABLE asset_types RENAME COLUMN category_old TO category"
    ))
    conn.execute(sa.text(
        "ALTER TYPE asset_category_old RENAME TO asset_category"
    ))
    conn.execute(sa.text(
        "UPDATE asset_types SET category = 'vdi' WHERE category IS NULL"
    ))
    conn.execute(sa.text(
        "ALTER TABLE asset_types ALTER COLUMN category SET NOT NULL"
    ))

    # ── Downgrade config: list → flat dict (first option value) ──────────────
    rows = conn.execute(
        sa.text("SELECT id, config FROM asset_types WHERE config IS NOT NULL")
    ).fetchall()
    for row_id, config_raw in rows:
        if isinstance(config_raw, list):
            old_config = {
                attr["key"]: attr["options"][0] if attr.get("options") else ""
                for attr in config_raw
                if attr.get("key")
            }
            conn.execute(
                sa.text(
                    "UPDATE asset_types SET config = CAST(:cfg AS jsonb) WHERE id = :id"
                ),
                {"cfg": json.dumps(old_config), "id": row_id},
            )
