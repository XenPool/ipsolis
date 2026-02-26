# SQL - dbo.VDIAssets - Backup V_Child1_Name VDI-SelfserviceDB
# Creates JSON backups of VDI database tables and maintains retention policy

#region Logging Function
function Write-Log {
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
Write-Log "=== Starting VDI Database Backup Script ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Centralized configuration
$config = @{
    # SQL Server settings
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        # Tables to backup
        Tables         = @(
            "^[SQLVDIAssetTable]",
            "^[SQLVDIOrderTable]",
            "^[SQLVDIPoolTable]",
            "^[SQLVDIUseCaseTable]"
        )
    }
    
    # Backup settings
    Backup = @{
        Folder          = "E:\SQLBackup"
        FilePrefix      = "SQLBackup_"
        FileExtension   = ".sql"
        RetentionCount  = 28  # Number of backups to retain
        DateFormat      = "yyyy-MM-dd_HH-mm-ss"  # ISO-like format for sorting
    }
}

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Tables to backup: $($config.SQL.Tables.Count)" 'INFO'
Write-Log "Backup folder: $($config.Backup.Folder)" 'INFO'
Write-Log "Retention policy: Keep last $($config.Backup.RetentionCount) backups" 'INFO'
#endregion

#region Function: Initialize Backup Folder
function Initialize-BackupFolder {
    <#
    .SYNOPSIS
    Ensures the backup folder exists and is accessible
    
    .PARAMETER FolderPath
    Path to the backup folder
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$FolderPath
    )
    
    try {
        Write-Log "Checking backup folder: ${FolderPath}..." 'INFO'
        
        if (-not (Test-Path -Path $FolderPath)) {
            Write-Log "Backup folder does not exist, creating it..." 'WARNING'
            
            try {
                New-Item -ItemType Directory -Path $FolderPath -Force -ErrorAction Stop | Out-Null
                Write-Log "Backup folder created successfully" 'SUCCESS'
            } catch {
                Write-Log "Failed to create backup folder: $_" 'ERROR'
                return $false
            }
        } else {
            Write-Log "Backup folder exists" 'SUCCESS'
        }
        
        # Test write access
        $testFile = Join-Path -Path $FolderPath -ChildPath ".write_test_$(Get-Date -Format 'yyyyMMddHHmmss').tmp"
        try {
            "test" | Out-File -FilePath $testFile -ErrorAction Stop
            Remove-Item -Path $testFile -Force -ErrorAction SilentlyContinue
            Write-Log "Backup folder is writable" 'SUCCESS'
        } catch {
            Write-Log "Backup folder is not writable: $_" 'ERROR'
            return $false
        }
        
        return $true
        
    } catch {
        Write-Log "Error initializing backup folder: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Backup SQL Tables
function Backup-SQLTables {
    <#
    .SYNOPSIS
    Creates JSON backups of specified SQL tables
    
    .PARAMETER Config
    Configuration object containing SQL and backup settings
    
    .RETURNS
    Hashtable with backup results (success, filepath, tables backed up)
    #>
    param (
        [Parameter(Mandatory = $true)]
        [hashtable]$Config
    )
    
    try {
        Write-Log "Starting SQL table backup process..." 'INFO'
        
        # Import SQLServer module
        Write-Log "Loading SQLServer module..." 'INFO'
        try {
            Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
            Write-Log "SQLServer module loaded successfully" 'SUCCESS'
        } catch {
            Write-Log "Failed to load SQLServer module: $_" 'ERROR'
            throw "SQLServer module not available. Please install it using: Install-Module -Name SqlServer"
        }
        
        # Generate backup filename with timestamp
        $timestamp = Get-Date -Format $Config.Backup.DateFormat
        $backupFileName = "$($Config.Backup.FilePrefix)${timestamp}$($Config.Backup.FileExtension)"
        $backupFilePath = Join-Path -Path $Config.Backup.Folder -ChildPath $backupFileName
        
        Write-Log "Backup file: ${backupFilePath}" 'INFO'
        
        # Initialize backup content
        $backupContent = @()
        $backupContent += "-- VDI Database Backup"
        $backupContent += "-- Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        $backupContent += "-- Database: $($Config.SQL.Database)"
        $backupContent += "-- Server: $($Config.SQL.ServerInstance)"
        $backupContent += "-- Tables: $($Config.SQL.Tables.Count)"
        $backupContent += ""
        
        $successfulBackups = 0
        $failedBackups = 0
        
        # Loop through each table and create backup
        foreach ($table in $Config.SQL.Tables) {
            try {
                Write-Log "Backing up table: ${table}..." 'INFO'
                
                # First, check if table has any rows
                $countQuery = "SELECT COUNT(*) as [RowCount] FROM ${table}"
                Write-Log "  Checking row count..." 'INFO'
                
                $countResult = Invoke-Sqlcmd -ServerInstance $Config.SQL.ServerInstance `
                                            -Database $Config.SQL.Database `
                                            -Username $Config.SQL.LoginUser `
                                            -Password $Config.SQL.LoginPW `
                                            -Query $countQuery `
                                            -ErrorAction Stop
                
                $rowCount = $countResult.RowCount
                Write-Log "  Table contains ${rowCount} row(s)" 'INFO'
                
                # Add table backup header to content
                $backupContent += "-- =========================================="
                $backupContent += "-- Table: ${table}"
                $backupContent += "-- Backed up: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
                $backupContent += "-- Row count: ${rowCount}"
                $backupContent += "-- =========================================="
                
                if ($rowCount -eq 0) {
                    Write-Log "  Table ${table} is empty, skipping JSON export" 'WARNING'
                    $backupContent += "-- No data"
                } else {
                    # Query table data as JSON with proper encoding
                    $query = "SELECT * FROM ${table} FOR JSON AUTO, INCLUDE_NULL_VALUES"
                    
                    Write-Log "  Executing JSON export query..." 'INFO'
                    
                    # Use -As DataSet to get clean data without PowerShell metadata
                    $result = Invoke-Sqlcmd -ServerInstance $Config.SQL.ServerInstance `
                                           -Database $Config.SQL.Database `
                                           -Username $Config.SQL.LoginUser `
                                           -Password $Config.SQL.LoginPW `
                                           -Query $query `
                                           -MaxCharLength ([int]::MaxValue) `
                                           -As DataSet `
                                           -ErrorAction Stop
                    
                    # Extract the actual data table from the dataset
                    if ($result -and $result.Tables.Count -gt 0) {
                        $dataTable = $result.Tables[0]
                        
                        if ($dataTable.Rows.Count -gt 0) {
                            Write-Log "  Retrieved $($dataTable.Rows.Count) result row(s) from SQL Server" 'INFO'
                            
                            # Get the first column name (SQL Server uses a GUID-based column name for JSON)
                            $jsonColumnName = $dataTable.Columns[0].ColumnName
                            
                            # Concatenate all rows (in case JSON spans multiple rows)
                            $jsonData = ($dataTable.Rows | ForEach-Object { 
                                $_[$jsonColumnName]
                            }) -join ''
                            
                            if (-not [string]::IsNullOrWhiteSpace($jsonData)) {
                                Write-Log "  JSON data retrieved successfully ($($jsonData.Length) characters)" 'SUCCESS'
                                
                                # Add JSON data to backup
                                $backupContent += $jsonData
                            } else {
                                Write-Log "  Warning: JSON data is empty" 'WARNING'
                                $backupContent += "-- Warning: JSON export returned empty data"
                            }
                        } else {
                            Write-Log "  Warning: Query returned no data rows" 'WARNING'
                            $backupContent += "-- Warning: JSON query returned no data rows"
                        }
                    } else {
                        Write-Log "  Warning: Query returned no results" 'WARNING'
                        $backupContent += "-- Warning: JSON query returned no results"
                    }
                }
                
                $backupContent += ""
                $backupContent += ""
                
                $successfulBackups++
                Write-Log "  Table ${table} backed up successfully" 'SUCCESS'
                
            } catch {
                $failedBackups++
                Write-Log "  Failed to backup table ${table}: $_" 'ERROR'
                
                # Add error information to backup file
                $backupContent += "-- =========================================="
                $backupContent += "-- Table: ${table}"
                $backupContent += "-- ERROR: Backup failed"
                $backupContent += "-- Error message: $_"
                $backupContent += "-- =========================================="
                $backupContent += ""
                $backupContent += ""
            }
        }
        
        # Write backup to file
        Write-Log "Writing backup to file..." 'INFO'
        try {
            $backupContent | Out-File -FilePath $backupFilePath -Encoding UTF8 -ErrorAction Stop
            
            # Verify file was created
            if (Test-Path -Path $backupFilePath) {
                $fileSize = (Get-Item -Path $backupFilePath).Length
                $fileSizeKB = [math]::Round($fileSize / 1KB, 2)
                Write-Log "Backup file created successfully (${fileSizeKB} KB)" 'SUCCESS'
            } else {
                throw "Backup file was not created"
            }
            
        } catch {
            Write-Log "Failed to write backup file: $_" 'ERROR'
            throw
        }
        
        # Return backup results
        $results = @{
            Success           = ($failedBackups -eq 0)
            FilePath          = $backupFilePath
            TablesBackedUp    = $successfulBackups
            TablesFailed      = $failedBackups
            TotalTables       = $Config.SQL.Tables.Count
            FileSizeKB        = $fileSizeKB
        }
        
        if ($results.Success) {
            Write-Log "All $($results.TablesBackedUp) tables backed up successfully" 'SUCCESS'
        } else {
            Write-Log "Backup completed with errors: $($results.TablesBackedUp) succeeded, $($results.TablesFailed) failed" 'WARNING'
        }
        
        return $results
        
    } catch {
        Write-Log "Critical error during backup process: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Cleanup Old Backups
function Remove-OldBackups {
    <#
    .SYNOPSIS
    Removes old backup files based on retention policy
    
    .PARAMETER BackupFolder
    Path to the backup folder
    
    .PARAMETER FilePrefix
    Prefix of backup files to consider
    
    .PARAMETER FileExtension
    Extension of backup files to consider
    
    .PARAMETER RetentionCount
    Number of most recent backups to keep
    
    .RETURNS
    Hashtable with cleanup results
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$BackupFolder,
        [Parameter(Mandatory = $true)]
        [string]$FilePrefix,
        [Parameter(Mandatory = $true)]
        [string]$FileExtension,
        [Parameter(Mandatory = $true)]
        [int]$RetentionCount
    )
    
    try {
        Write-Log "Starting backup cleanup process..." 'INFO'
        Write-Log "Retention policy: Keep last ${RetentionCount} backups" 'INFO'
        
        # Get all backup files sorted by creation time (newest first)
        $filePattern = "${FilePrefix}*${FileExtension}"
        Write-Log "Searching for backup files matching pattern: ${filePattern}..." 'INFO'
        
        $backupFiles = Get-ChildItem -Path $BackupFolder -Filter $filePattern -ErrorAction Stop | 
                       Sort-Object -Property LastWriteTime -Descending
        
        $totalFiles = $backupFiles.Count
        Write-Log "Found ${totalFiles} backup file(s)" 'INFO'
        
        if ($totalFiles -le $RetentionCount) {
            Write-Log "No cleanup needed (${totalFiles} files <= ${RetentionCount} retention limit)" 'SUCCESS'
            return @{
                TotalFiles    = $totalFiles
                FilesDeleted  = 0
                FilesRetained = $totalFiles
            }
        }
        
        # Determine files to delete (skip the first N files to keep)
        $filesToDelete = $backupFiles | Select-Object -Skip $RetentionCount
        $deleteCount = $filesToDelete.Count
        
        Write-Log "Will delete ${deleteCount} old backup file(s)..." 'WARNING'
        
        $deletedCount = 0
        $failedCount = 0
        
        foreach ($file in $filesToDelete) {
            try {
                $fileAge = (Get-Date) - $file.LastWriteTime
                $fileSizeKB = [math]::Round($file.Length / 1KB, 2)
                
                Write-Log "  Deleting: $($file.Name) (Age: $([math]::Round($fileAge.TotalDays, 1)) days, Size: ${fileSizeKB} KB)..." 'INFO'
                
                Remove-Item -Path $file.FullName -Force -ErrorAction Stop
                $deletedCount++
                
                Write-Log "  Deleted successfully" 'SUCCESS'
                
            } catch {
                $failedCount++
                Write-Log "  Failed to delete file $($file.Name): $_" 'ERROR'
            }
        }
        
        # Summary
        $retainedCount = $totalFiles - $deletedCount
        Write-Log "Cleanup completed: ${deletedCount} deleted, ${retainedCount} retained" 'SUCCESS'
        
        if ($failedCount -gt 0) {
            Write-Log "Warning: ${failedCount} file(s) could not be deleted" 'WARNING'
        }
        
        return @{
            TotalFiles    = $totalFiles
            FilesDeleted  = $deletedCount
            FilesFailed   = $failedCount
            FilesRetained = $retainedCount
        }
        
    } catch {
        Write-Log "Error during cleanup process: $_" 'ERROR'
        throw
    }
}
#endregion

#region Main Execution
try {
    # Initialize backup folder
    $folderInitialized = Initialize-BackupFolder -FolderPath $config.Backup.Folder
    
    if (-not $folderInitialized) {
        Write-Log "Cannot proceed: Backup folder initialization failed" 'ERROR'
        exit 1
    }
    
    # Perform database backup
    $backupResults = Backup-SQLTables -Config $config
    
    if (-not $backupResults.Success) {
        Write-Log "Backup completed with errors - check logs above" 'WARNING'
    }
    
    # Display backup summary
    Write-Log "=== Backup Summary ===" 'INFO'
    Write-Log "  Backup file: $($backupResults.FilePath)" 'INFO'
    Write-Log "  File size: $($backupResults.FileSizeKB) KB" 'INFO'
    Write-Log "  Tables backed up: $($backupResults.TablesBackedUp) / $($backupResults.TotalTables)" 'INFO'
    if ($backupResults.TablesFailed -gt 0) {
        Write-Log "  Tables failed: $($backupResults.TablesFailed)" 'WARNING'
    }
    
    # Cleanup old backups
    $cleanupResults = Remove-OldBackups -BackupFolder $config.Backup.Folder `
                                        -FilePrefix $config.Backup.FilePrefix `
                                        -FileExtension $config.Backup.FileExtension `
                                        -RetentionCount $config.Backup.RetentionCount
    
    # Display cleanup summary
    Write-Log "=== Cleanup Summary ===" 'INFO'
    Write-Log "  Total backup files: $($cleanupResults.TotalFiles)" 'INFO'
    Write-Log "  Files deleted: $($cleanupResults.FilesDeleted)" 'INFO'
    Write-Log "  Files retained: $($cleanupResults.FilesRetained)" 'INFO'
    
    Write-Log "=== VDI Database Backup Script Completed Successfully ===" 'SUCCESS'
    exit 0
    
} catch {
    Write-Log "Unhandled error during backup process: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    exit 1
}
#endregion