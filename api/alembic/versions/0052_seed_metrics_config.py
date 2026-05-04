"""Seed Prometheus metrics config flag.

The /metrics endpoint is enabled by default since most operators want it
on day one. Set ``metrics.enabled = false`` to return 404 from /metrics
in environments that prefer to disable the endpoint entirely.

Revision ID: 0052
Revises: 0051
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES (
            'metrics.enabled',
            'true',
            'Expose the Prometheus /metrics endpoint. Set to false to return 404.',
            false,
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'metrics.enabled'")
