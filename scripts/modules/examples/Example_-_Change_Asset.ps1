# NAME: Example - Change Asset
# DESC: Example module: log change-request context and signal success. Use as a starting point for real change steps.
param(
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

    # ── Add your change logic here ─────────────────────────────────────────────
    # Examples:
    #   - Update AD group membership
    #   - Adjust VM resource allocation
    #   - Notify previous owner / new owner
    #   - Update CMDB record
    # All global variables (e.g. server addresses) are available via $VARS:
    #   $myServer = $VARS.'my.server.host'
    # ──────────────────────────────────────────────────────────────────────────

    Write-Log "Change step completed successfully."
    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)
}
catch {
    Write-Log "Change step failed: $($_.Exception.Message)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
