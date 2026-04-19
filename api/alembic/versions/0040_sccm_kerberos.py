"""Switch SCCM integration to pure PowerShell + Kerberos (GSSAPI).

- Adds sccm.realm + sccm.kdc config keys.
- Overwrites script_content of the three SCCM script_modules with the new
  PS+Kerberos implementation (reads from /app/scripts/sccm/*.ps1 — mounted
  into the api container).
- The worker helper /app/tasks/utils/sccm_admin.py is no longer required.

Revision ID: 0040
Revises: 0039
Create Date: 2026-04-19
"""
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCRIPT_DIR = Path("/app/scripts/sccm")

MODULES = [
    ("SCCM - Delete Device",                         "SCCM-Delete-Device.ps1"),
    ("SCCM - Import Device and Assign Collections", "SCCM-Import-Device-And-Assign-Collections.ps1"),
    ("SCCM - Wait for Task Sequence",                "SCCM-Wait-For-Task-Sequence.ps1"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # New config keys for Kerberos
    conn.execute(sa.text("""
        INSERT INTO app_config (key, value, description, is_secret) VALUES
        ('sccm.realm', '', 'Kerberos realm (AD domain, UPPERCASE), e.g. CORP.EXAMPLE.COM', false),
        ('sccm.kdc',   '', 'Kerberos KDC hostname (domain controller), e.g. dc01.corp.example.com', false)
        ON CONFLICT (key) DO NOTHING
    """))

    # Overwrite each SCCM module's script_content with the new PS + Kerberos version
    for name, filename in MODULES:
        path = SCRIPT_DIR / filename
        if not path.exists():
            # Scripts folder is mounted as a volume — fail loudly so the mismatch is caught
            raise RuntimeError(f"Required script not found in api container: {path}")
        content = path.read_text(encoding="utf-8")
        conn.execute(
            sa.text("""
                UPDATE script_modules
                SET script_content = :content,
                    description    = :desc,
                    updated_at     = NOW()
                WHERE name = :name
            """),
            {
                "name":    name,
                "content": content,
                "desc":    f"{name} — pure PowerShell against the SCCM Admin Service "
                           f"using Kerberos (GSSAPI) via kinit.",
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DELETE FROM app_config WHERE key IN ('sccm.realm', 'sccm.kdc')
    """))
    # Leave module content alone on downgrade — restoring the previous NTLM
    # versions would require the deleted sccm_admin.py helper anyway.
