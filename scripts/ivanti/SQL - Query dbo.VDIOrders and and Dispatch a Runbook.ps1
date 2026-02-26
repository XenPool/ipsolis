# SQL - Query VDIOrders and Dispatch a Runbook
# Main orchestration script that queries pending VDI orders and dispatches appropriate runbooks

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
Write-Log "=== Starting VDI Order Dispatcher ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Configuration for API and database connections
$config = @{
    API = @{
        User     = "^[IAAPIUser]"
        Password = "^[IAAPIPW]"
        BaseUrl  = "^[IAAPIUrl]"
        Endpoint = "/Dispatcher/SchedulingService/jobs"
    }
    
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        OrderTable     = "^[SQLVDIOrderTable]"
        PoolTable      = "^[SQLVDIPoolTable]"
        AssetTable     = "VDIAssets"
        UsecaseTable   = "VDIUsecases"
    }
    
    Runbooks = @{
        New    = "^[GUID-Runbook-1]"
        Change = "^[GUID-Runbook-2]"
        Delete = "^[GUID-Runbook-3]"
        Dispatcher = "^[GUID-Dispatcher]"
    }
    
    # Domain priority for VM selection
    DomainPriority = @('V_Child1_Name', 'V_Child1_NetBIOS_old')
}

Write-Log "API URL: $($config.API.BaseUrl)$($config.API.Endpoint)" 'INFO'
Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
#endregion

#region API Credentials Setup
try {
    Write-Log "Setting up API credentials..." 'INFO'
    
    # Convert password to secure string and create credential object
    $securePassword = ConvertTo-SecureString $config.API.Password -AsPlainText -Force
    $apiCredentials = New-Object System.Management.Automation.PSCredential(
        $config.API.User,
        $securePassword
    )
    
    # Construct full API URL
    $apiUrl = "$($config.API.BaseUrl)$($config.API.Endpoint)"
    
    Write-Log "API credentials configured for user: $($config.API.User)" 'SUCCESS'
    
} catch {
    Write-Log "Failed to configure API credentials: $_" 'ERROR'
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

#region Query Pending Orders
try {
    Write-Log "Querying pending VDI orders..." 'INFO'
    
    # SQL query to retrieve all pending orders that should be processed
    $orderQuery = @"
SELECT 
    VDIOrders.ID,
    VDIOrders.Action,
    VDIOrders.AssetUUID,
    VDIOrders.Status,
    VDIOrders.UsecaseID,
    VDIOrders.Requestor,
    VDIOrders.Owner,
    VDIOrders.SecondOwner,
    VDIOrders.RDPUserIDs,
    VDIOrders.LocalAdmins,
    CONVERT(varchar(10), VDIOrders.OrderDate, 23) AS OrderDate,
    CONVERT(varchar(10), VDIOrders.CreationDatePlan, 23) AS CreationDatePlan,
    CONVERT(varchar(10), VDIOrders.DeactivationDatePlan, 23) AS DeactivationDatePlan,
    VDIOrders.LifeCycle,
    VDIOrders.Snow_REQ,
    VDIOrders.Snow_RITM,
    VDIOrders.CostCenter,
    VDIOrders.PricePerDay,
    VDIAssets.VMName
FROM $($config.SQL.OrderTable) AS VDIOrders
LEFT JOIN $($config.SQL.AssetTable) AS VDIAssets ON VDIOrders.AssetUUID = VDIAssets.UUID
WHERE VDIOrders.Status = 'Pending' 
    AND VDIOrders.CreationDatePlan <= GETDATE()
ORDER BY VDIOrders.OrderDate
"@
    
    # Execute the query
    $pendingOrders = Invoke-Sqlcmd -Query $orderQuery `
                                   -ServerInstance $config.SQL.ServerInstance `
                                   -Database $config.SQL.Database `
                                   -Username $config.SQL.LoginUser `
                                   -Password $config.SQL.LoginPW `
                                   -QueryTimeout 30 `
                                   -ErrorAction Stop
    
    # Ensure $pendingOrders is always an array for consistent Count property
    if ($null -eq $pendingOrders) {
        $pendingOrders = @()
    } elseif ($pendingOrders -isnot [System.Array]) {
        $pendingOrders = @($pendingOrders)
    }
    
    # Check if any orders were found
    if ($pendingOrders.Count -eq 0) {
        Write-Log "No pending orders found to process" 'INFO'
        Write-Log "=== VDI Order Dispatcher Completed Successfully ===" 'SUCCESS'
        exit 0
    }
    
    Write-Log "Found $($pendingOrders.Count) pending order(s) to process" 'INFO'
    
} catch {
    Write-Log "Failed to query pending orders: $_" 'ERROR'
    throw
}
#endregion

#region Helper Functions

function Get-AvailableVM {
    <#
    .SYNOPSIS
    Finds an available VM for a specific UseCase ID
    
    .PARAMETER UsecaseID
    The UseCase ID to search for
    
    .PARAMETER DomainList
    List of domains to search in priority order
    #>
    param(
        [string]$UsecaseID,
        [array]$DomainList
    )
    
    foreach ($domain in $DomainList) {
        try {
            Write-Log "  Searching for available VM in domain: ${domain}..." 'INFO'
            
            # Sanitize UsecaseID to prevent SQL injection
            $sanitizedUsecaseID = $UsecaseID.Replace("'", "''")
            
            # Query to get an available VM for the usecase in the specified domain
            $poolQuery = @"
SELECT TOP (1) 
    VDIUsecases.UsecaseID,
    VDIUsecases.UsecaseName,
    VDIUsecases.UsecaseDescription,
    t1.VMName,
    t1.Domain,
    VDIUsecases.PricePerDay,
    VDIUsecases.LifeCycles
FROM $($config.SQL.PoolTable) AS t1
INNER JOIN (
    SELECT TOP (1) ID, UsecaseID
    FROM $($config.SQL.PoolTable)
    WHERE (Status = 'Available') 
        AND (UsecaseID = '${sanitizedUsecaseID}')
        AND (Domain = '${domain}')
    ORDER BY NEWID()
) AS t2 ON t1.ID = t2.ID
INNER JOIN $($config.SQL.UsecaseTable) AS VDIUsecases ON t1.UsecaseID = VDIUsecases.UsecaseID
"@
            
            # Execute the query
            $result = Invoke-Sqlcmd -Query $poolQuery `
                                   -ServerInstance $config.SQL.ServerInstance `
                                   -Database $config.SQL.Database `
                                   -Username $config.SQL.LoginUser `
                                   -Password $config.SQL.LoginPW `
                                   -QueryTimeout 30 `
                                   -ErrorAction Stop
            
            if ($null -ne $result) {
                Write-Log "  Found available VM: $($result.VMName) in domain ${domain}" 'SUCCESS'
                return $result
            }
            
            Write-Log "  No available VM found in domain ${domain}" 'WARNING'
            
        } catch {
            Write-Log "  Error searching for VM in domain ${domain}: $_" 'ERROR'
        }
    }
    
    # No VM found in any domain
    return $null
}

function Update-OrderStatus {
    <#
    .SYNOPSIS
    Updates the status of an order in the database
    
    .PARAMETER OrderID
    The order ID to update
    
    .PARAMETER Status
    The new status value
    #>
    param(
        [int]$OrderID,
        [string]$Status
    )
    
    try {
        Write-Log "  Updating order $OrderID status to '${Status}'..." 'INFO'
        
        $updateQuery = @"
UPDATE $($config.SQL.OrderTable)
SET 
    [Status] = '${Status}',
    [LastUpdate] = GETDATE()
WHERE [ID] = ${OrderID}
"@
        
        Invoke-Sqlcmd -Query $updateQuery `
                     -ServerInstance $config.SQL.ServerInstance `
                     -Database $config.SQL.Database `
                     -Username $config.SQL.LoginUser `
                     -Password $config.SQL.LoginPW `
                     -QueryTimeout 30 `
                     -ErrorAction Stop
        
        Write-Log "  Order status updated successfully" 'SUCCESS'
        return $true
        
    } catch {
        Write-Log "  Failed to update order status: $_" 'ERROR'
        return $false
    }
}

function Update-PoolStatus {
    <#
    .SYNOPSIS
    Updates the status of a VM in the pool
    
    .PARAMETER VMName
    The VM name to update
    
    .PARAMETER Status
    The new status value
    #>
    param(
        [string]$VMName,
        [string]$Status
    )
    
    try {
        Write-Log "  Updating pool status for ${VMName} to '${Status}'..." 'INFO'
        
        # Sanitize VMName
        $sanitizedVMName = $VMName.Replace("'", "''")
        
        $updateQuery = @"
UPDATE $($config.SQL.PoolTable)
SET 
    [Status] = '${Status}',
    [LastUpdate] = GETDATE()
WHERE VMName = '${sanitizedVMName}'
"@
        
        Invoke-Sqlcmd -Query $updateQuery `
                     -ServerInstance $config.SQL.ServerInstance `
                     -Database $config.SQL.Database `
                     -Username $config.SQL.LoginUser `
                     -Password $config.SQL.LoginPW `
                     -QueryTimeout 30 `
                     -ErrorAction Stop
        
        Write-Log "  Pool status updated successfully" 'SUCCESS'
        return $true
        
    } catch {
        Write-Log "  Failed to update pool status: $_" 'ERROR'
        return $false
    }
}

function Invoke-RunbookDispatch {
    <#
    .SYNOPSIS
    Dispatches a runbook via API
    
    .PARAMETER RunbookGuid
    The GUID of the runbook to execute
    
    .PARAMETER Description
    Description of the job
    
    .PARAMETER Parameters
    Hashtable of parameters to pass to the runbook
    #>
    param(
        [string]$RunbookGuid,
        [string]$Description,
        [hashtable]$Parameters
    )
    
    try {
        Write-Log "  Dispatching runbook: ${Description}" 'INFO'
        
        # Build job parameters array
        $jobParameters = @()
        foreach ($key in $Parameters.Keys) {
            $jobParameters += @{
                Name        = $key
                Type        = 0
                Description = ""
                Value1      = $Parameters[$key]
                Value2      = ""
                Value3      = ""
                Hint        = "Please provide the necessary input"
                Selection   = ""
            }
        }
        
        # Construct API request body
        $requestBody = @{
            Description = $Description
            When = @{
                Immediate     = $true
                IsLocalTime   = $true
                UseWakeOnLAN  = $false
            }
            What = @(
                @{
                    ID   = $RunbookGuid
                    Type = 2
                    Name = $Description
                }
            )
            Who = @(
                @{
                    ID   = $config.Runbooks.Dispatcher
                    Type = 1
                }
            )
            Parameters = @(
                @{
                    Identifier        = ""
                    Type              = 2
                    TaskContainerGuid = $RunbookGuid
                    TaskContainerName = $Description
                    JobGuid           = "{00000000-0000-0000-0000-000000000000}"
                    JobName           = ""
                    JobParameters     = $jobParameters
                }
            )
            outsideLaunchWindow = "LaunchWindowFailJob"
            scheduleInParallel  = $true
        } | ConvertTo-Json -Depth 10
        
        # Make API call to dispatch the runbook
        $response = Invoke-WebRequest -Uri $apiUrl `
                                     -Method Post `
                                     -Credential $apiCredentials `
                                     -ContentType "application/json" `
                                     -Body $requestBody `
                                     -ErrorAction Stop
        
        if ($response.StatusCode -eq 200 -or $response.StatusCode -eq 201) {
            Write-Log "  Runbook dispatched successfully (Status: $($response.StatusCode))" 'SUCCESS'
            return $true
        } else {
            Write-Log "  Runbook dispatch returned unexpected status: $($response.StatusCode)" 'WARNING'
            return $false
        }
        
    } catch {
        Write-Log "  Failed to dispatch runbook: $_" 'ERROR'
        Write-Log "  Response: $($_.Exception.Response)" 'ERROR'
        return $false
    }
}

#endregion

#region Process Orders
try {
    Write-Log "Processing pending orders..." 'INFO'
    
    # Initialize statistics
    $stats = @{
        Total          = $pendingOrders.Count
        NewProcessed   = 0
        ChangeProcessed = 0
        DeleteProcessed = 0
        Errors         = 0
        Skipped        = 0
    }
    
    # Process each pending order
    foreach ($order in $pendingOrders) {
        try {
            Write-Log "Processing Order ID: $($order.ID) | Action: $($order.Action)" 'INFO'
            Write-Log "  Requestor: $($order.Requestor) | UseCase: $($order.UsecaseID)" 'INFO'
            
            $orderID = [int]$order.ID
            
            #region Process NEW Action
            if ($order.Action -eq 'New') {
                Write-Log "  Action: NEW - Creating new VDI asset" 'INFO'
                
                # Find an available VM for the usecase
                $availableVM = Get-AvailableVM -UsecaseID $order.UsecaseID `
                                               -DomainList $config.DomainPriority
                
                # Check if a VM was found
                if ($null -eq $availableVM) {
                    Write-Log "  No available VM found for UseCase $($order.UsecaseID) in any domain" 'ERROR'
                    Write-Log "  Order $orderID will remain in 'Pending' status" 'WARNING'
                    $stats.Skipped++
                    continue
                }
                
                $vmName = $availableVM.VMName
                $vmDomain = $availableVM.Domain
                
                Write-Log "  Selected VM: ${vmName} (Domain: ${vmDomain})" 'SUCCESS'
                
                # Reserve the VM in the pool
                if (-not (Update-PoolStatus -VMName $vmName -Status 'Reserved')) {
                    Write-Log "  Failed to reserve VM - skipping order" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                # Update order status to InProgress
                if (-not (Update-OrderStatus -OrderID $orderID -Status 'InProgress')) {
                    Write-Log "  Failed to update order status - VM remains reserved" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                # Prepare runbook parameters
                $runbookParams = @{
                    VMName              = $vmName
                    VMDomain            = $vmDomain
                    OrderID             = $orderID.ToString()
                    OrderDate           = $order.OrderDate
                    UsecaseID           = $order.UsecaseID
                    Requestor           = $order.Requestor
                    Owner               = $order.Owner
                    SecondOwner         = $order.SecondOwner
                    RDPUserIDs          = $order.RDPUserIDs
                    LocalAdmins         = $order.LocalAdmins
                    CreationDatePlan    = $order.CreationDatePlan
                    DeactivationDatePlan = $order.DeactivationDatePlan
                    LifeCycle           = $order.LifeCycle
                    Snow_REQ            = $order.Snow_REQ
                    Snow_RITM           = $order.Snow_RITM
                    CostCenter          = $order.CostCenter
                    PricePerDay         = $order.PricePerDay
                }
                
                $description = "IA - MyServe VDI New - Create Asset ${vmName} with usecase $($order.UsecaseID) for $($order.Requestor)"
                
                # Dispatch the runbook
                if (Invoke-RunbookDispatch -RunbookGuid $config.Runbooks.New `
                                          -Description $description `
                                          -Parameters $runbookParams) {
                    $stats.NewProcessed++
                } else {
                    Write-Log "  Runbook dispatch failed for order ${orderID}" 'ERROR'
                    $stats.Errors++
                }
            }
            #endregion
            
            #region Process CHANGE Action
            elseif ($order.Action -eq 'Change') {
                Write-Log "  Action: CHANGE - Modifying existing VDI asset" 'INFO'
                
                # Validate AssetUUID is provided
                if ([string]::IsNullOrWhiteSpace($order.AssetUUID)) {
                    Write-Log "  AssetUUID is missing for Change action - skipping order" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                # Lookup VMName and Domain from VDIAssets and VDIPool
                try {
                    Write-Log "  Looking up asset details for UUID: $($order.AssetUUID)" 'INFO'
                    
                    # Convert AssetUUID to string and sanitize for SQL injection protection
                    $sanitizedUUID = $order.AssetUUID.ToString().Replace("'", "''")
                    
                    $assetQuery = @"
SELECT 
    a.VMName,
    p.Domain
FROM $($config.SQL.AssetTable) AS a
INNER JOIN $($config.SQL.PoolTable) AS p ON a.VMName = p.VMName
WHERE a.UUID = '${sanitizedUUID}'
"@
                    
                    $asset = Invoke-Sqlcmd -Query $assetQuery `
                                          -ServerInstance $config.SQL.ServerInstance `
                                          -Database $config.SQL.Database `
                                          -Username $config.SQL.LoginUser `
                                          -Password $config.SQL.LoginPW `
                                          -QueryTimeout 30 `
                                          -ErrorAction Stop
                    
                    if ($null -eq $asset) {
                        Write-Log "  Asset with UUID $($order.AssetUUID) not found - skipping order" 'ERROR'
                        $stats.Errors++
                        continue
                    }
                    
                    $vmName = $asset.VMName
                    $vmDomain = $asset.Domain
                    
                    Write-Log "  Asset found: ${vmName} (Domain: ${vmDomain})" 'SUCCESS'
                    
                } catch {
                    Write-Log "  Failed to lookup asset details: $_" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                # Update order status to InProgress
                if (-not (Update-OrderStatus -OrderID $orderID -Status 'InProgress')) {
                    Write-Log "  Failed to update order status" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                # Prepare runbook parameters (convert UUID to string for API)
                $runbookParams = @{
                    UUID                = $order.AssetUUID.ToString()
                    VMName              = $vmName
                    VMDomain            = $vmDomain
                    OrderID             = $orderID.ToString()
                    OrderDate           = $order.OrderDate
                    UsecaseID           = $order.UsecaseID
                    Requestor           = $order.Requestor
                    Owner               = $order.Owner
                    SecondOwner         = $order.SecondOwner
                    RDPUserIDs          = $order.RDPUserIDs
                    LocalAdmins         = $order.LocalAdmins
                    CreationDatePlan    = $order.CreationDatePlan
                    DeactivationDatePlan = $order.DeactivationDatePlan
                    LifeCycle           = $order.LifeCycle
                    Snow_REQ            = $order.Snow_REQ
                    Snow_RITM           = $order.Snow_RITM
                    CostCenter          = $order.CostCenter
                    PricePerDay         = $order.PricePerDay
                }
                
                $description = "IA - MyServe VDI Change - Change Asset $($order.AssetUUID) for $($order.Requestor)"
                
                # Dispatch the runbook
                if (Invoke-RunbookDispatch -RunbookGuid $config.Runbooks.Change `
                                          -Description $description `
                                          -Parameters $runbookParams) {
                    $stats.ChangeProcessed++
                } else {
                    Write-Log "  Runbook dispatch failed for order ${orderID}" 'ERROR'
                    $stats.Errors++
                }
            }
            #endregion
            
            #region Process DELETE Action
            elseif ($order.Action -eq 'Delete') {
                Write-Log "  Action: DELETE - Removing VDI asset" 'INFO'
                
                # Validate AssetUUID and VMName are provided
                if ([string]::IsNullOrWhiteSpace($order.AssetUUID)) {
                    Write-Log "  AssetUUID is missing for Delete action - skipping order" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                if ([string]::IsNullOrWhiteSpace($order.VMName)) {
                    Write-Log "  VMName is missing for Delete action - skipping order" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                $vmName = $order.VMName
                
                # Get VM domain from pool table
                try {
                    $sanitizedVMName = $vmName.Replace("'", "''")
                    
                    $domainQuery = @"
SELECT Domain
FROM $($config.SQL.PoolTable)
WHERE VMName = '${sanitizedVMName}'
"@
                    
                    $domainResult = Invoke-Sqlcmd -Query $domainQuery `
                                                  -ServerInstance $config.SQL.ServerInstance `
                                                  -Database $config.SQL.Database `
                                                  -Username $config.SQL.LoginUser `
                                                  -Password $config.SQL.LoginPW `
                                                  -QueryTimeout 30 `
                                                  -ErrorAction Stop
                    
                    if ($null -eq $domainResult) {
                        Write-Log "  VM ${vmName} not found in pool - using empty domain" 'WARNING'
                        $vmDomain = ""
                    } else {
                        $vmDomain = $domainResult.Domain
                        Write-Log "  VM Domain: ${vmDomain}" 'INFO'
                    }
                    
                } catch {
                    Write-Log "  Failed to lookup VM domain: $_" 'WARNING'
                    $vmDomain = ""
                }
                
                # Update order status to InProgress
                if (-not (Update-OrderStatus -OrderID $orderID -Status 'InProgress')) {
                    Write-Log "  Failed to update order status" 'ERROR'
                    $stats.Errors++
                    continue
                }
                
                # Prepare runbook parameters (convert UUID to string for API)
                $runbookParams = @{
                    UUID                = $order.AssetUUID.ToString()
                    VMName              = $vmName
                    VMDomain            = $vmDomain
                    OrderID             = $orderID.ToString()
                    OrderDate           = $order.OrderDate
                    UsecaseID           = $order.UsecaseID
                    Requestor           = $order.Requestor
                    Owner               = $order.Owner
                    SecondOwner         = $order.SecondOwner
                    DeactivationDatePlan = $order.DeactivationDatePlan
                    Snow_REQ            = $order.Snow_REQ
                    Snow_RITM           = $order.Snow_RITM
                }
                
                $description = "IA - MyServe VDI Delete - Delete Asset $($order.AssetUUID) for $($order.Requestor)"
                
                # Dispatch the runbook
                if (Invoke-RunbookDispatch -RunbookGuid $config.Runbooks.Delete `
                                          -Description $description `
                                          -Parameters $runbookParams) {
                    $stats.DeleteProcessed++
                } else {
                    Write-Log "  Runbook dispatch failed for order ${orderID}" 'ERROR'
                    $stats.Errors++
                }
            }
            #endregion
            
            #region Unknown Action
            else {
                Write-Log "  Unknown action type: $($order.Action) - skipping order" 'ERROR'
                $stats.Errors++
            }
            #endregion
            
        } catch {
            Write-Log "  Critical error processing order $($order.ID): $_" 'ERROR'
            $stats.Errors++
        }
    }
    
} catch {
    Write-Log "Critical error during order processing: $_" 'ERROR'
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Order Dispatcher Completed ===" 'SUCCESS'
Write-Log "Processing Summary:" 'INFO'
Write-Log "  Total Orders Checked: $($stats.Total)" 'INFO'
Write-Log "  NEW Orders Processed: $($stats.NewProcessed)" 'SUCCESS'
Write-Log "  CHANGE Orders Processed: $($stats.ChangeProcessed)" 'SUCCESS'
Write-Log "  DELETE Orders Processed: $($stats.DeleteProcessed)" 'SUCCESS'
Write-Log "  Orders Skipped (no VM available): $($stats.Skipped)" 'WARNING'

if ($stats.Errors -gt 0) {
    Write-Log "  Errors Encountered: $($stats.Errors)" 'ERROR'
} else {
    Write-Log "  Errors Encountered: 0" 'SUCCESS'
}

Write-Log "Dispatcher run completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion


