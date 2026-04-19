"""Seed SCCM script_modules and add Delete Device to Virtual Machine Recycler

Revision ID: 0039
Revises: 0038
Create Date: 2026-04-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DELETE_DEVICE_SCRIPT = r"""param(
    [Parameter(Mandatory=$true)][string]$VMName
)

if ([string]::IsNullOrWhiteSpace($VMName)) {
    Write-Output (@{ success = $false; error = "VMName is empty" } | ConvertTo-Json -Compress)
    exit 1
}

$json = python /app/tasks/utils/sccm_admin.py delete-device --name "$VMName"
$exit = $LASTEXITCODE

Write-Output $json
if ($exit -ne 0) { exit $exit }

try {
    $parsed = $json | ConvertFrom-Json
    $global:SCCMDeleteResourceID = $parsed.resource_id
    $global:SCCMDeleteCount      = $parsed.deleted
} catch { }
"""


IMPORT_DEVICE_SCRIPT = r"""param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [Parameter(Mandatory=$true)][string]$MACAddress,
    [Parameter(Mandatory=$true)][string]$SCCMGuiD,
    [string]$AppCollectionIDs = "",
    [int]$ResourceIdRetries = 60
)

$args = @(
    "import-machine",
    "--name",                $VMName,
    "--os-collection",       $OSCollectionID,
    "--mac",                 $MACAddress,
    "--guid",                $SCCMGuiD,
    "--resource-id-retries", "$ResourceIdRetries"
)

if (-not [string]::IsNullOrWhiteSpace($AppCollectionIDs)) {
    $normalised = ($AppCollectionIDs -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ','
    if ($normalised) {
        $args += @("--app-collections", $normalised)
    }
}

$json = python /app/tasks/utils/sccm_admin.py @args
$exit = $LASTEXITCODE

Write-Output $json
if ($exit -ne 0) { exit $exit }

try {
    $parsed = $json | ConvertFrom-Json
    $global:SCCMResourceID     = $parsed.resource_id
    $global:SCCMImportStatus   = $parsed.status
    $global:SCCMAppCollections = $parsed.app_collections
} catch { }
"""


WAIT_TS_SCRIPT = r"""param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [int]$TimeoutMinutes = 360,
    [int]$PollSeconds = 60
)

$json = python /app/tasks/utils/sccm_admin.py wait-task-sequence `
    --name "$VMName" `
    --os-collection "$OSCollectionID" `
    --timeout-minutes "$TimeoutMinutes" `
    --poll-seconds "$PollSeconds"
$exit = $LASTEXITCODE

Write-Output $json
if ($exit -ne 0) { exit $exit }

try {
    $parsed = $json | ConvertFrom-Json
    $global:SCCMLastStatus     = $parsed.status_description
    $global:TaskSequenceResult = $parsed.result
    $global:DeploymentID       = $parsed.deployment_id
} catch { }
"""


DELETE_DEVICE_PARAMS = '[{"name":"VMName","type":"string","required":true}]'

IMPORT_DEVICE_PARAMS = (
    '[{"name":"VMName","type":"string","required":true},'
    '{"name":"OSCollectionID","type":"string","required":true},'
    '{"name":"MACAddress","type":"string","required":true},'
    '{"name":"SCCMGuiD","type":"string","required":true},'
    '{"name":"AppCollectionIDs","type":"string","required":false,"default":""},'
    '{"name":"ResourceIdRetries","type":"int","required":false,"default":"60"}]'
)

WAIT_TS_PARAMS = (
    '[{"name":"VMName","type":"string","required":true},'
    '{"name":"OSCollectionID","type":"string","required":true},'
    '{"name":"TimeoutMinutes","type":"int","required":false,"default":"360"},'
    '{"name":"PollSeconds","type":"int","required":false,"default":"60"}]'
)


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text("""
            INSERT INTO script_modules (name, description, script_content, script_type, param_schema, is_active)
            VALUES
              (:n1, :d1, :s1, 'powershell', CAST(:p1 AS jsonb), true),
              (:n2, :d2, :s2, 'powershell', CAST(:p2 AS jsonb), true),
              (:n3, :d3, :s3, 'powershell', CAST(:p3 AS jsonb), true)
            ON CONFLICT (name) DO NOTHING
        """),
        {
            "n1": "SCCM - Delete Device",
            "d1": "Deletes a device from SCCM via the Admin Service (NTLM). "
                  "Aborts if the name resolves to multiple devices.",
            "s1": DELETE_DEVICE_SCRIPT,
            "p1": DELETE_DEVICE_PARAMS,
            "n2": "SCCM - Import Device and Assign Collections",
            "d2": "Imports a device into SCCM (MAC+GUID) via the Admin Service, adds it to the "
                  "OS deployment collection and any optional app collections, then triggers refreshes.",
            "s2": IMPORT_DEVICE_SCRIPT,
            "p2": IMPORT_DEVICE_PARAMS,
            "n3": "SCCM - Wait for Task Sequence",
            "d3": "Polls per-device deployment status until the task sequence completes, fails, "
                  "or the timeout elapses. Uses SMS status messages for log-compatible descriptions.",
            "s3": WAIT_TS_SCRIPT,
            "p3": WAIT_TS_PARAMS,
        },
    )

    # Append "SCCM - Delete Device" as next step of the "Virtual Machine Recycler" runbook.
    # params_template carries the VMName exported by step 1 (global var RecycleVmName).
    conn.execute(
        sa.text("""
            INSERT INTO standalone_runbook_steps
                (runbook_id, position, step_name, script_module_id, params_template,
                 is_critical, retry_count, timeout_seconds)
            SELECT
                rb.id,
                COALESCE((SELECT MAX(position) FROM standalone_runbook_steps WHERE runbook_id = rb.id), 0) + 1,
                'SCCM - Delete Device',
                sm.id,
                CAST('{"VMName": "{{RecycleVmName}}"}' AS json),
                true, 3, 120
            FROM standalone_runbooks rb
            CROSS JOIN script_modules sm
            WHERE rb.name = 'Virtual Machine Recycler'
              AND sm.name = 'SCCM - Delete Device'
              AND NOT EXISTS (
                  SELECT 1 FROM standalone_runbook_steps s
                  WHERE s.runbook_id = rb.id AND s.script_module_id = sm.id
              )
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            DELETE FROM standalone_runbook_steps
            WHERE script_module_id IN (
                SELECT id FROM script_modules WHERE name IN (
                    'SCCM - Delete Device',
                    'SCCM - Import Device and Assign Collections',
                    'SCCM - Wait for Task Sequence'
                )
            )
        """)
    )
    conn.execute(
        sa.text("""
            DELETE FROM script_modules WHERE name IN (
                'SCCM - Delete Device',
                'SCCM - Import Device and Assign Collections',
                'SCCM - Wait for Task Sequence'
            )
        """)
    )
