# SQL - Query VDIPool for Setup Parameters
# Retrieves VM configuration parameters from the VDI pool table for provisioning

#region Logging Function
function Write-Log {
    <#
    .SYNOPSIS
    Writes formatted log messages to console with color coding
    
    .PARAMETER Message
    The message to log
    
    .PARAMETER Level
    Log level (INFO, WARNING, ERROR, SUCCESS)
    #>
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
Write-Log "=== Starting VDI Pool Setup Parameters Query ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Configuration for SQL Server connection
$config = @{
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        PoolTable      = "^[SQLVDIPoolTable]"
    }
}

# Input parameters
$inputParams = @{
    VMName = '$[VMName]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Pool Table: $($config.SQL.PoolTable)" 'INFO'
Write-Log "Target VM: $($inputParams.VMName)" 'INFO'
#endregion

#region Input Validation
try {
    Write-Log "Validating input parameters..." 'INFO'
    
    # Validate VMName
    if ([string]::IsNullOrWhiteSpace($inputParams.VMName)) {
        throw "VMName is required but was not provided"
    }
    
    # Sanitize VMName to prevent SQL injection
    $sanitizedVMName = $inputParams.VMName.Replace("'", "''")
    
    Write-Log "Input validation completed successfully" 'SUCCESS'
    Write-Log "Sanitized VM Name: ${sanitizedVMName}" 'INFO'
    
} catch {
    Write-Log "Input validation failed: $_" 'ERROR'
    throw
}
#endregion

#region SQL Server Module
try {
    Write-Log "Loading SQLServer module..." 'INFO'
    
    # Import SQLServer module with error handling
    Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
    
    Write-Log "SQLServer module loaded successfully" 'SUCCESS'
    
} catch {
    Write-Log "Failed to load SQLServer module: $_" 'ERROR'
    Write-Log "Please install the module using: Install-Module -Name SqlServer" 'ERROR'
    throw
}
#endregion

#region Database Connection Test
try {
    Write-Log "Testing database connection..." 'INFO'
    
    # Test connection with a simple query
    $testQuery = "SELECT 1 AS TestConnection"
    $testResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                -Database $config.SQL.Database `
                                -Username $config.SQL.LoginUser `
                                -Password $config.SQL.LoginPW `
                                -Query $testQuery `
                                -QueryTimeout 5 `
                                -ErrorAction Stop
    
    if ($testResult.TestConnection -eq 1) {
        Write-Log "Database connection successful" 'SUCCESS'
    } else {
        throw "Database connection test returned unexpected result"
    }
    
} catch {
    Write-Log "Database connection test failed: $_" 'ERROR'
    throw
}
#endregion

#region Query VDI Pool Parameters
try {
    Write-Log "Querying VDI pool for setup parameters..." 'INFO'
    
    # Construct SQL query to retrieve all setup parameters for the VM
    $query = @"
SELECT 
    VMName,
    UsecaseID,
    MACAddress,
    SCCMGuiD,
    OSCollectionID,
    AppCollectionIDs,
    Status,
    Domain,
    AssetUUID,
    Snow_REQ,
    Snow_RITM
FROM $($config.SQL.PoolTable)
WHERE VMName = '${sanitizedVMName}'
"@
    
    Write-Log "Executing query for VM: ${sanitizedVMName}" 'INFO'
    
    # Execute the query
    $result = Invoke-Sqlcmd -Query $query `
                            -ServerInstance $config.SQL.ServerInstance `
                            -Database $config.SQL.Database `
                            -Username $config.SQL.LoginUser `
                            -Password $config.SQL.LoginPW `
                            -QueryTimeout 30 `
                            -ErrorAction Stop
    
    # Validate that a result was returned
    if ($null -eq $result) {
        $errorMsg = "VM '$($inputParams.VMName)' not found in pool table"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "VM configuration retrieved successfully" 'SUCCESS'
    
} catch {
    Write-Log "Failed to query pool parameters: $_" 'ERROR'
    throw
}
#endregion

#region Validate Required Parameters
try {
    Write-Log "Validating required parameters..." 'INFO'
    
    # Track validation status
    $validationErrors = @()
    
    # Validate UsecaseID (critical for provisioning)
    if ([string]::IsNullOrWhiteSpace($result.UsecaseID)) {
        $validationErrors += "UsecaseID is missing or empty"
        Write-Log "ERROR: UsecaseID is required but not set" 'ERROR'
    } else {
        Write-Log "  UsecaseID: $($result.UsecaseID)" 'INFO'
    }
    
    # Validate MACAddress (critical for network provisioning)
    if ([string]::IsNullOrWhiteSpace($result.MACAddress)) {
        $validationErrors += "MACAddress is missing or empty"
        Write-Log "ERROR: MACAddress is required but not set" 'ERROR'
    } else {
        Write-Log "  MACAddress: $($result.MACAddress)" 'INFO'
    }
    
    # Validate SCCMGuiD (critical for SCCM operations)
    if ([string]::IsNullOrWhiteSpace($result.SCCMGuiD)) {
        $validationErrors += "SCCMGuiD is missing or empty"
        Write-Log "ERROR: SCCMGuiD is required but not set" 'ERROR'
    } else {
        Write-Log "  SCCMGuiD: $($result.SCCMGuiD)" 'INFO'
    }
    
    # Validate OSCollectionID (critical for OS deployment)
    if ([string]::IsNullOrWhiteSpace($result.OSCollectionID)) {
        $validationErrors += "OSCollectionID is missing or empty"
        Write-Log "ERROR: OSCollectionID is required but not set" 'ERROR'
    } else {
        Write-Log "  OSCollectionID: $($result.OSCollectionID)" 'INFO'
    }
    
    # Validate AppCollectionIDs (warning if missing, not critical)
    if ([string]::IsNullOrWhiteSpace($result.AppCollectionIDs)) {
        Write-Log "WARNING: AppCollectionIDs is empty - no applications will be deployed" 'WARNING'
    } else {
        Write-Log "  AppCollectionIDs: $($result.AppCollectionIDs)" 'INFO'
    }
    
    # Display additional informational parameters
    Write-Log "Additional Parameters:" 'INFO'
    Write-Log "  VM Name: $($result.VMName)" 'INFO'
    Write-Log "  Status: $($result.Status)" 'INFO'
    Write-Log "  Domain: $($result.Domain)" 'INFO'
    
    if (-not [string]::IsNullOrWhiteSpace($result.AssetUUID)) {
        Write-Log "  AssetUUID: $($result.AssetUUID)" 'INFO'
    }
    
    if (-not [string]::IsNullOrWhiteSpace($result.Snow_REQ)) {
        Write-Log "  ServiceNow REQ: $($result.Snow_REQ)" 'INFO'
    }
    
    if (-not [string]::IsNullOrWhiteSpace($result.Snow_RITM)) {
        Write-Log "  ServiceNow RITM: $($result.Snow_RITM)" 'INFO'
    }
    
    # Check if there are any validation errors
    if ($validationErrors.Count -gt 0) {
        $errorMsg = "Parameter validation failed with $($validationErrors.Count) error(s):`n" + ($validationErrors -join "`n")
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "All required parameters validated successfully" 'SUCCESS'
    
} catch {
    Write-Log "Parameter validation failed: $_" 'ERROR'
    throw
}
#endregion

#region Set Global Variables
try {
    Write-Log "Setting global variables for downstream processes..." 'INFO'
    
    # Set global variables with the retrieved values
    # These variables are used by subsequent automation steps
    $Global:UsecaseID = $result.UsecaseID
    $Global:MACAddress = $result.MACAddress
    $Global:SCCMGuiD = $result.SCCMGuiD
    $Global:OSCollectionID = $result.OSCollectionID
    $Global:AppCollectionIDs = $result.AppCollectionIDs
    
    # Set additional global variables for enhanced functionality
    $Global:VMName = $result.VMName
    $Global:VMDomain = $result.Domain
    $Global:VMStatus = $result.Status
    
    Write-Log "Global variables set successfully:" 'SUCCESS'
    Write-Log "  Global:UsecaseID = ${Global:UsecaseID}" 'INFO'
    Write-Log "  Global:MACAddress = ${Global:MACAddress}" 'INFO'
    Write-Log "  Global:SCCMGuiD = ${Global:SCCMGuiD}" 'INFO'
    Write-Log "  Global:OSCollectionID = ${Global:OSCollectionID}" 'INFO'
    Write-Log "  Global:AppCollectionIDs = ${Global:AppCollectionIDs}" 'INFO'
    Write-Log "  Global:VMName = ${Global:VMName}" 'INFO'
    Write-Log "  Global:VMDomain = ${Global:VMDomain}" 'INFO'
    Write-Log "  Global:VMStatus = ${Global:VMStatus}" 'INFO'
    
} catch {
    Write-Log "Failed to set global variables: $_" 'ERROR'
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Pool Setup Parameters Query Completed ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  VM Name: $($inputParams.VMName)" 'INFO'
Write-Log "  UseCase ID: ${Global:UsecaseID}" 'INFO'
Write-Log "  OS Collection: ${Global:OSCollectionID}" 'INFO'
Write-Log "  App Collections: ${Global:AppCollectionIDs}" 'INFO'
Write-Log "  SCCM GUID: ${Global:SCCMGuiD}" 'INFO'
Write-Log "  MAC Address: ${Global:MACAddress}" 'INFO'
Write-Log "  Configuration ready for VM provisioning" 'SUCCESS'
#endregion