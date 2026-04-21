"""Maintenance section: db_backups table + retention config keys.

- Adds db_backups table (id, filename, size_bytes, status, trigger, created_by,
  note, error, created_at).
- Seeds app_config retention defaults.

Revision ID: 0041
Revises: 0040
Create Date: 2026-04-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "db_backups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String(length=255), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("trigger", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index("ix_db_backups_created_at", "db_backups", ["created_at"])

    # Retention defaults (0 disables cleanup for that table)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret) VALUES
        ('retention.orders_days',           '180', 'Days to keep rows in the orders table (0 disables cleanup).', false),
        ('retention.audit_log_days',        '365', 'Days to keep rows in the audit_log table (0 disables cleanup).', false),
        ('retention.standalone_runs_days',  '90',  'Days to keep rows in standalone_runbook_runs (0 disables cleanup).', false),
        ('backup.keep_last_n',              '14',  'Maximum number of local backup files to keep (0 = unlimited).', false)
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
            'retention.orders_days',
            'retention.audit_log_days',
            'retention.standalone_runs_days',
            'backup.keep_last_n'
        )
    """)
    op.drop_index("ix_db_backups_created_at", table_name="db_backups")
    op.drop_table("db_backups")
