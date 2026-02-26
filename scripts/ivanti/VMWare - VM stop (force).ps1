# VMWare - VM stop (force)
# Performs a forced (hard) shutdown of a specified virtual machine

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
Write-Log "=== Starting VMware Forced Stop Script ===" 'INFO'

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
    
    # Stop settings
    Stop = @{
        TimeoutSeconds       = 120  # 2 minutes timeout for forced stop
        CheckIntervalSeconds = 5    # Check power state every 5 seconds
        MaxRetries          = 3     # Maximum number of stop attempts
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Forced stop timeout: $($config.Stop.TimeoutSeconds) seconds" 'INFO'
#endregion

#region Function: Wait for VM to Power Off
function Wait-ForVMPowerOff {
    <#
    .SYNOPSIS
    Waits for a VM to be completely powered off within specified timeout
    
    .PARAMETER VMName
    The name of the VM to monitor
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait for power off in seconds
    
    .PARAMETER IntervalSeconds
    Check interval in seconds
    
    .RETURNS
    Boolean indicating if VM powered off successfully within timeout
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [int]$TimeoutSeconds = 120,
        [int]$IntervalSeconds = 5
    )
    
    Write-Log "Waiting for VM '${VMName}' to power off (Timeout: ${TimeoutSeconds}s)..." 'INFO'
    
    $elapsedTime = 0
    $checks = 0
    
    while ($elapsedTime -lt $TimeoutSeconds) {
        $checks++
        
        try {
            # Refresh VM state
            $currentVM = Get-VM -Name $VMName -ErrorAction Stop
            $powerState = $currentVM.PowerState
            
            Write-Log "Check ${checks}: VM power state is '${powerState}' (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)" 'INFO'
            
            # Check if VM is powered off
            if ($powerState -eq 'PoweredOff') {
                Write-Log "VM '${VMName}' successfully powered off after ${elapsedTime} seconds" 'SUCCESS'
                return $true
            }
            
        } catch {
            Write-Log "Error checking VM power state: $_" 'WARNING'
        }
        
        # Wait before next check
        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }
    
    Write-Log "VM '${VMName}' did not power off within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Perform Forced Stop with Retry Logic
function Invoke-ForcedStopWithRetry {
    <#
    .SYNOPSIS
    Performs forced stop of a VM with retry logic
    
    .PARAMETER VM
    The VM object to stop
    
    .PARAMETER MaxRetries
    Maximum number of stop attempts
    
    .PARAMETER TimeoutSeconds
    Timeout for each stop attempt
    
    .PARAMETER CheckIntervalSeconds
    Interval between power state checks
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$MaxRetries = 3,
        [int]$TimeoutSeconds = 120,
        [int]$CheckIntervalSeconds = 5
    )
    
    Write-Log "Initiating forced stop for VM: $($VM.Name)..." 'INFO'
    
    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            Write-Log "Forced stop attempt ${attempt} of ${MaxRetries}..." 'INFO'
            
            # Check current power state before attempting stop
            $currentState = $VM.PowerState
            Write-Log "Current VM power state before stop: ${currentState}" 'INFO'
            
            if ($currentState -eq 'PoweredOff') {
                Write-Log "VM is already powered off - no action needed" 'INFO'
                return $true
            }
            
            # Perform forced stop
            Stop-VM -VM $VM -Confirm:$false -ErrorAction Stop
            Write-Log "Forced stop command sent to VM: $($VM.Name)" 'SUCCESS'
            
            # Wait for VM to power off
            $stopSuccess = Wait-ForVMPowerOff -VMName $VM.Name -TimeoutSeconds $TimeoutSeconds -IntervalSeconds $CheckIntervalSeconds
            
            if ($stopSuccess) {
                return $true
            } else {
                if ($attempt -lt $MaxRetries) {
                    Write-Log "VM did not power off within timeout - retrying..." 'WARNING'
                }
            }
            
        } catch {
            $errorMessage = "Forced stop attempt ${attempt} failed for $($VM.Name): $_"
            
            if ($attempt -lt $MaxRetries) {
                Write-Log "${errorMessage} - Retrying..." 'WARNING'
                Start-Sleep -Seconds 5
            } else {
                Write-Log "${errorMessage} - Max retries reached" 'ERROR'
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
        Write-Log "VM Hardware Version: $($vm.HardwareVersion)" 'INFO'
        Write-Log "VM Host: $($vm.VMHost.Name)" 'INFO'
    } catch {
        Write-Log "VM '${VMName}' not found in vSphere inventory" 'ERROR'
        throw "VM '${VMName}' does not exist"
    }
    
    # Process VM based on current power state
    switch ($vm.PowerState) {
        'PoweredOn' {
            Write-Log "VM is powered on - proceeding with forced stop..." 'INFO'
            
            # Attempt forced stop with retry logic
            $stopSuccess = Invoke-ForcedStopWithRetry -VM $vm `
                                                      -MaxRetries $config.Stop.MaxRetries `
                                                      -TimeoutSeconds $config.Stop.TimeoutSeconds `
                                                      -CheckIntervalSeconds $config.Stop.CheckIntervalSeconds
            
            if ($stopSuccess) {
                Write-Log "VM '${VMName}' has been successfully stopped (forced stop)" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to stop VM '${VMName}' after $($config.Stop.MaxRetries) attempts" 'ERROR'
                exit 1
            }
        }
        'PoweredOff' {
            Write-Log "VM '${VMName}' is already powered off - no action needed" 'INFO'
            exit 0
        }
        'Suspended' {
            Write-Log "VM '${VMName}' is currently suspended - attempting forced stop..." 'WARNING'
            
            $stopSuccess = Invoke-ForcedStopWithRetry -VM $vm `
                                                      -MaxRetries $config.Stop.MaxRetries `
                                                      -TimeoutSeconds $config.Stop.TimeoutSeconds `
                                                      -CheckIntervalSeconds $config.Stop.CheckIntervalSeconds
            
            if ($stopSuccess) {
                Write-Log "Suspended VM '${VMName}' has been successfully powered off" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to power off suspended VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        default {
            Write-Log "VM '${VMName}' is in an unexpected power state: $($vm.PowerState)" 'WARNING'
            Write-Log "Attempting forced stop anyway..." 'INFO'
            
            $stopSuccess = Invoke-ForcedStopWithRetry -VM $vm `
                                                      -MaxRetries $config.Stop.MaxRetries `
                                                      -TimeoutSeconds $config.Stop.TimeoutSeconds `
                                                      -CheckIntervalSeconds $config.Stop.CheckIntervalSeconds
            
            if ($stopSuccess) {
                Write-Log "VM '${VMName}' has been successfully stopped" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to stop VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
    }
    
} catch {
    Write-Log "Unhandled error during forced VM stop: $_" 'ERROR'
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
    
    Write-Log "=== VMware Forced Stop Script Completed ===" 'SUCCESS'
}
#endregion