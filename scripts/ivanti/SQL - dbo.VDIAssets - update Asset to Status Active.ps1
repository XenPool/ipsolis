# SQL - dbo.VDIAssets - Update Asset to Status Active
# Updates VDI asset status to 'Active' and links it to an order

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
Write-Log "=== Starting VDI Asset Status Update ===" 'INFO'

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
        AssetTable     = "^[SQLVDIAssetTable]"
        OrderTable     = "^[SQLVDIOrderTable]"
    }
}

# Input parameters
$inputParams = @{
    VMName  = '$[VMName]'
    OrderID = '$[OrderID]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "VM Name: $($inputParams.VMName)" 'INFO'
Write-Log "Order ID: $($inputParams.OrderID)" 'INFO'
#endregion

#region Input Validation
try {
    Write-Log "Validating input parameters..." 'INFO'
    
    # Validate VMName
    if ([string]::IsNullOrWhiteSpace($inputParams.VMName)) {
        throw "VMName is required but was not provided"
    }
    
    # Validate OrderID
    if ([string]::IsNullOrWhiteSpace($inputParams.OrderID)) {
        throw "OrderID is required but was not provided"
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

#region Retrieve AssetUUID from Pool Table
try {
    Write-Log "Retrieving AssetUUID from pool table..." 'INFO'
    
    # Sanitize VMName to prevent SQL injection
    $sanitizedVMName = $inputParams.VMName.Replace("'", "''")
    
    # Construct SQL query to get AssetUUID for the specified VM
    $poolQuery = @"
SELECT AssetUUID
FROM $($config.SQL.PoolTable)
WHERE VMName = '${sanitizedVMName}'
"@
    
    Write-Log "Executing pool query for VM: $($inputParams.VMName)" 'INFO'
    
    # Execute query to retrieve AssetUUID
    $poolQueryResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                     -Database $config.SQL.Database `
                                     -Username $config.SQL.LoginUser `
                                     -Password $config.SQL.LoginPW `
                                     -Query $poolQuery `
                                     -QueryTimeout 30 `
                                     -ErrorAction Stop
    
    # Ensure result is always treated as an array
    if ($null -eq $poolQueryResult) {
        $poolQueryResult = @()
    } elseif ($poolQueryResult -isnot [System.Array]) {
        $poolQueryResult = @($poolQueryResult)
    }
    
    # Validate query result
    if ($poolQueryResult.Count -eq 0) {
        $errorMsg = "No pool entry found for VM: $($inputParams.VMName)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    # Check if multiple results were returned
    if ($poolQueryResult.Count -gt 1) {
        Write-Log "Warning: Multiple pool entries found for VM '$($inputParams.VMName)', using first match" 'WARNING'
    }
    
    # Extract AssetUUID from first result
    $assetUUID = $poolQueryResult[0].AssetUUID
    
    # Validate AssetUUID
    if ([string]::IsNullOrWhiteSpace($assetUUID)) {
        $errorMsg = "AssetUUID is null or empty for VM: $($inputParams.VMName)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "AssetUUID retrieved successfully: ${assetUUID}" 'SUCCESS'
    
} catch {
    Write-Log "Failed to retrieve AssetUUID: $_" 'ERROR'
    throw
}
#endregion

#region Update Asset Status to Active
try {
    Write-Log "Updating asset status to 'Active'..." 'INFO'
    
    # Construct SQL query to update asset status
    # Using OUTPUT clause to verify the update
    $updateAssetQuery = @"
UPDATE $($config.SQL.AssetTable)
SET [AssetStatus] = 'Active',
    [LastUpdate] = GETDATE()
OUTPUT Inserted.UUID, Inserted.VMName, Inserted.AssetStatus
WHERE UUID = '${assetUUID}'
"@
    
    Write-Log "Executing asset status update for UUID: ${assetUUID}" 'INFO'
    
    # Execute update query
    $updateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                  -Database $config.SQL.Database `
                                  -Username $config.SQL.LoginUser `
                                  -Password $config.SQL.LoginPW `
                                  -Query $updateAssetQuery `
                                  -QueryTimeout 30 `
                                  -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $updateResult) {
        $errorMsg = "Asset update failed - no rows were affected. UUID may not exist: ${assetUUID}"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset status updated successfully" 'SUCCESS'
    Write-Log "  UUID: $($updateResult.UUID)" 'INFO'
    Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
    Write-Log "  New Status: $($updateResult.AssetStatus)" 'INFO'
    
} catch {
    Write-Log "Failed to update asset status: $_" 'ERROR'
    throw
}
#endregion

#region Update Order Table with AssetUUID
try {
    Write-Log "Linking AssetUUID to Order..." 'INFO'
    
    # Sanitize OrderID to prevent SQL injection
    $sanitizedOrderID = $inputParams.OrderID.ToString().Replace("'", "''")
    
    # Construct SQL query to update order with AssetUUID
    # Using OUTPUT clause to verify the update
    $updateOrderQuery = @"
UPDATE $($config.SQL.OrderTable)
SET AssetUUID = '${assetUUID}',
    [LastUpdate] = GETDATE()
OUTPUT Inserted.ID, Inserted.AssetUUID, Inserted.Status, Deleted.AssetUUID AS PreviousAssetUUID
WHERE ID = '${sanitizedOrderID}'
"@
    
    Write-Log "Executing order update for OrderID: $($inputParams.OrderID)" 'INFO'
    
    # Execute update query
    $orderUpdateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                       -Database $config.SQL.Database `
                                       -Username $config.SQL.LoginUser `
                                       -Password $config.SQL.LoginPW `
                                       -Query $updateOrderQuery `
                                       -QueryTimeout 30 `
                                       -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $orderUpdateResult) {
        $errorMsg = "Order update failed - no rows were affected. OrderID may not exist: $($inputParams.OrderID)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Order updated successfully" 'SUCCESS'
    Write-Log "  Order ID: $($orderUpdateResult.ID)" 'INFO'
    
    # Check if AssetUUID was already linked
    if (-not [string]::IsNullOrWhiteSpace($orderUpdateResult.PreviousAssetUUID)) {
        Write-Log "  Previous AssetUUID: $($orderUpdateResult.PreviousAssetUUID)" 'WARNING'
        Write-Log "  Note: AssetUUID was overwritten" 'WARNING'
    }
    
    Write-Log "  New AssetUUID: $($orderUpdateResult.AssetUUID)" 'SUCCESS'
    Write-Log "  Order Status: $($orderUpdateResult.Status)" 'INFO'
    
} catch {
    Write-Log "Failed to update order: $_" 'ERROR'
    
    # This is critical but not fatal - asset is already activated
    Write-Log "Asset status was updated to 'Active' but order linking failed" 'WARNING'
    Write-Log "Manual intervention may be required to link OrderID $($inputParams.OrderID) to AssetUUID ${assetUUID}" 'WARNING'
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Asset Status Update Completed Successfully ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  VM Name: $($inputParams.VMName)" 'INFO'
Write-Log "  AssetUUID: ${assetUUID}" 'INFO'
Write-Log "  Asset Status: Active" 'INFO'
Write-Log "  Linked Order ID: $($inputParams.OrderID)" 'INFO'
Write-Log "  Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion