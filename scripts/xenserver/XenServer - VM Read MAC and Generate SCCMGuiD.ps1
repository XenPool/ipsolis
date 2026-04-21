# XenServer - VM Read MAC and Generate SCCMGuiD
# Reads the primary MAC address of the given VM, stores it in
# asset_pool.metadata.mac_address, and derives the SCCM/MDT GUID
# from the VM's UUID. Exports $global:MACAddress and $global:SCCMGuiD
# for consumption by subsequent runbook steps.

param(
    [Parameter(Mandatory=$true)][string]$VMName
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet('INFO','WARNING','ERROR','SUCCESS')]
        [string]$Level = 'INFO'
    )
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] [$Level] $Message"
}

function Convert-ToSCCMGuid {
    <#
    .SYNOPSIS
    Transposes a hypervisor UUID into the SCCM/MDT GUID format.
    Sections 1-3 (bytes 0-7) are reversed as little-endian; sections 4-5
    (bytes 8-15) remain unchanged.
    #>
    param([Parameter(Mandatory=$true)][string]$RawUuid)

    $hex = ($RawUuid -replace '-', '').ToLower()
    if ($hex.Length -ne 32) {
        throw "Expected 32 hex chars in UUID; got '$hex' (length $($hex.Length))"
    }
    $s1 = $hex.Substring(6,2) + $hex.Substring(4,2) + $hex.Substring(2,2) + $hex.Substring(0,2)
    $s2 = $hex.Substring(10,2) + $hex.Substring(8,2)
    $s3 = $hex.Substring(14,2) + $hex.Substring(12,2)
    $s4 = $hex.Substring(16,4)
    $s5 = $hex.Substring(20,12)
    return "$s1-$s2-$s3-$s4-$s5"
}

try {
    Import-Module XenServerPSModule -ErrorAction Stop
    Write-Log "XenServerPSModule loaded" 'SUCCESS'

    Write-Log "Connecting to XenServer: $($VARS.'xenserver.host')..." 'INFO'
    Connect-XenServer -Server   $VARS.'xenserver.host' `
                      -UserName $VARS.'xenserver.username' `
                      -Password $VARS.'xenserver.password' `
                      -NoWarnCertificates `
                      -SetDefaultSession `
                      -ErrorAction Stop | Out-Null
    Write-Log "Connected to XenServer" 'SUCCESS'

    $vms = Get-XenVM -Name $VMName -ErrorAction Stop |
           Where-Object { -not $_.is_a_template -and -not $_.is_control_domain }
    if (-not $vms) { throw "VM '$VMName' not found" }
    $vm = $vms | Select-Object -First 1
    Write-Log "VM found: $($vm.name_label) (UUID: $($vm.uuid))" 'SUCCESS'

    # Read the MAC of the first VIF (device 0 if available, otherwise first)
    $mac = $null
    $vifs = @()
    foreach ($ref in $vm.VIFs) {
        $vif = Get-XenVIF -Ref $ref -ErrorAction SilentlyContinue
        if ($vif -and $vif.MAC) { $vifs += $vif }
    }
    if (-not $vifs) { throw "No VIF with a MAC address found on VM '$VMName'" }

    $primary = $vifs | Sort-Object { [int]$_.device } | Select-Object -First 1
    $mac = ($primary.MAC).ToLower()
    Write-Log "Primary MAC (device $($primary.device)): $mac" 'INFO'

    # Local variable is named $guid (not $sccmGuid) on purpose: PS variable
    # names are case-insensitive, and top-level script assignments under
    # `pwsh -File` land in global scope. Using $sccmGuid would silently
    # claim the canonical global name 'sccmGuid', preventing the later
    # `$global:SCCMGuiD = ...` from creating a distinctly-named global,
    # which breaks `{{SCCMGuiD}}` template substitution in downstream steps.
    $guid = Convert-ToSCCMGuid -RawUuid $vm.uuid
    Write-Log "SCCM GUID: $guid" 'INFO'

    # Persist MAC on the asset_pool row. The metadata column is JSON (not JSONB),
    # so we cast to jsonb, apply jsonb_set, then cast back to json.
    $sql = "UPDATE asset_pool SET metadata = jsonb_set(COALESCE(metadata::jsonb, '{}'::jsonb), '{mac_address}', to_jsonb(%s::text))::json, updated_at = NOW() WHERE name = %s"
    $raw = python /app/tasks/utils/db_execute.py $sql $mac $VMName 2>&1
    $text = if ($raw -is [array]) { $raw -join "`n" } else { [string]$raw }
    try { $dbRes = $text | ConvertFrom-Json } catch {
        throw "db_execute.py returned non-JSON: $text"
    }
    if (-not $dbRes.success) { throw "DB update failed: $($dbRes.error)" }
    if ([int]$dbRes.rowcount -lt 1) {
        Write-Log "No asset_pool row matched name '$VMName' (MAC still exported to globals)" 'WARNING'
    } else {
        Write-Log "asset_pool.mac_address stored for '$VMName' ($($dbRes.rowcount) row)" 'SUCCESS'
    }

    $global:MACAddress = $mac
    $global:SCCMGuiD   = $guid

    Write-Output (@{
        success     = $true
        vm_name     = $VMName
        vm_uuid     = $vm.uuid
        mac_address = $mac
        sccm_guid   = $guid
        rows_updated = [int]$dbRes.rowcount
    } | ConvertTo-Json -Compress)
    # No `exit 0` – the runner appends an epilogue that exports $global:* vars
    # for subsequent steps. `exit` inside try kills the runspace before the
    # epilogue runs, which breaks MACAddress/SCCMGuiD forwarding.
}
catch {
    $msg = $_.Exception.Message
    Write-Log "Failed: $msg" 'ERROR'
    Write-Output (@{ success = $false; error = $msg } | ConvertTo-Json -Compress)
    exit 1
}
finally {
    try { Disconnect-XenServer -ErrorAction SilentlyContinue } catch {}
}
