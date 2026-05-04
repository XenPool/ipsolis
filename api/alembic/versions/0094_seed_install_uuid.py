"""Seed a per-install UUID for license-binding.

Generates a stable UUID4 on first apply and stores it under the
``install.uuid`` ``app_config`` key. Subsequent migrations / fresh
container starts find the value already set and leave it alone, so
the UUID is preserved across image rebuilds and persists for the
lifetime of the install (i.e. for as long as the database survives).

The license verifier in ``api/app/utils/license.py`` uses this value
to enforce install-bound licenses: when a license JSON includes an
``install_uuid`` field, it must match the local install UUID or the
license falls back to the Community edition. Licenses without an
``install_uuid`` field continue to verify normally — backwards-compat
for legacy licenses issued before this binding existed.

Revision ID: 0094
Revises: 0093
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0094"
down_revision: Union[str, None] = "0093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # gen_random_uuid() comes from pgcrypto; if missing, fall back to a
    # Python-generated UUID via the migration runner. Postgres 13+ ships
    # pgcrypto by default but the extension might not be enabled on the
    # target DB, so we CREATE EXTENSION IF NOT EXISTS first.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES (
            'install.uuid',
            gen_random_uuid()::text,
            'Stable per-install identifier generated on first migration. '
            'Used by the license verifier to bind purchased Enterprise '
            'licenses to a single deployment. Provide this value when '
            'requesting or renewing a license so the issued .lic file '
            'will only validate on this install. Do not edit manually — '
            'changing this value invalidates any install-bound license.',
            false,
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'install.uuid'")
