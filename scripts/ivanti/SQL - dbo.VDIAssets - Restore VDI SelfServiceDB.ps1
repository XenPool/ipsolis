# SQL - dbo.VDIAssets - Restore V_Child1_Name VDI-SelfserviceDB
# Restores VDI database tables from JSON backup files

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
Write-Log "=== Starting VDI Database Restore Script ===" 'INFO'

$ErrorActionPreference = "Stop"

# Centralized configuration - MUST match backup script
$config = @{
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
    }
    
    Backup = @{
        Folder          = "E:\SQLBackup"
        FilePrefix      = "SQLBackup_"
        FileExtension   = ".sql"
    }
    
    Restore = @{
        # TRUNCATE: Delete all existing data first, then insert
        # SKIP_EXISTING: Only insert new records (based on primary key)
        Mode = "TRUNCATE"  # Options: "TRUNCATE" or "SKIP_EXISTING"
    }
}

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Restore mode: $($config.Restore.Mode)" 'INFO'
#endregion

#region Function: Find Latest Backup
function Get-LatestBackupFile {
    param (
        [Parameter(Mandatory = $true)]
        [string]$BackupFolder,
        [Parameter(Mandatory = $true)]
        [string]$FilePrefix,
        [Parameter(Mandatory = $true)]
        [string]$FileExtension
    )
    
    try {
        Write-Log "Searching for backup files in: ${BackupFolder}..." 'INFO'
        
        if (-not (Test-Path -Path $BackupFolder)) {
            throw "Backup folder does not exist: ${BackupFolder}"
        }
        
        $filePattern = "${FilePrefix}*${FileExtension}"
        $backupFiles = Get-ChildItem -Path $BackupFolder -Filter $filePattern -ErrorAction Stop | 
                       Sort-Object -Property LastWriteTime -Descending
        
        if ($backupFiles.Count -eq 0) {
            throw "No backup files found matching pattern: ${filePattern}"
        }
        
        Write-Log "Found $($backupFiles.Count) backup file(s)" 'INFO'
        
        $latestBackup = $backupFiles[0]
        Write-Log "Latest backup: $($latestBackup.Name) ($($latestBackup.LastWriteTime))" 'SUCCESS'
        
        return $latestBackup.FullName
        
    } catch {
        Write-Log "Error finding backup files: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Parse Backup File
function Get-BackupData {
    param (
        [Parameter(Mandatory = $true)]
        [string]$BackupFilePath
    )
    
    try {
        Write-Log "Parsing backup file: ${BackupFilePath}..." 'INFO'
        
        if (-not (Test-Path -Path $BackupFilePath)) {
            throw "Backup file not found: ${BackupFilePath}"
        }
        
        $content = Get-Content -Path $BackupFilePath -Raw -Encoding UTF8
        
        # Extract table data blocks using regex
        $tablePattern = '-- Table: (?<TableName>[^\r\n]+)\r?\n.*?-- Row count: (?<RowCount>\d+)\r?\n-- =+\r?\n(?<JsonData>\[.*?\])?'
        
        $tableMatches = [regex]::Matches($content, $tablePattern, [System.Text.RegularExpressions.RegexOptions]::Singleline)
        
        $backupData = @{}
        
        foreach ($match in $tableMatches) {
            $tableName = $match.Groups['TableName'].Value.Trim()
            $rowCount = [int]$match.Groups['RowCount'].Value
            $jsonData = $match.Groups['JsonData'].Value.Trim()
            
            Write-Log "  Found table: ${tableName} (${rowCount} rows)" 'INFO'
            
            if ($jsonData -and $jsonData.Length -gt 0) {
                Write-Log "    JSON data extracted ($($jsonData.Length) characters)" 'SUCCESS'
                $backupData[$tableName] = $jsonData
            } else {
                Write-Log "    No JSON data (empty table)" 'WARNING'
                $backupData[$tableName] = $null
            }
        }
        
        Write-Log "Parsed $($backupData.Count) table(s) from backup" 'SUCCESS'
        
        return $backupData
        
    } catch {
        Write-Log "Error parsing backup file: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Restore Table
function Restore-TableData {
    param (
        [Parameter(Mandatory = $true)]
        [hashtable]$Config,
        [Parameter(Mandatory = $true)]
        [string]$TableName,
        [Parameter(Mandatory = $false)]
        [string]$JsonData
    )
    
    try {
        Write-Log "Restoring table: ${TableName}..." 'INFO'
        
        if ([string]::IsNullOrWhiteSpace($JsonData)) {
            Write-Log "  No data to restore (table was empty in backup)" 'WARNING'
            return @{ Success = $true; RowsRestored = 0; Message = "No data" }
        }
        
        # Import SQLServer module
        Import-Module -Name SQLServer -ErrorAction Stop
        
        # Escape single quotes in JSON
        $escapedJson = $JsonData -replace "'", "''"
        
        # Get table schema to detect identity columns and build column lists
        Write-Log "  Retrieving table schema..." 'INFO'
        
        $schemaQuery = @"
SELECT 
    c.COLUMN_NAME,
    c.ORDINAL_POSITION,
    CASE 
        WHEN COLUMNPROPERTY(OBJECT_ID(c.TABLE_SCHEMA + '.' + c.TABLE_NAME), c.COLUMN_NAME, 'IsIdentity') = 1 
        THEN 1 
        ELSE 0 
    END AS IS_IDENTITY
FROM INFORMATION_SCHEMA.COLUMNS c
WHERE c.TABLE_NAME = '$(($TableName -split '\.')[1])'
  AND c.TABLE_SCHEMA = '$(($TableName -split '\.')[0])'
ORDER BY c.ORDINAL_POSITION
"@
        
        $columns = Invoke-Sqlcmd -ServerInstance $Config.SQL.ServerInstance `
                                 -Database $Config.SQL.Database `
                                 -Username $Config.SQL.LoginUser `
                                 -Password $Config.SQL.LoginPW `
                                 -Query $schemaQuery `
                                 -ErrorAction Stop
        
        if (-not $columns -or $columns.Count -eq 0) {
            throw "Could not retrieve schema for table ${TableName}"
        }
        
        Write-Log "  Found $($columns.Count) columns" 'INFO'
        
        # Detect identity column (assume first column if exists)
        $identityColumn = $columns | Where-Object { $_.IS_IDENTITY -eq 1 } | Select-Object -First 1
        $hasIdentity = $null -ne $identityColumn
        
        if ($hasIdentity) {
            Write-Log "  Identity column detected: $($identityColumn.COLUMN_NAME)" 'INFO'
        }
        
        # Build column list (exclude identity column for INSERT)
        $insertColumns = $columns | Where-Object { $_.IS_IDENTITY -eq 0 }
        $columnList = ($insertColumns | ForEach-Object { "[$($_.COLUMN_NAME)]" }) -join ", "
        
        Write-Log "  Building restore query..." 'INFO'
        
        # Build restore query based on mode
        if ($Config.Restore.Mode -eq "TRUNCATE") {
            # TRUNCATE mode: Clear table, then insert all data
            $restoreQuery = @"
DECLARE @JsonData NVARCHAR(MAX) = N'$escapedJson';
DECLARE @RowsAffected INT = 0;

-- Clear existing data
TRUNCATE TABLE ${TableName};

-- Insert data from JSON (excluding identity column)
INSERT INTO ${TableName} ($columnList)
SELECT $columnList
FROM OPENJSON(@JsonData)
WITH (
$(($insertColumns | ForEach-Object { "    [$($_.COLUMN_NAME)] NVARCHAR(MAX)" }) -join ",`n")
);

SET @RowsAffected = @@ROWCOUNT;
SELECT @RowsAffected AS RowsAffected;
"@
        } else {
            # SKIP_EXISTING mode: Only insert if primary key doesn't exist
            $primaryKeyColumn = $columns[0].COLUMN_NAME  # Assume first column is PK
            
            $restoreQuery = @"
DECLARE @JsonData NVARCHAR(MAX) = N'$escapedJson';
DECLARE @RowsAffected INT = 0;

-- Insert only new records (skip existing by primary key)
INSERT INTO ${TableName} ($columnList)
SELECT $columnList
FROM OPENJSON(@JsonData)
WITH (
$(($columns | ForEach-Object { "    [$($_.COLUMN_NAME)] NVARCHAR(MAX)" }) -join ",`n")
) AS source
WHERE NOT EXISTS (
    SELECT 1 
    FROM ${TableName} AS target 
    WHERE target.[$primaryKeyColumn] = source.[$primaryKeyColumn]
);

SET @RowsAffected = @@ROWCOUNT;
SELECT @RowsAffected AS RowsAffected;
"@
        }
        
        Write-Log "  Executing restore query..." 'INFO'
        
        $result = Invoke-Sqlcmd -ServerInstance $Config.SQL.ServerInstance `
                               -Database $Config.SQL.Database `
                               -Username $Config.SQL.LoginUser `
                               -Password $Config.SQL.LoginPW `
                               -Query $restoreQuery `
                               -QueryTimeout 120 `
                               -ErrorAction Stop
        
        $rowsAffected = if ($result -and $result.RowsAffected) { $result.RowsAffected } else { 0 }
        
        Write-Log "  Restore completed: ${rowsAffected} row(s) restored" 'SUCCESS'
        
        return @{
            Success       = $true
            RowsRestored  = $rowsAffected
            Message       = "Restored successfully"
        }
        
    } catch {
        Write-Log "  Failed to restore table: $_" 'ERROR'
        return @{
            Success       = $false
            RowsRestored  = 0
            Message       = $_.Exception.Message
        }
    }
}
#endregion

#region Main Execution
try {
    # Find latest backup file
    $backupFile = Get-LatestBackupFile -BackupFolder $config.Backup.Folder `
                                        -FilePrefix $config.Backup.FilePrefix `
                                        -FileExtension $config.Backup.FileExtension
    
    # Parse backup file
    $backupData = Get-BackupData -BackupFilePath $backupFile
    
    if ($backupData.Count -eq 0) {
        Write-Log "No tables found in backup file" 'ERROR'
        exit 1
    }
    
    # Restore each table
    $successCount = 0
    $failCount = 0
    $totalRows = 0
    
    foreach ($tableName in $backupData.Keys | Sort-Object) {
        $result = Restore-TableData -Config $config `
                                    -TableName $tableName `
                                    -JsonData $backupData[$tableName]
        
        if ($result.Success) {
            $successCount++
            $totalRows += $result.RowsRestored
        } else {
            $failCount++
        }
    }
    
    # Display summary
    Write-Log "=== Restore Summary ===" 'INFO'
    Write-Log "  Backup file: $(Split-Path -Leaf $backupFile)" 'INFO'
    Write-Log "  Tables restored successfully: ${successCount}" 'INFO'
    Write-Log "  Tables failed: ${failCount}" $(if ($failCount -gt 0) { 'WARNING' } else { 'INFO' })
    Write-Log "  Total rows restored: ${totalRows}" 'INFO'
    
    if ($failCount -eq 0) {
        Write-Log "=== VDI Database Restore Completed Successfully ===" 'SUCCESS'
        exit 0
    } else {
        Write-Log "=== VDI Database Restore Completed with Errors ===" 'WARNING'
        exit 1
    }
    
} catch {
    Write-Log "Critical error during restore process: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    exit 1
}
#endregion