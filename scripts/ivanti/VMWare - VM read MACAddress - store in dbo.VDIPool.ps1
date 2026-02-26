# VMWare - VM read MACAddress - store in dbo.VDIPool
# Retrieves the MAC address from a VM and stores it in the VDI Pool database

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
Write-Log "=== Starting MAC Address Read and Store Script ===" 'INFO'

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

#region Function: Get VM MAC Address
function Get-VMMACAddress {
    <#
    .SYNOPSIS
    Retrieves the MAC address from a VM's network adapter
    
    .PARAMETER VMName
    The name of the VM to query
    
    .RETURNS
    String containing the MAC address, or null if not found
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName
    )
    
    try {
        Write-Log "Retrieving VM object for: ${VMName}..." 'INFO'
        
        # Get VM object
        $vm = Get-VM -Name $VMName -ErrorAction Stop
        Write-Log "VM found: $($vm.Name)" 'SUCCESS'
        Write-Log "VM Power State: $($vm.PowerState)" 'INFO'
        
        # Get network adapters
        Write-Log "Retrieving network adapter information..." 'INFO'
        $adapters = Get-NetworkAdapter -VM $vm -ErrorAction Stop
        
        if (-not $adapters -or $adapters.Count -eq 0) {
            Write-Log "No network adapters found on VM: ${VMName}" 'WARNING'
            return $null
        }
        
        Write-Log "Found $($adapters.Count) network adapter(s)" 'INFO'
        
        # Get MAC address from first adapter
        $macAddress = $adapters[0].MacAddress
        
        if ([string]::IsNullOrWhiteSpace($macAddress)) {
            Write-Log "MAC address is empty or null for VM: ${VMName}" 'WARNING'
            return $null
        }
        
        Write-Log "MAC Address retrieved: ${macAddress}" 'SUCCESS'
        
        # If multiple adapters, log all MAC addresses
        if ($adapters.Count -gt 1) {
            Write-Log "Multiple network adapters detected:" 'INFO'
            for ($i = 0; $i -lt $adapters.Count; $i++) {
                Write-Log "  Adapter $($i + 1): $($adapters[$i].MacAddress) (Name: $($adapters[$i].Name))" 'INFO'
            }
            Write-Log "Using MAC address from first adapter: ${macAddress}" 'INFO'
        }
        
        return $macAddress
        
    } catch {
        Write-Log "Failed to retrieve MAC address from VM '${VMName}': $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Update MAC Address in Database
function Update-MACAddressInDatabase {
    <#
    .SYNOPSIS
    Updates the MAC address in the VDI Pool database table
    
    .PARAMETER VMName
    The name of the VM
    
    .PARAMETER MACAddress
    The MAC address to store
    
    .PARAMETER Config
    Configuration object containing SQL settings
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [string]$MACAddress,
        [Parameter(Mandatory = $true)]
        [hashtable]$Config
    )
    
    try {
        Write-Log "Preparing to update MAC address in database..." 'INFO'
        
        # Import SQLServer module
        Write-Log "Loading SQLServer module..." 'INFO'
        try {
            Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
            Write-Log "SQLServer module loaded successfully" 'SUCCESS'
        } catch {
            Write-Log "Failed to load SQLServer module: $_" 'ERROR'
            throw "SQLServer module not available. Please install it using: Install-Module -Name SqlServer"
        }
        
        # Validate MAC address format (basic validation)
        if ($MACAddress -notmatch '^([0-9A-Fa-f]{2}[:-]?){5}([0-9A-Fa-f]{2})$') {
            Write-Log "Invalid MAC address format: ${MACAddress}" 'WARNING'
            Write-Log "Proceeding anyway as VMware may use different format" 'WARNING'
        }
        
        # Build SQL query with parameterized values for safety
        Write-Log "Building SQL UPDATE query..." 'INFO'
        $query = "UPDATE $($Config.SQL.VDIPoolTable)
SET MACAddress = @MACAddress
WHERE VMName = @VMName"
        
        Write-Log "SQL Query prepared:" 'INFO'
        Write-Log "  Table: $($Config.SQL.VDIPoolTable)" 'INFO'
        Write-Log "  VMName: ${VMName}" 'INFO'
        Write-Log "  MACAddress: ${MACAddress}" 'INFO'
        
        # Execute SQL query
        Write-Log "Executing SQL query..." 'INFO'
        
        # Execute with error handling
        try {
            # Execute UPDATE query (no result needed for UPDATE statements)
            Invoke-Sqlcmd -Query $query `
                         -ServerInstance $Config.SQL.ServerInstance `
                         -Database $Config.SQL.Database `
                         -Username $Config.SQL.LoginUser `
                         -Password $Config.SQL.LoginPW `
                         -Variable @("MACAddress='$MACAddress'", "VMName='$VMName'") `
                         -ErrorAction Stop | Out-Null
            
            Write-Log "SQL query executed successfully" 'SUCCESS'
            
            # Verify the update
            Write-Log "Verifying database update..." 'INFO'
            $verifyQuery = "SELECT MACAddress FROM $($Config.SQL.VDIPoolTable) WHERE VMName = @VMName"
            
            $verifyResult = Invoke-Sqlcmd -Query $verifyQuery `
                                          -ServerInstance $Config.SQL.ServerInstance `
                                          -Database $Config.SQL.Database `
                                          -Username $Config.SQL.LoginUser `
                                          -Password $Config.SQL.LoginPW `
                                          -Variable "VMName='$VMName'" `
                                          -ErrorAction Stop
            
            if ($verifyResult -and $verifyResult.MACAddress -eq $MACAddress) {
                Write-Log "Database update verified successfully" 'SUCCESS'
                Write-Log "Stored MAC Address: $($verifyResult.MACAddress)" 'INFO'
            } else {
                Write-Log "Database update verification warning: Stored value may differ" 'WARNING'
                if ($verifyResult) {
                    Write-Log "Database value: $($verifyResult.MACAddress)" 'WARNING'
                }
            }
            
            return $true
            
        } catch {
            Write-Log "SQL query execution failed: $_" 'ERROR'
            throw
        }
        
    } catch {
        Write-Log "Failed to update MAC address in database: $_" 'ERROR'
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
    
    # Get MAC address from VM
    $macAddress = Get-VMMACAddress -VMName $VMName
    
    if ([string]::IsNullOrWhiteSpace($macAddress)) {
        Write-Log "Cannot proceed: No MAC address retrieved from VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Update MAC address in database
    $updateSuccess = Update-MACAddressInDatabase -VMName $VMName `
                                                  -MACAddress $macAddress `
                                                  -Config $config
    
    if (-not $updateSuccess) {
        Write-Log "Failed to update MAC address in database for VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Set global variable with the MAC address (for backward compatibility)
    Write-Log "Setting global variable: Global:MACAddress = ${macAddress}" 'INFO'
    $Global:MACAddress = $macAddress
    Write-Log "Global variable set successfully" 'SUCCESS'
    
    Write-Log "MAC address successfully retrieved and stored for VM '${VMName}'" 'SUCCESS'
    Write-Log "MAC Address: ${macAddress}" 'SUCCESS'
    exit 0
    
} catch {
    Write-Log "Unhandled error during MAC address retrieval and storage: $_" 'ERROR'
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
    
    Write-Log "=== MAC Address Read and Store Script Completed ===" 'SUCCESS'
}
#endregion