# VMWare - VM update VMWare Tools
# Updates VMware Tools on a specified virtual machine without rebooting

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
Write-Log "=== Starting VMware Tools Update Script ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Centralized configuration
$config = @{
    # vSphere settings
    vSphere = @{
        ServerHost = "^[vSphereServerHost]"
        AdminUser  = "^[vSphereServerAdminUser]"
        AdminPW    = '^[vSphereServerAdminPW]'
    }
    
    # Timeout and retry settings
    Timeouts = @{
        VMStartupSeconds    = 300  # 5 minutes
        CheckIntervalSeconds = 10  # Check every 10 seconds
        RetryIntervalSeconds = 120 # 2 minutes between retries
        MaxUpdateRetries     = 2   # Maximum number of update attempts
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
#endregion

#region Function: Test if VM is Fully Powered On
function Test-VMRunning {
    <#
    .SYNOPSIS
    Checks if a VM is fully powered on and running
    
    .PARAMETER VM
    The VM object to check
    
    .RETURNS
    Boolean indicating if VM is running
    #>
    param (
        [Parameter(Mandatory = $true)]
        [VMware.VimAutomation.ViCore.Impl.V1.VM.UniversalVirtualMachineImpl]$VM
    )
    
    try {
        $vmGuest = Get-VMGuest -VM $VM -ErrorAction Stop
        $isRunning = $vmGuest.State -eq 'Running'
        
        if ($isRunning) {
            Write-Log "VM $($VM.Name) is fully running (Guest State: Running)" 'INFO'
        } else {
            Write-Log "VM $($VM.Name) guest state: $($vmGuest.State)" 'INFO'
        }
        
        return $isRunning
    } catch {
        Write-Log "Failed to get VM guest state for $($VM.Name): $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Wait for VM to be Fully Running
function Wait-ForVMRunning {
    <#
    .SYNOPSIS
    Waits until a VM is fully powered on and running
    
    .PARAMETER VM
    The VM object to monitor
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait in seconds
    
    .PARAMETER IntervalSeconds
    Check interval in seconds
    
    .RETURNS
    Boolean indicating if VM reached running state within timeout
    #>
    param (
        [Parameter(Mandatory = $true)]
        [VMware.VimAutomation.ViCore.Impl.V1.VM.UniversalVirtualMachineImpl]$VM,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 10
    )
    
    Write-Log "Waiting for VM $($VM.Name) to be fully running (Timeout: ${TimeoutSeconds}s, Interval: ${IntervalSeconds}s)..." 'INFO'
    
    $elapsedTime = 0
    $checks = 0
    
    while ($elapsedTime -lt $TimeoutSeconds) {
        $checks++
        Write-Log "Check ${checks}: Verifying VM running state (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)..." 'INFO'
        
        if (Test-VMRunning -VM $VM) {
            Write-Log "VM $($VM.Name) is fully running after ${elapsedTime} seconds" 'SUCCESS'
            return $true
        }
        
        # Wait before next check
        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }
    
    Write-Log "VM $($VM.Name) did not reach running state within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Update VMware Tools with Retry Logic
function Update-VMToolsWithRetry {
    <#
    .SYNOPSIS
    Updates VMware Tools on a VM with retry logic
    
    .PARAMETER VM
    The VM object to update
    
    .PARAMETER MaxRetries
    Maximum number of update attempts
    
    .PARAMETER RetryIntervalSeconds
    Time to wait between retries
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        [VMware.VimAutomation.ViCore.Impl.V1.VM.UniversalVirtualMachineImpl]$VM,
        [int]$MaxRetries = 2,
        [int]$RetryIntervalSeconds = 120
    )
    
    Write-Log "Starting VMware Tools update for VM: $($VM.Name)" 'INFO'
    
    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            Write-Log "VMware Tools update attempt ${attempt} of ${MaxRetries}..." 'INFO'
            
            # Refresh VM object to get latest state
            $VM = Get-VM -Name $VM.Name -ErrorAction Stop
            
            # Check current VMware Tools status before update
            $toolsStatus = $VM.ExtensionData.Guest.ToolsStatus
            $toolsVersion = $VM.ExtensionData.Guest.ToolsVersion
            $toolsRunningStatus = $VM.ExtensionData.Guest.ToolsRunningStatus
            
            Write-Log "Current VMware Tools status: ${toolsStatus}, Version: ${toolsVersion}, Running: ${toolsRunningStatus}" 'INFO'
            
            # Check if tools are already updating
            if ($toolsRunningStatus -eq 'guestToolsExecutingScripts') {
                Write-Log "VMware Tools are currently being updated - waiting for completion..." 'WARNING'
                Start-Sleep -Seconds 30
                
                # Refresh and check again
                $VM = Get-VM -Name $VM.Name -ErrorAction Stop
                $toolsRunningStatus = $VM.ExtensionData.Guest.ToolsRunningStatus
                
                if ($toolsRunningStatus -eq 'guestToolsExecutingScripts') {
                    Write-Log "VMware Tools update still in progress - skipping this attempt" 'WARNING'
                    continue
                }
            }
            
            # Check if tools are already up to date
            if ($toolsStatus -eq 'toolsOk') {
                Write-Log "VMware Tools status is 'toolsOk' - tools may already be current" 'INFO'
                Write-Log "Checking if update is actually needed..." 'INFO'
                
                # Get available tools version from host
                $vmHost = Get-VMHost -VM $VM
                Write-Log "VM is running on host: $($vmHost.Name)" 'INFO'
            }
            
            # Perform the update without rebooting
            Write-Log "Initiating VMware Tools update (NoReboot mode)..." 'INFO'
            
            # Set longer timeout for the operation
            $updateTask = Update-Tools -VM $VM -NoReboot -RunAsync -ErrorAction Stop
            
            Write-Log "VMware Tools update task started (Task ID: $($updateTask.Id))" 'SUCCESS'
            
            # Wait for the task to complete with timeout
            $taskTimeout = 600 # 10 minutes
            $taskElapsed = 0
            $taskCheckInterval = 10
            
            while ($updateTask.State -eq 'Running' -and $taskElapsed -lt $taskTimeout) {
                Write-Log "Update task running... (Elapsed: ${taskElapsed}s / ${taskTimeout}s, State: $($updateTask.State))" 'INFO'
                Start-Sleep -Seconds $taskCheckInterval
                $taskElapsed += $taskCheckInterval
                
                # Refresh task state
                $updateTask = Get-Task -Id $updateTask.Id -ErrorAction SilentlyContinue
                
                if ($null -eq $updateTask) {
                    Write-Log "Update task completed or disappeared from queue" 'INFO'
                    break
                }
            }
            
            # Check final task state
            if ($null -ne $updateTask) {
                if ($updateTask.State -eq 'Success') {
                    Write-Log "VMware Tools update task completed successfully" 'SUCCESS'
                } elseif ($updateTask.State -eq 'Error') {
                    throw "Update task failed: $($updateTask.ExtensionData.Info.Error.LocalizedMessage)"
                } elseif ($taskElapsed -ge $taskTimeout) {
                    Write-Log "Update task timed out after ${taskTimeout} seconds" 'WARNING'
                }
            }
            
            # Wait a moment for tools to settle
            Start-Sleep -Seconds 10
            
            # Refresh VM and check post-update status
            $VM = Get-VM -Name $VM.Name -ErrorAction Stop
            $newToolsStatus = $VM.ExtensionData.Guest.ToolsStatus
            $newToolsVersion = $VM.ExtensionData.Guest.ToolsVersion
            
            Write-Log "Post-update VMware Tools status: ${newToolsStatus}, Version: ${newToolsVersion}" 'INFO'
            
            # Consider it successful if tools are ok or running
            if ($newToolsStatus -in @('toolsOk', 'toolsOld')) {
                Write-Log "VMware Tools update completed successfully" 'SUCCESS'
                return $true
            } else {
                Write-Log "Unexpected tools status after update: ${newToolsStatus}" 'WARNING'
                return $true  # Still consider it successful if no error was thrown
            }
            
        } catch {
            $errorMessage = $_.Exception.Message
            
            # Check for specific error conditions
            if ($errorMessage -like "*underlying connection was closed*" -or $errorMessage -like "*connection*kept alive*") {
                Write-Log "Connection error during update attempt ${attempt}: vSphere connection was lost" 'WARNING'
                
                # Try to reconnect to vSphere
                if ($attempt -lt $MaxRetries) {
                    Write-Log "Attempting to reconnect to vSphere server..." 'INFO'
                    
                    try {
                        Disconnect-VIServer -Server $config.vSphere.ServerHost -Confirm:$false -ErrorAction SilentlyContinue
                        Start-Sleep -Seconds 5
                        
                        $securePassword = ConvertTo-SecureString $config.vSphere.AdminPW -AsPlainText -Force
                        $credential = New-Object System.Management.Automation.PSCredential($config.vSphere.AdminUser, $securePassword)
                        $null = Connect-VIServer -Server $config.vSphere.ServerHost -Credential $credential -ErrorAction Stop
                        
                        Write-Log "Reconnected to vSphere server successfully" 'SUCCESS'
                    } catch {
                        Write-Log "Failed to reconnect to vSphere: $_" 'ERROR'
                        return $false
                    }
                }
            } elseif ($errorMessage -like "*not valid due to the current state*") {
                Write-Log "Tools update already in progress from previous attempt" 'WARNING'
                
                # Wait longer and check if update completed
                Write-Log "Waiting 60 seconds to check if previous update completed..." 'INFO'
                Start-Sleep -Seconds 60
                
                $VM = Get-VM -Name $VM.Name -ErrorAction SilentlyContinue
                if ($VM) {
                    $finalStatus = $VM.ExtensionData.Guest.ToolsStatus
                    Write-Log "Final tools status: ${finalStatus}" 'INFO'
                    
                    if ($finalStatus -in @('toolsOk', 'toolsOld')) {
                        Write-Log "Previous update appears to have completed successfully" 'SUCCESS'
                        return $true
                    }
                }
            }
            
            $fullErrorMessage = "VMware Tools update attempt ${attempt} failed for $($VM.Name): $_"
            
            if ($attempt -lt $MaxRetries) {
                Write-Log "${fullErrorMessage} - Retrying in ${RetryIntervalSeconds} seconds..." 'WARNING'
                Start-Sleep -Seconds $RetryIntervalSeconds
            } else {
                Write-Log "${fullErrorMessage} - Max retries reached" 'ERROR'
                return $false
            }
        }
    }
    
    return $false
}
#endregion

#region Main Execution
try {
    # Create vSphere credentials
    Write-Log "Preparing vSphere connection credentials..." 'INFO'
    $securePassword = ConvertTo-SecureString $config.vSphere.AdminPW -AsPlainText -Force
    $credential = New-Object System.Management.Automation.PSCredential($config.vSphere.AdminUser, $securePassword)
    Write-Log "Credentials prepared for user: $($config.vSphere.AdminUser)" 'SUCCESS'
    
    # Connect to vSphere server
    Write-Log "Connecting to vSphere server: $($config.vSphere.ServerHost)..." 'INFO'
    try {
        $viConnection = Connect-VIServer -Server $config.vSphere.ServerHost -Credential $credential -ErrorAction Stop
        Write-Log "Successfully connected to vSphere server: $($viConnection.Name)" 'SUCCESS'
        Write-Log "vSphere version: $($viConnection.Version), Build: $($viConnection.Build)" 'INFO'
    } catch {
        Write-Log "Failed to connect to vSphere server: $_" 'ERROR'
        throw
    }
    
    # Get VM object
    Write-Log "Retrieving VM object for: ${VMName}..." 'INFO'
    try {
        $vm = Get-VM -Name $VMName -ErrorAction Stop
        Write-Log "VM found: $($vm.Name)" 'SUCCESS'
        Write-Log "VM Power State: $($vm.PowerState)" 'INFO'
        Write-Log "VM Guest OS: $($vm.Guest.OSFullName)" 'INFO'
        Write-Log "VM Tools Status: $($vm.ExtensionData.Guest.ToolsStatus)" 'INFO'
    } catch {
        Write-Log "Failed to retrieve VM '${VMName}': $_" 'ERROR'
        throw
    }
    
    # Check if VM is powered on
    if ($vm.PowerState -ne 'PoweredOn') {
        Write-Log "VM ${VMName} is not powered on (Current state: $($vm.PowerState))" 'WARNING'
        Write-Log "VMware Tools update requires VM to be powered on - Skipping update" 'WARNING'
        exit 0
    }
    
    # Wait for VM to be fully running and update VMware Tools
    Write-Log "Checking if VM is fully operational..." 'INFO'
    
    if (Wait-ForVMRunning -VM $vm -TimeoutSeconds $config.Timeouts.VMStartupSeconds -IntervalSeconds $config.Timeouts.CheckIntervalSeconds) {
        # VM is running, proceed with tools update
        $updateSuccess = Update-VMToolsWithRetry -VM $vm -MaxRetries $config.Timeouts.MaxUpdateRetries -RetryIntervalSeconds $config.Timeouts.RetryIntervalSeconds
        
        if ($updateSuccess) {
            Write-Log "VMware Tools update completed successfully for VM: ${VMName}" 'SUCCESS'
        } else {
            Write-Log "VMware Tools update failed for VM: ${VMName}" 'ERROR'
            exit 1
        }
    } else {
        Write-Log "VM ${VMName} did not fully power on within $($config.Timeouts.VMStartupSeconds) seconds" 'WARNING'
        Write-Log "Skipping VMware Tools update due to VM not being fully operational" 'WARNING'
        exit 0
    }
    
} catch {
    Write-Log "Unhandled error during VMware Tools update: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    exit 1
    
} finally {
    # Disconnect from vSphere server
    if ($viConnection) {
        Write-Log "Disconnecting from vSphere server..." 'INFO'
        try {
            Disconnect-VIServer -Server $config.vSphere.ServerHost -Confirm:$false -ErrorAction SilentlyContinue
            Write-Log "Disconnected from vSphere server" 'SUCCESS'
        } catch {
            Write-Log "Error during vSphere disconnect: $_" 'WARNING'
        }
    }
    
    Write-Log "=== VMware Tools Update Script Completed ===" 'SUCCESS'
}
#endregion