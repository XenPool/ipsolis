# VMWare - VM change startup to UEFI
# Changes the firmware type of a VM from BIOS to UEFI

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
Write-Log "=== Starting VMware Firmware Change to UEFI Script ===" 'INFO'

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
    
    # Firmware settings
    Firmware = @{
        TargetType = 'efi'  # Target firmware type (efi for UEFI)
    }
}

# VM name from input parameter
$VMName = "$[VMName]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Target firmware type: UEFI (EFI)" 'INFO'
#endregion

#region Function: Get Current Firmware Type
function Get-VMFirmwareType {
    <#
    .SYNOPSIS
    Retrieves the current firmware type of a VM
    
    .PARAMETER VM
    The VM object to query
    
    .RETURNS
    String representing the current firmware type
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    try {
        Write-Log "Retrieving current firmware type for VM: $($VM.Name)..." 'INFO'
        
        $currentFirmware = $VM.ExtensionData.Config.Firmware
        
        if ($currentFirmware) {
            Write-Log "Current firmware type: ${currentFirmware}" 'INFO'
        } else {
            Write-Log "Unable to determine current firmware type" 'WARNING'
        }
        
        return $currentFirmware
        
    } catch {
        Write-Log "Failed to retrieve current firmware type: $_" 'ERROR'
        return $null
    }
}
#endregion

#region Function: Change Firmware to UEFI
function Set-VMFirmwareToUEFI {
    <#
    .SYNOPSIS
    Changes the VM firmware type to UEFI
    
    .PARAMETER VM
    The VM object to configure
    
    .RETURNS
    Boolean indicating success or failure
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )
    
    try {
        Write-Log "Changing firmware type to UEFI for VM: $($VM.Name)..." 'INFO'
        
        # Check current firmware type
        $currentFirmware = $VM.ExtensionData.Config.Firmware
        
        if ($currentFirmware -eq 'efi') {
            Write-Log "VM firmware is already set to UEFI - no change needed" 'INFO'
            return $true
        }
        
        Write-Log "Current firmware type: ${currentFirmware}, changing to: efi (UEFI)..." 'INFO'
        
        # Create VM configuration specification object
        $spec = New-Object -TypeName VMware.Vim.VirtualMachineConfigSpec
        
        # Set firmware type to UEFI (EFI)
        $spec.Firmware = [VMware.Vim.GuestOsDescriptorFirmwareType]::efi
        
        Write-Log "Configuration specification created for UEFI firmware" 'SUCCESS'
        
        # Apply configuration to VM
        $VM.ExtensionData.ReconfigVM($spec)
        
        Write-Log "Firmware type change command sent successfully" 'SUCCESS'
        return $true
        
    } catch {
        Write-Log "Failed to change firmware type to UEFI: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Verify Firmware Type
function Test-VMFirmwareType {
    <#
    .SYNOPSIS
    Verifies that the VM firmware type has been changed correctly
    
    .PARAMETER VMName
    The name of the VM to verify
    
    .PARAMETER ExpectedType
    The expected firmware type
    
    .RETURNS
    Boolean indicating if firmware type matches expected
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedType
    )
    
    try {
        Write-Log "Verifying firmware type for VM: ${VMName}..." 'INFO'
        
        # Refresh VM object
        $refreshedVM = Get-VM -Name $VMName -ErrorAction Stop
        $actualFirmware = $refreshedVM.ExtensionData.Config.Firmware
        
        if ($actualFirmware -eq $ExpectedType) {
            Write-Log "Firmware type verification successful: ${actualFirmware}" 'SUCCESS'
            return $true
        } else {
            Write-Log "Firmware type verification failed: Expected '${ExpectedType}', found '${actualFirmware}'" 'WARNING'
            return $false
        }
        
    } catch {
        Write-Log "Failed to verify firmware type: $_" 'ERROR'
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
    
    # Check VM power state - UEFI change requires VM to be powered off
    if ($vm.PowerState -ne 'PoweredOff') {
        Write-Log "ERROR: VM '${VMName}' must be powered off to change firmware type" 'ERROR'
        Write-Log "Current power state: $($vm.PowerState)" 'ERROR'
        Write-Log "Please power off the VM before changing to UEFI firmware" 'ERROR'
        exit 1
    }
    
    Write-Log "VM is powered off - proceeding with firmware change" 'INFO'
    
    # Get and display current firmware type
    $currentFirmware = Get-VMFirmwareType -VM $vm
    
    # Check hardware version compatibility
    $hardwareVersion = $vm.HardwareVersion
    Write-Log "Checking hardware version compatibility for UEFI..." 'INFO'
    
    # UEFI requires hardware version 7 or higher (vmx-07 or newer)
    if ($hardwareVersion -match 'vmx-(\d+)') {
        $versionNumber = [int]$Matches[1]
        if ($versionNumber -lt 7) {
            Write-Log "WARNING: Hardware version ${hardwareVersion} may not fully support UEFI" 'WARNING'
            Write-Log "UEFI requires hardware version vmx-07 or higher" 'WARNING'
            Write-Log "Consider upgrading VM hardware version first" 'WARNING'
        } else {
            Write-Log "Hardware version ${hardwareVersion} supports UEFI" 'SUCCESS'
        }
    }
    
    # Change firmware to UEFI
    $changeSuccess = Set-VMFirmwareToUEFI -VM $vm
    
    if (-not $changeSuccess) {
        Write-Log "Failed to change firmware type to UEFI for VM '${VMName}'" 'ERROR'
        exit 1
    }
    
    # Verify the changes
    Write-Log "Performing post-change verification..." 'INFO'
    Start-Sleep -Seconds 2  # Brief pause to ensure changes are committed
    
    # Verify firmware type
    $verifySuccess = Test-VMFirmwareType -VMName $VMName -ExpectedType $config.Firmware.TargetType
    
    if ($verifySuccess) {
        Write-Log "Firmware type successfully changed to UEFI for VM '${VMName}'" 'SUCCESS'
        Write-Log "IMPORTANT: Next boot will use UEFI firmware" 'WARNING'
        Write-Log "NOTE: Ensure the guest OS supports UEFI boot" 'WARNING'
        exit 0
    } else {
        Write-Log "Firmware type change verification failed for VM '${VMName}'" 'ERROR'
        exit 1
    }
    
} catch {
    Write-Log "Unhandled error during firmware type change: $_" 'ERROR'
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
    
    Write-Log "=== VMware Firmware Change to UEFI Script Completed ===" 'SUCCESS'
}
#endregion