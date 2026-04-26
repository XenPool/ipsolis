"""Approval escalation — final-line notification when reminders are exhausted.

Adds:
* ``order_approvals.escalated_at`` — timestamp of the escalation.
  Once set, no more reminders or escalations fire for the row.
* ``approval.escalation_email`` config key — comma-separated list of
  addresses notified when an approval crosses ``max_reminders``
  without a decision. Empty (default) = escalation disabled.
* ``approval_escalated`` email template — body used by the new
  notification path.

Revision ID: 0059
Revises: 0058
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_approvals",
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES (
            'approval.escalation_email',
            '',
            'Comma-separated email(s) notified when an approval has burned through its reminders without a decision. Empty = escalation disabled.',
            false,
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)

    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active)
        VALUES (
            'approval_escalated',
            'Sent to the configured escalation contact(s) when an approval has run out of reminders without a decision.',
            '[{{company_name}}] Approval overdue — {{asset_type_name}}',
            '<p>Hello,</p>
<p>An approval request has not been acted on after {{reminder_count}} reminders and is now being escalated.</p>
<p><strong>Original approver:</strong> {{approver_name}} &lt;{{approver_email}}&gt;<br>
<strong>Requester:</strong> {{requester_name}} &lt;{{requester_email}}&gt;<br>
<strong>Asset:</strong> {{asset_type_name}}<br>
<strong>Requested period:</strong> {{from_date}} – {{until_date}}</p>
<p>Please intervene — chase the original approver, reassign the request, or cancel the order via the admin UI.</p>
<p><a href="{{approval_url}}" style="color:#BB0A30;font-weight:bold;">Open in {{app_title}} →</a></p>',
            '["company_name","app_title","approver_name","approver_email","requester_name","requester_email","asset_type_name","from_date","until_date","approval_url","reminder_count"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM email_templates WHERE event_key = 'approval_escalated'")
    op.execute("DELETE FROM app_config WHERE key = 'approval.escalation_email'")
    op.drop_column("order_approvals", "escalated_at")
