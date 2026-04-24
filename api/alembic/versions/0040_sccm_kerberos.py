"""Switch SCCM integration to pure PowerShell + Kerberos (GSSAPI).

- Adds sccm.realm + sccm.kdc config keys.
- Overwrites script_content of the three SCCM script_modules with the new
  PS+Kerberos implementation. Historically this read from the old layout
  ``scripts/sccm/*.ps1``; post-0046 the canonical disk location is
  ``scripts/modules/sccm/*.ps1``. When running on a fresh install the files
  at the new location are used; if neither exists, the update is skipped
  (0046 will seed from disk instead). The worker helper
  /app/tasks/utils/sccm_admin.py is no longer required.

Revision ID: 0040
Revises: 0039
Create Date: 2026-04-19
"""
import logging
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.migration.0040")

# Look in the current canonical layout first, then the pre-0046 layout.
SCRIPT_DIRS = (
    Path("/app/scripts/modules/sccm"),
    Path("/app/scripts/sccm"),
)

# (DB name, list of candidate filenames across layouts)
MODULES = [
    ("SCCM - Delete Device",
     ["SCCM_-_Delete_Device.ps1", "SCCM-Delete-Device.ps1"]),
    ("SCCM - Import Device and Assign Collections",
     ["SCCM_-_Import_Device_and_Assign_Collections.ps1", "SCCM-Import-Device-And-Assign-Collections.ps1"]),
    ("SCCM - Wait for Task Sequence",
     ["SCCM_-_Wait_for_Task_Sequence.ps1", "SCCM-Wait-For-Task-Sequence.ps1"]),
]


def _find_script(filenames: list[str]) -> Path | None:
    for d in SCRIPT_DIRS:
        for fn in filenames:
            p = d / fn
            if p.exists():
                return p
    return None


def _strip_seed_header(raw: str) -> str:
    """Drop ``# NAME:`` / ``# DESC:`` comment lines the seed exporter adds."""
    lines = raw.splitlines()
    idx = 0
    while idx < len(lines) and idx < 5:
        stripped = lines[idx].strip()
        if stripped.startswith("# NAME:") or stripped.startswith("# DESC:"):
            idx += 1
        elif stripped == "":
            idx += 1
        else:
            break
    return "\n".join(lines[idx:]).rstrip() + "\n"


def upgrade() -> None:
    conn = op.get_bind()

    # New config keys for Kerberos
    conn.execute(sa.text("""
        INSERT INTO app_config (key, value, description, is_secret) VALUES
        ('sccm.realm', '', 'Kerberos realm (AD domain, UPPERCASE), e.g. CORP.EXAMPLE.COM', false),
        ('sccm.kdc',   '', 'Kerberos KDC hostname (domain controller), e.g. dc01.corp.example.com', false)
        ON CONFLICT (key) DO NOTHING
    """))

    for name, filenames in MODULES:
        path = _find_script(filenames)
        if path is None:
            logger.warning(
                "migration 0040: no on-disk script for %r — skipping content update "
                "(0046 will seed from scripts/modules/ if available)",
                name,
            )
            continue
        content = _strip_seed_header(path.read_text(encoding="utf-8"))
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
