# VMWare - VM change NIC to vmxnet3
# Changes the network adapter type of a VM to vmxnet3 for optimal performance

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
Write-Log "=== Starting VMware NIC Type Change Script ===" 'INFO'

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
    
    # Network adapter settings
    NetworkAdapter = @{
        TargetType = 'vmxnet3'  # Target adapter type (vmxnet3 for optimal performance)
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Target NIC type: $($config.NetworkAdapter.TargetType)" 'INFO'
#endregion

#region Function: Get Network Adapter Information
function Get-NetworkAdapterInfo {
    <#
    .SYNOPSIS
    Retrieves detailed information about VM network adapters
    
    .PARAMETER VM
    The VM object to query
    
    .RETURNS
    Array of network adapter objects
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    try {
        Write-Log "Retrieving network adapter information for VM: $($VM.Name)..." 'INFO'
        $adapters = Get-NetworkAdapter -VM $VM -ErrorAction Stop
        
        if ($adapters) {
            Write-Log "Found $($adapters.Count) network adapter(s) on VM: $($VM.Name)" 'SUCCESS'
            
            $adapterIndex = 1
            foreach ($adapter in $adapters) {
                Write-Log "Adapter ${adapterIndex}: Type = $($adapter.Type), Name = $($adapter.Name), NetworkName = $($adapter.NetworkName), Connected = $($adapter.ConnectionState.Connected)" 'INFO'
                $adapterIndex++
            }
        } else {
            Write-Log "No network adapters found on VM: $($VM.Name)" 'WARNING'
        }
        
        return $adapters
        
    } catch {
        Write-Log "Failed to retrieve network adapter information: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Change Network Adapter Type
function Set-NetworkAdapterType {
    <#
    .SYNOPSIS
    Changes the network adapter type to vmxnet3
    
    .PARAMETER Adapters
    Array of network adapter objects to change
    
    .PARAMETER TargetType
    Target adapter type (e.g., vmxnet3)
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $Adapters,
        [Parameter(Mandatory = $true)]
        [string]$TargetType
    )
    
    Write-Log "Starting network adapter type change to '${TargetType}'..." 'INFO'
    
    $changedCount = 0
    $skippedCount = 0
    $failedCount = 0
    
    foreach ($adapter in $Adapters) {
        try {
            # Check if adapter already has the target type
            if ($adapter.Type -eq $TargetType) {
                Write-Log "Adapter '$($adapter.Name)' is already type '${TargetType}' - skipping" 'INFO'
                $skippedCount++
                continue
            }
            
            Write-Log "Changing adapter '$($adapter.Name)' from '$($adapter.Type)' to '${TargetType}'..." 'INFO'
            
            # Store original properties for verification
            $originalType = $adapter.Type
            $adapterName = $adapter.Name
            $networkName = $adapter.NetworkName
            
            # Change adapter type
            $result = Set-NetworkAdapter -NetworkAdapter $adapter -Type $TargetType -Confirm:$false -ErrorAction Stop
            
            if ($result) {
                Write-Log "Successfully changed adapter '${adapterName}' from '${originalType}' to '${TargetType}'" 'SUCCESS'
                Write-Log "Adapter '${adapterName}' is connected to network: ${networkName}" 'INFO'
                $changedCount++
            } else {
                Write-Log "Failed to change adapter '${adapterName}' - no result returned" 'WARNING'
                $failedCount++
            }
            
        } catch {
            Write-Log "Error changing adapter '$($adapter.Name)': $_" 'ERROR'
            $failedCount++
        }
    }
    
    # Summary
    Write-Log "Network adapter type change summary:" 'INFO'
    Write-Log "  - Changed: ${changedCount}" 'INFO'
    Write-Log "  - Skipped (already correct type): ${skippedCount}" 'INFO'
    Write-Log "  - Failed: ${failedCount}" 'INFO'
    
    # Return success if at least one adapter was changed or all were already correct
    return ($changedCount -gt 0 -or ($skippedCount -gt 0 -and $failedCount -eq 0))
}
#endregion

#region Function: Verify Network Adapter Types
function Test-NetworkAdapterTypes {
    <#
    .SYNOPSIS
    Verifies that all network adapters have the correct type
    
    .PARAMETER VM
    The VM object to verify
    
    .PARAMETER ExpectedType
    Expected adapter type
    
    .RETURNS
    Boolean indicating if all adapters are correct type
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedType
    )
    
    try {
        Write-Log "Verifying network adapter types for VM: $($VM.Name)..." 'INFO'
        $adapters = Get-NetworkAdapter -VM $VM -ErrorAction Stop
        
        $allCorrect = $true
        foreach ($adapter in $adapters) {
            if ($adapter.Type -ne $ExpectedType) {
                Write-Log "Adapter '$($adapter.Name)' has incorrect type: $($adapter.Type) (expected: ${ExpectedType})" 'WARNING'
                $allCorrect = $false
            } else {
                Write-Log "Adapter '$($adapter.Name)' has correct type: $($adapter.Type)" 'SUCCESS'
            }
        }
        
        return $allCorrect
        
    } catch {
        Write-Log "Failed to verify network adapter types: $_" 'ERROR'
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
    
    # Check VM power state and warn if powered on
    if ($vm.PowerState -eq 'PoweredOn') {
        Write-Log "WARNING: VM '${VMName}' is currently powered on" 'WARNING'
        Write-Log "Changing network adapter type on a running VM may cause network interruption" 'WARNING'
        Write-Log "It is recommended to power off the VM before changing adapter types" 'WARNING'
    } else {
        Write-Log "VM is powered off - safe to change network adapter type" 'INFO'
    }
    
    # Get network adapter information
    $adapters = Get-NetworkAdapterInfo -VM $vm
    
    if (-not $adapters -or $adapters.Count -eq 0) {
        Write-Log "VM '${VMName}' has no network adapters - nothing to change" 'WARNING'
        exit 0
    }
    
    # Change network adapter type
    $changeSuccess = Set-NetworkAdapterType -Adapters $adapters -TargetType $config.NetworkAdapter.TargetType
    
    if (-not $changeSuccess) {
        Write-Log "Failed to change network adapter types for VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Verify the changes
    Write-Log "Performing post-change verification..." 'INFO'
    Start-Sleep -Seconds 2  # Brief pause to ensure changes are committed
    
    # Refresh VM object
    $vm = Get-VM -Name $VMName -ErrorAction Stop
    
    # Verify adapter types
    $verifySuccess = Test-NetworkAdapterTypes -VM $vm -ExpectedType $config.NetworkAdapter.TargetType
    
    if ($verifySuccess) {
        Write-Log "All network adapters successfully changed and verified for VM '${VMName}'" 'SUCCESS'
        
        # Provide recommendations if VM is powered on
        if ($vm.PowerState -eq 'PoweredOn') {
            Write-Log "RECOMMENDATION: Restart the VM guest OS to ensure optimal driver loading" 'WARNING'
        }
        
        exit 0
    } else {
        Write-Log "Network adapter type verification failed for VM '${VMName}'" 'WARNING'
        exit 1
    }
    
} catch {
    Write-Log "Unhandled error during network adapter type change: $_" 'ERROR'
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
    
    Write-Log "=== VMware NIC Type Change Script Completed ===" 'SUCCESS'
}
#endregion