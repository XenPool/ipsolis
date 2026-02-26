# SQL - dbo.VDIAssets - Update Asset to Status Deleted
# Marks VDI asset as 'Deleted' and unlinks it from the pool table

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
Write-Log "=== Starting VDI Asset Deletion Process ===" 'INFO'

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
    }
}

# Input parameters
$inputParams = @{
    VMName = '$[VMName]'
}

Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "VM Name: $($inputParams.VMName)" 'INFO'
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
    Write-Log "Retrieving AssetUUID(s) from pool table..." 'INFO'
    
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
    
    # Check if any results were returned
    if ($poolQueryResult.Count -eq 0) {
        Write-Log "No pool entry found for VM: $($inputParams.VMName)" 'WARNING'
        Write-Log "VM may have already been deleted or never existed in pool" 'WARNING'
        $hasAssets = $false
    } else {
        Write-Log "Found $($poolQueryResult.Count) pool entry/entries for VM: $($inputParams.VMName)" 'INFO'
        $hasAssets = $true
    }
    
} catch {
    Write-Log "Failed to retrieve AssetUUID from pool: $_" 'ERROR'
    throw
}
#endregion

#region Update Asset Status to Deleted
if ($hasAssets) {
    try {
        Write-Log "Updating asset status to 'Deleted'..." 'INFO'
        
        $updatedCount = 0
        $failedCount = 0
        
        # Loop through each asset UUID found in the pool
        foreach ($item in $poolQueryResult) {
            $assetUUID = $item.AssetUUID
            
            # Validate AssetUUID before processing
            if ([string]::IsNullOrWhiteSpace($assetUUID)) {
                Write-Log "Skipping entry with null or empty AssetUUID" 'WARNING'
                continue
            }
            
            try {
                Write-Log "Processing AssetUUID: ${assetUUID}" 'INFO'
                
                # Construct SQL query to update asset status to 'Deleted'
                # Using OUTPUT clause to verify the update and capture details
                $updateAssetQuery = @"
UPDATE $($config.SQL.AssetTable)
SET [AssetStatus] = 'Deleted',
    [LastUpdate] = GETDATE()
OUTPUT Inserted.UUID, Inserted.VMName, Inserted.AssetStatus, Deleted.AssetStatus AS PreviousStatus
WHERE UUID = '${assetUUID}'
"@
                
                # Execute the update query
                $updateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                              -Database $config.SQL.Database `
                                              -Username $config.SQL.LoginUser `
                                              -Password $config.SQL.LoginPW `
                                              -Query $updateAssetQuery `
                                              -QueryTimeout 30 `
                                              -ErrorAction Stop
                
                # Verify the update was successful
                if ($null -eq $updateResult) {
                    Write-Log "Asset update failed - UUID may not exist: ${assetUUID}" 'WARNING'
                    $failedCount++
                } else {
                    Write-Log "Asset status updated successfully" 'SUCCESS'
                    Write-Log "  UUID: $($updateResult.UUID)" 'INFO'
                    Write-Log "  VM Name: $($updateResult.VMName)" 'INFO'
                    Write-Log "  Previous Status: $($updateResult.PreviousStatus)" 'INFO'
                    Write-Log "  New Status: $($updateResult.AssetStatus)" 'INFO'
                    $updatedCount++
                }
                
            } catch {
                Write-Log "Failed to update asset ${assetUUID}: $_" 'ERROR'
                $failedCount++
            }
        }
        
        # Summary of asset updates
        Write-Log "Asset update summary: ${updatedCount} succeeded, ${failedCount} failed" 'INFO'
        
        if ($failedCount -gt 0) {
            Write-Log "Some assets failed to update - check logs above for details" 'WARNING'
        }
        
    } catch {
        Write-Log "Critical error during asset status update: $_" 'ERROR'
        throw
    }
} else {
    Write-Log "No assets to update (no AssetUUID found in pool)" 'INFO'
}
#endregion

#region Unlink Asset from Pool Table
try {
    Write-Log "Unlinking asset from pool table..." 'INFO'
    
    # Construct SQL query to set AssetUUID to NULL in pool table
    # This disassociates the VM from any asset
    $updatePoolQuery = @"
UPDATE $($config.SQL.PoolTable)
SET AssetUUID = NULL,
    [LastUpdate] = GETDATE()
OUTPUT Inserted.VMName, Deleted.AssetUUID AS PreviousAssetUUID
WHERE VMName = '${sanitizedVMName}'
"@
    
    Write-Log "Executing pool table update for VM: $($inputParams.VMName)" 'INFO'
    
    # Execute the update query
    $poolUpdateResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                      -Database $config.SQL.Database `
                                      -Username $config.SQL.LoginUser `
                                      -Password $config.SQL.LoginPW `
                                      -Query $updatePoolQuery `
                                      -QueryTimeout 30 `
                                      -ErrorAction Stop
    
    # Verify the update was successful
    if ($null -eq $poolUpdateResult) {
        Write-Log "Pool update returned no results - VM may not exist in pool: $($inputParams.VMName)" 'WARNING'
    } else {
        Write-Log "Pool table updated successfully" 'SUCCESS'
        Write-Log "  VM Name: $($poolUpdateResult.VMName)" 'INFO'
        
        if ([string]::IsNullOrWhiteSpace($poolUpdateResult.PreviousAssetUUID)) {
            Write-Log "  Previous AssetUUID: (none - was already unlinked)" 'INFO'
        } else {
            Write-Log "  Unlinked AssetUUID: $($poolUpdateResult.PreviousAssetUUID)" 'INFO'
        }
    }
    
} catch {
    Write-Log "Failed to update pool table: $_" 'ERROR'
    
    # This is critical but we want to know if assets were updated
    if ($hasAssets -and $updatedCount -gt 0) {
        Write-Log "Assets were marked as 'Deleted' but pool unlinking failed" 'WARNING'
        Write-Log "Manual intervention may be required to unlink VM: $($inputParams.VMName)" 'WARNING'
    }
    
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Asset Deletion Process Completed ===" 'SUCCESS'
Write-Log "Summary:" 'INFO'
Write-Log "  VM Name: $($inputParams.VMName)" 'INFO'

if ($hasAssets) {
    Write-Log "  Assets Processed: $($poolQueryResult.Count)" 'INFO'
    Write-Log "  Assets Updated: ${updatedCount}" 'INFO'
    
    if ($failedCount -gt 0) {
        Write-Log "  Assets Failed: ${failedCount}" 'WARNING'
    }
} else {
    Write-Log "  Assets Processed: 0 (no assets found)" 'INFO'
}

Write-Log "  Pool Status: Unlinked" 'INFO'
Write-Log "  Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion