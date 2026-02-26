# SQL - dbo.VDIOrders - Update Order Status to Completed
# Marks a VDI order as completed in the database

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
Write-Log "=== Starting Order Status Update to Completed ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Configuration for SQL Server connection
$config = @{
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        OrderTable     = "^[SQLVDIOrderTable]"
    }
}

# Input parameters
$inputParams = @{
    OrderID = '$[OrderID]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Order Table: $($config.SQL.OrderTable)" 'INFO'
Write-Log "Order ID (raw): $($inputParams.OrderID)" 'INFO'
#endregion

#region Input Validation
try {
    Write-Log "Validating input parameters..." 'INFO'
    
    # Validate OrderID is provided
    if ([string]::IsNullOrWhiteSpace($inputParams.OrderID)) {
        throw "OrderID is required but was not provided"
    }
    
    # Convert and validate OrderID as integer
    $orderID = 0
    if (-not [int]::TryParse($inputParams.OrderID, [ref]$orderID)) {
        throw "OrderID must be a valid integer value, received: '$($inputParams.OrderID)'"
    }
    
    # Validate OrderID is positive
    if ($orderID -le 0) {
        throw "OrderID must be a positive integer, received: ${orderID}"
    }
    
    Write-Log "OrderID validated: ${orderID}" 'SUCCESS'
    
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

#region Verify Order Exists
try {
    Write-Log "Verifying order exists..." 'INFO'
    
    # Query to check if order exists and get its current status
    $verifyQuery = @"
SELECT 
    ID,
    Action,
    Status,
    AssetUUID,
    UsecaseID,
    Requestor,
    OrderDate
FROM $($config.SQL.OrderTable)
WHERE ID = ${orderID}
"@
    
    $existingOrder = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                   -Database $config.SQL.Database `
                                   -Username $config.SQL.LoginUser `
                                   -Password $config.SQL.LoginPW `
                                   -Query $verifyQuery `
                                   -QueryTimeout 30 `
                                   -ErrorAction Stop
    
    # Validate order exists
    if ($null -eq $existingOrder) {
        $errorMsg = "Order ID '${orderID}' does not exist in the database"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Order found successfully" 'SUCCESS'
    Write-Log "  Order ID: $($existingOrder.ID)" 'INFO'
    Write-Log "  Action: $($existingOrder.Action)" 'INFO'
    Write-Log "  Current Status: $($existingOrder.Status)" 'INFO'
    Write-Log "  UseCase ID: $($existingOrder.UsecaseID)" 'INFO'
    Write-Log "  Requestor: $($existingOrder.Requestor)" 'INFO'
    Write-Log "  Order Date: $($existingOrder.OrderDate)" 'INFO'
    
    # Check if order is already completed
    if ($existingOrder.Status -eq 'Completed') {
        Write-Log "Warning: Order is already marked as 'Completed'" 'WARNING'
        Write-Log "No update needed, but will proceed to ensure consistency" 'INFO'
    } elseif ($existingOrder.Status -eq 'Pending') {
        Write-Log "Warning: Order status is 'Pending' - typically should be 'InProgress' before completion" 'WARNING'
    }
    
} catch {
    Write-Log "Order verification failed: $_" 'ERROR'
    throw
}
#endregion

#region Update Order Status
try {
    Write-Log "Updating order status to 'Completed'..." 'INFO'
    
    # Construct SQL query to update order status
    # Using OUTPUT clause to verify the update and capture changes
    $updateQuery = @"
UPDATE $($config.SQL.OrderTable)
SET
    [Status] = 'Completed',
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.ID,
    Inserted.Status,
    Inserted.Action,
    Inserted.AssetUUID,
    Inserted.UsecaseID,
    Inserted.Requestor,
    Deleted.Status AS PreviousStatus
WHERE ID = ${orderID}
"@
    
    Write-Log "Executing update query for Order ID: ${orderID}" 'INFO'
    
    # Execute the update query
    $updateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                  -Database $config.SQL.Database `
                                  -Username $config.SQL.LoginUser `
                                  -Password $config.SQL.LoginPW `
                                  -Query $updateQuery `
                                  -QueryTimeout 30 `
                                  -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $updateResult) {
        $errorMsg = "Update failed - no rows were affected. Order ID may not exist: ${orderID}"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Order status updated successfully" 'SUCCESS'
    Write-Log "Update Details:" 'INFO'
    Write-Log "  Order ID: $($updateResult.ID)" 'INFO'
    Write-Log "  Action: $($updateResult.Action)" 'INFO'
    Write-Log "  Previous Status: $($updateResult.PreviousStatus)" 'INFO'
    Write-Log "  New Status: $($updateResult.Status)" 'SUCCESS'
    Write-Log "  UseCase ID: $($updateResult.UsecaseID)" 'INFO'
    Write-Log "  Requestor: $($updateResult.Requestor)" 'INFO'
    
    # Log status transition
    if ($updateResult.PreviousStatus -ne $updateResult.Status) {
        Write-Log "  Status Transition: '$($updateResult.PreviousStatus)' → '$($updateResult.Status)'" 'SUCCESS'
    } else {
        Write-Log "  Status unchanged (already 'Completed')" 'INFO'
    }
    
    # Display AssetUUID if available
    if (-not [string]::IsNullOrWhiteSpace($updateResult.AssetUUID)) {
        Write-Log "  AssetUUID: $($updateResult.AssetUUID)" 'INFO'
    }
    
} catch {
    Write-Log "Failed to update order status: $_" 'ERROR'
    
    # Provide additional context for common errors
    if ($_.Exception.Message -like "*timeout*") {
        Write-Log "Database query timed out - database may be overloaded" 'ERROR'
    }
    elseif ($_.Exception.Message -like "*deadlock*") {
        Write-Log "Database deadlock detected - another process may be updating the same order" 'ERROR'
    }
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== Order Status Update to Completed Successfully ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  Order ID: ${orderID}" 'INFO'
Write-Log "  Final Status: Completed" 'SUCCESS'
Write-Log "  Action Type: $($updateResult.Action)" 'INFO'
Write-Log "  Requestor: $($updateResult.Requestor)" 'INFO'
Write-Log "  Last Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion