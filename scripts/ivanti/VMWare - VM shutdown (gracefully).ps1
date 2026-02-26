# VMWare - VM shutdown (gracefully)
# Performs a graceful shutdown of a specified virtual machine with timeout handling

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
Write-Log "=== Starting VMware Graceful Shutdown Script ===" 'INFO'

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
    
    # Shutdown settings
    Shutdown = @{
        TimeoutSeconds       = 300  # 5 minutes timeout for graceful shutdown
        CheckIntervalSeconds = 5    # Check power state every 5 seconds
        ForcedShutdownDelay  = 30   # Wait 30 seconds before forcing shutdown
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Shutdown timeout: $($config.Shutdown.TimeoutSeconds) seconds" 'INFO'
#endregion

#region Function: Wait for VM Shutdown
function Wait-ForVMShutdown {
    <#
    .SYNOPSIS
    Waits for a VM to complete shutdown within specified timeout
    
    .PARAMETER VM
    The VM object to monitor
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait for shutdown in seconds
    
    .PARAMETER IntervalSeconds
    Check interval in seconds
    
    .RETURNS
    Boolean indicating if VM shut down successfully within timeout
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$TimeoutSeconds = 300,
        [int]$IntervalSeconds = 5
    )
    
    Write-Log "Waiting for VM $($VM.Name) to shut down (Timeout: ${TimeoutSeconds}s)..." 'INFO'
    
    $elapsedTime = 0
    $checks = 0
    
    while ($elapsedTime -lt $TimeoutSeconds) {
        $checks++
        
        # Refresh VM state
        try {
            $currentVM = Get-VM -Name $VM.Name -ErrorAction Stop
            $powerState = $currentVM.PowerState
            
            Write-Log "Check ${checks}: VM power state is '${powerState}' (Elapsed: ${elapsedTime}s / ${TimeoutSeconds}s)" 'INFO'
            
            # Check if VM is powered off
            if ($powerState -eq 'PoweredOff') {
                Write-Log "VM $($VM.Name) successfully shut down after ${elapsedTime} seconds" 'SUCCESS'
                return $true
            }
            
        } catch {
            Write-Log "Error checking VM power state: $_" 'WARNING'
        }
        
        # Wait before next check
        Start-Sleep -Seconds $IntervalSeconds
        $elapsedTime += $IntervalSeconds
    }
    
    Write-Log "VM $($VM.Name) did not shut down within ${TimeoutSeconds} seconds" 'WARNING'
    return $false
}
#endregion

#region Function: Perform Graceful Shutdown
function Invoke-GracefulShutdown {
    <#
    .SYNOPSIS
    Performs graceful shutdown of a VM with timeout and optional force shutdown
    
    .PARAMETER VM
    The VM object to shut down
    
    .PARAMETER TimeoutSeconds
    Maximum time to wait for graceful shutdown
    
    .PARAMETER CheckIntervalSeconds
    Interval between power state checks
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [int]$TimeoutSeconds = 300,
        [int]$CheckIntervalSeconds = 5
    )
    
    Write-Log "Initiating graceful shutdown for VM: $($VM.Name)..." 'INFO'
    
    try {
        # Check VMware Tools status before attempting graceful shutdown
        $toolsStatus = $VM.ExtensionData.Guest.ToolsStatus
        Write-Log "VMware Tools status: ${toolsStatus}" 'INFO'
        
        if ($toolsStatus -ne 'toolsOk' -and $toolsStatus -ne 'toolsOld') {
            Write-Log "VMware Tools not running properly (Status: ${toolsStatus})" 'WARNING'
            Write-Log "Graceful shutdown may not be possible - consider forced shutdown" 'WARNING'
        }
        
        # Initiate graceful shutdown
        Shutdown-VMGuest -VM $VM -Confirm:$false -ErrorAction Stop
        Write-Log "Graceful shutdown command sent to VM: $($VM.Name)" 'SUCCESS'
        
        # Wait for VM to shut down
        $shutdownSuccess = Wait-ForVMShutdown -VM $VM -TimeoutSeconds $TimeoutSeconds -IntervalSeconds $CheckIntervalSeconds
        
        return $shutdownSuccess
        
    } catch {
        Write-Log "Failed to initiate graceful shutdown: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Perform Forced Shutdown
function Invoke-ForcedShutdown {
    <#
    .SYNOPSIS
    Performs forced (hard) shutdown of a VM
    
    .PARAMETER VM
    The VM object to shut down
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    Write-Log "Initiating forced shutdown for VM: $($VM.Name)..." 'WARNING'
    
    try {
        Stop-VM -VM $VM -Confirm:$false -ErrorAction Stop
        Write-Log "Forced shutdown command sent to VM: $($VM.Name)" 'SUCCESS'
        
        # Wait a moment and verify shutdown
        Start-Sleep -Seconds 5
        $currentVM = Get-VM -Name $VM.Name -ErrorAction Stop
        
        if ($currentVM.PowerState -eq 'PoweredOff') {
            Write-Log "VM $($VM.Name) successfully powered off (forced shutdown)" 'SUCCESS'
            return $true
        } else {
            Write-Log "VM $($VM.Name) power state after forced shutdown: $($currentVM.PowerState)" 'WARNING'
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
    } catch {
        Write-Log "VM '${VMName}' not found in vSphere inventory" 'ERROR'
        throw "VM '${VMName}' does not exist"
    }
    
    # Check current power state and proceed with shutdown
    switch ($vm.PowerState) {
        'PoweredOn' {
            Write-Log "VM is powered on - proceeding with graceful shutdown..." 'INFO'
            
            # Attempt graceful shutdown
            $gracefulSuccess = Invoke-GracefulShutdown -VM $vm `
                                                       -TimeoutSeconds $config.Shutdown.TimeoutSeconds `
                                                       -CheckIntervalSeconds $config.Shutdown.CheckIntervalSeconds
            
            if ($gracefulSuccess) {
                Write-Log "VM '${VMName}' has shut down successfully (graceful shutdown)" 'SUCCESS'
                exit 0
            } else {
                Write-Log "VM '${VMName}' did not shut down gracefully within the timeout period" 'WARNING'
                Write-Log "Waiting $($config.Shutdown.ForcedShutdownDelay) seconds before attempting forced shutdown..." 'WARNING'
                Start-Sleep -Seconds $config.Shutdown.ForcedShutdownDelay
                
                # Check if VM shut down during wait period
                $currentVM = Get-VM -Name $VMName -ErrorAction Stop
                if ($currentVM.PowerState -eq 'PoweredOff') {
                    Write-Log "VM '${VMName}' shut down during waiting period" 'SUCCESS'
                    exit 0
                }
                
                # Attempt forced shutdown as fallback
                Write-Log "Attempting forced shutdown as fallback method..." 'WARNING'
                $forcedSuccess = Invoke-ForcedShutdown -VM $currentVM
                
                if ($forcedSuccess) {
                    Write-Log "VM '${VMName}' has shut down successfully (forced shutdown)" 'SUCCESS'
                    exit 0
                } else {
                    Write-Log "Failed to shut down VM '${VMName}' using both graceful and forced methods" 'ERROR'
                    exit 1
                }
            }
        }
        'PoweredOff' {
            Write-Log "VM '${VMName}' is already powered off - no action needed" 'INFO'
            exit 0
        }
        'Suspended' {
            Write-Log "VM '${VMName}' is currently suspended" 'WARNING'
            Write-Log "Attempting to power off suspended VM..." 'INFO'
            
            $forcedSuccess = Invoke-ForcedShutdown -VM $vm
            if ($forcedSuccess) {
                Write-Log "Suspended VM '${VMName}' has been powered off successfully" 'SUCCESS'
                exit 0
            } else {
                Write-Log "Failed to power off suspended VM '${VMName}'" 'ERROR'
                exit 1
            }
        }
        default {
            Write-Log "VM '${VMName}' is in an unexpected power state: $($vm.PowerState)" 'WARNING'
            exit 0
        }
    }
    
} catch {
    Write-Log "Unhandled error during VM shutdown: $_" 'ERROR'
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
    
    Write-Log "=== VMware Graceful Shutdown Script Completed ===" 'SUCCESS'
}
#endregion