"""Seed audit-log retention config.

Single global window for slice 1 — most operators want a simple
"prune everything older than N days" knob before they want per-class
differentiation. The trigger we installed in 0062 protects the table
from accidental tampering; the Beat task uses the documented
``ipsolis.allow_audit_mutation`` escape hatch to do legitimate pruning.

The classification metadata captured per asset attribute (slice from
2026-04-26) gives a foundation for per-class retention windows
(``retention.pii_days``, ``retention.phi_days``, ``retention.pci_days``)
once a real customer needs it — the pruning task will pick those up
without further migration work.

Revision ID: 0063
Revises: 0062
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        ('retention.audit_log_days', '0',
         'Audit log retention window in days. 0 disables pruning. The retention Beat task runs daily at 03:00 and uses the documented append-only bypass to delete rows past this age.',
         false, NOW()),
        ('retention.last_run_at', '',
         'Auto-managed — ISO timestamp of the last successful retention run.',
         false, NOW()),
        ('retention.last_pruned', '0',
         'Auto-managed — number of rows deleted in the last retention run.',
         false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'retention.audit_log_days',
          'retention.last_run_at',
          'retention.last_pruned'
        )
    """)
