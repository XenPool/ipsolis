param(
    [Parameter(Mandatory=$true)][string]$VMName
)

# SCCM - Delete Device
# Thin wrapper around the Python Admin Service helper (NTLM).
# The helper reads connection config from app_config (sccm.base_url, sccm.username,
# sccm.password, sccm.verify_tls, sccm.site_code). No credentials in this script.
#
# Behaviour (mirrors the legacy "Get-CMDevice | Select -Last 1 | Remove-CMDevice"):
#   - Looks up SMS_R_System by Name via the Admin Service
#   - If zero matches            → success, deleted=0
#   - If exactly one match       → DELETE SMS_R_System(ResourceID), deleted=1
#   - If multiple matches        → aborts (the legacy "-Last 1" behaviour is unsafe
#                                  for a recycling runbook and would silently leave
#                                  duplicates around). Surface the problem instead.

if ([string]::IsNullOrWhiteSpace($VMName)) {
    Write-Output (@{ success = $false; error = "VMName is empty" } | ConvertTo-Json -Compress)
    exit 1
}

$json = python /app/tasks/utils/sccm_admin.py delete-device --name "$VMName"
$exit = $LASTEXITCODE

# Pass the helper's JSON straight through so the UI log stays useful.
Write-Output $json

if ($exit -ne 0) { exit $exit }

try {
    $parsed = $json | ConvertFrom-Json
    $global:SCCMDeleteResourceID = $parsed.resource_id
    $global:SCCMDeleteCount      = $parsed.deleted
} catch {
    # helper already returned a JSON error and non-zero exit — nothing else to do
}
