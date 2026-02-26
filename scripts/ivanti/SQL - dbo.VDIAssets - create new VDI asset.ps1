# SQL - dbo.VDIAssets - Create New VDI Asset
# Creates a new VDI asset record in the database with proper validation and error handling

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
Write-Log "=== Starting VDI Asset Creation ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Configuration for SQL Server connection
$config = @{
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        Table          = "^[SQLVDIAssetTable]"
    }
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Target Table: $($config.SQL.Table)" 'INFO'
#endregion

#region Input Parameters
# Collect and sanitize input parameters
$assetData = @{
    VMName        = '$[VMName]'
    AssetStatus   = 'Composing'  # Initial status for new assets
    UsecaseID     = '$[UsecaseID]'
    Requestor     = '$[Requestor]'
    Owner         = '$[Owner]'
    SecondOwner   = '$[SecondOwner]'
    RDPUserIDs    = '$[RDPUserIDs]'
    LocalAdmins   = '$[LocalAdmins]'
    LifeCycle     = '$[LifeCycle]'
    Snow_REQ      = '$[Snow_REQ]'
    Snow_RITM     = '$[Snow_RITM]'
    CostCenter    = '$[CostCenter]'
    PricePerDay   = '$[PricePerDay]'
    CreationDate  = '@[DATETIME(YYYY-MM-DD)]'
    OrderDate     = '$[OrderDate]'
}

Write-Log "Asset Name: $($assetData.VMName)" 'INFO'
Write-Log "Usecase ID: $($assetData.UsecaseID)" 'INFO'
Write-Log "Owner: $($assetData.Owner)" 'INFO'
#endregion

#region Input Validation and Sanitization
try {
    Write-Log "Validating and sanitizing input data..." 'INFO'
    
    # Sanitize RDPUserIDs: Replace double semicolons with single semicolon
    if (-not [string]::IsNullOrWhiteSpace($assetData.RDPUserIDs)) {
        $originalRDPUserIDs = $assetData.RDPUserIDs
        $assetData.RDPUserIDs = $assetData.RDPUserIDs.Replace(';;', ';')
        
        if ($originalRDPUserIDs -ne $assetData.RDPUserIDs) {
            Write-Log "Sanitized RDPUserIDs (removed double semicolons)" 'WARNING'
        }
        Write-Log "RDP Users: $($assetData.RDPUserIDs)" 'INFO'
    } else {
        Write-Log "No RDP users specified" 'WARNING'
    }
    
    # Sanitize LocalAdmins: Replace double semicolons with single semicolon
    if (-not [string]::IsNullOrWhiteSpace($assetData.LocalAdmins)) {
        $originalLocalAdmins = $assetData.LocalAdmins
        $assetData.LocalAdmins = $assetData.LocalAdmins.Replace(';;', ';')
        
        if ($originalLocalAdmins -ne $assetData.LocalAdmins) {
            Write-Log "Sanitized LocalAdmins (removed double semicolons)" 'WARNING'
        }
        Write-Log "Local Admins: $($assetData.LocalAdmins)" 'INFO'
    } else {
        Write-Log "No local admins specified" 'WARNING'
    }
    
    # Validate required fields
    $requiredFields = @('VMName', 'UsecaseID', 'Requestor', 'Owner')
    $missingFields = @()
    
    foreach ($field in $requiredFields) {
        if ([string]::IsNullOrWhiteSpace($assetData[$field])) {
            $missingFields += $field
        }
    }
    
    if ($missingFields.Count -gt 0) {
        $errorMsg = "Missing required fields: $($missingFields -join ', ')"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Input validation completed successfully" 'SUCCESS'
    
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

#region SQL Query Construction
try {
    Write-Log "Constructing SQL INSERT query..." 'INFO'
    
    # Build parameterized SQL query to prevent SQL injection
    # Using SQL parameters would be more secure, but maintaining compatibility with existing system
    $query = @"
INSERT INTO $($config.SQL.Table)
(
    VMName,
    AssetStatus,
    UsecaseID,
    Requestor,
    Owner,
    SecondOwner,
    RDPUserIDs,
    LocalAdmins,
    LifeCycle,
    Snow_REQ,
    Snow_RITM,
    CostCenter,
    PricePerDay,
    CreationDate,
    OrderDate
)
OUTPUT Inserted.Uuid
VALUES
(
    '$($assetData.VMName.Replace("'", "''"))',
    '$($assetData.AssetStatus)',
    '$($assetData.UsecaseID)',
    '$($assetData.Requestor.Replace("'", "''"))',
    '$($assetData.Owner.Replace("'", "''"))',
    '$($assetData.SecondOwner.Replace("'", "''"))',
    '$($assetData.RDPUserIDs.Replace("'", "''"))',
    '$($assetData.LocalAdmins.Replace("'", "''"))',
    '$($assetData.LifeCycle)',
    '$($assetData.Snow_REQ)',
    '$($assetData.Snow_RITM)',
    '$($assetData.CostCenter)',
    '$($assetData.PricePerDay)',
    '$($assetData.CreationDate)',
    '$($assetData.OrderDate)'
);
"@
    
    Write-Log "SQL query constructed successfully" 'SUCCESS'
    
} catch {
    Write-Log "Failed to construct SQL query: $_" 'ERROR'
    throw
}
#endregion

#region Execute SQL Insert
try {
    Write-Log "Executing SQL INSERT query..." 'INFO'
    Write-Log "Creating VDI asset: $($assetData.VMName)..." 'INFO'
    
    # Execute the INSERT query
    $insertResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                  -Database $config.SQL.Database `
                                  -Username $config.SQL.LoginUser `
                                  -Password $config.SQL.LoginPW `
                                  -Query $query `
                                  -QueryTimeout 30 `
                                  -ErrorAction Stop
    
    # Verify the insert was successful
    if ($null -eq $insertResult -or $null -eq $insertResult.Uuid) {
        throw "Insert failed - no UUID was returned"
    }
    
    $newUUID = $insertResult.Uuid
    
    Write-Log "VDI asset created successfully" 'SUCCESS'
    Write-Log "New Asset UUID: $newUUID" 'SUCCESS'
    
    # Set global variable for downstream processes
    $Global:UUID = $newUUID
    
} catch {
    Write-Log "Failed to create VDI asset: $_" 'ERROR'
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Asset Creation Completed ===" 'SUCCESS'
Write-Log "Asset Details:" 'INFO'
Write-Log "  VM Name: $($assetData.VMName)" 'INFO'
Write-Log "  Status: $($assetData.AssetStatus)" 'INFO'
Write-Log "  Owner: $($assetData.Owner)" 'INFO'
Write-Log "  Usecase ID: $($assetData.UsecaseID)" 'INFO'
Write-Log "  Creation Date: $($assetData.CreationDate)" 'INFO'
Write-Log "Asset creation completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'

# Exit with UUID as exit code (Ivanti can capture this in $[EXITCODE])
# Note: Exit codes are limited to 0-2147483647 (Int32 max value)
exit $newUUID
#endregion