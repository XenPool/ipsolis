"""Cost-threshold alerts: per-(cost_center, currency) projected-spend ceilings.

Operators set monthly limits per cost-center / currency. A daily Beat
task computes the projected monthly spend from active orders against
each threshold and emails the configured recipients when the
projection crosses the limit. Hysteresis via ``last_alerted_at`` —
the same row is alerted at most once per
``cost.threshold_alert_quiet_hours`` window (default 24h) so a spend
hovering near the limit doesn't spam.

Composite-PK on (cost_center, currency) so the same cost center can
hold separate thresholds per currency without forcing FX conversion
(which is its own queued slice).

Revision ID: 0079
Revises: 0078
Create Date: 2026-04-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0079"
down_revision: Union[str, None] = "0078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_thresholds",
        sa.Column("cost_center", sa.String(length=100), primary_key=True),
        sa.Column("currency", sa.String(length=3), primary_key=True),
        sa.Column("monthly_limit", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "recipients",
            sa.Text(),
            nullable=False,
            comment="Comma-separated email recipients for breach alerts",
        ),
        sa.Column("last_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_alerted_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
        ),
    )

    # Quiet window so a spend that hovers near the threshold doesn't
    # spam alerts. 24h default — admins can shorten for staging /
    # lengthen for low-noise prod.
    op.execute(
        """
        INSERT INTO app_config (key, value, description, is_secret)
        VALUES (
            'cost.threshold_alert_quiet_hours',
            '24',
            'Minimum hours between repeat breach alerts on the same threshold row. 0 = alert every Beat tick (testing only).',
            false
        )
        ON CONFLICT (key) DO NOTHING
        """
    )

    # Email template — admins customise via Settings → Email Templates.
    op.execute(
        """
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active)
        VALUES (
            'cost_threshold_breach',
            'Sent to the recipients listed on a cost_thresholds row when projected monthly spend crosses the configured limit.',
            '[{{company_name}}] Cost threshold breached — {{cost_center}}',
            '<p>Hello,</p>
<p>Projected monthly spend for cost center <strong>{{cost_center}}</strong> has crossed the configured limit.</p>
<table cellspacing="0" cellpadding="6" style="border-collapse:collapse;border:1px solid #ddd;">
  <tr><td style="border:1px solid #ddd;">Configured limit</td><td style="border:1px solid #ddd;font-family:monospace;">{{monthly_limit}} {{currency}}</td></tr>
  <tr><td style="border:1px solid #ddd;">Current projection</td><td style="border:1px solid #ddd;font-family:monospace;color:#BB0A30;font-weight:bold;">{{projected_total}} {{currency}}</td></tr>
  <tr><td style="border:1px solid #ddd;">Active orders</td><td style="border:1px solid #ddd;">{{active_orders}}</td></tr>
  <tr><td style="border:1px solid #ddd;">Asset definitions in scope</td><td style="border:1px solid #ddd;">{{asset_types}}</td></tr>
</table>
<p>Review the full breakdown in the admin UI:</p>
<p><a href="{{cost_report_url}}" style="color:#BB0A30;font-weight:bold;">Open Cost Report in {{app_title}} →</a></p>
<p style="font-size:11px;color:#888;">Repeat alerts on this row are suppressed for {{quiet_hours}} hours unless the threshold is edited.</p>',
            '["company_name","app_title","cost_center","currency","monthly_limit","projected_total","active_orders","asset_types","cost_report_url","quiet_hours"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM email_templates WHERE event_key = 'cost_threshold_breach'")
    op.execute("DELETE FROM app_config WHERE key = 'cost.threshold_alert_quiet_hours'")
    op.drop_table("cost_thresholds")
