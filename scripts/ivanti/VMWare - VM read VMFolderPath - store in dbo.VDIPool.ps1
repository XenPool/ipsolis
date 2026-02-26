# VMWare - VM read VMFolderPath - store in dbo.VDIPool
# Retrieves the vSphere folder path from a VM and stores it in the VDI Pool database

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
Write-Log "=== Starting VM Folder Path Read and Store Script ===" 'INFO'

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

#region Function: Get VM Folder Path
function Get-VMFolderPath {
    <#
    .SYNOPSIS
    Retrieves the vSphere folder path for a VM
    
    .DESCRIPTION
    Recursively builds the complete folder path for a VM in vSphere.
    Returns the full path from the datacenter down to the VM's folder.
    
    .PARAMETER FolderId
    The folder ID (ManagedObjectReference) to process
    
    .PARAMETER Moref
    If specified, returns ManagedObjectReference IDs instead of folder names
    
    .RETURNS
    String containing the complete folder path
    
    .EXAMPLE
    Get-VMFolderPath -FolderId (Get-VM "MyVM").FolderId
    Returns: "Datacenter\vm\Production\Servers"
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, ValueFromPipelineByPropertyName = $true)]
        [string]$FolderId,
        [switch]$Moref
    )
 
    try {
        Write-Log "Processing folder ID: ${FolderId}..." 'INFO'
        
        # Get folder object from vSphere
        $folderParent = Get-View $FolderId -ErrorAction Stop
        
        # Initialize path variable (using script scope to maintain across recursion)
        if (-not $script:folderPath) {
            $script:folderPath = ""
        }
        
        # Check if we've reached the root 'vm' folder
        if ($folderParent.Name -ne 'vm') {
            # Build path incrementally
            if ($Moref) {
                $script:folderPath = $folderParent.MoRef.ToString() + '\' + $script:folderPath
            } else {
                $script:folderPath = $folderParent.Name + '\' + $script:folderPath
            }
            
            # Recursively process parent folder if it exists
            if ($folderParent.Parent) {
                if ($Moref) {
                    Get-VMFolderPath -FolderId $folderParent.Parent.ToString() -Moref
                } else {
                    Get-VMFolderPath -FolderId $folderParent.Parent.ToString()
                }
            }
        } else {
            # Reached the 'vm' root folder, add datacenter and return complete path
            $datacenterName = (Get-View $folderParent.Parent).Name.ToString()
            
            if ($Moref) {
                $completePath = (Get-View $folderParent.Parent).MoRef.ToString() + '\' + $folderParent.MoRef.ToString() + '\' + $script:folderPath
            } else {
                $completePath = $datacenterName + '\' + $folderParent.Name.ToString() + '\' + $script:folderPath
            }
            
            # Clean up trailing backslash
            $completePath = $completePath.TrimEnd('\')
            
            # Reset script variable for next use
            $script:folderPath = $null
            
            return $completePath
        }
        
    } catch {
        Write-Log "Error retrieving folder path: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Get VM vSphere Folder Path
function Get-VMvSphereFolderPath {
    <#
    .SYNOPSIS
    Gets the complete vSphere folder path for a VM
    
    .PARAMETER VM
    The VM object to get the folder path from
    
    .RETURNS
    String containing the complete vSphere folder path
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    try {
        Write-Log "Retrieving vSphere folder path for VM: $($VM.Name)..." 'INFO'
        
        # Get the folder ID from the VM
        $folderId = $VM.FolderId
        
        if ([string]::IsNullOrWhiteSpace($folderId)) {
            Write-Log "VM folder ID is empty or null" 'WARNING'
            return $null
        }
        
        # Get the complete folder path
        $folderPath = Get-VMFolderPath -FolderId $folderId
        
        if ([string]::IsNullOrWhiteSpace($folderPath)) {
            Write-Log "Failed to retrieve folder path for VM: $($VM.Name)" 'WARNING'
            return $null
        }
        
        Write-Log "vSphere folder path retrieved: ${folderPath}" 'SUCCESS'
        return $folderPath
        
    } catch {
        Write-Log "Failed to get vSphere folder path: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Update VM Folder Path in Database
function Update-VMFolderPathInDatabase {
    <#
    .SYNOPSIS
    Updates the vSphere folder path in the VDI Pool database table
    
    .PARAMETER VMName
    The name of the VM
    
    .PARAMETER FolderPath
    The vSphere folder path to store
    
    .PARAMETER Config
    Configuration object containing SQL settings
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [string]$FolderPath,
        [Parameter(Mandatory = $true)]
        [hashtable]$Config
    )
    
    try {
        Write-Log "Preparing to update vSphere folder path in database..." 'INFO'
        
        # Import SQLServer module
        Write-Log "Loading SQLServer module..." 'INFO'
        try {
            Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
            Write-Log "SQLServer module loaded successfully" 'SUCCESS'
        } catch {
            Write-Log "Failed to load SQLServer module: $_" 'ERROR'
            throw "SQLServer module not available. Please install it using: Install-Module -Name SqlServer"
        }
        
        # Build SQL query with parameterized values for safety
        Write-Log "Building SQL UPDATE query..." 'INFO'
        $query = "UPDATE $($Config.SQL.VDIPoolTable)
SET VSphereFolder = @FolderPath
WHERE VMName = @VMName"
        
        Write-Log "SQL Query prepared:" 'INFO'
        Write-Log "  Table: $($Config.SQL.VDIPoolTable)" 'INFO'
        Write-Log "  VMName: ${VMName}" 'INFO'
        Write-Log "  FolderPath: ${FolderPath}" 'INFO'
        
        # Execute SQL query with parameterized values
        Write-Log "Executing SQL query..." 'INFO'
        
        try {
            # Execute UPDATE query (no result needed for UPDATE statements)
            Invoke-Sqlcmd -Query $query `
                         -ServerInstance $Config.SQL.ServerInstance `
                         -Database $Config.SQL.Database `
                         -Username $Config.SQL.LoginUser `
                         -Password $Config.SQL.LoginPW `
                         -Variable @("FolderPath='$FolderPath'", "VMName='$VMName'") `
                         -ErrorAction Stop | Out-Null
            
            Write-Log "SQL query executed successfully" 'SUCCESS'
            
            # Verify the update
            Write-Log "Verifying database update..." 'INFO'
            $verifyQuery = "SELECT VSphereFolder FROM $($Config.SQL.VDIPoolTable) WHERE VMName = @VMName"
            
            $verifyResult = Invoke-Sqlcmd -Query $verifyQuery `
                                          -ServerInstance $Config.SQL.ServerInstance `
                                          -Database $Config.SQL.Database `
                                          -Username $Config.SQL.LoginUser `
                                          -Password $Config.SQL.LoginPW `
                                          -Variable "VMName='$VMName'" `
                                          -ErrorAction Stop
            
            if ($verifyResult -and $verifyResult.VSphereFolder -eq $FolderPath) {
                Write-Log "Database update verified successfully" 'SUCCESS'
                Write-Log "Stored folder path: $($verifyResult.VSphereFolder)" 'INFO'
            } else {
                Write-Log "Database update verification warning: Stored value may differ" 'WARNING'
                if ($verifyResult) {
                    Write-Log "Database value: $($verifyResult.VSphereFolder)" 'WARNING'
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
        Write-Log "Failed to update vSphere folder path in database: $_" 'ERROR'
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
    
    # Get vSphere folder path from VM
    $vSphereFolderPath = Get-VMvSphereFolderPath -VM $vm
    
    if ([string]::IsNullOrWhiteSpace($vSphereFolderPath)) {
        Write-Log "Cannot proceed: No vSphere folder path retrieved from VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    Write-Log "vSphere folder path retrieved successfully: ${vSphereFolderPath}" 'SUCCESS'
    
    # Update vSphere folder path in database
    $updateSuccess = Update-VMFolderPathInDatabase -VMName $VMName `
                                                    -FolderPath $vSphereFolderPath `
                                                    -Config $config
    
    if (-not $updateSuccess) {
        Write-Log "Failed to update vSphere folder path in database for VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Set global variable with the folder path (for backward compatibility)
    Write-Log "Setting global variable: Global:VSphereFolder = ${vSphereFolderPath}" 'INFO'
    $Global:VSphereFolder = $vSphereFolderPath
    Write-Log "Global variable set successfully" 'SUCCESS'
    
    Write-Log "vSphere folder path successfully retrieved and stored for VM '${VMName}'" 'SUCCESS'
    Write-Log "Folder Path: ${vSphereFolderPath}" 'SUCCESS'
    exit 0
    
} catch {
    Write-Log "Unhandled error during vSphere folder path retrieval and storage: $_" 'ERROR'
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
    
    Write-Log "=== VM Folder Path Read and Store Script Completed ===" 'SUCCESS'
}
#endregion