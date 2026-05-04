# NAME: XenServer - VM stop (force)
# DESC: Forced (hard) shutdown of a XenServer/XCP-ng VM with retry logic
# XenServer - VM stop (force)
# Performs a forced (hard) shutdown of a XenServer VM with retry logic
# XCP-ng / XenServer equivalent of: VMWare - VM stop (force).ps1

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
Write-Log "=== Starting XenServer Forced Stop Script ===" 'INFO'

$ErrorActionPreference = "Stop"

# Centralized configuration - hosting from $VARS (global), VM name from param()
$config = @{
    XenServer = @{
        ServerHost = $VARS.'xenserver.host'
        AdminUser  = $VARS.'xenserver.username'
        AdminPW    = $VARS.'xenserver.password'
    }

    Stop = @{
        TimeoutSeconds       = 120  # 2 minutes timeout per attempt
        CheckIntervalSeconds = 5    # Check power state every 5 seconds
        MaxRetries           = 3    # Maximum number of stop attempts
    }
}

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Forced stop timeout: $($config.Stop.TimeoutSeconds) seconds, max retries: $($config.Stop.MaxRetries)" 'INFO'
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

#region Function: Wait for VM to Halt
function Wait-ForVMHalted {
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
    Boolean indicating if VM halted within timeout.
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [int]$TimeoutSeconds = 120,
        [int]$IntervalSeconds = 5
    )

    Write-Log "Waiting for VM '${VMName}' to halt (Timeout: ${TimeoutSeconds}s)..." 'INFO'

    $elapsedTime = 0
    $checks = 0

    while ($elapsedTime -lt $TimeoutSeconds) {
        $checks++

        try {
            $currentVM    = Get-XenVMFresh -VMName $VMName
            $currentState = $currentVM.power_state.ToString()

            Write-Log "Check ${checks}: VM power state is '${currentState}' (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)" 'INFO'

            if ($currentState -eq 'Halted') {
                Write-Log "VM '${VMName}' successfully halted after ${elapsedTime} seconds" 'SUCCESS'
                return $true
            }
        } catch {
            Write-Log "Error checking VM power state: $_" 'WARNING'
        }

        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }

    Write-Log "VM '${VMName}' did not halt within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Perform Forced Stop with Retry Logic
function Invoke-XenVMForcedStopWithRetry {
    <#
    .SYNOPSIS
    Performs forced (hard) shutdown of a XenServer VM with retry logic.
    Uses HardShutdown - equivalent to pulling the power plug.

    .PARAMETER VM
    The VM object to stop.

    .PARAMETER MaxRetries
    Maximum number of stop attempts.

    .PARAMETER TimeoutSeconds
    Timeout per attempt waiting for Halted state.

    .PARAMETER CheckIntervalSeconds
    Interval between power state checks.

    .RETURNS
    Boolean indicating success or failure.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$MaxRetries = 3,
        [int]$TimeoutSeconds = 120,
        [int]$CheckIntervalSeconds = 5
    )

    Write-Log "Initiating forced stop for VM: $($VM.name_label)..." 'INFO'

    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            Write-Log "Forced stop attempt ${attempt} of ${MaxRetries}..." 'INFO'

            # Refresh VM state before each attempt
            $currentVM    = Get-XenVMFresh -VMName $VM.name_label
            $currentState = $currentVM.power_state.ToString()
            Write-Log "Current VM power state: ${currentState}" 'INFO'

            if ($currentState -eq 'Halted') {
                Write-Log "VM is already halted - no action needed" 'INFO'
                return $true
            }

            # Perform forced (hard) shutdown
            Invoke-XenVM -VM $currentVM -XenAction HardShutdown -ErrorAction Stop
            Write-Log "Forced stop command sent to VM: $($VM.name_label)" 'SUCCESS'

            $stopSuccess = Wait-ForVMHalted -VMName $VM.name_label `
                                            -TimeoutSeconds $TimeoutSeconds `
                                            -IntervalSeconds $CheckIntervalSeconds

            if ($stopSuccess) {
                return $true
            } else {
                if ($attempt -lt $MaxRetries) {
                    Write-Log "VM did not halt within timeout - retrying..." 'WARNING'
                }
            }

        } catch {
            $errorMessage = "Forced stop attempt ${attempt} failed for $($VM.name_label): $_"

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
            Write-Log "VM '${VMName}' is already halted - no action needed" 'INFO'
            exit 0
        }
        'Running' {
            Write-Log "VM is running - proceeding with forced stop..." 'INFO'

            $stopSuccess = Invoke-XenVMForcedStopWithRetry -VM $vm `
                                                           -MaxRetries $config.Stop.MaxRetries `
                                                           -TimeoutSeconds $config.Stop.TimeoutSeconds `
                                                           -CheckIntervalSeconds $config.Stop.CheckIntervalSeconds

            if ($stopSuccess) {
                Write-Log "VM '${VMName}' has been successfully stopped (forced)" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to stop VM '${VMName}' after $($config.Stop.MaxRetries) attempts" 'ERROR'
                exit 1
            }
        }
        'Suspended' {
            Write-Log "VM '${VMName}' is suspended - attempting forced stop..." 'WARNING'

            $stopSuccess = Invoke-XenVMForcedStopWithRetry -VM $vm `
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
        'Paused' {
            Write-Log "VM '${VMName}' is paused - attempting forced stop..." 'WARNING'

            $stopSuccess = Invoke-XenVMForcedStopWithRetry -VM $vm `
                                                           -MaxRetries $config.Stop.MaxRetries `
                                                           -TimeoutSeconds $config.Stop.TimeoutSeconds `
                                                           -CheckIntervalSeconds $config.Stop.CheckIntervalSeconds

            if ($stopSuccess) {
                Write-Log "Paused VM '${VMName}' has been successfully powered off" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to power off paused VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        default {
            Write-Log "VM '${VMName}' is in an unexpected power state: $($vm.power_state) - attempting forced stop anyway..." 'WARNING'

            $stopSuccess = Invoke-XenVMForcedStopWithRetry -VM $vm `
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
    Write-Log "Disconnecting from XenServer..." 'INFO'
    try {
        Disconnect-XenServer -ErrorAction SilentlyContinue
        Write-Log "Disconnected from XenServer" 'SUCCESS'
    } catch {
        Write-Log "Error during XenServer disconnect: $_" 'WARNING'
    }

    Write-Log "=== XenServer Forced Stop Script Completed ===" 'SUCCESS'
}
#endregion
