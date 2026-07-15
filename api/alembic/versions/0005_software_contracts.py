"""Software license / contract lifecycle: contracts store + asset-type binding.

Adds:
1. ``software_contracts`` table — the customer's vendor software contracts
   (vendor, product, contract_value, licensed_seats, renewal_date, …). NOT
   the product ``.lic`` licensing system (that is config-driven).
2. ``asset_types.contract_id`` FK (0..1 per type, SET NULL on delete) — the
   1 contract : N asset types binding.
3. Seeds the ``contract.*`` renewal-reminder config defaults (opt-in).

Revision ID: 0005
Revises: 0004
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
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
    op.create_table(
        "software_contracts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vendor", sa.String(200), nullable=False),
        sa.Column("product", sa.String(200), nullable=False),
        sa.Column("contract_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("billing_interval", sa.String(20), nullable=False, server_default="annual"),
        sa.Column("licensed_seats", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("renewal_date", sa.Date(), nullable=True),
        sa.Column("notice_period_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cost_center", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_renewal_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_software_contracts_renewal", "software_contracts", ["renewal_date"])

    op.add_column(
        "asset_types",
        sa.Column("contract_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_asset_types_contract_id", "asset_types",
        "software_contracts", ["contract_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_asset_types_contract_id", "asset_types", ["contract_id"])

    conn = op.get_bind()
    _seed(conn, "contract.renewal_reminder_enabled", "false",
          "Master switch for the software-contract renewal-reminder Beat task (opt-in).")
    _seed(conn, "contract.renewal_reminder_email", "",
          "Recipient for contract renewal reminders (falls back to health.alert_email).")


def downgrade() -> None:
    op.drop_index("ix_asset_types_contract_id", table_name="asset_types")
    op.drop_constraint("fk_asset_types_contract_id", "asset_types", type_="foreignkey")
    op.drop_column("asset_types", "contract_id")
    op.drop_index("ix_software_contracts_renewal", table_name="software_contracts")
    op.drop_table("software_contracts")
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM app_config WHERE key IN "
        "('contract.renewal_reminder_enabled', 'contract.renewal_reminder_email')"
    ))
