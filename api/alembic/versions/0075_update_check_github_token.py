"""Add updates.github_token for private-repo update checks.

Required when ``updates.repo_url`` points at a private GitHub
repository (e.g. while the public release hasn't happened yet).
GitHub Personal Access Tokens — classic with ``repo`` scope, or
fine-grained with read-only Metadata + Contents — both work.

Stored with ``is_secret=true`` so the value is masked in the
Settings UI, mirroring the SMTP / AD / SCCM password pattern.

Revision ID: 0075
Revises: 0074
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app_config (key, value, is_secret, description)
        VALUES
          ('updates.github_token', '', true,
           'Optional GitHub Personal Access Token for the update notifier. Required when ``updates.repo_url`` points at a private repository. Use a classic PAT with ``repo`` scope or a fine-grained PAT with read-only Metadata + Contents on the target repo.')
        ON CONFLICT (key) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE key = 'updates.github_token'")
