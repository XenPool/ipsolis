"""Access certification campaigns — slice 1.

Quarterly "managers must re-confirm their team's access" workflow,
required for ISO 27001 / SOX / PCI compliance audits. Slice 1 covers
the schema + admin CRUD + campaign kickoff (turning a draft campaign
into a set of review rows). Reminders, escalation, auto-revoke on
overdue, and the manager-facing portal page are queued for slice 2.

Two tables:

* ``certification_campaigns`` — header row per audit cycle, with the
  scope filter (JSONB), due date, and current status.
* ``certification_reviews`` — one row per (campaign, order) generated
  at kickoff. The reviewer is captured per row so subsequent manager
  changes don't shift the audit trail.

Status semantics:
* Campaign: ``draft`` → ``running`` (after kickoff) → ``closed``
  (manual or all-reviews-complete) | ``cancelled`` (operator abort).
* Review: ``pending`` → ``confirmed`` | ``revoked`` (manager decision)
  | ``auto_revoked`` (slice 2 — overdue with no decision).

Revision ID: 0081
Revises: 0080
Create Date: 2026-04-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0081"
down_revision: Union[str, None] = "0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "certification_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "scope",
            sa.JSON(),
            nullable=False,
            comment=(
                "Filter applied at kickoff to select active orders. Shape: "
                "{asset_type_ids?: int[], cost_centers?: str[], "
                "departments?: str[], requester_emails?: str[]}. "
                "Empty / null fields are wildcards. AND across keys, OR within."
            ),
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
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

    op.create_table(
        "certification_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("certification_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Snapshot at kickoff so a later manager change doesn't shift the
        # audit trail. Falls back to the order's existing manager approval
        # row when no AD-attribute manager is available.
        sa.Column("reviewer_email", sa.String(length=255), nullable=False),
        sa.Column("reviewer_name", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "campaign_id", "order_id",
            name="uq_certification_reviews_campaign_order",
        ),
    )

    # Reverse-lookup indexes for the dashboards the UI fires.
    op.create_index(
        "ix_certification_reviews_status",
        "certification_reviews",
        ["campaign_id", "status"],
    )
    op.create_index(
        "ix_certification_reviews_reviewer",
        "certification_reviews",
        ["reviewer_email", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_certification_reviews_reviewer", table_name="certification_reviews")
    op.drop_index("ix_certification_reviews_status", table_name="certification_reviews")
    op.drop_table("certification_reviews")
    op.drop_table("certification_campaigns")
