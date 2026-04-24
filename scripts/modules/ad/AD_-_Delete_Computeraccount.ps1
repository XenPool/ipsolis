# NAME: AD - Delete Computeraccount
# DESC: Delete the Active Directory computer account for a given VM name.
param(
    [Parameter(Mandatory=$true)][string]$VMName
)

# AD - Delete Computeraccount
# Deletes the computer account named $VMName from Active Directory.
# Thin PowerShell wrapper around the msldap-backed Python helper.

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] [$Level] $Message"
}

try {
    if ([string]::IsNullOrWhiteSpace($VMName)) {
        Write-Output (@{ success = $false; error = 'VMName is empty' } | ConvertTo-Json -Compress)
        exit 1
    }

    Write-Log "Deleting computer account '$VMName' from Active Directory..." 'INFO'

    $json = python /app/scripts/ad/ad_delete_computer.py $VMName 2>&1
    $exit = $LASTEXITCODE
    $text = if ($json -is [array]) { $json -join "`n" } else { [string]$json }

    try { $result = $text | ConvertFrom-Json } catch {
        Write-Log "Helper returned non-JSON output (exit $exit): $text" 'ERROR'
        Write-Output (@{ success = $false; error = "ad_delete_computer.py: non-JSON output (exit $exit)"; raw = $text } | ConvertTo-Json -Compress)
        exit 1
    }

    if ($result.success) {
        $global:ADDeleteDN    = $result.dn
        $global:ADDeleteCount = [int]$result.deleted
        if ([int]$result.deleted -eq 0) {
            Write-Log "No computer account named '$VMName' found in AD (treated as success)." 'WARNING'
        } else {
            Write-Log "Deleted computer account: $($result.dn)" 'SUCCESS'
        }
    } else {
        Write-Log "AD delete failed: $($result.error)" 'ERROR'
    }

    Write-Output ($result | ConvertTo-Json -Compress)
    if (-not $result.success) { exit 1 }
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
