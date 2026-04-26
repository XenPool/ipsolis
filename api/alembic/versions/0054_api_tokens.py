"""Per-integration API tokens with scopes, expiry, and revocation.

Replaces the single shared ``X-Admin-Key`` for machine-to-machine access.
The legacy ``ADMIN_API_KEY`` (.env) keeps working as a fallback so
existing integrations don't break on upgrade.

Revision ID: 0054
Revises: 0053
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        # SHA-256 of the raw token. Index ensures O(1) lookup on auth.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        # First six chars of the raw token (e.g. ``xpat_a1``) so the UI can
        # display "which token did this without revealing the secret".
        sa.Column("token_prefix", sa.String(12), nullable=False),
        # JSON array of scope strings. Slice 1 always seeds ['admin:*']
        # — scope decorators land in a follow-up slice.
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_table("api_tokens")
