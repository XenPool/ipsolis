"""Make ``audit_log`` tamper-evident at the database layer.

Adds three BEFORE-statement triggers (DELETE / UPDATE / TRUNCATE) that
raise an exception unless the session explicitly opts in via the
``ipsolis.allow_audit_mutation`` GUC. The intent is defense-in-depth:
even an operator with full DB credentials can't quietly rewrite history,
and any mutation attempt fails loudly rather than silently succeeding.

A documented escape hatch exists for legitimate maintenance (e.g. a
classification-driven retention Beat task — see TASKS.md). To prune:

    BEGIN;
    SET LOCAL ipsolis.allow_audit_mutation = 'true';
    DELETE FROM audit_log WHERE timestamp < NOW() - INTERVAL '7 years';
    COMMIT;

The ``SET LOCAL`` ensures the bypass is scoped to the current
transaction only — it does not leak across sessions or reconnects.

Why triggers (vs. revoking ``DELETE``/``UPDATE`` from the app role):
the application role owns the table, so a role-grant approach would
require running maintenance under a separate privileged role and
creating a non-owner app role. Triggers let us ship the protection
in a single migration without any operator-side role re-engineering,
and the GUC bypass keeps future maintenance simple.

Revision ID: 0062
Revises: 0061
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_no_mutate()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- Honor an explicit per-transaction opt-in. Use
            -- ``current_setting(name, true)`` so an unset GUC returns
            -- NULL instead of erroring; that makes "default deny" the
            -- behaviour without any session setup.
            IF current_setting('ipsolis.allow_audit_mutation', true) = 'true' THEN
                RETURN NULL;
            END IF;
            RAISE EXCEPTION
                'audit_log is append-only — % blocked. To bypass for legitimate maintenance, set ipsolis.allow_audit_mutation = ''true'' inside the transaction.',
                TG_OP;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER audit_log_no_delete
        BEFORE DELETE ON audit_log
        FOR EACH STATEMENT
        EXECUTE FUNCTION audit_log_no_mutate();
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_log
        FOR EACH STATEMENT
        EXECUTE FUNCTION audit_log_no_mutate();
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT
        EXECUTE FUNCTION audit_log_no_mutate();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_no_mutate()")
