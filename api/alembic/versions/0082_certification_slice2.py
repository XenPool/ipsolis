"""Access certification campaigns — slice 2 config + email templates.

Adds the notification + auto-revoke layer on top of the slice-1
schema. Pure config-only migration:

* 4 new ``app_config`` keys (``certification.*``) controlling
  reminder cadence, auto-revoke policy, escalation contact list.
* 4 new ``email_templates`` rows for kickoff / reminder / overdue /
  escalation. All seeded with sane HTML defaults; admins customise
  via *Settings → Email Templates*.

No schema changes — the ``certification_reviews.status`` column from
slice 1 already accepts ``auto_revoked`` and ``last_reminder_*`` data
isn't worth a column when the audit log already records every
notification sent.

Revision ID: 0082
Revises: 0081
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0082"
down_revision: Union[str, None] = "0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KEYS = [
    (
        "certification.reminder_days",
        "7,1",
        "Comma-separated days-before-due offsets at which to email pending "
        "reviewers a reminder (e.g. '7,1' fires reminders 7 days and 1 day "
        "before the campaign's due_at). Leave blank to disable reminders. "
        "Each (review, offset) combination is dispatched at most once "
        "per campaign.",
    ),
    (
        "certification.overdue_reminder_enabled",
        "true",
        "When true, pending reviewers get one final email the day after the "
        "campaign's due_at. Independent of the day-offset reminders so "
        "operators can disable nag-reminders while keeping the overdue "
        "warning, or vice-versa.",
    ),
    (
        "certification.auto_revoke_on_overdue",
        "false",
        "When true, the daily Beat task auto-revokes reviews that remain "
        "pending after the campaign's due_at. Off by default — auto-revoke "
        "yanks live access, so it should be an explicit opt-in.",
    ),
    (
        "certification.escalation_email",
        "",
        "Comma-separated email recipients for the escalation notice that "
        "fires once per campaign on the overdue date. Typical recipients: "
        "the IT compliance officer or the access-management mailing list. "
        "Leave blank to disable escalation entirely.",
    ),
]


_EMAIL_TEMPLATES = [
    {
        "event_key": "certification_kickoff",
        "description": (
            "Sent to each reviewer when a certification campaign is started. "
            "Lists the orders they need to re-confirm and links to a signed-"
            "token review page (no portal login required)."
        ),
        "subject": "[{{company_name}}] Access certification: {{review_count}} review(s) due {{due_date}}",
        "body": """<p>Hello {{reviewer_name}},</p>
<p>An access certification campaign — <strong>{{campaign_name}}</strong> — has been started.
You have <strong>{{review_count}} review(s)</strong> assigned and the cycle is due
<strong>{{due_date}}</strong>.</p>
<p>Each entry lists one access grant you originally approved. For each one, please confirm whether
the user still needs the access; revoking via this page will pull the access immediately.</p>
<p><a href="{{review_url}}" style="color:#BB0A30;font-weight:bold;">Open my review queue →</a></p>
<p style="font-size:11px;color:#888;">This link is signed and valid for 14 days. No portal login required.</p>""",
        "available_variables": (
            '["company_name","app_title","reviewer_name","reviewer_email",'
            '"campaign_name","campaign_id","review_count","due_date","review_url"]'
        ),
    },
    {
        "event_key": "certification_reminder",
        "description": (
            "Sent to reviewers with pending decisions at each configured "
            "days-before-due offset (default 7d, 1d). Each (review, offset) "
            "combination dispatches at most once per campaign."
        ),
        "subject": "[{{company_name}}] Reminder ({{days_left}}d): {{pending_count}} access review(s) pending",
        "body": """<p>Hello {{reviewer_name}},</p>
<p>You still have <strong>{{pending_count}} pending access review(s)</strong> for the
<strong>{{campaign_name}}</strong> certification cycle, due in <strong>{{days_left}} day(s)</strong>
({{due_date}}).</p>
<p><a href="{{review_url}}" style="color:#BB0A30;font-weight:bold;">Decide pending reviews →</a></p>
<p style="font-size:11px;color:#888;">This link is signed and valid for 14 days. No portal login required.</p>""",
        "available_variables": (
            '["company_name","app_title","reviewer_name","reviewer_email",'
            '"campaign_name","campaign_id","pending_count","days_left","due_date","review_url"]'
        ),
    },
    {
        "event_key": "certification_overdue",
        "description": (
            "Sent to reviewers the day after a campaign's due_at if they "
            "still have pending reviews. Carries a stronger tone than the "
            "regular reminder and warns that auto-revoke may fire if "
            "configured."
        ),
        "subject": "[{{company_name}}] OVERDUE: {{pending_count}} access review(s) past due",
        "body": """<p>Hello {{reviewer_name}},</p>
<p>You have <strong>{{pending_count}} access review(s) past due</strong> for the
<strong>{{campaign_name}}</strong> certification cycle (was due {{due_date}}).</p>
{{auto_revoke_warning}}
<p><a href="{{review_url}}" style="color:#BB0A30;font-weight:bold;">Decide pending reviews →</a></p>
<p style="font-size:11px;color:#888;">This link is signed and valid for 14 days. No portal login required.</p>""",
        "available_variables": (
            '["company_name","app_title","reviewer_name","reviewer_email",'
            '"campaign_name","campaign_id","pending_count","due_date",'
            '"review_url","auto_revoke_warning"]'
        ),
    },
    {
        "event_key": "certification_escalation",
        "description": (
            "Sent to the configured escalation contact(s) on the overdue "
            "date with the full picture of which reviewers still have "
            "pending decisions. Fires at most once per campaign."
        ),
        "subject": "[{{company_name}}] Certification campaign overdue: {{campaign_name}}",
        "body": """<p>Hello,</p>
<p>The access certification campaign <strong>{{campaign_name}}</strong> passed its due date
({{due_date}}) with <strong>{{pending_count}} review(s) still pending</strong> across
<strong>{{reviewer_count}} reviewer(s)</strong>.</p>
<p>Reviewers with outstanding decisions:</p>
<pre style="background:#f5f5f5;border:1px solid #ddd;padding:8px;font-size:12px;">{{reviewer_summary}}</pre>
{{auto_revoke_status}}
<p><a href="{{campaign_url}}" style="color:#BB0A30;font-weight:bold;">Open campaign in {{app_title}} →</a></p>""",
        "available_variables": (
            '["company_name","app_title","campaign_name","campaign_id",'
            '"due_date","pending_count","reviewer_count","reviewer_summary",'
            '"campaign_url","auto_revoke_status"]'
        ),
    },
]


def upgrade() -> None:
    for key, value, description in _KEYS:
        op.execute(
            f"""
            INSERT INTO app_config (key, value, description, is_secret)
            VALUES ({_lit(key)}, {_lit(value)}, {_lit(description)}, false)
            ON CONFLICT (key) DO NOTHING
            """
        )
    for tpl in _EMAIL_TEMPLATES:
        op.execute(
            f"""
            INSERT INTO email_templates (event_key, description, subject, body,
                                         available_variables, is_active)
            VALUES ({_lit(tpl['event_key'])},
                    {_lit(tpl['description'])},
                    {_lit(tpl['subject'])},
                    {_lit(tpl['body'])},
                    {_lit(tpl['available_variables'])},
                    true)
            ON CONFLICT (event_key) DO NOTHING
            """
        )


def downgrade() -> None:
    for tpl in _EMAIL_TEMPLATES:
        op.execute(
            f"DELETE FROM email_templates WHERE event_key = {_lit(tpl['event_key'])}"
        )
    for key, _, _ in _KEYS:
        op.execute(f"DELETE FROM app_config WHERE key = {_lit(key)}")


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
