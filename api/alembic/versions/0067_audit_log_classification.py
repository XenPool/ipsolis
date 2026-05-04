"""Add classification column to audit_log + per-class retention config keys.

Slice 2 of audit retention: each ``audit_log`` row carries the data
classification of the touched entity (``internal`` / ``pii`` / ``phi``
/ ``pci``) so the prune task can apply per-class windows on top of the
global default. The strictest classification of any attribute on the
referenced asset type wins; unrelated audit rows fall under the
default ``internal``.

Existing rows are backfilled to ``internal`` — no historical context
to retroactively classify them, and the global window already covers
that bucket.

Revision ID: 0067
Revises: 0066
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Column. Seed pre-existing rows with 'internal' so the prune
    # task's per-class buckets behave deterministically. The UPDATE
    # would otherwise be blocked by the audit-log append-only trigger
    # installed in 0062 — we opt into the documented bypass via
    # ``SET LOCAL`` for the duration of this transaction only. A
    # fresh database with no audit rows yet skips the UPDATE entirely.
    op.add_column(
        "audit_log",
        sa.Column(
            "classification",
            sa.String(length=20),
            nullable=True,
            server_default="internal",
        ),
    )
    op.create_index(
        "ix_audit_log_classification", "audit_log", ["classification"]
    )
    op.execute("SET LOCAL ipsolis.allow_audit_mutation = 'true'")
    op.execute("UPDATE audit_log SET classification = 'internal' WHERE classification IS NULL")

    # 2) Per-class retention windows. Default 0 (= disabled) so this
    # migration is observably no-op for tenants who haven't opted in.
    # The catch-all ``retention.audit_log_days`` from migration 0063
    # continues to govern the ``internal`` bucket.
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('retention.pii_days', '0',
         'Per-class retention window for PII-tagged audit rows in days. 0 = fall under retention.audit_log_days.',
         false, NOW()),
        ('retention.phi_days', '0',
         'Per-class retention window for PHI-tagged audit rows in days. 0 = fall under retention.audit_log_days.',
         false, NOW()),
        ('retention.pci_days', '0',
         'Per-class retention window for PCI-tagged audit rows in days. 0 = fall under retention.audit_log_days.',
         false, NOW()),
        ('retention.last_pruned_by_class', '{}',
         'Auto-managed — JSON map of classification → rows pruned in the last run.',
         false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'retention.pii_days',
          'retention.phi_days',
          'retention.pci_days',
          'retention.last_pruned_by_class'
        )
    """)
    op.drop_index("ix_audit_log_classification", table_name="audit_log")
    op.drop_column("audit_log", "classification")
