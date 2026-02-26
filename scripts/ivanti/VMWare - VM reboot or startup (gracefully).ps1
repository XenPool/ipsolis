# VMWare - VM reboot or startup (gracefully)
# Performs graceful reboot for powered-on VMs or startup for powered-off VMs

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
Write-Log "=== Starting VMware Reboot/Startup Script ===" 'INFO'

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
    
    # Timeout and wait settings
    Timeouts = @{
        ShutdownTimeoutSeconds = 300  # 5 minutes timeout for shutdown
        StartupTimeoutSeconds  = 300  # 5 minutes timeout for startup
        CheckIntervalSeconds   = 3    # Check power state every 3 seconds
        PostShutdownWaitSeconds = 5   # Wait 5 seconds after shutdown before starting
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
#endregion

#region Function: Wait for VM Power State
function Wait-ForVMPowerState {
    <#
    .SYNOPSIS
    Waits for a VM to reach a specific power state within timeout
    
    .PARAMETER VMName
    The name of the VM to monitor
    
    .PARAMETER TargetState
    Target power state to wait for (PoweredOn or PoweredOff)
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait in seconds
    
    .PARAMETER IntervalSeconds
    Check interval in seconds
    
    .RETURNS
    Boolean indicating if target state was reached within timeout
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [ValidateSet('PoweredOn', 'PoweredOff')]
        [string]$TargetState,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 3
    )
    
    Write-Log "Waiting for VM '${VMName}' to reach state: ${TargetState} (Timeout: ${TimeoutSeconds}s)..." 'INFO'
    
    $elapsedTime = 0
    $checks = 0
    
    while ($elapsedTime -lt $TimeoutSeconds) {
        $checks++
        
        try {
            # Refresh VM state
            $currentVM = Get-VM -Name $VMName -ErrorAction Stop
            $currentState = $currentVM.PowerState
            
            Write-Log "Check ${checks}: VM power state is '${currentState}' (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)" 'INFO'
            
            # Check if target state is reached
            if ($currentState -eq $TargetState) {
                Write-Log "VM '${VMName}' reached target state '${TargetState}' after ${elapsedTime} seconds" 'SUCCESS'
                return $true
            }
            
        } catch {
            Write-Log "Error checking VM power state: $_" 'WARNING'
        }
        
        # Wait before next check
        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }
    
    Write-Log "VM '${VMName}' did not reach state '${TargetState}' within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Gracefully Shutdown VM
function Invoke-VMGracefulShutdown {
    <#
    .SYNOPSIS
    Performs graceful shutdown of a VM
    
    .PARAMETER VM
    The VM object to shut down
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait for shutdown
    
    .PARAMETER IntervalSeconds
    Check interval in seconds
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 3
    )
    
    Write-Log "Initiating graceful shutdown for VM: $($VM.Name)..." 'INFO'
    
    try {
        # Check VMware Tools status
        $toolsStatus = $VM.ExtensionData.Guest.ToolsStatus
        Write-Log "VMware Tools status: ${toolsStatus}" 'INFO'
        
        if ($toolsStatus -ne 'toolsOk' -and $toolsStatus -ne 'toolsOld') {
            Write-Log "VMware Tools not running properly (Status: ${toolsStatus})" 'WARNING'
            Write-Log "Graceful shutdown may not work - consider forced shutdown" 'WARNING'
        }
        
        # Initiate graceful shutdown
        Shutdown-VMGuest -VM $VM -Confirm:$false -ErrorAction Stop
        Write-Log "Graceful shutdown command sent to VM: $($VM.Name)" 'SUCCESS'
        
        # Wait for shutdown to complete
        $shutdownSuccess = Wait-ForVMPowerState -VMName $VM.Name `
                                                 -TargetState 'PoweredOff' `
                                                 -TimeoutSeconds $TimeoutSeconds `
                                                 -IntervalSeconds $IntervalSeconds
        
        return $shutdownSuccess
        
    } catch {
        Write-Log "Failed to initiate graceful shutdown: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Start VM
function Invoke-VMStartup {
    <#
    .SYNOPSIS
    Starts a VM and waits for it to power on
    
    .PARAMETER VM
    The VM object to start
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait for startup
    
    .PARAMETER IntervalSeconds
    Check interval in seconds
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 3
    )
    
    Write-Log "Starting VM: $($VM.Name)..." 'INFO'
    
    try {
        # Start the VM
        Start-VM -VM $VM -Confirm:$false -ErrorAction Stop
        Write-Log "VM startup command sent to: $($VM.Name)" 'SUCCESS'
        
        # Wait for VM to power on
        $startupSuccess = Wait-ForVMPowerState -VMName $VM.Name `
                                                -TargetState 'PoweredOn' `
                                                -TimeoutSeconds $TimeoutSeconds `
                                                -IntervalSeconds $IntervalSeconds
        
        return $startupSuccess
        
    } catch {
        Write-Log "Failed to start VM: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Perform VM Reboot
function Invoke-VMReboot {
    <#
    .SYNOPSIS
    Performs graceful reboot of a VM (shutdown then startup)
    
    .PARAMETER VM
    The VM object to reboot
    
    .PARAMETER ShutdownTimeoutSeconds
    Maximum time to wait for shutdown
    
    .PARAMETER StartupTimeoutSeconds
    Maximum time to wait for startup
    
    .PARAMETER CheckIntervalSeconds
    Check interval in seconds
    
    .PARAMETER PostShutdownWaitSeconds
    Wait time after shutdown before starting
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$ShutdownTimeoutSeconds = 300,
        [int]$StartupTimeoutSeconds = 300,
        [int]$CheckIntervalSeconds = 3,
        [int]$PostShutdownWaitSeconds = 5
    )
    
    Write-Log "Starting VM reboot process for: $($VM.Name)..." 'INFO'
    
    # Step 1: Graceful shutdown
    $shutdownSuccess = Invoke-VMGracefulShutdown -VM $VM `
                                                  -TimeoutSeconds $ShutdownTimeoutSeconds `
                                                  -IntervalSeconds $CheckIntervalSeconds
    
    if (-not $shutdownSuccess) {
        Write-Log "VM shutdown failed - aborting reboot process" 'ERROR'
        return $false
    }
    
    # Step 2: Wait after shutdown (allows hardware to settle)
    Write-Log "Waiting ${PostShutdownWaitSeconds} seconds before starting VM..." 'INFO'
    Start-Sleep -Seconds $PostShutdownWaitSeconds
    
    # Step 3: Refresh VM object
    try {
        $VM = Get-VM -Name $VM.Name -ErrorAction Stop
        Write-Log "VM object refreshed after shutdown" 'INFO'
    } catch {
        Write-Log "Failed to refresh VM object: $_" 'ERROR'
        return $false
    }
    
    # Step 4: Start the VM
    $startupSuccess = Invoke-VMStartup -VM $VM `
                                       -TimeoutSeconds $StartupTimeoutSeconds `
                                       -IntervalSeconds $CheckIntervalSeconds
    
    if (-not $startupSuccess) {
        Write-Log "VM startup failed after shutdown" 'ERROR'
        return $false
    }
    
    Write-Log "VM reboot completed successfully for: $($VM.Name)" 'SUCCESS'
    return $true
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
        Write-Log "VM Tools Status: $($vm.ExtensionData.Guest.ToolsStatus)" 'INFO'
    } catch {
        Write-Log "VM '${VMName}' not found in vSphere inventory" 'ERROR'
        throw "VM '${VMName}' does not exist"
    }
    
    # Process VM based on current power state
    switch ($vm.PowerState) {
        'PoweredOff' {
            Write-Log "VM '${VMName}' is powered off - performing startup..." 'INFO'
            
            $startupSuccess = Invoke-VMStartup -VM $vm `
                                               -TimeoutSeconds $config.Timeouts.StartupTimeoutSeconds `
                                               -IntervalSeconds $config.Timeouts.CheckIntervalSeconds
            
            if ($startupSuccess) {
                Write-Log "VM '${VMName}' has started successfully" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to start VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        'PoweredOn' {
            Write-Log "VM '${VMName}' is powered on - performing graceful reboot..." 'INFO'
            
            $rebootSuccess = Invoke-VMReboot -VM $vm `
                                             -ShutdownTimeoutSeconds $config.Timeouts.ShutdownTimeoutSeconds `
                                             -StartupTimeoutSeconds $config.Timeouts.StartupTimeoutSeconds `
                                             -CheckIntervalSeconds $config.Timeouts.CheckIntervalSeconds `
                                             -PostShutdownWaitSeconds $config.Timeouts.PostShutdownWaitSeconds
            
            if ($rebootSuccess) {
                Write-Log "VM '${VMName}' has rebooted successfully" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to reboot VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        'Suspended' {
            Write-Log "VM '${VMName}' is currently suspended" 'WARNING'
            Write-Log "Suspended VMs cannot be gracefully rebooted - use resume or stop/start operations instead" 'WARNING'
            exit 0
        }
        default {
            Write-Log "VM '${VMName}' is in an unexpected power state: $($vm.PowerState)" 'WARNING'
            Write-Log "Cannot perform reboot or startup operation" 'WARNING'
            exit 0
        }
    }
    
} catch {
    Write-Log "Unhandled error during VM reboot/startup: $_" 'ERROR'
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
    
    Write-Log "=== VMware Reboot/Startup Script Completed ===" 'SUCCESS'
}
#endregion