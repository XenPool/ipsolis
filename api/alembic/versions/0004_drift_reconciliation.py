"""Drift / out-of-band reconciliation: per-type opt-in + findings store + config.

Adds:
1. ``asset_types.drift_monitor`` (bool, default false) — the per-asset-type
   opt-in for drift reconciliation (the global master switch is
   ``drift.enabled`` in app_config).
2. ``drift_findings`` table — one row per detected divergence between what
   ipSolis provisioned and what actually exists in AD, in either direction
   (``missing_access`` / ``out_of_band``), with remediation state.
3. Seeds the ``drift.*`` config defaults (opt-in: disabled, detect-only).

Revision ID: 0004
Revises: 0003
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels = None
depends_on = None


def _seed(conn, key: str, value: str, description: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) "
            "VALUES (:k, :v, :d, false, NOW(), NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"k": key, "v": value, "d": description},
    )


def upgrade() -> None:
    # 1) Per-asset-type opt-in flag.
    op.add_column(
        "asset_types",
        sa.Column("drift_monitor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 2) Findings store. Plain String columns (no DB enum) so we avoid the
    #    "type already exists" pitfall and stay migration-friendly.
    op.create_table(
        "drift_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_type_id", sa.Integer(), sa.ForeignKey("asset_types.id", ondelete="SET NULL"), nullable=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_type", sa.String(50), nullable=False, server_default="ad_group"),
        sa.Column("identifier", sa.Text(), nullable=False),           # group DN
        sa.Column("principal", sa.String(255), nullable=False),       # user email / sAMAccountName
        sa.Column("direction", sa.String(20), nullable=False),        # missing_access | out_of_band
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),   # open | remediated | ignored
        sa.Column("remediation", sa.String(30), nullable=False, server_default="detected"),  # detected|re_granted|revoked|failed|skipped
        sa.Column("detail", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_drift_findings_status", "drift_findings", ["status"])
    op.create_index(
        "ix_drift_findings_key", "drift_findings",
        ["identifier", "principal", "direction"],
    )

    # 3) Config defaults — opt-in, detect-only.
    conn = op.get_bind()
    _seed(conn, "drift.enabled", "false",
          "Master switch for drift / out-of-band reconciliation (opt-in).")
    _seed(conn, "drift.schedule_cron", "0 3 * * *",
          "Cron for the drift reconciliation Beat task (Europe/Berlin).")
    _seed(conn, "drift.remediation_mode", "detect_only",
          "detect_only = alert only; auto_remediate = also re-grant/revoke via AD.")


def downgrade() -> None:
    op.drop_index("ix_drift_findings_key", table_name="drift_findings")
    op.drop_index("ix_drift_findings_status", table_name="drift_findings")
    op.drop_table("drift_findings")
    op.drop_column("asset_types", "drift_monitor")
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM app_config WHERE key IN "
        "('drift.enabled', 'drift.schedule_cron', 'drift.remediation_mode')"
    ))
