"""Add manager approval workflow

Revision ID: 0028
Revises: 0027
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. New OrderStatus enum values
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'pending_approval'")
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'rejected'")

    # 2. New columns on asset_types
    op.add_column("asset_types", sa.Column("requires_manager_approval", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("asset_types", sa.Column("requires_owner_approval", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("asset_types", sa.Column("approval_owners", sa.JSON(), nullable=True))

    # 3. Create order_approvals table
    op.create_table(
        "order_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approver_type", sa.String(30), nullable=False),
        sa.Column("approver_email", sa.String(255), nullable=False),
        sa.Column("approver_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_approvals_approver_status", "order_approvals", ["approver_email", "status"])
    op.create_index("ix_order_approvals_order_id", "order_approvals", ["order_id"])

    # 4. Seed email templates
    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active)
        VALUES (
            'approval_request',
            'Sent to each approver when an order requires their approval',
            '[{{company_name}}] Approval required – {{asset_type_name}}',
            '<p>Hello {{approver_name}},</p>
<p><strong>{{requester_name}}</strong> ({{requester_email}}) has requested access to <strong>{{asset_type_name}}</strong> and requires your approval.</p>
<p><strong>Requested period:</strong> {{from_date}} – {{until_date}}</p>
<p>Please review and approve or decline this request in the Self-Service Portal:</p>
<p><a href="{{approval_url}}" style="color:#BB0A30;font-weight:bold;">Review Request →</a></p>',
            '["company_name","approver_name","requester_name","requester_email","asset_type_name","from_date","until_date","approval_url"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active)
        VALUES (
            'approval_granted',
            'Sent to the requester when all approvals are granted',
            '[{{company_name}}] Your order has been approved – {{asset_type_name}}',
            '<p>Hello {{requester_name}},</p>
<p>Your request for <strong>{{asset_type_name}}</strong> has been approved by all required approvers.</p>
<p>Your order is now being processed and you will receive a confirmation once provisioning is complete.</p>',
            '["company_name","requester_name","requester_email","asset_type_name"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active)
        VALUES (
            'approval_declined',
            'Sent to the requester when an approver declines',
            '[{{company_name}}] Your order was declined – {{asset_type_name}}',
            '<p>Hello {{requester_name}},</p>
<p>Your request for <strong>{{asset_type_name}}</strong> has been declined by <strong>{{approver_name}}</strong>.</p>
{{decline_reason_block}}
<p>If you believe this was a mistake, please contact your manager or the application owner directly.</p>',
            '["company_name","requester_name","requester_email","asset_type_name","approver_name","decline_reason_block"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM email_templates WHERE event_key IN ('approval_request', 'approval_granted', 'approval_declined')")
    op.drop_index("ix_order_approvals_order_id", table_name="order_approvals")
    op.drop_index("ix_order_approvals_approver_status", table_name="order_approvals")
    op.drop_table("order_approvals")
    op.drop_column("asset_types", "approval_owners")
    op.drop_column("asset_types", "requires_owner_approval")
    op.drop_column("asset_types", "requires_manager_approval")
    # Note: PostgreSQL does not support removing enum values; pending_approval/rejected remain
