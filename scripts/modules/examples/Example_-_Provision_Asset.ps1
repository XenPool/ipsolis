# NAME: Example - Provision Asset
# DESC: Example module: log provisioning context and signal success. Use as a starting point for real provision steps.
param(
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

    # ── Add your provisioning logic here ──────────────────────────────────────
    # Examples:
    #   - Create VM snapshot / clone
    #   - Import device into SCCM
    #   - Add user to AD group
    #   - Send welcome notification
    # All global variables (e.g. server addresses) are available via $VARS:
    #   $myServer = $VARS.'my.server.host'
    # ──────────────────────────────────────────────────────────────────────────

    Write-Log "Provision step completed successfully."
    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)
}
catch {
    Write-Log "Provision step failed: $($_.Exception.Message)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
