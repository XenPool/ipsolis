"""Seed the api_tokens hard-delete purge config key.

Slice 1 of the API token feature (migration ``0054_api_tokens``)
soft-deletes via ``revoked_at`` — rows stay in the database
indefinitely so the audit trail of "we used to have a token X"
remains intact.

Some tenants (regulated industries with strict record-retention
mandates) want the opposite: revoked credentials must not linger
in the application database past a defined window. This migration
seeds the policy knob; the Beat task that enacts it lives in
``worker/tasks/workflows/api_token_purge.py``. The same task also
hard-deletes *expired* tokens (``expires_at`` past the window) on
the same schedule — same intent (remove dead credentials), same
record-retention argument.

Default is ``0`` (disabled) so existing installs upgrade silently
with the slice-1 retain-forever behaviour.

Revision ID: 0093
Revises: 0092
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0093"
down_revision: Union[str, None] = "0092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES (
            'api_tokens.purge_after_days',
            '0',
            'Hard-delete API token rows whose revoked_at OR expires_at is older '
            'than this many days. Default 0 = disabled (slice-1 retain-forever '
            'behaviour). Each delete writes one audit row capturing the token '
            'name + prefix + reason (revoked / expired) for the forensic trail. '
            'The token_hash itself is not retained — once the row is gone, the '
            'audit trail names ''token:<name>'' but no replay attempt is possible.',
            false,
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'api_tokens.purge_after_days'")
