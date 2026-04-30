"""Add compatibility column to ps_modules — track Linux/Core support.

PowerShell modules published to PSGallery may carry ``PSEdition_Core``
(works on PowerShell 7 / Linux) and/or ``PSEdition_Desktop`` (Windows
PowerShell 5.1 only) tags. We resolve these at search time and store
the derived flag at install time so the modules table can show a
compatibility badge without re-querying PSGallery on every render.

Allowed values:
- ``core``         — has PSEdition_Core tag, Linux-compatible
- ``desktop_only`` — has PSEdition_Desktop tag only, won't run on Linux
- ``unknown``      — no PSEdition tag (legacy / poorly tagged module)

Default ``unknown`` for back-compat with existing rows; the next
reinstall (or admin-triggered refresh) populates the column.

Revision ID: 0077
Revises: 0076
Create Date: 2026-04-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0077"
down_revision: Union[str, None] = "0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ps_modules",
        sa.Column(
            "compatibility",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ps_modules", "compatibility")
