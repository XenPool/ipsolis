# NAME: Example - Deprovision Asset
# DESC: Example module: log deprovisioning context and signal success. Use as a starting point for real deprovision steps.
param(
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

    # ── Add your deprovisioning logic here ────────────────────────────────────
    # Examples:
    #   - Remove user from AD group
    #   - Snapshot VM before recycle
    #   - Delete SCCM device record
    #   - Revoke RDP permissions
    #   - Send return confirmation email
    # All global variables (e.g. server addresses) are available via $VARS:
    #   $myServer = $VARS.'my.server.host'
    # ──────────────────────────────────────────────────────────────────────────

    Write-Log "Deprovision step completed successfully."
    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)
}
catch {
    Write-Log "Deprovision step failed: $($_.Exception.Message)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
