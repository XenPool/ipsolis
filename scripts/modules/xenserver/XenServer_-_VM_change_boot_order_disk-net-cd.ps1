# NAME: XenServer - VM change boot order (disk-net-cd)
# DESC: Sets the XenServer HVM boot order to Hard Disk -> Network -> CD-ROM (cnd). Used at the end of a recycle runbook so the rebuilt VM boots from disk next time while still keeping PXE as a fallback.
# XenServer - VM change boot order (disk-net-cd)
# Changes the boot order of a HVM VM to: Hard Disk -> Network -> CD-ROM
# Used at the end of a VDI recycle flow so a fresh boot comes from the OS
# disk, but PXE is still available as a fallback if the disk is blanked.
#
# XenServer HVM boot order codes:
#   c = Hard Disk   d = CD-ROM / DVD   n = Network (PXE)
# This script sets order = "cnd" (Disk -> Network -> CD-ROM)

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
Write-Log "=== Starting XenServer Boot Order Change Script (Disk-Net-CD) ===" 'INFO'

$ErrorActionPreference = "Stop"

$config = @{
    XenServer = @{
        ServerHost = $VARS.'xenserver.host'
        AdminUser  = $VARS.'xenserver.username'
        AdminPW    = $VARS.'xenserver.password'
    }

    BootOrder = @{
        Order       = 'cnd'
        Description = 'Hard Disk -> Network -> CD-ROM'
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
    param (
        [Parameter(Mandatory = $true)]
        $VM,
        [Parameter(Mandatory = $true)]
        [string]$Order
    )
    try {
        Write-Log "Applying boot order '$Order' to VM: $($VM.name_label)..." 'INFO'
        # Clone existing hvm_boot_params so we preserve other keys (e.g. firmware).
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
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

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

    if ([string]::IsNullOrEmpty($vm.HVM_boot_policy)) {
        Write-Log "VM '${VMName}' is a PV VM  - HVM boot order is not applicable" 'WARNING'
        Write-Output (@{ success = $true; skipped = $true; reason = 'PV VM' } | ConvertTo-Json -Compress)
        exit 0
    }

    if ($vm.power_state.ToString() -eq 'Running') {
        Write-Log "VM '${VMName}' is currently running  - boot order change takes effect on next reboot" 'WARNING'
    } else {
        Write-Log "VM is $($vm.power_state)  - boot order change takes effect on next startup" 'INFO'
    }

    $currentOrder = Get-XenVMBootOrder -VM $vm
    $applySuccess = Set-XenVMBootOrder -VM $vm -Order $config.BootOrder.Order

    if (-not $applySuccess) {
        Write-Log "Failed to apply boot order to VM '${VMName}'" 'ERROR'
        Write-Output (@{ success = $false; error = "Set-XenVM failed" } | ConvertTo-Json -Compress)
        exit 1
    }

    Write-Log "Verifying boot order change..." 'INFO'
    $verifySuccess = Test-XenVMBootOrder -VMName $VMName -ExpectedOrder $config.BootOrder.Order

    if ($verifySuccess) {
        Write-Log "Boot order successfully changed for VM '${VMName}'" 'SUCCESS'
        Write-Log "New boot order: $($config.BootOrder.Description)" 'SUCCESS'
        Write-Output (@{
            success       = $true
            vm_name       = $VMName
            previous_order = [string]$currentOrder
            new_order     = $config.BootOrder.Order
            description   = $config.BootOrder.Description
        } | ConvertTo-Json -Compress)
        exit 0
    } else {
        Write-Log "Boot order change completed but verification returned a warning for VM '${VMName}'" 'WARNING'
        Write-Output (@{
            success = $false
            error   = "Verification mismatch after Set-XenVM"
            expected = $config.BootOrder.Order
        } | ConvertTo-Json -Compress)
        exit 1
    }

} catch {
    Write-Log "Unhandled error during boot order change: $_" 'ERROR'
    Write-Log "Stack trace: $($_.ScriptStackTrace)" 'ERROR'
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
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
