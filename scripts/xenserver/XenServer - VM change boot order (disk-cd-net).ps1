# XenServer - VM change boot order (disk-cd-net)
# Changes the boot order of a HVM VM to: Hard Disk -> CD-ROM -> Network
# XCP-ng / XenServer equivalent of: VMWare - VM change boot order (disk-cd-net).ps1
#
# XenServer HVM boot order codes:
#   c = Hard Disk   d = CD-ROM / DVD   n = Network (PXE)
# This script sets order = "cdn" (Disk -> CD-ROM -> Network)

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
Write-Log "=== Starting XenServer Boot Order Change Script (Disk-CD-Net) ===" 'INFO'

$ErrorActionPreference = "Stop"

# Centralized configuration  - hosting from $VARS (global), VM name from param()
$config = @{
    XenServer = @{
        ServerHost = $VARS.'xenserver.host'
        AdminUser  = $VARS.'xenserver.username'
        AdminPW    = $VARS.'xenserver.password'
    }

    BootOrder = @{
        # XenServer HVM boot order string: c=disk, d=cdrom, n=network
        Order       = 'cdn'
        Description = 'Hard Disk -> CD-ROM -> Network'
    }
}

Write-Log "Configuration loaded successfully" 'SUCCESS'
Write-Log "Target VM: ${VMName}" 'INFO'
Write-Log "Desired boot order: $($config.BootOrder.Description)" 'INFO'
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

#region Function: Get Current Boot Order
function Get-XenVMBootOrder {
    <#
    .SYNOPSIS
    Reads and displays the current HVM boot order of a XenServer VM.

    .PARAMETER VM
    The VM object to query.

    .RETURNS
    Current boot order string (e.g. 'cdn'), or $null for PV VMs.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM
    )

    try {
        $policy = $VM.HVM_boot_policy
        if ([string]::IsNullOrEmpty($policy)) {
            Write-Log "VM '$($VM.name_label)' is a PV VM  - HVM boot order does not apply" 'WARNING'
            return $null
        }

        $currentOrder = $VM.HVM_boot_params["order"]
        if ($currentOrder) {
            # Decode letter codes for display
            $decoded = $currentOrder.ToCharArray() | ForEach-Object {
                switch ($_) {
                    'c' { 'Hard Disk' }
                    'd' { 'CD-ROM' }
                    'n' { 'Network (PXE)' }
                    default { "Unknown($_)" }
                }
            }
            Write-Log "Current boot order: $($decoded -join ' -> ') (raw: ${currentOrder})" 'INFO'
        } else {
            Write-Log "No explicit boot order set (using BIOS default)" 'WARNING'
        }

        return $currentOrder

    } catch {
        Write-Log "Failed to retrieve current boot order: $_" 'ERROR'
        return $null
    }
}
#endregion

#region Function: Set Boot Order
function Set-XenVMBootOrder {
    <#
    .SYNOPSIS
    Sets the HVM boot order on a XenServer VM by updating hvm_boot_params["order"].

    .PARAMETER VM
    The VM object to configure.

    .PARAMETER Order
    Boot order string using XenServer codes (e.g. 'cdn').

    .RETURNS
    Boolean indicating success or failure.
    #>
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [Parameter(Mandatory = $true)]
        [string]$Order
    )

    try {
        Write-Log "Applying boot order '$Order' to VM: $($VM.name_label)..." 'INFO'

        # Clone existing hvm_boot_params to preserve other keys (e.g. firmware = uefi/bios)
        $newParams = @{}
        foreach ($key in $VM.HVM_boot_params.Keys) {
            $newParams[$key] = $VM.HVM_boot_params[$key]
        }
        $newParams["order"] = $Order

        Set-XenVM -VM $VM -HVMBootParams $newParams -ErrorAction Stop

        Write-Log "Boot order applied successfully" 'SUCCESS'
        return $true

    } catch {
        Write-Log "Failed to set boot order: $_" 'ERROR'
        return $false
    }
}
#endregion

#region Function: Verify Boot Order
function Test-XenVMBootOrder {
    <#
    .SYNOPSIS
    Re-reads the VM from XenServer and verifies the boot order was applied.

    .PARAMETER VMName
    Name of the VM to verify.

    .PARAMETER ExpectedOrder
    Expected boot order string (e.g. 'cdn').

    .RETURNS
    Boolean indicating if the boot order matches.
    #>
    param (
        [Parameter(Mandatory = $true)]
        [string]$VMName,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedOrder
    )

    try {
        $refreshedVM = Get-XenVMFresh -VMName $VMName
        $actualOrder = $refreshedVM.HVM_boot_params["order"]

        if ($actualOrder -eq $ExpectedOrder) {
            $decoded = $ExpectedOrder.ToCharArray() | ForEach-Object {
                switch ($_) {
                    'c' { 'Hard Disk' }
                    'd' { 'CD-ROM' }
                    'n' { 'Network (PXE)' }
                    default { "Unknown($_)" }
                }
            }
            Write-Log "Verification passed: boot order is $($decoded -join ' -> ') (raw: ${actualOrder})" 'SUCCESS'
            return $true
        } else {
            Write-Log "Verification failed: expected '${ExpectedOrder}', got '${actualOrder}'" 'WARNING'
            return $false
        }

    } catch {
        Write-Log "Failed to verify boot order: $_" 'ERROR'
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
        Write-Log "VM HVM boot policy: '$($vm.HVM_boot_policy)'" 'INFO'
    } catch {
        Write-Log "VM '${VMName}' not found on XenServer" 'ERROR'
        throw "VM '${VMName}' does not exist"
    }

    # Verify VM is HVM (PV VMs don't use hvm_boot_params)
    if ([string]::IsNullOrEmpty($vm.HVM_boot_policy)) {
        Write-Log "VM '${VMName}' is a PV (paravirtualized) VM  - HVM boot order is not applicable" 'WARNING'
        Write-Log "PV VMs use a bootloader (pygrub/grub2) instead of a BIOS boot order" 'WARNING'
        exit 0
    }

    # Warn if VM is running (change takes effect on next boot)
    if ($vm.power_state.ToString() -eq 'Running') {
        Write-Log "VM '${VMName}' is currently running  - boot order change takes effect on next reboot" 'WARNING'
    } else {
        Write-Log "VM is $($vm.power_state)  - boot order change takes effect on next startup" 'INFO'
    }

    # Display current boot order
    $currentOrder = Get-XenVMBootOrder -VM $vm

    # Apply new boot order
    $applySuccess = Set-XenVMBootOrder -VM $vm -Order $config.BootOrder.Order

    if (-not $applySuccess) {
        Write-Log "Failed to apply boot order to VM '${VMName}'" 'ERROR'
        exit 1
    }

    # Verify
    Write-Log "Verifying boot order change..." 'INFO'
    $verifySuccess = Test-XenVMBootOrder -VMName $VMName -ExpectedOrder $config.BootOrder.Order

    if ($verifySuccess) {
        Write-Log "Boot order successfully changed for VM '${VMName}'" 'SUCCESS'
        Write-Log "New boot order: $($config.BootOrder.Description)" 'SUCCESS'
        exit 0
    } else {
        Write-Log "Boot order change completed but verification returned a warning for VM '${VMName}'" 'WARNING'
        exit 0
    }

} catch {
    Write-Log "Unhandled error during boot order change: $_" 'ERROR'
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

    Write-Log "=== XenServer Boot Order Change Script Completed ===" 'SUCCESS'
}
#endregion
