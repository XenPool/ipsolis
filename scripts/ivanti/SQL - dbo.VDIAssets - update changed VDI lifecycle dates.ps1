# SQL - dbo.VDIAssets - Update Changed VDI Lifecycle Dates
# Updates VDI asset lifecycle dates and resets deactivation flags for extended assets

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
Write-Log "=== Starting VDI Asset Lifecycle Extension ===" 'INFO'

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
        PoolTable      = "^[SQLVDIPoolTable]"
    }
    
    # Lifecycle calculation settings
    Lifecycle = @{
        NotificationDaysBefore = 14  # Send notification 14 days before deactivation
        DeletionDaysAfter      = 7   # Delete 7 days after deactivation
    }
}

# Input parameters
$inputParams = @{
    UUID                  = '$[UUID]'
    DeactivationDatePlan  = '$[DeactivationDatePlan]'
    Snow_REQ              = '$[Snow_REQ]'
    Snow_RITM             = '$[Snow_RITM]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Asset UUID: $($inputParams.UUID)" 'INFO'
Write-Log "New Deactivation Date: $($inputParams.DeactivationDatePlan)" 'INFO'
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
    
    # Validate DeactivationDatePlan format
    if ([string]::IsNullOrWhiteSpace($inputParams.DeactivationDatePlan)) {
        throw "DeactivationDatePlan is required but was not provided"
    }
    
    # Validate ServiceNow reference
    if ([string]::IsNullOrWhiteSpace($inputParams.Snow_REQ) -and 
        [string]::IsNullOrWhiteSpace($inputParams.Snow_RITM)) {
        Write-Log "Warning: No ServiceNow reference (REQ or RITM) provided" 'WARNING'
    }
    
    Write-Log "Input validation completed successfully" 'SUCCESS'
    
} catch {
    Write-Log "Input validation failed: $_" 'ERROR'
    throw
}
#endregion

#region Date Calculations
try {
    Write-Log "Calculating lifecycle dates..." 'INFO'
    
    # Parse the input DeactivationDatePlan (expected format: yyyy-MM-dd)
    try {
        $deactivationDate = [DateTime]::ParseExact(
            $inputParams.DeactivationDatePlan,
            "yyyy-MM-dd",
            $null
        )
        Write-Log "Parsed deactivation date: $($deactivationDate.ToString('yyyy-MM-dd'))" 'INFO'
    } catch {
        throw "Invalid date format for DeactivationDatePlan: '$($inputParams.DeactivationDatePlan)'. Expected format: yyyy-MM-dd"
    }
    
    # Calculate NotificationDatePlan (14 days before deactivation)
    $notificationDate = $deactivationDate.AddDays(-$config.Lifecycle.NotificationDaysBefore)
    $notificationDatePlan = $notificationDate.ToString("yyyy-MM-dd")
    Write-Log "Calculated notification date: ${notificationDatePlan}" 'INFO'
    
    # Format DeactivationDatePlan for SQL
    $deactivationDatePlan = $deactivationDate.ToString("yyyy-MM-dd")
    Write-Log "Formatted deactivation date: ${deactivationDatePlan}" 'INFO'
    
    # Calculate DeletionDatePlan (7 days after deactivation)
    $deletionDate = $deactivationDate.AddDays($config.Lifecycle.DeletionDaysAfter)
    $deletionDatePlan = $deletionDate.ToString("yyyy-MM-dd")
    Write-Log "Calculated deletion date: ${deletionDatePlan}" 'INFO'
    
    # Validate date logic (notification should be before deactivation)
    $today = Get-Date
    if ($notificationDate -lt $today) {
        Write-Log "Warning: Notification date (${notificationDatePlan}) is in the past" 'WARNING'
    }
    
    if ($deactivationDate -lt $today) {
        Write-Log "Warning: Deactivation date (${deactivationDatePlan}) is in the past" 'WARNING'
    }
    
    # Set global variables for downstream processes (if needed)
    $Global:DeletionDatePlan = $deletionDatePlan
    $Global:NotificationDatePlan = $notificationDatePlan
    
    Write-Log "Lifecycle date calculations completed successfully" 'SUCCESS'
    
} catch {
    Write-Log "Date calculation failed: $_" 'ERROR'
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

#region Create IAEvent Log Entry
try {
    Write-Log "Creating IAEvent log entry..." 'INFO'
    
    # Get current date for V_Child1_Namet trail
    $currentDate = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    
    # Build the IAEvent log message with ServiceNow reference
    $snowReference = if ($inputParams.Snow_RITM) {
        "$($inputParams.Snow_REQ)/$($inputParams.Snow_RITM)"
    } elseif ($inputParams.Snow_REQ) {
        $inputParams.Snow_REQ
    } else {
        "Manual"
    }
    
    $newIAEvent = "Asset extension on ${currentDate} by ${snowReference}"
    
    # Sanitize the log entry to prevent SQL injection
    $sanitizedIAEvent = $newIAEvent.Replace("'", "''")
    
    Write-Log "IAEvent entry: ${newIAEvent}" 'INFO'
    
} catch {
    Write-Log "Failed to create IAEvent log entry: $_" 'ERROR'
    throw
}
#endregion

#region Update Asset Lifecycle Dates
try {
    Write-Log "Updating asset lifecycle dates..." 'INFO'
    
    # Sanitize UUID to prevent SQL injection
    $sanitizedUUID = $inputParams.UUID.Replace("'", "''")
    
    # Construct SQL query to update asset lifecycle dates
    # This resets the lifecycle, clears notification/deactivation flags, and logs the extension
    $assetUpdateQuery = @"
UPDATE $($config.SQL.AssetTable)
SET
    [DeletionDatePlan] = '${deletionDatePlan}',
    [DeactivationDatePlan] = '${deactivationDatePlan}',
    [NotificationDatePlan] = '${notificationDatePlan}',
    [Notified] = NULL,
    [Deactivated] = NULL,
    [IAEventLog] = CONCAT(ISNULL(IAEventLog, ''), '${sanitizedIAEvent};'),
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.UUID,
    Inserted.VMName,
    Inserted.DeactivationDatePlan,
    Inserted.NotificationDatePlan,
    Inserted.DeletionDatePlan,
    Deleted.DeactivationDatePlan AS PreviousDeactivationDate,
    Deleted.Notified AS WasNotified,
    Deleted.Deactivated AS WasDeactivated
WHERE UUID = '${sanitizedUUID}'
"@
    
    Write-Log "Executing asset update for UUID: $($inputParams.UUID)" 'INFO'
    
    # Execute the asset update query
    $assetUpdateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                       -Database $config.SQL.Database `
                                       -Username $config.SQL.LoginUser `
                                       -Password $config.SQL.LoginPW `
                                       -Query $assetUpdateQuery `
                                       -QueryTimeout 30 `
                                       -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $assetUpdateResult) {
        $errorMsg = "Asset update failed - UUID may not exist: $($inputParams.UUID)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset lifecycle dates updated successfully" 'SUCCESS'
    Write-Log "  UUID: $($assetUpdateResult.UUID)" 'INFO'
    Write-Log "  VM Name: $($assetUpdateResult.VMName)" 'INFO'
    Write-Log "  Previous Deactivation Date: $($assetUpdateResult.PreviousDeactivationDate)" 'INFO'
    Write-Log "  New Deactivation Date: $($assetUpdateResult.DeactivationDatePlan)" 'INFO'
    Write-Log "  New Notification Date: $($assetUpdateResult.NotificationDatePlan)" 'INFO'
    Write-Log "  New Deletion Date: $($assetUpdateResult.DeletionDatePlan)" 'INFO'
    
    # Log if notification/deactivation flags were reset
    if ($assetUpdateResult.WasNotified) {
        Write-Log "  Notification flag reset (was previously notified)" 'INFO'
    }
    if ($assetUpdateResult.WasDeactivated) {
        Write-Log "  Deactivation flag reset (was previously deactivated)" 'INFO'
    }
    
} catch {
    Write-Log "Failed to update asset lifecycle dates: $_" 'ERROR'
    
    # Provide additional context for common errors
    if ($_.Exception.Message -like "*invalid*date*") {
        Write-Log "Invalid date format in SQL query" 'ERROR'
    }
    
    throw
}
#endregion

#region Update Pool Status to Occupied
try {
    Write-Log "Updating pool status to 'Occupied'..." 'INFO'
    
    # Construct SQL query to update pool status
    # This ensures the VM is marked as occupied after lifecycle extension
    $poolUpdateQuery = @"
UPDATE $($config.SQL.PoolTable)
SET
    [Status] = 'Occupied',
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.VMName,
    Inserted.AssetUUID,
    Inserted.Status,
    Deleted.Status AS PreviousStatus
WHERE AssetUUID = '${sanitizedUUID}'
"@
    
    Write-Log "Executing pool status update for UUID: $($inputParams.UUID)" 'INFO'
    
    # Execute the pool update query
    $poolUpdateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                      -Database $config.SQL.Database `
                                      -Username $config.SQL.LoginUser `
                                      -Password $config.SQL.LoginPW `
                                      -Query $poolUpdateQuery `
                                      -QueryTimeout 30 `
                                      -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $poolUpdateResult) {
        Write-Log "Pool update returned no results - Asset may not be in pool table" 'WARNING'
        Write-Log "This is not critical - asset lifecycle dates were updated successfully" 'INFO'
    } else {
        Write-Log "Pool status updated successfully" 'SUCCESS'
        Write-Log "  VM Name: $($poolUpdateResult.VMName)" 'INFO'
        Write-Log "  AssetUUID: $($poolUpdateResult.AssetUUID)" 'INFO'
        Write-Log "  Previous Status: $($poolUpdateResult.PreviousStatus)" 'INFO'
        Write-Log "  New Status: $($poolUpdateResult.Status)" 'INFO'
    }
    
} catch {
    Write-Log "Failed to update pool status: $_" 'ERROR'
    
    # Pool update failure is not critical - lifecycle dates are already updated
    Write-Log "Asset lifecycle dates were updated, but pool status update failed" 'WARNING'
    Write-Log "Manual verification of pool status may be required" 'WARNING'
    
    # Don't throw - this is a non-critical failure
}
#endregion

#region Completion Summary
Write-Log "=== VDI Asset Lifecycle Extension Completed ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  Asset UUID: $($inputParams.UUID)" 'INFO'

if ($assetUpdateResult) {
    Write-Log "  VM Name: $($assetUpdateResult.VMName)" 'INFO'
    Write-Log "  Lifecycle Extended:" 'INFO'
    Write-Log "    Notification Date: ${notificationDatePlan}" 'INFO'
    Write-Log "    Deactivation Date: ${deactivationDatePlan}" 'INFO'
    Write-Log "    Deletion Date: ${deletionDatePlan}" 'INFO'
    Write-Log "  Flags Reset:" 'INFO'
    Write-Log "    Notified: Cleared" 'INFO'
    Write-Log "    Deactivated: Cleared" 'INFO'
}

Write-Log "  ServiceNow Reference: ${snowReference}" 'INFO'
Write-Log "  Extension Timestamp: ${currentDate}" 'INFO'

if ($poolUpdateResult) {
    Write-Log "  Pool Status: $($poolUpdateResult.Status)" 'INFO'
}

Write-Log "Asset is now active with extended lifecycle dates" 'SUCCESS'
#endregion