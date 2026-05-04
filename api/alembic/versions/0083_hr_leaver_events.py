"""HR leaver events — audit table for the SCIM + HR-webhook deprovision path.

Captures every received leaver event so auditors can answer "did we
deprovision this user, and how quickly?" without reverse-engineering
the audit_log. Each row is the high-water mark per receive — re-firing
a leaver event for the same user produces a new row (idempotency at the
order level is handled by the existing revoke flow, which short-circuits
on already-revoked / already-rejected orders).

Status semantics:
* ``received`` — webhook / SCIM call accepted, ready for processing
  (only ever lives momentarily; processing happens inline).
* ``processed`` — leaver flow completed, counts populated.
* ``failed`` — exception during processing; ``error_message`` carries
  the detail. The flow itself is best-effort, so failures here are
  unusual and indicate a real problem (DB outage, malformed payload).

Source semantics:
* ``hr_webhook`` — `/hr/leaver` POST authenticated via HMAC.
* ``scim`` — SCIM `DELETE /Users/{id}` or `PATCH /Users/{id}` with
  ``active: false``.

Revision ID: 0083
Revises: 0082
Create Date: 2026-04-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: Union[str, None] = "0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hr_leaver_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column(
            "user_external_id",
            sa.String(length=255),
            nullable=True,
            comment=(
                "Vendor-specific identifier (employeeID, sAMAccountName, "
                "Workday WID, SCIM externalId). Captured for audit; ipSolis "
                "operates on user_email."
            ),
        ),
        sa.Column(
            "raw_payload",
            sa.JSON(),
            nullable=True,
            comment="Verbatim JSON payload received from the IDP / HR system.",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="received",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("orders_revoked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approvals_superseded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviews_superseded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=False),
    )

    # Reverse-lookup index for "all leaver events for this email" queries
    # (admin UI history view, audit drill-down).
    op.create_index(
        "ix_hr_leaver_events_email",
        "hr_leaver_events",
        ["user_email"],
    )
    op.create_index(
        "ix_hr_leaver_events_status_received",
        "hr_leaver_events",
        ["status", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hr_leaver_events_status_received", table_name="hr_leaver_events")
    op.drop_index("ix_hr_leaver_events_email", table_name="hr_leaver_events")
    op.drop_table("hr_leaver_events")
