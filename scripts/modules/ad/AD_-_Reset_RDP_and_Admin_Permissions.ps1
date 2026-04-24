# NAME: AD - Reset RDP and Admin Permissions
# DESC: Remove all members from the RDP and Admin domain groups (reset VDI access on recycle).
param(
    [Parameter(Mandatory=$true)][string]$RdpGroupDN,
    [Parameter(Mandatory=$true)][string]$AdminGroupDN
)

# AD - Reset RDP and Admin Permissions
# Empties both domain groups by removing every `member` value.
# Group DNs are configured per step in the runbook editor.

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] [$Level] $Message"
}

try {
    if ([string]::IsNullOrWhiteSpace($RdpGroupDN) -or [string]::IsNullOrWhiteSpace($AdminGroupDN)) {
        Write-Output (@{ success = $false; error = 'RdpGroupDN and AdminGroupDN are both required' } | ConvertTo-Json -Compress)
        exit 1
    }

    Write-Log "Resetting RDP group   : $RdpGroupDN" 'INFO'
    Write-Log "Resetting Admin group : $AdminGroupDN" 'INFO'

    $json = python /app/scripts/ad/ad_clear_group_members.py $RdpGroupDN $AdminGroupDN 2>&1
    $exit = $LASTEXITCODE
    $text = if ($json -is [array]) { $json -join "`n" } else { [string]$json }

    try { $result = $text | ConvertFrom-Json } catch {
        Write-Log "Helper returned non-JSON output (exit $exit): $text" 'ERROR'
        Write-Output (@{ success = $false; error = "ad_clear_group_members.py: non-JSON output (exit $exit)"; raw = $text } | ConvertTo-Json -Compress)
        exit 1
    }

    if ($result.groups) {
        foreach ($g in $result.groups) {
            if ($g.success) {
                Write-Log "Cleared $($g.group_dn): removed $($g.removed) member(s)" 'SUCCESS'
            } else {
                Write-Log "Failed $($g.group_dn): $($g.error)" 'ERROR'
            }
        }
    }

    $global:ADResetTotalRemoved = [int]($result.total_removed)
    $global:ADResetRdpGroup     = $RdpGroupDN
    $global:ADResetAdminGroup   = $AdminGroupDN

    Write-Output ($result | ConvertTo-Json -Compress -Depth 6)
    if (-not $result.success) { exit 1 }
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
