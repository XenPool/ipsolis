# SQL - dbo.VDIAssets - Update Changed VDI Asset
# Updates VDI asset with modified ownership, access rights, and lifecycle information

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
Write-Log "=== Starting VDI Asset Update ===" 'INFO'

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
$updateParams = @{
    UUID         = '$[UUID]'
    Owner        = '$[Owner]'
    SecondOwner  = '$[SecondOwner]'
    RDPUserIDs   = '$[RDPUserIDs]'
    LocalAdmins  = '$[LocalAdmins]'
    Snow_REQ     = '$[Snow_REQ]'
    Snow_RITM    = '$[Snow_RITM]'
    LifeCycle    = '$[LifeCycle]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Target Table: $($config.SQL.AssetTable)" 'INFO'
Write-Log "Asset UUID: $($updateParams.UUID)" 'INFO'
#endregion

#region Input Validation and Sanitization
try {
    Write-Log "Validating and sanitizing input parameters..." 'INFO'
    
    # Validate UUID (required field)
    if ([string]::IsNullOrWhiteSpace($updateParams.UUID)) {
        throw "UUID is required but was not provided"
    }
    
    # Sanitize RDPUserIDs: Replace double semicolons with single semicolon
    if (-not [string]::IsNullOrWhiteSpace($updateParams.RDPUserIDs)) {
        $originalRDPUserIDs = $updateParams.RDPUserIDs
        $updateParams.RDPUserIDs = $updateParams.RDPUserIDs.Replace(';;', ';')
        
        if ($originalRDPUserIDs -ne $updateParams.RDPUserIDs) {
            Write-Log "Sanitized RDPUserIDs (removed double semicolons)" 'WARNING'
        }
        Write-Log "RDP Users: $($updateParams.RDPUserIDs)" 'INFO'
    } else {
        Write-Log "RDP Users: (empty)" 'INFO'
    }
    
    # Sanitize LocalAdmins: Replace double semicolons with single semicolon
    if (-not [string]::IsNullOrWhiteSpace($updateParams.LocalAdmins)) {
        $originalLocalAdmins = $updateParams.LocalAdmins
        $updateParams.LocalAdmins = $updateParams.LocalAdmins.Replace(';;', ';')
        
        if ($originalLocalAdmins -ne $updateParams.LocalAdmins) {
            Write-Log "Sanitized LocalAdmins (removed double semicolons)" 'WARNING'
        }
        Write-Log "Local Admins: $($updateParams.LocalAdmins)" 'INFO'
    } else {
        Write-Log "Local Admins: (empty)" 'INFO'
    }
    
    # Validate and log LifeCycle parameter
    # LifeCycle = '0' means "do not update" (user didn't change it)
    if ($updateParams.LifeCycle -eq '0') {
        Write-Log "LifeCycle: Not updated (user did not modify)" 'INFO'
        $updateLifeCycle = $false
    } else {
        Write-Log "LifeCycle: $($updateParams.LifeCycle)" 'INFO'
        $updateLifeCycle = $true
    }
    
    # Sanitize all string fields to prevent SQL injection
    $sanitizedParams = @{
        UUID        = $updateParams.UUID.Replace("'", "''")
        Owner       = $updateParams.Owner.Replace("'", "''")
        SecondOwner = $updateParams.SecondOwner.Replace("'", "''")
        RDPUserIDs  = $updateParams.RDPUserIDs.Replace("'", "''")
        LocalAdmins = $updateParams.LocalAdmins.Replace("'", "''")
        Snow_REQ    = $updateParams.Snow_REQ.Replace("'", "''")
        Snow_RITM   = $updateParams.Snow_RITM.Replace("'", "''")
        LifeCycle   = $updateParams.LifeCycle.Replace("'", "''")
    }
    
    Write-Log "Input validation and sanitization completed successfully" 'SUCCESS'
    
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

#region Verify Asset Exists
try {
    Write-Log "Verifying asset exists before update..." 'INFO'
    
    # Check if the asset exists in the database
    $verifyQuery = @"
SELECT UUID, VMName, AssetStatus
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
        $errorMsg = "Asset with UUID '$($updateParams.UUID)' does not exist"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset found: $($existingAsset.VMName) (Status: $($existingAsset.AssetStatus))" 'SUCCESS'
    
} catch {
    Write-Log "Asset verification failed: $_" 'ERROR'
    throw
}
#endregion

#region Construct SQL Update Query
try {
    Write-Log "Constructing SQL UPDATE query..." 'INFO'
    
    # Build the UPDATE query dynamically with sanitized parameters
    # Using OUTPUT clause to capture updated values for verification
    $query = @"
UPDATE $($config.SQL.AssetTable)
SET
    [Owner] = '$($sanitizedParams.Owner)',
    [SecondOwner] = '$($sanitizedParams.SecondOwner)',
    [RDPUserIDs] = '$($sanitizedParams.RDPUserIDs)',
    [LocalAdmins] = '$($sanitizedParams.LocalAdmins)',
    [Snow_REQ] = '$($sanitizedParams.Snow_REQ)',
    [Snow_RITM] = '$($sanitizedParams.Snow_RITM)',
"@
    
    # Conditionally add LifeCycle update
    # Only include LifeCycle in UPDATE if user actually changed it (not '0')
    if ($updateLifeCycle) {
        $query += "`n    [LifeCycle] = '$($sanitizedParams.LifeCycle)',"
        Write-Log "LifeCycle will be updated to: $($updateParams.LifeCycle)" 'INFO'
    } else {
        Write-Log "LifeCycle will NOT be updated (value is 0)" 'INFO'
    }
    
    # Add LastUpdate timestamp and OUTPUT clause
    $query += @"
    [LastUpdate] = GETDATE()
OUTPUT 
    Inserted.UUID,
    Inserted.VMName,
    Inserted.Owner,
    Inserted.SecondOwner,
    Inserted.LifeCycle,
    Deleted.Owner AS PreviousOwner,
    Deleted.SecondOwner AS PreviousSecondOwner,
    Deleted.LifeCycle AS PreviousLifeCycle
WHERE UUID = '$($sanitizedParams.UUID)';
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
    Write-Log "Updating asset: $($existingAsset.VMName)..." 'INFO'
    
    # Execute the UPDATE query
    $updateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                  -Database $config.SQL.Database `
                                  -Username $config.SQL.LoginUser `
                                  -Password $config.SQL.LoginPW `
                                  -Query $query `
                                  -QueryTimeout 30 `
                                  -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $updateResult) {
        $errorMsg = "Update failed - no rows were affected. UUID may not exist: $($updateParams.UUID)"
        Write-Log $errorMsg 'ERROR'
        throw $errorMsg
    }
    
    Write-Log "Asset updated successfully" 'SUCCESS'
    
    # Log the changes made
    Write-Log "Update Details:" 'INFO'
    Write-Log "  UUID: $($updateResult.UUID)" 'INFO'
    Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
    
    # Show Owner changes
    if ($updateResult.PreviousOwner -ne $updateResult.Owner) {
        Write-Log "  Owner: '$($updateResult.PreviousOwner)' → '$($updateResult.Owner)'" 'INFO'
    } else {
        Write-Log "  Owner: $($updateResult.Owner) (unchanged)" 'INFO'
    }
    
    # Show SecondOwner changes
    if ($updateResult.PreviousSecondOwner -ne $updateResult.SecondOwner) {
        Write-Log "  Second Owner: '$($updateResult.PreviousSecondOwner)' → '$($updateResult.SecondOwner)'" 'INFO'
    } else {
        Write-Log "  Second Owner: $($updateResult.SecondOwner) (unchanged)" 'INFO'
    }
    
    # Show LifeCycle changes (only if it was updated)
    if ($updateLifeCycle) {
        if ($updateResult.PreviousLifeCycle -ne $updateResult.LifeCycle) {
            Write-Log "  LifeCycle: '$($updateResult.PreviousLifeCycle)' → '$($updateResult.LifeCycle)'" 'INFO'
        } else {
            Write-Log "  LifeCycle: $($updateResult.LifeCycle) (unchanged)" 'INFO'
        }
    } else {
        Write-Log "  LifeCycle: $($updateResult.LifeCycle) (not modified by user)" 'INFO'
    }
    
} catch {
    Write-Log "Failed to update asset: $_" 'ERROR'
    
    # Provide additional context for common errors
    if ($_.Exception.Message -like "*foreign key*") {
        Write-Log "Invalid reference - check Owner or SecondOwner values" 'ERROR'
    }
    elseif ($_.Exception.Message -like "*timeout*") {
        Write-Log "Database query timed out - database may be overloaded" 'ERROR'
    }
    elseif ($_.Exception.Message -like "*deadlock*") {
        Write-Log "Database deadlock detected - another process may be updating the same asset" 'ERROR'
    }
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Asset Update Completed Successfully ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  Asset UUID: $($updateParams.UUID)" 'INFO'
Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
Write-Log "  Owner: $($updateResult.Owner)" 'INFO'
Write-Log "  Second Owner: $($updateResult.SecondOwner)" 'INFO'
Write-Log "  RDP Users: $($updateParams.RDPUserIDs)" 'INFO'
Write-Log "  Local Admins: $($updateParams.LocalAdmins)" 'INFO'
Write-Log "  ServiceNow REQ: $($updateParams.Snow_REQ)" 'INFO'
Write-Log "  ServiceNow RITM: $($updateParams.Snow_RITM)" 'INFO'

if ($updateLifeCycle) {
    Write-Log "  LifeCycle: $($updateResult.LifeCycle)" 'INFO'
} else {
    Write-Log "  LifeCycle: Not modified" 'INFO'
}

Write-Log "  Last Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion