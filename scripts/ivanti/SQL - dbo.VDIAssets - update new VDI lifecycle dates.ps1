# SQL - dbo.VDIAssets - Update New VDI Lifecycle Dates
# Sets initial lifecycle dates for a newly created VDI asset

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
Write-Log "=== Starting New VDI Asset Lifecycle Date Setup ===" 'INFO'

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
    
    # Lifecycle calculation settings
    Lifecycle = @{
        NotificationDaysBefore = 14  # Send notification 14 days before deactivation
        DeletionDaysAfter      = 7   # Delete 7 days after deactivation
    }
}

# Input parameters
$inputParams = @{
    UUID                 = '$[UUID]'
    DeactivationDatePlan = '$[DeactivationDatePlan]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Asset UUID: $($inputParams.UUID)" 'INFO'
Write-Log "Deactivation Date: $($inputParams.DeactivationDatePlan)" 'INFO'
#endregion

#region Input Validation
try {
    Write-Log "Validating input parameters..." 'INFO'
    
    # Validate UUID
    if ([string]::IsNullOrWhiteSpace($inputParams.UUID)) {
        throw "UUID is required but was not provided"
    }
    
    # Validate DeactivationDatePlan
    if ([string]::IsNullOrWhiteSpace($inputParams.DeactivationDatePlan)) {
        throw "DeactivationDatePlan is required but was not provided"
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
    
    # Validate date logic
    $today = Get-Date
    if ($notificationDate -lt $today) {
        Write-Log "Warning: Notification date (${notificationDatePlan}) is in the past" 'WARNING'
    }
    
    if ($deactivationDate -lt $today) {
        Write-Log "Warning: Deactivation date (${deactivationDatePlan}) is in the past" 'WARNING'
    }
    
    # Set global variables for downstream processes (compatibility with existing workflow)
    $Global:DeletionDatePlan = $deletionDatePlan
    $Global:NotificationDatePlan = $notificationDatePlan
    
    Write-Log "Lifecycle date calculations completed successfully" 'SUCCESS'
    Write-Log "  Notification: ${notificationDatePlan} ($($config.Lifecycle.NotificationDaysBefore) days before deactivation)" 'INFO'
    Write-Log "  Deactivation: ${deactivationDatePlan}" 'INFO'
    Write-Log "  Deletion: ${deletionDatePlan} ($($config.Lifecycle.DeletionDaysAfter) days after deactivation)" 'INFO'
    
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

#region Verify Asset Exists
try {
    Write-Log "Verifying asset exists before update..." 'INFO'
    
    # Clean the UUID input - remove any whitespace, newlines, or embedded log messages
    $cleanUUID = $inputParams.UUID -replace '\s+', '' -replace '\[.*?\]', '' -replace '[^\d]', ''
    
    # Validate UUID is numeric
    if (-not ($cleanUUID -match '^\d+$')) {
        $errorMsg = "Invalid UUID format: '$($inputParams.UUID)'. Expected numeric value, got: '$cleanUUID'"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Cleaned UUID: $cleanUUID (original: $($inputParams.UUID))" 'INFO'
    
    # Sanitize UUID to prevent SQL injection
    $sanitizedUUID = $cleanUUID.Replace("'", "''")
    
    # Check if the asset exists in the database
    $verifyQuery = @"
SELECT UUID, VMName, AssetStatus, DeactivationDatePlan
FROM $($config.SQL.AssetTable)
WHERE UUID = '${sanitizedUUID}'
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
        $errorMsg = "Asset with UUID '$cleanUUID' does not exist in database"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset found: $($existingAsset.VMName) (Status: $($existingAsset.AssetStatus))" 'SUCCESS'
    
    # Check if lifecycle dates are already set
    if (-not [string]::IsNullOrWhiteSpace($existingAsset.DeactivationDatePlan)) {
        Write-Log "Warning: Asset already has lifecycle dates set (DeactivationDatePlan: $($existingAsset.DeactivationDatePlan))" 'WARNING'
        Write-Log "Existing dates will be overwritten" 'WARNING'
    }
    
} catch {
    Write-Log "Asset verification failed: $_" 'ERROR'
    throw
}
#endregion

#region Construct SQL Update Query
try {
    Write-Log "Constructing SQL UPDATE query..." 'INFO'
    
    # Build the UPDATE query with OUTPUT clause to verify changes
    $updateQuery = @"
UPDATE $($config.SQL.AssetTable)
SET
    [DeletionDatePlan] = '${deletionDatePlan}',
    [DeactivationDatePlan] = '${deactivationDatePlan}',
    [NotificationDatePlan] = '${notificationDatePlan}',
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.UUID,
    Inserted.VMName,
    Inserted.DeactivationDatePlan,
    Inserted.NotificationDatePlan,
    Inserted.DeletionDatePlan,
    Deleted.DeactivationDatePlan AS PreviousDeactivationDate,
    Deleted.NotificationDatePlan AS PreviousNotificationDate,
    Deleted.DeletionDatePlan AS PreviousDeletionDate
WHERE UUID = '${sanitizedUUID}'
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
    Write-Log "Setting lifecycle dates for asset: $($existingAsset.VMName)..." 'INFO'
    
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
    
    Write-Log "Lifecycle dates updated successfully" 'SUCCESS'
    
    # Log the changes made
    Write-Log "Update Details:" 'INFO'
    Write-Log "  UUID: $($updateResult.UUID)" 'INFO'
    Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
    
    # Show if dates were previously set or newly set
    if ([string]::IsNullOrWhiteSpace($updateResult.PreviousDeactivationDate)) {
        Write-Log "  Lifecycle dates newly set:" 'INFO'
    } else {
        Write-Log "  Lifecycle dates updated from previous values:" 'INFO'
        Write-Log "    Previous Notification: $(Get-Date $updateResult.PreviousNotificationDate -Format 'yyyy-MM-dd')" 'INFO'
        Write-Log "    Previous Deactivation: $(Get-Date $updateResult.PreviousDeactivationDate -Format 'yyyy-MM-dd')" 'INFO'
        Write-Log "    Previous Deletion: $(Get-Date $updateResult.PreviousDeletionDate -Format 'yyyy-MM-dd')" 'INFO'
    }
    
    Write-Log "  New lifecycle dates:" 'INFO'
    Write-Log "    Notification Date: $(Get-Date $updateResult.NotificationDatePlan -Format 'yyyy-MM-dd')" 'SUCCESS'
    Write-Log "    Deactivation Date: $(Get-Date $updateResult.DeactivationDatePlan -Format 'yyyy-MM-dd')" 'SUCCESS'
    Write-Log "    Deletion Date: $(Get-Date $updateResult.DeletionDatePlan -Format 'yyyy-MM-dd')" 'SUCCESS'
    
} catch {
    Write-Log "Failed to update lifecycle dates: $_" 'ERROR'
    
    # Provide additional context for common errors
    if ($_.Exception.Message -like "*invalid*date*") {
        Write-Log "Invalid date format in SQL query" 'ERROR'
    }
    elseif ($_.Exception.Message -like "*timeout*") {
        Write-Log "Database query timed out - database may be overloaded" 'ERROR'
    }
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== New VDI Asset Lifecycle Date Setup Completed ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  Asset UUID: $($inputParams.UUID)" 'INFO'
Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
Write-Log "  Lifecycle Configuration:" 'INFO'
Write-Log "    Notification Date: ${notificationDatePlan}" 'INFO'
Write-Log "    Deactivation Date: ${deactivationDatePlan}" 'INFO'
Write-Log "    Deletion Date: ${deletionDatePlan}" 'INFO'
Write-Log "  Timeline:" 'INFO'
Write-Log "    User will be notified $($config.Lifecycle.NotificationDaysBefore) days before deactivation" 'INFO'
Write-Log "    Asset will be deleted $($config.Lifecycle.DeletionDaysAfter) days after deactivation" 'INFO'
Write-Log "  Last Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion