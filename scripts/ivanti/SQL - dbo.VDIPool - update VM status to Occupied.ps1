# SQL - dbo.VDIPool - Update VM Status to Occupied
# Marks a VM as occupied in the pool and completes the associated order

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
Write-Log "=== Starting VM Status Update to Occupied ===" 'INFO'

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
        OrderTable     = "^[SQLVDIOrderTable]"
    }
}

# Input parameters
$inputParams = @{
    UUID      = '$[UUID]'
    VMName    = '$[VMName]'
    OrderID   = '$[OrderID]'
    Snow_REQ  = '$[Snow_REQ]'
    Snow_RITM = '$[Snow_RITM]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "VM Name: $($inputParams.VMName)" 'INFO'
Write-Log "Asset UUID: $($inputParams.UUID)" 'INFO'
Write-Log "Order ID: $($inputParams.OrderID)" 'INFO'
Write-Log "ServiceNow REQ: $($inputParams.Snow_REQ)" 'INFO'
Write-Log "ServiceNow RITM: $($inputParams.Snow_RITM)" 'INFO'
#endregion

#region Input Validation
try {
    Write-Log "Validating input parameters..." 'INFO'
    
    # Validate UUID
    if ([string]::IsNullOrWhiteSpace($inputParams.UUID)) {
        throw "UUID is required but was not provided"
    }
    
    # Validate VMName
    if ([string]::IsNullOrWhiteSpace($inputParams.VMName)) {
        throw "VMName is required but was not provided"
    }
    
    # Validate OrderID
    if ([string]::IsNullOrWhiteSpace($inputParams.OrderID)) {
        throw "OrderID is required but was not provided"
    }
    
    # Convert and validate OrderID as integer
    $orderID = 0
    if (-not [int]::TryParse($inputParams.OrderID, [ref]$orderID)) {
        throw "OrderID must be a valid integer value, received: '$($inputParams.OrderID)'"
    }
    
    if ($orderID -le 0) {
        throw "OrderID must be a positive integer, received: ${orderID}"
    }
    
    Write-Log "OrderID validated: ${orderID}" 'INFO'
    
    # Validate ServiceNow references
    if ([string]::IsNullOrWhiteSpace($inputParams.Snow_REQ) -and 
        [string]::IsNullOrWhiteSpace($inputParams.Snow_RITM)) {
        Write-Log "Warning: No ServiceNow reference (REQ or RITM) provided" 'WARNING'
    }
    
    # Sanitize all inputs to prevent SQL injection
    $sanitizedParams = @{
        UUID      = $inputParams.UUID.Replace("'", "''")
        VMName    = $inputParams.VMName.Replace("'", "''")
        OrderID   = $orderID
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

#region Verify VM Exists in Pool
try {
    Write-Log "Verifying VM exists in pool table..." 'INFO'
    
    # Query to check if VM exists and get its current status
    $verifyVMQuery = @"
SELECT 
    VMName,
    AssetUUID,
    Status,
    UsecaseID
FROM $($config.SQL.PoolTable)
WHERE VMName = '$($sanitizedParams.VMName)'
"@
    
    $existingVM = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                -Database $config.SQL.Database `
                                -Username $config.SQL.LoginUser `
                                -Password $config.SQL.LoginPW `
                                -Query $verifyVMQuery `
                                -QueryTimeout 30 `
                                -ErrorAction Stop
    
    # Validate VM exists
    if ($null -eq $existingVM) {
        $errorMsg = "VM '$($inputParams.VMName)' does not exist in pool table"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "VM found in pool table" 'SUCCESS'
    Write-Log "  Current Status: $($existingVM.Status)" 'INFO'
    Write-Log "  Current AssetUUID: $($existingVM.AssetUUID)" 'INFO'
    Write-Log "  UseCase ID: $($existingVM.UsecaseID)" 'INFO'
    
    # Warn if VM is already occupied
    if ($existingVM.Status -eq 'Occupied') {
        Write-Log "Warning: VM is already marked as 'Occupied'" 'WARNING'
        
        if (-not [string]::IsNullOrWhiteSpace($existingVM.AssetUUID)) {
            Write-Log "Warning: VM is already linked to AssetUUID: $($existingVM.AssetUUID)" 'WARNING'
        }
    }
    
} catch {
    Write-Log "VM verification failed: $_" 'ERROR'
    throw
}
#endregion

#region Update Pool Table - Set VM to Occupied
try {
    Write-Log "Updating pool table - setting VM to Occupied..." 'INFO'
    
    # Construct SQL query to update VM status in pool table
    # Using OUTPUT clause to verify the update and capture changes
    $updatePoolQuery = @"
UPDATE $($config.SQL.PoolTable)
SET
    [AssetUUID] = '$($sanitizedParams.UUID)',
    [Snow_REQ] = '$($sanitizedParams.Snow_REQ)',
    [Snow_RITM] = '$($sanitizedParams.Snow_RITM)',
    [Status] = 'Occupied',
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.VMName,
    Inserted.AssetUUID,
    Inserted.Status,
    Inserted.Snow_REQ,
    Inserted.Snow_RITM,
    Deleted.Status AS PreviousStatus,
    Deleted.AssetUUID AS PreviousAssetUUID
WHERE VMName = '$($sanitizedParams.VMName)'
"@
    
    Write-Log "Executing pool table update for VM: $($inputParams.VMName)" 'INFO'
    
    # Execute the pool update query
    $poolUpdateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                      -Database $config.SQL.Database `
                                      -Username $config.SQL.LoginUser `
                                      -Password $config.SQL.LoginPW `
                                      -Query $updatePoolQuery `
                                      -QueryTimeout 30 `
                                      -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $poolUpdateResult) {
        $errorMsg = "Pool update failed - no rows were affected. VM may not exist: $($inputParams.VMName)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Pool table updated successfully" 'SUCCESS'
    Write-Log "  VM Name: $($poolUpdateResult.VMName)" 'INFO'
    Write-Log "  AssetUUID: $($poolUpdateResult.AssetUUID)" 'INFO'
    Write-Log "  Previous Status: $($poolUpdateResult.PreviousStatus)" 'INFO'
    Write-Log "  New Status: $($poolUpdateResult.Status)" 'INFO'
    Write-Log "  ServiceNow REQ: $($poolUpdateResult.Snow_REQ)" 'INFO'
    Write-Log "  ServiceNow RITM: $($poolUpdateResult.Snow_RITM)" 'INFO'
    
} catch {
    Write-Log "Failed to update pool table: $_" 'ERROR'
    throw
}
#endregion

#region Verify Order Exists
try {
    Write-Log "Verifying order exists..." 'INFO'
    
    # Query to check if order exists and get its current status
    $verifyOrderQuery = @"
SELECT 
    ID,
    Action,
    Status,
    AssetUUID,
    Requestor,
    OrderDate
FROM $($config.SQL.OrderTable)
WHERE ID = '$($sanitizedParams.OrderID)'
"@
    
    $existingOrder = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                   -Database $config.SQL.Database `
                                   -Username $config.SQL.LoginUser `
                                   -Password $config.SQL.LoginPW `
                                   -Query $verifyOrderQuery `
                                   -QueryTimeout 30 `
                                   -ErrorAction Stop
    
    # Validate order exists
    if ($null -eq $existingOrder) {
        $errorMsg = "Order ID '$($sanitizedParams.OrderID)' does not exist"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Order found" 'SUCCESS'
    Write-Log "  Order ID: $($existingOrder.ID)" 'INFO'
    Write-Log "  Action: $($existingOrder.Action)" 'INFO'
    Write-Log "  Current Status: $($existingOrder.Status)" 'INFO'
    Write-Log "  Requestor: $($existingOrder.Requestor)" 'INFO'
    Write-Log "  Order Date: $($existingOrder.OrderDate)" 'INFO'
    
    # Warn if order is already completed
    if ($existingOrder.Status -eq 'Completed') {
        Write-Log "Warning: Order is already marked as 'Completed'" 'WARNING'
    }
    
} catch {
    Write-Log "Order verification failed: $_" 'ERROR'
    throw
}
#endregion

#region Update Order Table - Set Status to Completed
try {
    Write-Log "Updating order table - setting status to Completed..." 'INFO'
    
    # Construct SQL query to update order status to 'Completed'
    # Using OUTPUT clause to verify the update and capture changes
    $updateOrderQuery = @"
UPDATE $($config.SQL.OrderTable)
SET
    [Status] = 'Completed',
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.ID,
    Inserted.Status,
    Inserted.Action,
    Inserted.AssetUUID,
    Deleted.Status AS PreviousStatus
WHERE ID = '$($sanitizedParams.OrderID)'
"@
    
    Write-Log "Executing order table update for Order ID: $($sanitizedParams.OrderID)" 'INFO'
    
    # Execute the order update query
    $orderUpdateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                       -Database $config.SQL.Database `
                                       -Username $config.SQL.LoginUser `
                                       -Password $config.SQL.LoginPW `
                                       -Query $updateOrderQuery `
                                       -QueryTimeout 30 `
                                       -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $orderUpdateResult) {
        $errorMsg = "Order update failed - no rows were affected. Order ID may not exist: $($sanitizedParams.OrderID)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Order table updated successfully" 'SUCCESS'
    Write-Log "  Order ID: $($orderUpdateResult.ID)" 'INFO'
    Write-Log "  Action: $($orderUpdateResult.Action)" 'INFO'
    Write-Log "  Previous Status: $($orderUpdateResult.PreviousStatus)" 'INFO'
    Write-Log "  New Status: $($orderUpdateResult.Status)" 'INFO'
    Write-Log "  AssetUUID: $($orderUpdateResult.AssetUUID)" 'INFO'
    
} catch {
    Write-Log "Failed to update order table: $_" 'ERROR'
    
    # This is critical - VM is marked as occupied but order update failed
    Write-Log "VM was marked as 'Occupied' but order completion failed" 'WARNING'
    Write-Log "Manual intervention may be required to complete Order ID: $($sanitizedParams.OrderID)" 'WARNING'
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VM Status Update to Occupied Completed Successfully ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  VM Name: $($inputParams.VMName)" 'INFO'
Write-Log "  AssetUUID: $($inputParams.UUID)" 'INFO'
Write-Log "  VM Status: Occupied" 'SUCCESS'
Write-Log "  Order ID: $($sanitizedParams.OrderID)" 'INFO'
Write-Log "  Order Status: Completed" 'SUCCESS'
Write-Log "  ServiceNow Reference: $($inputParams.Snow_REQ)/$($inputParams.Snow_RITM)" 'INFO'
Write-Log "  Processing completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion