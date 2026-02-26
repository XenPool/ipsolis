# SQL - dbo.VDIAssets - Update Deleted VDI Asset
# Updates lifecycle and ServiceNow reference information for a deleted VDI asset

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
Write-Log "=== Starting Deleted VDI Asset Update ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Configuration for SQL Server connection
$config = @{
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        AssetTable     = "^[SQLVDIAssetTable]"
    }
}

# Input parameters for asset update
$inputParams = @{
    UUID      = '$[UUID]'
    LifeCycle = '$[LifeCycle]'
    Snow_REQ  = '$[Snow_REQ]'
    Snow_RITM = '$[Snow_RITM]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Target Table: $($config.SQL.AssetTable)" 'INFO'
Write-Log "Asset UUID: $($inputParams.UUID)" 'INFO'
Write-Log "LifeCycle: $($inputParams.LifeCycle)" 'INFO'
Write-Log "ServiceNow REQ: $($inputParams.Snow_REQ)" 'INFO'
Write-Log "ServiceNow RITM: $($inputParams.Snow_RITM)" 'INFO'
#endregion

#region Input Validation
try {
    Write-Log "Validating input parameters..." 'INFO'
    
    # Validate UUID (required field)
    if ([string]::IsNullOrWhiteSpace($inputParams.UUID)) {
        throw "UUID is required but was not provided"
    }
    
    # Validate LifeCycle if provided (optional field)
    $lifeCycleValue = $null
    if (-not [string]::IsNullOrWhiteSpace($inputParams.LifeCycle)) {
        $tempValue = 0
        if (-not [int]::TryParse($inputParams.LifeCycle, [ref]$tempValue)) {
            throw "LifeCycle must be a valid integer value, received: '$($inputParams.LifeCycle)'"
        }
        
        if ($tempValue -lt 0) {
            throw "LifeCycle cannot be negative, received: ${tempValue}"
        }
        
        $lifeCycleValue = $tempValue
        Write-Log "LifeCycle will be updated to: ${lifeCycleValue}" 'INFO'
    } else {
        Write-Log "LifeCycle not provided - will not be updated" 'INFO'
    }
    
    # Validate ServiceNow references (both optional)
    if ([string]::IsNullOrWhiteSpace($inputParams.Snow_REQ) -and 
        [string]::IsNullOrWhiteSpace($inputParams.Snow_RITM)) {
        Write-Log "Warning: No ServiceNow reference (REQ or RITM) provided" 'WARNING'
    }
    
    # Sanitize all inputs to prevent SQL injection
    $sanitizedParams = @{
        UUID      = $inputParams.UUID.Replace("'", "''")
        LifeCycle = if ($lifeCycleValue) { $inputParams.LifeCycle.Replace("'", "''") } else { $null }
        Snow_REQ  = $inputParams.Snow_REQ.Replace("'", "''")
        Snow_RITM = $inputParams.Snow_RITM.Replace("'", "''")
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

#region Verify Asset Exists and Is Deleted
try {
    Write-Log "Verifying asset exists and checking status..." 'INFO'
    
    # Query to check if asset exists and get its current status
    $verifyQuery = @"
SELECT 
    UUID,
    VMName,
    AssetStatus,
    LifeCycle AS CurrentLifeCycle,
    Snow_REQ AS CurrentSnow_REQ,
    Snow_RITM AS CurrentSnow_RITM
FROM $($config.SQL.AssetTable)
WHERE UUID = '$($sanitizedParams.UUID)'
"@
    
    $existingAsset = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                   -Database $config.SQL.Database `
                                   -Username $config.SQL.LoginUser `
                                   -Password $config.SQL.LoginPW `
                                   -Query $verifyQuery `
                                   -QueryTimeout 30 `
                                   -ErrorAction Stop
    
    # Validate asset exists
    if ($null -eq $existingAsset) {
        $errorMsg = "Asset with UUID '$($inputParams.UUID)' does not exist"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset found: $($existingAsset.VMName)" 'SUCCESS'
    Write-Log "  Current Status: $($existingAsset.AssetStatus)" 'INFO'
    Write-Log "  Current LifeCycle: $($existingAsset.CurrentLifeCycle)" 'INFO'
    
    # Verify asset is in 'Deleted' status
    if ($existingAsset.AssetStatus -ne 'Deleted') {
        Write-Log "Warning: Asset status is '$($existingAsset.AssetStatus)', not 'Deleted'" 'WARNING'
        Write-Log "This script is intended for deleted assets only" 'WARNING'
        Write-Log "Continuing with update, but please verify this is intended" 'WARNING'
    }
    
} catch {
    Write-Log "Asset verification failed: $_" 'ERROR'
    throw
}
#endregion

#region Construct SQL Update Query
try {
    Write-Log "Constructing SQL UPDATE query..." 'INFO'
    
    # Build SET clause dynamically based on provided parameters
    $setClauses = @()
    
    # Add LifeCycle to SET clause only if provided
    if ($null -ne $sanitizedParams.LifeCycle) {
        $setClauses += "[LifeCycle] = '$($sanitizedParams.LifeCycle)'"
    }
    
    # Add ServiceNow references (always included, may be empty strings)
    $setClauses += "[Snow_REQ] = '$($sanitizedParams.Snow_REQ)'"
    $setClauses += "[Snow_RITM] = '$($sanitizedParams.Snow_RITM)'"
    
    # Always update LastUpdate timestamp
    $setClauses += "[LastUpdate] = GETDATE()"
    
    # Join all SET clauses
    $setClause = $setClauses -join ",`n    "
    
    # Build the UPDATE query with OUTPUT clause to capture changes
    $updateQuery = @"
UPDATE $($config.SQL.AssetTable)
SET
    $setClause
OUTPUT 
    Inserted.UUID,
    Inserted.VMName,
    Inserted.AssetStatus,
    Inserted.LifeCycle,
    Inserted.Snow_REQ,
    Inserted.Snow_RITM,
    Deleted.LifeCycle AS PreviousLifeCycle,
    Deleted.Snow_REQ AS PreviousSnow_REQ,
    Deleted.Snow_RITM AS PreviousSnow_RITM
WHERE UUID = '$($sanitizedParams.UUID)'
"@
    
    Write-Log "SQL query constructed successfully" 'SUCCESS'
    
} catch {
    Write-Log "Failed to construct SQL query: $_" 'ERROR'
    throw
}
#endregion

#region Execute SQL Update
try {
    Write-Log "Executing SQL UPDATE query..." 'INFO'
    Write-Log "Updating deleted asset: $($existingAsset.VMName)..." 'INFO'
    
    # Execute the UPDATE query
    $updateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                  -Database $config.SQL.Database `
                                  -Username $config.SQL.LoginUser `
                                  -Password $config.SQL.LoginPW `
                                  -Query $updateQuery `
                                  -QueryTimeout 30 `
                                  -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $updateResult) {
        $errorMsg = "Update failed - no rows were affected. UUID may not exist: $($inputParams.UUID)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset updated successfully" 'SUCCESS'
    
    # Log the changes made
    Write-Log "Update Details:" 'INFO'
    Write-Log "  UUID: $($updateResult.UUID)" 'INFO'
    Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
    Write-Log "  Asset Status: $($updateResult.AssetStatus)" 'INFO'
    
    # Show LifeCycle changes (only if it was updated)
    if ($null -ne $sanitizedParams.LifeCycle) {
        if ($updateResult.PreviousLifeCycle -ne $updateResult.LifeCycle) {
            Write-Log "  LifeCycle: '$($updateResult.PreviousLifeCycle)' → '$($updateResult.LifeCycle)'" 'INFO'
        } else {
            Write-Log "  LifeCycle: $($updateResult.LifeCycle) (unchanged)" 'INFO'
        }
    } else {
        Write-Log "  LifeCycle: $($updateResult.LifeCycle) (not updated)" 'INFO'
    }
    
    # Show ServiceNow REQ changes
    if ($updateResult.PreviousSnow_REQ -ne $updateResult.Snow_REQ) {
        $prevREQ = if ([string]::IsNullOrWhiteSpace($updateResult.PreviousSnow_REQ)) { "(empty)" } else { $updateResult.PreviousSnow_REQ }
        $newREQ = if ([string]::IsNullOrWhiteSpace($updateResult.Snow_REQ)) { "(empty)" } else { $updateResult.Snow_REQ }
        Write-Log "  ServiceNow REQ: '${prevREQ}' → '${newREQ}'" 'INFO'
    } else {
        Write-Log "  ServiceNow REQ: $($updateResult.Snow_REQ) (unchanged)" 'INFO'
    }
    
    # Show ServiceNow RITM changes
    if ($updateResult.PreviousSnow_RITM -ne $updateResult.Snow_RITM) {
        $prevRITM = if ([string]::IsNullOrWhiteSpace($updateResult.PreviousSnow_RITM)) { "(empty)" } else { $updateResult.PreviousSnow_RITM }
        $newRITM = if ([string]::IsNullOrWhiteSpace($updateResult.Snow_RITM)) { "(empty)" } else { $updateResult.Snow_RITM }
        Write-Log "  ServiceNow RITM: '${prevRITM}' → '${newRITM}'" 'INFO'
    } else {
        Write-Log "  ServiceNow RITM: $($updateResult.Snow_RITM) (unchanged)" 'INFO'
    }
    
} catch {
    Write-Log "Failed to update asset: $_" 'ERROR'
    
    # Provide additional context for common errors
    if ($_.Exception.Message -like "*timeout*") {
        Write-Log "Database query timed out - database may be overloaded" 'ERROR'
    }
    elseif ($_.Exception.Message -like "*deadlock*") {
        Write-Log "Database deadlock detected - another process may be updating the same asset" 'ERROR'
    }
    elseif ($_.Exception.Message -like "*constraint*") {
        Write-Log "Database constraint violation - check LifeCycle value or ServiceNow references" 'ERROR'
    }
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== Deleted VDI Asset Update Completed Successfully ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  Asset UUID: $($inputParams.UUID)" 'INFO'
Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
Write-Log "  Asset Status: $($updateResult.AssetStatus)" 'INFO'
Write-Log "  Updated Fields:" 'INFO'
if ($null -ne $sanitizedParams.LifeCycle) {
    Write-Log "    LifeCycle: $($updateResult.LifeCycle)" 'INFO'
}
Write-Log "    ServiceNow REQ: $($updateResult.Snow_REQ)" 'INFO'
Write-Log "    ServiceNow RITM: $($updateResult.Snow_RITM)" 'INFO'
Write-Log "  Last Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion