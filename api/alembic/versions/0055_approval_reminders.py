"""Approval reminders — track when approvers were last nudged.

Two new columns on ``order_approvals``:

* ``last_reminded_at`` — timestamp of the most recent reminder
* ``reminder_count`` — how many reminders have been sent so far

Plus three ``app_config`` keys controlling the Beat task:

* ``approval.reminders_enabled`` (default ``true``)
* ``approval.reminder_after_hours`` (default ``24``)
* ``approval.max_reminders`` (default ``3``)

Revision ID: 0055
Revises: 0054
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_approvals",
        sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_approvals",
        sa.Column(
            "reminder_count", sa.Integer(),
            nullable=False, server_default="0",
        ),
    )
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('approval.reminders_enabled', 'true',
         'Send reminder notifications to approvers who have not yet decided.',
         false, NOW()),
        ('approval.reminder_after_hours', '24',
         'Hours since the last notification before a reminder is sent.',
         false, NOW()),
        ('approval.max_reminders', '3',
         'Maximum number of reminders sent per pending approval.',
         false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'approval.reminders_enabled',
          'approval.reminder_after_hours',
          'approval.max_reminders'
        )
    """)
    op.drop_column("order_approvals", "reminder_count")
    op.drop_column("order_approvals", "last_reminded_at")
