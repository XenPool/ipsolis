# VMWare - VM read SCCMGuiD - store in dbo.VDIPool
# Retrieves the SCCM GUID (UUID) from a VM and stores it in the VDI Pool database

#region Logging Function
function Write-Log {
    param(
        [string]$Message,
        [ValidateSet('INFO', 'WARNING', 'ERROR', 'SUCCESS')]
        [string]$Level = 'INFO'
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] [$Level] $Message"
    
    switch ($Level) {
        'ERROR'   { Write-Host $logMessage -ForegroundColor Red }
        'WARNING' { Write-Host $logMessage -ForegroundColor Yellow }
        'SUCCESS' { Write-Host $logMessage -ForegroundColor Green }
        default   { Write-Host $logMessage }
    }
}
#endregion

#region Configuration
Write-Log "=== Starting SCCM GUID Read and Store Script ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Centralized configuration
$config = @{
    # vSphere settings
    vSphere = @{
        ServerHost = "^[vSphereServerHost]"
        AdminUser  = "^[vSphereServerAdminUser]"
        AdminPW    = '^[vSphereServerAdminPW]'
    }
    
    # SQL Server settings
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        VDIPoolTable   = "^[SQLVDIPoolTable]"
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Table: $($config.SQL.VDIPoolTable)" 'INFO'
#endregion

#region Function: Get vSphere VM UUID in SCCM Format
function Get-vSphereVMUUID {
    <#
    .SYNOPSIS
    Retrieves the UUID from a specified VM and formats it correctly for use with MDT/SCCM
    
    .DESCRIPTION
    Retrieves the UUID from a specified VM and formats it correctly for use with MDT/SCCM.
    Returns the UUID as a string in the format expected by SCCM.
    The UUID is transposed from vSphere format to SCCM/BIOS format.
    
    .PARAMETER VM
    Specifies the VM object to retrieve the UUID from
    
    .RETURNS
    String containing the formatted UUID for SCCM/MDT
    
    .EXAMPLE
    PS C:\> Get-vSphereVMUUID -VM (Get-VM "W7VM1")
    Retrieves the UUID from a VM named W7VM1
    
    .NOTES
    The vSphere UUID needs to be transposed because VMware stores it in a different
    byte order than what SCCM expects (little-endian vs big-endian for certain sections)
    #>
    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
        [ValidateNotNull()]
        $VM
    )
    
    begin {
        Write-Log "Starting UUID retrieval and conversion..." 'INFO'
    }
    
    process {
        try {
            # Retrieve raw UUID from vSphere
            Write-Log "Retrieving raw UUID from VM: $($VM.Name)..." 'INFO'
            $rawUUID = (Get-View $VM.Id).Config.Uuid
            
            if ([string]::IsNullOrWhiteSpace($rawUUID)) {
                Write-Log "Raw UUID is empty or null" 'ERROR'
                throw "Failed to retrieve UUID from VM"
            }
            
            Write-Log "Raw UUID retrieved: ${rawUUID}" 'INFO'
            
            # Transpose UUID into SCCM expected format
            Write-Log "Converting UUID to SCCM/BIOS format..." 'INFO'
            
            # Section 1 (bytes 0-3, reversed)
            $section1_1 = $rawUUID.Substring(0, 2)
            $section1_2 = $rawUUID.Substring(2, 2)
            $section1_3 = $rawUUID.Substring(4, 2)
            $section1_4 = $rawUUID.Substring(6, 2)
            
            # Section 2 (bytes 4-5, reversed)
            $section2_1 = $rawUUID.Substring(9, 2)
            $section2_2 = $rawUUID.Substring(11, 2)
            
            # Section 3 (bytes 6-7, reversed)
            $section3_1 = $rawUUID.Substring(14, 2)
            $section3_2 = $rawUUID.Substring(16, 2)
            
            # Section 4 (bytes 8-9, not reversed)
            $section4 = $rawUUID.Substring(19, 4)
            
            # Section 5 (bytes 10-15, not reversed)
            $section5 = $rawUUID.Substring(24, 12)
            
            # Piece the sections together in SCCM format (little-endian for first 3 sections)
            $formattedUUID = "$section1_4$section1_3$section1_2$section1_1-$section2_2$section2_1-$section3_2$section3_1-$section4-$section5"
            
            Write-Log "UUID converted successfully" 'SUCCESS'
            Write-Log "SCCM-formatted UUID: ${formattedUUID}" 'INFO'
            
            return $formattedUUID
            
        } catch {
            Write-Log "Failed to retrieve or convert UUID: $_" 'ERROR'
            throw
        }
    }
    
    end {
        Write-Log "UUID retrieval and conversion completed" 'INFO'
    }
}
#endregion

#region Function: Update SCCM GUID in Database
function Update-SCCMGuidInDatabase {
    <#
    .SYNOPSIS
    Updates the SCCM GUID in the VDI Pool database table
    
    .PARAMETER VMName
    The name of the VM
    
    .PARAMETER SCCMGuid
    The SCCM GUID to store
    
    .PARAMETER Config
    Configuration object containing SQL settings
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [string]$SCCMGuid,
        [Parameter(Mandatory = $true)]
        [hashtable]$Config
    )
    
    try {
        Write-Log "Preparing to update SCCM GUID in database..." 'INFO'
        
        # Import SQLServer module
        Write-Log "Loading SQLServer module..." 'INFO'
        try {
            Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
            Write-Log "SQLServer module loaded successfully" 'SUCCESS'
        } catch {
            Write-Log "Failed to load SQLServer module: $_" 'ERROR'
            throw "SQLServer module not available. Please install it using: Install-Module -Name SqlServer"
        }
        
        # Validate GUID format (basic validation)
        if ($SCCMGuid -notmatch '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$') {
            Write-Log "Warning: SCCM GUID format appears invalid: ${SCCMGuid}" 'WARNING'
            Write-Log "Expected format: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX" 'WARNING'
        }
        
        # Build SQL query with parameterized values for safety
        Write-Log "Building SQL UPDATE query..." 'INFO'
        $query = "UPDATE $($Config.SQL.VDIPoolTable)
SET SCCMGuiD = @SCCMGuid
WHERE VMName = @VMName"
        
        Write-Log "SQL Query prepared:" 'INFO'
        Write-Log "  Table: $($Config.SQL.VDIPoolTable)" 'INFO'
        Write-Log "  VMName: ${VMName}" 'INFO'
        Write-Log "  SCCMGuid: ${SCCMGuid}" 'INFO'
        
        # Execute SQL query with parameterized values
        Write-Log "Executing SQL query..." 'INFO'
        
        try {
            # Execute UPDATE query (no result needed for UPDATE statements)
            Invoke-Sqlcmd -Query $query `
                         -ServerInstance $Config.SQL.ServerInstance `
                         -Database $Config.SQL.Database `
                         -Username $Config.SQL.LoginUser `
                         -Password $Config.SQL.LoginPW `
                         -Variable @("SCCMGuid='$SCCMGuid'", "VMName='$VMName'") `
                         -ErrorAction Stop | Out-Null
            
            Write-Log "SQL query executed successfully" 'SUCCESS'
            
            # Verify the update
            Write-Log "Verifying database update..." 'INFO'
            $verifyQuery = "SELECT SCCMGuiD FROM $($Config.SQL.VDIPoolTable) WHERE VMName = @VMName"
            
            $verifyResult = Invoke-Sqlcmd -Query $verifyQuery `
                                          -ServerInstance $Config.SQL.ServerInstance `
                                          -Database $Config.SQL.Database `
                                          -Username $Config.SQL.LoginUser `
                                          -Password $Config.SQL.LoginPW `
                                          -Variable "VMName='$VMName'" `
                                          -ErrorAction Stop
            
            if ($verifyResult -and $verifyResult.SCCMGuiD -eq $SCCMGuid) {
                Write-Log "Database update verified successfully" 'SUCCESS'
                Write-Log "Stored SCCM GUID: $($verifyResult.SCCMGuiD)" 'INFO'
            } else {
                Write-Log "Database update verification warning: Stored value may differ" 'WARNING'
                if ($verifyResult) {
                    Write-Log "Database value: $($verifyResult.SCCMGuiD)" 'WARNING'
                } else {
                    Write-Log "No record found for VM: ${VMName}" 'WARNING'
                }
            }
            
            return $true
            
        } catch {
            Write-Log "SQL query execution failed: $_" 'ERROR'
            throw
        }
        
    } catch {
        Write-Log "Failed to update SCCM GUID in database: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Main Execution
try {
    # Create vSphere credentials
    Write-Log "Preparing vSphere connection credentials..." 'INFO'
    $securePassword = ConvertTo-SecureString $config.vSphere.AdminPW -AsPlainText -Force
    $credential = New-Object System.Management.Automation.PSCredential($config.vSphere.AdminUser, $securePassword)
    Write-Log "Credentials prepared for user: $($config.vSphere.AdminUser)" 'SUCCESS'
    
    # Connect to vSphere server
    Write-Log "Connecting to vSphere server: $($config.vSphere.ServerHost)..." 'INFO'
    try {
        $viConnection = Connect-VIServer -Server $config.vSphere.ServerHost -Credential $credential -ErrorAction Stop
        Write-Log "Successfully connected to vSphere server: $($viConnection.Name)" 'SUCCESS'
        Write-Log "vSphere version: $($viConnection.Version), Build: $($viConnection.Build)" 'INFO'
    } catch {
        Write-Log "Failed to connect to vSphere server: $_" 'ERROR'
        throw
    }
    
    # Get VM object
    Write-Log "Retrieving VM object for: ${VMName}..." 'INFO'
    try {
        $vm = Get-VM -Name $VMName -ErrorAction Stop
        Write-Log "VM found: $($vm.Name)" 'SUCCESS'
        Write-Log "VM Power State: $($vm.PowerState)" 'INFO'
        Write-Log "VM Guest OS: $($vm.Guest.OSFullName)" 'INFO'
        Write-Log "VM Hardware Version: $($vm.HardwareVersion)" 'INFO'
    } catch {
        Write-Log "VM '${VMName}' not found in vSphere inventory" 'ERROR'
        throw "VM '${VMName}' does not exist"
    }
    
    # Get SCCM GUID (formatted UUID) from VM
    Write-Log "Retrieving and converting VM UUID to SCCM format..." 'INFO'
    $sccmGuid = Get-vSphereVMUUID -VM $vm
    
    if ([string]::IsNullOrWhiteSpace($sccmGuid)) {
        Write-Log "Cannot proceed: No SCCM GUID retrieved from VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    Write-Log "SCCM GUID retrieved successfully: ${sccmGuid}" 'SUCCESS'
    
    # Update SCCM GUID in database
    $updateSuccess = Update-SCCMGuidInDatabase -VMName $VMName `
                                               -SCCMGuid $sccmGuid `
                                               -Config $config
    
    if (-not $updateSuccess) {
        Write-Log "Failed to update SCCM GUID in database for VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Set global variable with the SCCM GUID (for backward compatibility)
    Write-Log "Setting global variable: Global:SCCMGuiD = ${sccmGuid}" 'INFO'
    $Global:SCCMGuiD = $sccmGuid
    Write-Log "Global variable set successfully" 'SUCCESS'
    
    Write-Log "SCCM GUID successfully retrieved and stored for VM '${VMName}'" 'SUCCESS'
    Write-Log "SCCM GUID: ${sccmGuid}" 'SUCCESS'
    exit 0
    
} catch {
    Write-Log "Unhandled error during SCCM GUID retrieval and storage: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    exit 1
    
} finally {
    # Disconnect from vSphere server
    if ($viConnection) {
        Write-Log "Disconnecting from vSphere server..." 'INFO'
        try {
            Disconnect-VIServer -Server $config.vSphere.ServerHost -Confirm:$false -ErrorAction SilentlyContinue
            Write-Log "Disconnected from vSphere server" 'SUCCESS'
        } catch {
            Write-Log "Error during vSphere disconnect: $_" 'WARNING'
        }
    }
    
    Write-Log "=== SCCM GUID Read and Store Script Completed ===" 'SUCCESS'
}
#endregion