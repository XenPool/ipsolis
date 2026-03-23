# XenServer - VM reboot or startup (gracefully)
# Performs graceful reboot for running VMs or startup for halted VMs
# XCP-ng / XenServer equivalent of: VMWare - VM reboot or startup (gracefully).ps1

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
Write-Log "=== Starting XenServer Reboot/Startup Script ===" 'INFO'

$ErrorActionPreference = "Stop"

# Centralized configuration  - values injected via $PARAMS (XenPool module system)
$config = @{
    XenServer = @{
        ServerHost = $PARAMS.XenServerHost
        AdminUser  = $PARAMS.XenServerAdminUser
        AdminPW    = $PARAMS.XenServerAdminPW
    }

    Timeouts = @{
        ShutdownTimeoutSeconds  = 300   # 5 minutes timeout for shutdown
        StartupTimeoutSeconds   = 300   # 5 minutes timeout for startup
        CheckIntervalSeconds    = 3     # Check power state every 3 seconds
        PostShutdownWaitSeconds = 5     # Wait 5 seconds after shutdown before starting
    }
}

# VM name from input parameter
$VMName = $PARAMS.VMName

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
#endregion

#region Import XenServer PowerShell SDK
try {
    Import-Module XenServerPSModule -ErrorAction Stop
    Write-Log "XenServerPSModule loaded successfully" 'SUCCESS'
} catch {
    Write-Log "Failed to load XenServerPSModule: $_" 'ERROR'
    Write-Log "Ensure the XenServer PowerShell SDK is installed on this host" 'ERROR'
    exit 1
}
#endregion

#region Function: Get Fresh VM Object
function Get-XenVMFresh {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VMName
    )
    $vms = Get-XenVM -Name $VMName -ErrorAction Stop |
           Where-Object { -not $_.is_a_template -and -not $_.is_control_domain }
    if (-not $vms) { throw "VM '${VMName}' not found" }
    return $vms | Select-Object -First 1
}
#endregion

#region Function: Wait for VM Power State
function Wait-ForVMPowerState {
    <#
    .SYNOPSIS
    Waits for a XenServer VM to reach a specific power state within timeout.

    .PARAMETER VMName
    The name of the VM to monitor.

    .PARAMETER TargetState
    Target power state: 'Running' or 'Halted'

    .PARAMETER TimeoutSeconds
    Maximum time to wait in seconds.

    .PARAMETER IntervalSeconds
    Check interval in seconds.

    .RETURNS
    Boolean indicating if target state was reached within timeout.
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Running', 'Halted')]
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
            $currentVM    = Get-XenVMFresh -VMName $VMName
            $currentState = $currentVM.power_state.ToString()

            Write-Log "Check ${checks}: VM power state is '${currentState}' (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)" 'INFO'

            if ($currentState -eq $TargetState) {
                Write-Log "VM '${VMName}' reached target state '${TargetState}' after ${elapsedTime} seconds" 'SUCCESS'
                return $true
            }
        } catch {
            Write-Log "Error checking VM power state: $_" 'WARNING'
        }

        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }

    Write-Log "VM '${VMName}' did not reach state '${TargetState}' within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Gracefully Shutdown VM
function Invoke-XenVMGracefulShutdown {
    <#
    .SYNOPSIS
    Performs graceful (clean) shutdown of a XenServer VM.
    Requires XenServer Tools (PV drivers) to be installed in the guest.

    .PARAMETER VM
    The VM object to shut down.

    .PARAMETER TimeoutSeconds
    Maximum time to wait for shutdown.

    .PARAMETER IntervalSeconds
    Check interval in seconds.

    .RETURNS
    Boolean indicating success or failure.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 3
    )

    Write-Log "Initiating graceful shutdown for VM: $($VM.name_label)..." 'INFO'

    try {
        # Check XenServer Tools (PV drivers)  - equivalent to VMware Tools check
        $guestMetricsRef = $VM.guest_metrics
        $toolsAvailable  = ($null -ne $guestMetricsRef -and $guestMetricsRef.opaque_ref -ne 'OpaqueRef:NULL')

        if ($toolsAvailable) {
            Write-Log "XenServer Tools (PV drivers) detected  - clean shutdown available" 'INFO'
        } else {
            Write-Log "XenServer Tools not detected  - graceful shutdown may not work" 'WARNING'
            Write-Log "Consider installing PV drivers in the guest OS" 'WARNING'
        }

        # Initiate graceful (clean) shutdown via ACPI / PV drivers
        Invoke-XenVM -VM $VM -XenAction CleanShutdown -ErrorAction Stop
        Write-Log "Graceful shutdown command sent to VM: $($VM.name_label)" 'SUCCESS'

        # Wait for shutdown to complete
        $shutdownSuccess = Wait-ForVMPowerState -VMName $VM.name_label `
                                                -TargetState 'Halted' `
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
function Invoke-XenVMStartup {
    <#
    .SYNOPSIS
    Starts a halted XenServer VM and waits for it to reach Running state.

    .PARAMETER VM
    The VM object to start.

    .PARAMETER TimeoutSeconds
    Maximum time to wait for startup.

    .PARAMETER IntervalSeconds
    Check interval in seconds.

    .RETURNS
    Boolean indicating success or failure.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 3
    )

    Write-Log "Starting VM: $($VM.name_label)..." 'INFO'

    try {
        Invoke-XenVM -VM $VM -XenAction Start -ErrorAction Stop
        Write-Log "VM startup command sent to: $($VM.name_label)" 'SUCCESS'

        $startupSuccess = Wait-ForVMPowerState -VMName $VM.name_label `
                                               -TargetState 'Running' `
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
function Invoke-XenVMReboot {
    <#
    .SYNOPSIS
    Performs graceful reboot of a XenServer VM (clean shutdown then start).

    .PARAMETER VM
    The VM object to reboot.

    .PARAMETER ShutdownTimeoutSeconds
    Maximum time to wait for shutdown.

    .PARAMETER StartupTimeoutSeconds
    Maximum time to wait for startup.

    .PARAMETER CheckIntervalSeconds
    Check interval in seconds.

    .PARAMETER PostShutdownWaitSeconds
    Wait time after shutdown before starting.

    .RETURNS
    Boolean indicating success or failure.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$ShutdownTimeoutSeconds  = 300,
        [int]$StartupTimeoutSeconds   = 300,
        [int]$CheckIntervalSeconds    = 3,
        [int]$PostShutdownWaitSeconds = 5
    )

    Write-Log "Starting VM reboot process for: $($VM.name_label)..." 'INFO'

    # Step 1: Graceful shutdown
    $shutdownSuccess = Invoke-XenVMGracefulShutdown -VM $VM `
                                                     -TimeoutSeconds $ShutdownTimeoutSeconds `
                                                     -IntervalSeconds $CheckIntervalSeconds

    if (-not $shutdownSuccess) {
        Write-Log "VM shutdown failed - aborting reboot process" 'ERROR'
        return $false
    }

    # Step 2: Wait after shutdown
    Write-Log "Waiting ${PostShutdownWaitSeconds} seconds before starting VM..." 'INFO'
    Start-Sleep -Seconds $PostShutdownWaitSeconds

    # Step 3: Refresh VM object
    try {
        $VM = Get-XenVMFresh -VMName $VM.name_label
        Write-Log "VM object refreshed after shutdown" 'INFO'
    } catch {
        Write-Log "Failed to refresh VM object: $_" 'ERROR'
        return $false
    }

    # Step 4: Start the VM
    $startupSuccess = Invoke-XenVMStartup -VM $VM `
                                          -TimeoutSeconds $StartupTimeoutSeconds `
                                          -IntervalSeconds $CheckIntervalSeconds

    if (-not $startupSuccess) {
        Write-Log "VM startup failed after shutdown" 'ERROR'
        return $false
    }

    Write-Log "VM reboot completed successfully for: $($VM.name_label)" 'SUCCESS'
    return $true
}
#endregion

#region Main Execution
try {
    # Bypass SSL certificate validation  - XCP-ng / XenServer use self-signed certs by default.
    # Without this, Connect-XenServer tries to interactively prompt for cert trust,
    # which fails in NonInteractive (headless) mode.
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

    # Connect to XenServer
    Write-Log "Connecting to XenServer: $($config.XenServer.ServerHost)..." 'INFO'
    try {
        Connect-XenServer -Server $config.XenServer.ServerHost `
                          -UserName $config.XenServer.AdminUser `
                          -Password $config.XenServer.AdminPW `
                          -NoWarnCertificates `
                          -SetDefaultSession `
                          -ErrorAction Stop
        Write-Log "Successfully connected to XenServer: $($config.XenServer.ServerHost)" 'SUCCESS'
    } catch {
        Write-Log "Failed to connect to XenServer: $_" 'ERROR'
        throw
    }

    # Get VM object
    Write-Log "Retrieving VM object for: ${VMName}..." 'INFO'
    try {
        $vm = Get-XenVMFresh -VMName $VMName
        Write-Log "VM found: $($vm.name_label)" 'SUCCESS'
        Write-Log "VM Power State: $($vm.power_state)" 'INFO'
        Write-Log "VM UUID: $($vm.uuid)" 'INFO'
        Write-Log "VM Description: $($vm.name_description)" 'INFO'
    } catch {
        Write-Log "VM '${VMName}' not found on XenServer" 'ERROR'
        throw "VM '${VMName}' does not exist"
    }

    # Process VM based on current power state
    switch ($vm.power_state.ToString()) {
        'Halted' {
            Write-Log "VM '${VMName}' is halted - performing startup..." 'INFO'

            $startupSuccess = Invoke-XenVMStartup -VM $vm `
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
        'Running' {
            Write-Log "VM '${VMName}' is running - performing graceful reboot..." 'INFO'

            $rebootSuccess = Invoke-XenVMReboot -VM $vm `
                                                -ShutdownTimeoutSeconds  $config.Timeouts.ShutdownTimeoutSeconds `
                                                -StartupTimeoutSeconds   $config.Timeouts.StartupTimeoutSeconds `
                                                -CheckIntervalSeconds    $config.Timeouts.CheckIntervalSeconds `
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
            Write-Log "Suspended VMs cannot be gracefully rebooted - resume or stop/start instead" 'WARNING'
            exit 0
        }
        'Paused' {
            Write-Log "VM '${VMName}' is currently paused" 'WARNING'
            Write-Log "Paused VMs cannot be gracefully rebooted - unpause or stop/start instead" 'WARNING'
            exit 0
        }
        default {
            Write-Log "VM '${VMName}' is in an unexpected power state: $($vm.power_state)" 'WARNING'
            exit 0
        }
    }

} catch {
    Write-Log "Unhandled error during VM reboot/startup: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    exit 1

} finally {
    # Disconnect from XenServer
    Write-Log "Disconnecting from XenServer..." 'INFO'
    try {
        Disconnect-XenServer -ErrorAction SilentlyContinue
        Write-Log "Disconnected from XenServer" 'SUCCESS'
    } catch {
        Write-Log "Error during XenServer disconnect: $_" 'WARNING'
    }

    Write-Log "=== XenServer Reboot/Startup Script Completed ===" 'SUCCESS'
}
#endregion
