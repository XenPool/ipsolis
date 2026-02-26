# VMWare - VM change boot order (net-cd-disk)
# Changes the boot order of a VM to: Network → CD-ROM → Hard Disk

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
Write-Log "=== Starting VMware Boot Order Change Script ===" 'INFO'

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
    
    # Boot order settings
    BootOrder = @{
        # Desired boot sequence: Network → CD-ROM → Hard Disk
        Sequence = @('Network', 'CD-ROM', 'HardDisk')
    }
    
    # Device names (can be customized if needed)
    Devices = @{
        HardDiskName      = 'Hard disk 1'
        NetworkAdapterName = 'Network adapter 1'
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Desired boot order: $($config.BootOrder.Sequence -join ' → ')" 'INFO'
#endregion

#region Function: Get Current Boot Order
function Get-VMBootOrder {
    <#
    .SYNOPSIS
    Retrieves and displays the current boot order of a VM
    
    .PARAMETER VM
    The VM object to query
    
    .RETURNS
    Array of current boot order devices
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    try {
        Write-Log "Retrieving current boot order for VM: $($VM.Name)..." 'INFO'
        
        $currentBootOrder = $VM.ExtensionData.Config.BootOptions.BootOrder
        
        if ($currentBootOrder -and $currentBootOrder.Count -gt 0) {
            Write-Log "Current boot order has $($currentBootOrder.Count) device(s)" 'INFO'
            
            $index = 1
            foreach ($device in $currentBootOrder) {
                $deviceType = $device.GetType().Name -replace 'VirtualMachineBootOptionsBootable', '' -replace 'Device', ''
                Write-Log "  ${index}. ${deviceType}" 'INFO'
                $index++
            }
        } else {
            Write-Log "No specific boot order configured (using default)" 'WARNING'
        }
        
        return $currentBootOrder
        
    } catch {
        Write-Log "Failed to retrieve current boot order: $_" 'ERROR'
        return $null
    }
}
#endregion

#region Function: Create Boot Order Configuration
function New-BootOrderConfiguration {
    <#
    .SYNOPSIS
    Creates boot order configuration with Network, CD-ROM, and Hard Disk
    
    .PARAMETER VM
    The VM object to configure
    
    .PARAMETER HardDiskName
    Name of the hard disk device
    
    .PARAMETER NetworkAdapterName
    Name of the network adapter device
    
    .RETURNS
    VirtualMachineConfigSpec object with boot order configuration
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [string]$HardDiskName = 'Hard disk 1',
        [string]$NetworkAdapterName = 'Network adapter 1'
    )
    
    try {
        Write-Log "Creating boot order configuration..." 'INFO'
        
        # Create VM configuration specification object
        $spec = New-Object -TypeName VMware.Vim.VirtualMachineConfigSpec
        
        # Copy existing boot options from VM
        $spec.BootOptions = $VM.ExtensionData.Config.BootOptions
        Write-Log "Copied existing boot options from VM configuration" 'INFO'
        
        # Create bootable hard disk device object
        Write-Log "Configuring hard disk boot device: ${HardDiskName}..." 'INFO'
        $disk = New-Object -TypeName VMware.Vim.VirtualMachineBootOptionsBootableDiskDevice
        
        try {
            $hardDisk = Get-HardDisk -Name $HardDiskName -VM $VM -ErrorAction Stop
            $disk.DeviceKey = $hardDisk.ExtensionData.Key
            Write-Log "Hard disk device configured successfully (DeviceKey: $($disk.DeviceKey))" 'SUCCESS'
        } catch {
            Write-Log "Failed to get hard disk '${HardDiskName}': $_" 'ERROR'
            throw "Hard disk '${HardDiskName}' not found on VM"
        }
        
        # Create bootable CD-ROM device object
        Write-Log "Configuring CD-ROM boot device..." 'INFO'
        $cdrom = New-Object -TypeName VMware.Vim.VirtualMachineBootOptionsBootableCdromDevice
        Write-Log "CD-ROM device configured successfully" 'SUCCESS'
        
        # Create bootable network adapter device object
        Write-Log "Configuring network adapter boot device: ${NetworkAdapterName}..." 'INFO'
        $network = New-Object -TypeName VMware.Vim.VirtualMachineBootOptionsBootableEthernetDevice
        
        try {
            $networkAdapter = Get-NetworkAdapter -Name $NetworkAdapterName -VM $VM -ErrorAction Stop
            $network.DeviceKey = $networkAdapter.ExtensionData.Key
            Write-Log "Network adapter device configured successfully (DeviceKey: $($network.DeviceKey))" 'SUCCESS'
        } catch {
            Write-Log "Failed to get network adapter '${NetworkAdapterName}': $_" 'ERROR'
            throw "Network adapter '${NetworkAdapterName}' not found on VM"
        }
        
        # Set boot order: Network → CD-ROM → Hard Disk
        $spec.BootOptions.BootOrder = @($network, $cdrom, $disk)
        Write-Log "Boot order set to: Network → CD-ROM → Hard Disk" 'SUCCESS'
        
        return $spec
        
    } catch {
        Write-Log "Failed to create boot order configuration: $_" 'ERROR'
        throw
    }
}
#endregion

#region Function: Apply Boot Order Configuration
function Set-VMBootOrder {
    <#
    .SYNOPSIS
    Applies boot order configuration to a VM
    
    .PARAMETER VM
    The VM object to configure
    
    .PARAMETER ConfigSpec
    The configuration specification to apply
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [Parameter(Mandatory = $true)]
        $ConfigSpec
    )
    
    try {
        Write-Log "Applying boot order configuration to VM: $($VM.Name)..." 'INFO'
        
        # Reconfigure VM with new boot order
        $VM.ExtensionData.ReconfigVM($ConfigSpec)
        
        Write-Log "Boot order configuration applied successfully" 'SUCCESS'
        return $true
        
    } catch {
        Write-Log "Failed to apply boot order configuration: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Verify Boot Order
function Test-VMBootOrder {
    <#
    .SYNOPSIS
    Verifies the boot order has been applied correctly
    
    .PARAMETER VM
    The VM object to verify
    
    .RETURNS
    Boolean indicating if boot order is correct
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    try {
        Write-Log "Verifying boot order configuration..." 'INFO'
        
        # Refresh VM object
        $refreshedVM = Get-VM -Name $VM.Name -ErrorAction Stop
        $bootOrder = $refreshedVM.ExtensionData.Config.BootOptions.BootOrder
        
        if (-not $bootOrder -or $bootOrder.Count -eq 0) {
            Write-Log "Boot order verification failed: No boot order configured" 'WARNING'
            return $false
        }
        
        # Expected order: Network, CD-ROM, Hard Disk
        $expectedTypes = @('VirtualMachineBootOptionsBootableEthernetDevice', 
                          'VirtualMachineBootOptionsBootableCdromDevice', 
                          'VirtualMachineBootOptionsBootableDiskDevice')
        
        if ($bootOrder.Count -ne $expectedTypes.Count) {
            Write-Log "Boot order verification warning: Expected $($expectedTypes.Count) devices, found $($bootOrder.Count)" 'WARNING'
        }
        
        # Verify each device position
        $allCorrect = $true
        for ($i = 0; $i -lt [Math]::Min($bootOrder.Count, $expectedTypes.Count); $i++) {
            $actualType = $bootOrder[$i].GetType().FullName
            $expectedType = $expectedTypes[$i]
            $deviceName = $actualType -replace 'VMware.Vim.VirtualMachineBootOptionsBootable', '' -replace 'Device', ''
            
            if ($actualType -eq $expectedType) {
                Write-Log "Position $($i + 1): ${deviceName} - Correct" 'SUCCESS'
            } else {
                Write-Log "Position $($i + 1): ${deviceName} - Incorrect (expected: $($expectedType -replace 'VMware.Vim.VirtualMachineBootOptionsBootable', '' -replace 'Device', ''))" 'WARNING'
                $allCorrect = $false
            }
        }
        
        return $allCorrect
        
    } catch {
        Write-Log "Failed to verify boot order: $_" 'ERROR'
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
        Write-Log "Boot order changes will take effect on next VM restart" 'WARNING'
    } else {
        Write-Log "VM is powered off - boot order changes will take effect on next startup" 'INFO'
    }
    
    # Get and display current boot order
    $currentBootOrder = Get-VMBootOrder -VM $vm
    
    # Create boot order configuration
    $configSpec = New-BootOrderConfiguration -VM $vm `
                                             -HardDiskName $config.Devices.HardDiskName `
                                             -NetworkAdapterName $config.Devices.NetworkAdapterName
    
    if (-not $configSpec) {
        Write-Log "Failed to create boot order configuration" 'ERROR'
        exit 1
    }
    
    # Apply boot order configuration
    $applySuccess = Set-VMBootOrder -VM $vm -ConfigSpec $configSpec
    
    if (-not $applySuccess) {
        Write-Log "Failed to apply boot order configuration to VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Verify the changes
    Write-Log "Performing post-change verification..." 'INFO'
    Start-Sleep -Seconds 2  # Brief pause to ensure changes are committed
    
    # Verify boot order
    $verifySuccess = Test-VMBootOrder -VM $vm
    
    if ($verifySuccess) {
        Write-Log "Boot order successfully changed and verified for VM '${VMName}'" 'SUCCESS'
        Write-Log "New boot order: Network → CD-ROM → Hard Disk" 'SUCCESS'
        exit 0
    } else {
        Write-Log "Boot order verification completed with warnings for VM '${VMName}'" 'WARNING'
        exit 0
    }
    
} catch {
    Write-Log "Unhandled error during boot order change: $_" 'ERROR'
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
    
    Write-Log "=== VMware Boot Order Change Script Completed ===" 'SUCCESS'
}
#endregion