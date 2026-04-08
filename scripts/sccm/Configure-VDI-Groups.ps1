#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Creates XenPool VDI AD groups and assigns them to local groups.
    Intended to run during SCCM Task Sequence setup.

.DESCRIPTION
    - Creates two domain security groups in $orgUnitPath if they do not exist:
        XenPool-VDI-<hostname>-RDP-Users  → added to local "Remote Desktop Users"
        XenPool-VDI-<hostname>-ADM-Users  → added to local "Administrators"
    - Logs all actions to Windows Application Event Log and C:\Windows\debug\

.NOTES
    Requires: Domain-joined machine, rights to create objects in $orgUnitPath
#>

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
$hostname    = $env:COMPUTERNAME
$groupRDP    = "XenPool-VDI-$hostname-RDP-Users"
$groupADM    = "XenPool-VDI-$hostname-ADM-Users"
$orgUnitPath = "OU=VDI,OU=XenPool GmbH,DC=xenpool,DC=local"
$domainDN    = "DC=xenpool,DC=local"
$domainNetBios = "XENPOOL"

$logDir      = "C:\Windows\debug"
$logFile     = Join-Path $logDir "Configure-VDI-Groups.log"
$eventSource = "XenPool-VDI-Setup"
$eventLogName = "Application"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
function Initialize-Logging {
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    # Register custom event source once
    if (-not [System.Diagnostics.EventLog]::SourceExists($eventSource)) {
        try {
            [System.Diagnostics.EventLog]::CreateEventSource($eventSource, $eventLogName)
        } catch {
            # May fail if already registered by another instance; safe to ignore
        }
    }
}

function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO','WARN','ERROR')][string]$Level = 'INFO'
    )

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $entry     = "[$timestamp][$Level] $Message"

    # File log
    Add-Content -Path $logFile -Value $entry -Encoding UTF8

    # Console (visible in SCCM TS log viewer)
    switch ($Level) {
        'WARN'  { Write-Warning $Message }
        'ERROR' { Write-Error   $Message -ErrorAction Continue }
        default { Write-Host    $entry }
    }

    # Windows Event Log
    $evtType = switch ($Level) {
        'WARN'  { [System.Diagnostics.EventLogEntryType]::Warning }
        'ERROR' { [System.Diagnostics.EventLogEntryType]::Error }
        default { [System.Diagnostics.EventLogEntryType]::Information }
    }
    try {
        [System.Diagnostics.EventLog]::WriteEntry($eventSource, $Message, $evtType, 1000)
    } catch {
        Add-Content -Path $logFile -Value "[$timestamp][WARN] Could not write to EventLog: $_" -Encoding UTF8
    }
}

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
function Test-ADGroupExists {
    param([string]$GroupName)

    $searcher        = [adsisearcher]([ADSI]"LDAP://$domainDN")
    $searcher.Filter = "(&(objectClass=group)(sAMAccountName=$GroupName))"
    return ($null -ne $searcher.FindOne())
}

function New-ADSecurityGroup {
    param(
        [string]$GroupName,
        [string]$Description
    )

    try {
        $ouContainer = [ADSI]"LDAP://$orgUnitPath"
        $newGroup    = $ouContainer.Create("group", "CN=$GroupName")
        $newGroup.Put("sAMAccountName", $GroupName)
        $newGroup.Put("description",    $Description)
        $newGroup.Put("groupType", -2147483646)  # ADS_GROUP_TYPE_GLOBAL_GROUP | ADS_GROUP_TYPE_SECURITY_ENABLED
        $newGroup.SetInfo()
        Write-Log "Created AD group '$GroupName' in '$orgUnitPath'."
    } catch {
        Write-Log "Failed to create AD group '$GroupName': $_" -Level ERROR
        throw
    }
}

function Add-DomainGroupToLocalGroup {
    param(
        [string]$DomainGroup,
        [string]$LocalGroup
    )

    $fqGroup = "$domainNetBios\$DomainGroup"

    # Check if already member
    try {
        $localGroupObj = [ADSI]"WinNT://./$LocalGroup,group"
        $members = @($localGroupObj.Invoke("Members") | ForEach-Object {
            $_.GetType().InvokeMember("Name", 'GetProperty', $null, $_, $null)
        })

        if ($members -contains $DomainGroup) {
            Write-Log "Group '$fqGroup' is already a member of local '$LocalGroup'. Skipping."
            return
        }
    } catch {
        Write-Log "Could not enumerate members of '$LocalGroup': $_" -Level WARN
    }

    try {
        $result = & net localgroup $LocalGroup $fqGroup /add 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Added '$fqGroup' to local group '$LocalGroup'."
        } elseif ($result -match "already a member") {
            Write-Log "Group '$fqGroup' already in '$LocalGroup' (net localgroup). Skipping."
        } else {
            Write-Log "net localgroup returned exit code $LASTEXITCODE for '$fqGroup' → '$LocalGroup': $result" -Level WARN
        }
    } catch {
        Write-Log "Failed to add '$fqGroup' to local '$LocalGroup': $_" -Level ERROR
        throw
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Initialize-Logging

Write-Log "=== Configure-VDI-Groups started on '$hostname' ==="

$exitCode = 0

try {
    # ---- RDP group --------------------------------------------------------
    if (Test-ADGroupExists -GroupName $groupRDP) {
        Write-Log "AD group '$groupRDP' already exists. Skipping creation."
    } else {
        New-ADSecurityGroup -GroupName $groupRDP `
            -Description "XenPool VDI RDP users for $hostname"
    }

    Add-DomainGroupToLocalGroup -DomainGroup $groupRDP -LocalGroup "Remote Desktop Users"

    # ---- ADM group --------------------------------------------------------
    if (Test-ADGroupExists -GroupName $groupADM) {
        Write-Log "AD group '$groupADM' already exists. Skipping creation."
    } else {
        New-ADSecurityGroup -GroupName $groupADM `
            -Description "XenPool VDI local administrators for $hostname"
    }

    Add-DomainGroupToLocalGroup -DomainGroup $groupADM -LocalGroup "Administrators"

} catch {
    Write-Log "Unhandled error: $_" -Level ERROR
    $exitCode = 1
}

Write-Log "=== Configure-VDI-Groups finished. Exit code: $exitCode ==="
exit $exitCode
