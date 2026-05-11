"""Seed three Community example script modules.

These scripts live under scripts/modules/examples/ and demonstrate the
standard provision / change / deprovision module pattern. They are shipped
in both Community and PRO editions so every fresh install has working
examples to reference when building asset-type runbooks.

Revision ID: 0096
Revises: 0095
Create Date: 2026-05-11
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0096"
down_revision: Union[str, None] = "0095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.migration.0096")

EXAMPLES_DIR = Path("/app/scripts/modules/examples")

_HARDCODED = [
    {
        "name": "Example - Provision Asset",
        "description": "Example module: log provisioning context and signal success. Use as a starting point for real provision steps.",
        "script_type": "powershell",
        "script_content": r"""param(
    [Parameter(Mandatory=$true)]
    [string]$asset_name,

    [string]$asset_id,
    [string]$order_id,
    [string]$user_email,
    [string]$user_name,
    [string]$asset_type_name
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] [$Level] $Message"
}

try {
    Write-Log "Starting provisioning for asset '$asset_name' (order $order_id)"
    Write-Log "Asset type : $asset_type_name"
    Write-Log "Assigned to: $user_name <$user_email>"

    # Add your provisioning logic here.
    # Access global variables via $VARS, e.g.: $VARS.'my.server.host'

    Write-Log "Provision step completed successfully."
    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)
}
catch {
    Write-Log "Provision step failed: $($_.Exception.Message)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
""",
    },
    {
        "name": "Example - Change Asset",
        "description": "Example module: log change-request context and signal success. Use as a starting point for real change steps.",
        "script_type": "powershell",
        "script_content": r"""param(
    [Parameter(Mandatory=$true)]
    [string]$asset_name,

    [string]$asset_id,
    [string]$order_id,
    [string]$user_email,
    [string]$user_name,
    [string]$owner_email,
    [string]$owner_name,
    [string]$expires_at,
    [string]$asset_type_name
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] [$Level] $Message"
}

try {
    Write-Log "Starting change step for asset '$asset_name' (order $order_id)"
    Write-Log "Current owner : $owner_name <$owner_email>"
    Write-Log "New assignment: $user_name <$user_email>"
    Write-Log "Expires       : $(if ($expires_at) { $expires_at } else { 'no expiry' })"

    # Add your change logic here.
    # Access global variables via $VARS, e.g.: $VARS.'my.server.host'

    Write-Log "Change step completed successfully."
    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)
}
catch {
    Write-Log "Change step failed: $($_.Exception.Message)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
""",
    },
    {
        "name": "Example - Deprovision Asset",
        "description": "Example module: log deprovisioning context and signal success. Use as a starting point for real deprovision steps.",
        "script_type": "powershell",
        "script_content": r"""param(
    [Parameter(Mandatory=$true)]
    [string]$asset_name,

    [string]$asset_id,
    [string]$order_id,
    [string]$user_email,
    [string]$user_name,
    [string]$owner_email,
    [string]$owner_name,
    [string]$asset_type_name
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] [$Level] $Message"
}

try {
    Write-Log "Starting deprovisioning for asset '$asset_name' (order $order_id)"
    Write-Log "Returning from : $owner_name <$owner_email>"
    Write-Log "Asset type     : $asset_type_name"

    # Add your deprovisioning logic here.
    # Access global variables via $VARS, e.g.: $VARS.'my.server.host'

    Write-Log "Deprovision step completed successfully."
    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)
}
catch {
    Write-Log "Deprovision step failed: $($_.Exception.Message)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
""",
    },
]


def _sql(query: str):
    from sqlalchemy import text as sa_text
    return sa_text(query)


def upgrade() -> None:
    conn = op.get_bind()
    inserted = 0
    for script in _HARDCODED:
        existing = conn.execute(
            _sql("SELECT 1 FROM script_modules WHERE name = :n"),
            {"n": script["name"]},
        ).scalar()
        if existing:
            logger.info("seed: script_modules row %r already exists — skipping", script["name"])
            continue
        conn.execute(
            _sql(
                "INSERT INTO script_modules (name, description, script_content, script_type, is_active) "
                "VALUES (:n, :d, :c, :t, true)"
            ),
            {
                "n": script["name"],
                "d": script["description"],
                "c": script["script_content"],
                "t": script["script_type"],
            },
        )
        inserted += 1
        logger.info("seed: inserted example script_modules row %r", script["name"])
    logger.info("seed: inserted %d example script(s)", inserted)


def downgrade() -> None:
    pass
