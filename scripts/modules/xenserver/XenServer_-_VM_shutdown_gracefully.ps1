# NAME: XenServer - VM shutdown (gracefully)
# DESC: Graceful shutdown of a XenServer/XCP-ng VM with forced-shutdown fallback after timeout
# XenServer - VM shutdown (gracefully)
# Performs a graceful shutdown of a VM with timeout and forced-shutdown fallback
# XCP-ng / XenServer equivalent of: VMWare - VM shutdown (gracefully).ps1

param(
    [Parameter(Mandatory=$true)]
    [string]$VMName
)

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
Write-Log "=== Starting XenServer Graceful Shutdown Script ===" 'INFO'

$ErrorActionPreference = "Stop"

# Centralized configuration - hosting from $VARS (global), VM name from param()
$config = @{
    XenServer = @{
        ServerHost = $VARS.'xenserver.host'
        AdminUser  = $VARS.'xenserver.username'
        AdminPW    = $VARS.'xenserver.password'
    }

    Shutdown = @{
        TimeoutSeconds       = 300  # 5 minutes timeout for graceful shutdown
        CheckIntervalSeconds = 5    # Check power state every 5 seconds
        ForcedShutdownDelay  = 30   # Seconds to wait before attempting forced shutdown
    }
}

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Shutdown timeout: $($config.Shutdown.TimeoutSeconds) seconds" 'INFO'
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

#region Function: Wait for VM Shutdown
function Wait-ForVMShutdown {
    <#
    .SYNOPSIS
    Waits for a XenServer VM to reach Halted state within the specified timeout.

    .PARAMETER VMName
    Name of the VM to monitor.

    .PARAMETER TimeoutSeconds
    Maximum time to wait in seconds.

    .PARAMETER IntervalSeconds
    Check interval in seconds.

    .RETURNS
    Boolean indicating if VM shut down within timeout.
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 5
    )

    Write-Log "Waiting for VM '${VMName}' to shut down (Timeout: ${TimeoutSeconds}s)..." 'INFO'

    $elapsedTime = 0
    $checks = 0

    while ($elapsedTime -lt $TimeoutSeconds) {
        $checks++

        try {
            $currentVM    = Get-XenVMFresh -VMName $VMName
            $currentState = $currentVM.power_state.ToString()

            Write-Log "Check ${checks}: VM power state is '${currentState}' (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)" 'INFO'

            if ($currentState -eq 'Halted') {
                Write-Log "VM '${VMName}' successfully shut down after ${elapsedTime} seconds" 'SUCCESS'
                return $true
            }
        } catch {
            Write-Log "Error checking VM power state: $_" 'WARNING'
        }

        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }

    Write-Log "VM '${VMName}' did not shut down within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Perform Graceful Shutdown
function Invoke-XenVMGracefulShutdown {
    <#
    .SYNOPSIS
    Performs graceful (clean) shutdown of a XenServer VM via ACPI / PV drivers.
    Requires XenServer Tools (PV drivers) installed in the guest OS.

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
        [int]$IntervalSeconds = 5
    )

    Write-Log "Initiating graceful shutdown for VM: $($VM.name_label)..." 'INFO'

    try {
        # Check XenServer Tools (PV drivers) - equivalent to VMware Tools check
        $guestMetricsRef = $VM.guest_metrics
        $toolsAvailable  = ($null -ne $guestMetricsRef -and $guestMetricsRef.opaque_ref -ne 'OpaqueRef:NULL')

        if ($toolsAvailable) {
            Write-Log "XenServer Tools (PV drivers) detected - clean shutdown available" 'INFO'
        } else {
            Write-Log "XenServer Tools not detected - graceful shutdown may not work" 'WARNING'
            Write-Log "Consider installing PV drivers in the guest OS" 'WARNING'
        }

        # Send clean shutdown command (ACPI / PV drivers)
        Invoke-XenVM -VM $VM -XenAction CleanShutdown -ErrorAction Stop
        Write-Log "Graceful shutdown command sent to VM: $($VM.name_label)" 'SUCCESS'

        $shutdownSuccess = Wait-ForVMShutdown -VMName $VM.name_label `
                                              -TimeoutSeconds $TimeoutSeconds `
                                              -IntervalSeconds $IntervalSeconds
        return $shutdownSuccess

    } catch {
        Write-Log "Failed to initiate graceful shutdown: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Perform Forced Shutdown
function Invoke-XenVMForcedShutdown {
    <#
    .SYNOPSIS
    Performs a forced (hard) shutdown of a XenServer VM (equivalent to pulling the power).
    Use only as fallback when graceful shutdown fails.

    .PARAMETER VM
    The VM object to shut down.

    .RETURNS
    Boolean indicating success or failure.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )

    Write-Log "Initiating forced (hard) shutdown for VM: $($VM.name_label)..." 'WARNING'

    try {
        Invoke-XenVM -VM $VM -XenAction HardShutdown -ErrorAction Stop
        Write-Log "Forced shutdown command sent to VM: $($VM.name_label)" 'SUCCESS'

        # Brief pause then verify
        Start-Sleep -Seconds 5
        $currentVM = Get-XenVMFresh -VMName $VM.name_label

        if ($currentVM.power_state.ToString() -eq 'Halted') {
            Write-Log "VM '$($VM.name_label)' successfully powered off (forced shutdown)" 'SUCCESS'
            return $true
        } else {
            Write-Log "VM '$($VM.name_label)' power state after forced shutdown: $($currentVM.power_state)" 'WARNING'
            return $false
        }

    } catch {
        Write-Log "Failed to perform forced shutdown: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Main Execution
try {
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
        'Running' {
            Write-Log "VM is running - proceeding with graceful shutdown..." 'INFO'

            $gracefulSuccess = Invoke-XenVMGracefulShutdown -VM $vm `
                                                            -TimeoutSeconds $config.Shutdown.TimeoutSeconds `
                                                            -IntervalSeconds $config.Shutdown.CheckIntervalSeconds

            if ($gracefulSuccess) {
                Write-Log "VM '${VMName}' has shut down successfully (graceful)" 'SUCCESS'
                exit 0
            } else {
                Write-Log "VM '${VMName}' did not shut down gracefully within the timeout" 'WARNING'
                Write-Log "Waiting $($config.Shutdown.ForcedShutdownDelay) seconds before attempting forced shutdown..." 'WARNING'
                Start-Sleep -Seconds $config.Shutdown.ForcedShutdownDelay

                # Check if VM shut down during the wait
                $currentVM = Get-XenVMFresh -VMName $VMName
                if ($currentVM.power_state.ToString() -eq 'Halted') {
                    Write-Log "VM '${VMName}' shut down during waiting period" 'SUCCESS'
                    exit 0
                }

                # Fallback: forced shutdown
                Write-Log "Attempting forced shutdown as fallback..." 'WARNING'
                $forcedSuccess = Invoke-XenVMForcedShutdown -VM $currentVM

                if ($forcedSuccess) {
                    Write-Log "VM '${VMName}' has shut down successfully (forced)" 'SUCCESS'
                    exit 0
                } else {
                    Write-Log "Failed to shut down VM '${VMName}' using both graceful and forced methods" 'ERROR'
                    exit 1
                }
            }
        }
        'Halted' {
            Write-Log "VM '${VMName}' is already halted - no action needed" 'INFO'
            exit 0
        }
        'Suspended' {
            Write-Log "VM '${VMName}' is currently suspended" 'WARNING'
            Write-Log "Attempting forced shutdown of suspended VM..." 'INFO'

            $forcedSuccess = Invoke-XenVMForcedShutdown -VM $vm
            if ($forcedSuccess) {
                Write-Log "Suspended VM '${VMName}' has been powered off successfully" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to power off suspended VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        'Paused' {
            Write-Log "VM '${VMName}' is currently paused" 'WARNING'
            Write-Log "Attempting forced shutdown of paused VM..." 'INFO'

            $forcedSuccess = Invoke-XenVMForcedShutdown -VM $vm
            if ($forcedSuccess) {
                Write-Log "Paused VM '${VMName}' has been powered off successfully" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to power off paused VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        default {
            Write-Log "VM '${VMName}' is in an unexpected power state: $($vm.power_state)" 'WARNING'
            exit 0
        }
    }

} catch {
    Write-Log "Unhandled error during VM shutdown: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    exit 1

} finally {
    Write-Log "Disconnecting from XenServer..." 'INFO'
    try {
        Disconnect-XenServer -ErrorAction SilentlyContinue
        Write-Log "Disconnected from XenServer" 'SUCCESS'
    } catch {
        Write-Log "Error during XenServer disconnect: $_" 'WARNING'
    }

    Write-Log "=== XenServer Graceful Shutdown Script Completed ===" 'SUCCESS'
}
#endregion
