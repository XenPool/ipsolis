<#
.SYNOPSIS
    Adds domain local group to Remote Desktop Users group on VDI machines

.DESCRIPTION
    Automatically adds the domain local group AUDI-0200-L-AUDI-<COMPUTERNAME>-VDIRDP 
    to the local Remote Desktop Users group, enabling RDP access for authorized users.
    
    All operations are logged to a single Event Log entry for easy monitoring.

.NOTES
    Author: VDI SelfService Team I/FI-B6
    Version: 2.0
    Last Modified: 2026-01-15
    
.EXAMPLE
    .\VDIGroup2LocRDPGroup.ps1
    Adds AUDI-0200-L-AUDI-<COMPUTERNAME>-VDIRDP to Remote Desktop Users
#>

#region Configuration
$ErrorActionPreference = "Stop"

$config = @{
    Domain                  = "AUDI"
    DomainGroupTemplate     = "AUDI-0200-L-AUDI-{COMPUTERNAME}-VDIRDP"
    RemoteDesktopUsersSID   = "S-1-5-32-555"
    EventSource             = "VDIGroup2LocRDPGroup"
    EventLogName            = "Application"
}

# Initialize script variables
$script:ComputerName = $env:COMPUTERNAME.ToUpper()
$script:DomainGroupName = $config.DomainGroupTemplate -replace '\{COMPUTERNAME\}', $script:ComputerName
$script:LogBuffer = @()  # Collects all log messages for single Event Log entry
$script:HasErrors = $false
#endregion

#region Logging Functions
function Initialize-EventLogSource {
    <#
    .SYNOPSIS
    Creates Event Log source if it doesn't exist
    #>
    try {
        if (-not [System.Diagnostics.EventLog]::SourceExists($config.EventSource)) {
            New-EventLog -LogName $config.EventLogName -Source $config.EventSource -ErrorAction Stop
        }
        return $true
    } catch {
        Write-Host "WARNING: Could not create Event Log source: $_" -ForegroundColor Yellow
        return $false
    }
}

function Add-LogEntry {
    <#
    .SYNOPSIS
    Adds a message to the log buffer (for later Event Log write)
    
    .PARAMETER Message
    Log message text
    
    .PARAMETER Level
    Log level (INFO, WARNING, ERROR, SUCCESS)
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        
        [Parameter(Mandatory = $false)]
        [ValidateSet('INFO', 'WARNING', 'ERROR', 'SUCCESS')]
        [string]$Level = 'INFO'
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [$Level] $Message"
    
    # Add to buffer
    $script:LogBuffer += $logEntry
    
    # Track errors
    if ($Level -eq 'ERROR') {
        $script:HasErrors = $true
    }
    
    # Console output with color
    switch ($Level) {
        'ERROR'   { Write-Host $logEntry -ForegroundColor Red }
        'WARNING' { Write-Host $logEntry -ForegroundColor Yellow }
        'SUCCESS' { Write-Host $logEntry -ForegroundColor Green }
        default   { Write-Host $logEntry }
    }
}

function Write-EventLogSummary {
    <#
    .SYNOPSIS
    Writes all buffered log messages as a single Event Log entry
    #>
    try {
        if ($script:LogBuffer.Count -eq 0) {
            return
        }
        
        # Combine all log messages
        $fullMessage = $script:LogBuffer -join "`r`n"
        
        # Determine Event Type and ID
        if ($script:HasErrors) {
            $eventType = [System.Diagnostics.EventLogEntryType]::Error
            $eventId = 300
        } else {
            $eventType = [System.Diagnostics.EventLogEntryType]::Information
            $eventId = 100
        }
        
        # Add summary header
        $summary = @"
=== VDI Group Management Summary ===
Computer: $($script:ComputerName)
Domain Group: $($script:DomainGroupName)
Status: $(if ($script:HasErrors) { 'FAILED' } else { 'SUCCESS' })
Timestamp: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

=== Detailed Log ===
$fullMessage
"@
        
        # Write single Event Log entry
        [System.Diagnostics.EventLog]::WriteEntry(
            $config.EventSource,
            $summary,
            $eventType,
            $eventId
        )
        
        Write-Host "`nEvent Log entry written (Event ID: ${eventId})" -ForegroundColor Cyan
        
    } catch {
        Write-Host "WARNING: Failed to write Event Log: $_" -ForegroundColor Yellow
    }
}
#endregion

#region Active Directory Functions
function Get-ADRootPath {
    <#
    .SYNOPSIS
    Retrieves the Active Directory root path
    #>
    try {
        Add-LogEntry "Retrieving AD root path..." -Level "INFO"
        
        $rootDSE = [ADSI]"LDAP://rootDSE"
        $rootPath = $rootDSE.defaultNamingContext
        
        if ([string]::IsNullOrEmpty($rootPath)) {
            throw "Root path is empty"
        }
        
        Add-LogEntry "AD Root Path: $rootPath" -Level "SUCCESS"
        return $rootPath
        
    } catch {
        Add-LogEntry "Failed to retrieve AD root path: $_" -Level "ERROR"
        return ""
    }
}

function Test-DomainGroupExists {
    <#
    .SYNOPSIS
    Validates if a domain group exists using ADSI
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$DomainName,
        
        [Parameter(Mandatory = $true)]
        [string]$GroupName
    )
    
    try {
        Add-LogEntry "Verifying domain group: ${GroupName}..." -Level "INFO"
        
        $groupPath = "WinNT://${DomainName}/${GroupName},group"
        $group = [ADSI]$groupPath
        
        if ($group -and $group.Class -eq "Group") {
            Add-LogEntry "Domain group exists: ${GroupName}" -Level "SUCCESS"
            return $true
        } else {
            Add-LogEntry "Domain group not found: ${GroupName}" -Level "ERROR"
            return $false
        }
        
    } catch {
        Add-LogEntry "Error checking domain group: $_" -Level "ERROR"
        return $false
    }
}
#endregion

#region Local Group Functions
function Get-LocalizedRDPGroupName {
    <#
    .SYNOPSIS
    Detects the localized name of Remote Desktop Users group using well-known SID
    #>
    try {
        Add-LogEntry "Detecting localized Remote Desktop Users group..." -Level "INFO"
        
        $accounts = Get-WmiObject -Class Win32_Account -Filter "LocalAccount = True" -ErrorAction Stop
        
        foreach ($account in $accounts) {
            if ($account.SID -eq $config.RemoteDesktopUsersSID) {
                Add-LogEntry "Found localized RDP group: $($account.Name)" -Level "SUCCESS"
                return $account.Name
            }
        }
        
        # Fallback to default English name
        Add-LogEntry "Could not detect localized name, using default: Remote Desktop Users" -Level "WARNING"
        return "Remote Desktop Users"
        
    } catch {
        Add-LogEntry "Error detecting RDP group name: $_" -Level "WARNING"
        return "Remote Desktop Users"
    }
}

function Get-LocalGroupMembers {
    <#
    .SYNOPSIS
    Retrieves members of a local group
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$ComputerName,
        
        [Parameter(Mandatory = $true)]
        [string]$GroupName
    )
    
    try {
        $members = @()
        $localGroup = [ADSI]"WinNT://${ComputerName}/${GroupName},group"
        
        foreach ($member in $localGroup.psbase.Invoke("Members")) {
            $memberName = $member.GetType().InvokeMember("Name", 'GetProperty', $null, $member, $null)
            $members += $memberName
        }
        
        Add-LogEntry "Current members of ${GroupName}: $($members.Count)" -Level "INFO"
        foreach ($member in $members) {
            Add-LogEntry "  - ${member}" -Level "INFO"
        }
        
        return $members
        
    } catch {
        Add-LogEntry "Error retrieving group members: $_" -Level "WARNING"
        return @()
    }
}

function Test-GroupMembership {
    <#
    .SYNOPSIS
    Checks if a group is already a member of another group
    #>
    param (
        [Parameter(Mandatory = $true)]
        $LocalGroup,
        
        [Parameter(Mandatory = $true)]
        [string]$MemberGroupName
    )
    
    try {
        foreach ($member in $LocalGroup.psbase.Invoke("Members")) {
            $memberName = $member.GetType().InvokeMember("Name", 'GetProperty', $null, $member, $null)
            
            if ($memberName -eq $MemberGroupName) {
                return $true
            }
        }
        
        return $false
        
    } catch {
        Add-LogEntry "Error checking group membership: $_" -Level "WARNING"
        return $false
    }
}

function Add-DomainGroupToLocalGroup {
    <#
    .SYNOPSIS
    Adds a domain group to a local group
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$ComputerName,
        
        [Parameter(Mandatory = $true)]
        [string]$LocalGroupName,
        
        [Parameter(Mandatory = $true)]
        [string]$DomainName,
        
        [Parameter(Mandatory = $true)]
        [string]$DomainGroupName
    )
    
    try {
        Add-LogEntry "Preparing to add domain group to local group..." -Level "INFO"
        
        # Get ADSI objects
        $localGroup = [ADSI]"WinNT://${ComputerName}/${LocalGroupName},group"
        $domainGroup = [ADSI]"WinNT://${DomainName}/${DomainGroupName},group"
        
        # Check if already a member
        Add-LogEntry "Checking if ${DomainGroupName} is already a member of ${LocalGroupName}..." -Level "INFO"
        
        $isMember = Test-GroupMembership -LocalGroup $localGroup -MemberGroupName $DomainGroupName
        
        if ($isMember) {
            Add-LogEntry "${DomainGroupName} is already a member of ${LocalGroupName} - No action needed" -Level "INFO"
            return $true
        }
        
        # Add group
        Add-LogEntry "Adding ${DomainGroupName} to ${LocalGroupName}..." -Level "INFO"
        
        $localGroup.Add($domainGroup.Path)
        
        Add-LogEntry "Successfully added ${DomainGroupName} to ${LocalGroupName}" -Level "SUCCESS"
        
        return $true
        
    } catch {
        Add-LogEntry "Failed to add ${DomainGroupName} to ${LocalGroupName}: $_" -Level "ERROR"
        return $false
    }
}
#endregion

#region Main Execution
function Main {
    <#
    .SYNOPSIS
    Main script execution flow
    #>
    try {
        Add-LogEntry "=== VDI Group Management Script Started ===" -Level "INFO"
        Add-LogEntry "Computer: $($script:ComputerName)" -Level "INFO"
        Add-LogEntry "Target Domain Group: $($script:DomainGroupName)" -Level "INFO"
        
        # Step 1: Verify AD connectivity
        Add-LogEntry "Step 1: Verifying Active Directory connectivity..." -Level "INFO"
        $adRootPath = Get-ADRootPath
        
        if ([string]::IsNullOrEmpty($adRootPath)) {
            Add-LogEntry "Cannot proceed: Active Directory is not accessible" -Level "ERROR"
            return $false
        }
        
        # Step 2: Verify domain group exists
        Add-LogEntry "Step 2: Verifying domain group exists..." -Level "INFO"
        $groupExists = Test-DomainGroupExists -DomainName $config.Domain -GroupName $script:DomainGroupName
        
        if (-not $groupExists) {
            Add-LogEntry "Cannot proceed: Domain group ${script:DomainGroupName} does not exist in domain $($config.Domain)" -Level "ERROR"
            return $false
        }
        
        # Step 3: Detect localized Remote Desktop Users group
        Add-LogEntry "Step 3: Detecting localized Remote Desktop Users group..." -Level "INFO"
        $localizedRDPGroupName = Get-LocalizedRDPGroupName
        
        Add-LogEntry "Using local group: ${localizedRDPGroupName}" -Level "INFO"
        
        # Step 4: List current members and check for existing membership
        Add-LogEntry "Step 4: Listing current members of ${localizedRDPGroupName}..." -Level "INFO"
        $currentMembers = Get-LocalGroupMembers -ComputerName $script:ComputerName -GroupName $localizedRDPGroupName
        
        # Pre-check membership
        if ($currentMembers -contains $script:DomainGroupName) {
            Add-LogEntry "Pre-check: ${script:DomainGroupName} already exists in ${localizedRDPGroupName}" -Level "SUCCESS"
            Add-LogEntry "=== Group Management Completed (No Changes Needed) ===" -Level "SUCCESS"
            return $true
        }
        
        Add-LogEntry "Pre-check: ${script:DomainGroupName} needs to be added" -Level "INFO"
        
        # Step 5: Add domain group to local group
        Add-LogEntry "Step 5: Adding domain group to local RDP group..." -Level "INFO"
        $success = Add-DomainGroupToLocalGroup -ComputerName $script:ComputerName `
                                               -LocalGroupName $localizedRDPGroupName `
                                               -DomainName $config.Domain `
                                               -DomainGroupName $script:DomainGroupName
        
        if ($success) {
            Add-LogEntry "=== Group Management Completed Successfully ===" -Level "SUCCESS"
            return $true
        } else {
            Add-LogEntry "=== Group Management Failed ===" -Level "ERROR"
            return $false
        }
        
    } catch {
        Add-LogEntry "Critical error in main execution: $_" -Level "ERROR"
        Add-LogEntry "Stack trace: $($_.ScriptStackTrace)" -Level "ERROR"
        return $false
    }
}

# Script entry point
try {
    # Initialize Event Log source
    $eventLogReady = Initialize-EventLogSource
    
    if (-not $eventLogReady) {
        Write-Host "WARNING: Event Log source not available - continuing anyway" -ForegroundColor Yellow
    }
    
    # Execute main process
    $result = Main
    
    # Write single Event Log entry with all logs
    Write-EventLogSummary
    
    # Exit with appropriate code
    if ($result) {
        Write-Host "`nScript completed successfully" -ForegroundColor Green
        exit 0
    } else {
        Write-Host "`nScript completed with errors" -ForegroundColor Red
        exit 1
    }
    
} catch {
    Add-LogEntry "CRITICAL UNHANDLED ERROR: $_" -Level "ERROR"
    Write-EventLogSummary
    exit 1
}
#endregion