param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OU_DN
)

# AD - Create Computeraccount
# Creates a disabled computer account named $VMName under $OU_DN in Active Directory.
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
    if ([string]::IsNullOrWhiteSpace($OU_DN)) {
        Write-Output (@{ success = $false; error = 'OU_DN is empty' } | ConvertTo-Json -Compress)
        exit 1
    }

    Write-Log "Creating computer account '$VMName' under '$OU_DN'..." 'INFO'

    $json = python /app/scripts/ad/ad_create_computer.py $VMName $OU_DN 2>&1
    $exit = $LASTEXITCODE
    $text = if ($json -is [array]) { $json -join "`n" } else { [string]$json }

    try { $result = $text | ConvertFrom-Json } catch {
        Write-Log "Helper returned non-JSON output (exit $exit): $text" 'ERROR'
        Write-Output (@{ success = $false; error = "ad_create_computer.py: non-JSON output (exit $exit)"; raw = $text } | ConvertTo-Json -Compress)
        exit 1
    }

    if ($result.success) {
        $global:ADCreateDN    = $result.dn
        $global:ADCreateCount = [int]$result.created
        if ([int]$result.created -eq 0) {
            Write-Log "Computer account '$VMName' already exists at $($result.dn) (treated as success)." 'WARNING'
        } else {
            Write-Log "Created computer account: $($result.dn)" 'SUCCESS'
        }
    } else {
        Write-Log "AD create failed: $($result.error)" 'ERROR'
    }

    Write-Output ($result | ConvertTo-Json -Compress)
    if (-not $result.success) { exit 1 }
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
