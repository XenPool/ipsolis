"""Add modify_confirmation email template

Revision ID: 0020
Revises: 0019
Create Date: 2026-03-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO email_templates (event_key, subject, body, is_active)
        VALUES (
            'modify_confirmation',
            '[{{company_name}}] Your access {{asset_name}} has been updated',
            '<p>Hello {{requester_name}},</p>
<p>your access has been updated and is ready to use.</p>
<table style="font-size:13px;border-collapse:collapse;">
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Name:</td><td style="padding:4px 0;font-weight:bold;">{{asset_name}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">RDP Users:</td><td style="padding:4px 0;">{{rdp_users}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Valid until:</td><td style="padding:4px 0;">{{expires_at}}</td></tr>
</table>
<p style="margin-top:16px;">The RDP file is attached – open it to connect directly to your virtual machine.</p>',
            TRUE
        )
        ON CONFLICT (event_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM email_templates WHERE event_key = 'modify_confirmation'")
