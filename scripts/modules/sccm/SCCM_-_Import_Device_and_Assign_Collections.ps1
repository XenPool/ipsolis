# NAME: SCCM - Import Device and Assign Collections
# DESC: SCCM - Import Device and Assign Collections — pure PowerShell against the SCCM Admin Service using Kerberos (GSSAPI) via kinit.
param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [Parameter(Mandatory=$true)][string]$MACAddress,
    [Parameter(Mandatory=$true)][string]$SCCMGuiD,
    [string]$AppCollectionIDs = "",
    [int]$ResourceIdRetries = 60
)

# SCCM - Import Device and Assign Collections (pure PS + Kerberos/GSSAPI).
# Replaces Import-CMComputerInformation + Add-CMDeviceCollectionDirectMembershipRule loop.

# --- helpers ---------------------------------------------------------------
function Get-SccmConfig {
    $json = python /app/tasks/utils/db_query.py `
        "SELECT key, value FROM app_config WHERE key LIKE %s" "sccm.%"
    $rows = $json | ConvertFrom-Json
    $cfg = @{}
    foreach ($r in $rows) { $cfg[$r.key.Substring(5)] = $r.value }
    foreach ($k in 'base_url','username','password','realm','kdc') {
        if ([string]::IsNullOrWhiteSpace($cfg[$k])) { throw "app_config key 'sccm.$k' is empty" }
    }
    if (-not $cfg.ContainsKey('verify_tls')) { $cfg['verify_tls'] = 'true' }
    return $cfg
}

function Invoke-Kinit([hashtable]$cfg) {
    $krb5conf = "[libdefaults]`n    default_realm = $($cfg.realm)`n    dns_lookup_kdc = false`n    dns_lookup_realm = false`n[realms]`n    $($cfg.realm) = {`n        kdc = $($cfg.kdc)`n        admin_server = $($cfg.kdc)`n    }`n"
    $krbPath = "/tmp/krb5_xp_$PID.conf"
    [IO.File]::WriteAllText($krbPath, $krb5conf)
    $env:KRB5_CONFIG = $krbPath
    $env:KRB5CCNAME  = "/tmp/krb5cc_xp_$PID"
    # Kerberos principals are user@REALM. Strip any NT-style DOMAIN\ prefix.
    $principal = $cfg.username
    if ($principal -match '\\') { $principal = ($principal -split '\\')[-1] }
    if ($principal -notmatch '@') { $principal = "$principal@$($cfg.realm)" }
    $pwFile = "/tmp/kinit_pw_$PID"
    [IO.File]::WriteAllText($pwFile, $cfg.password)
    try {
        $out = bash -c "kinit -V '$principal' < '$pwFile' 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "kinit failed ($LASTEXITCODE): $out" }
    } finally { Remove-Item $pwFile -Force -ErrorAction SilentlyContinue }
}
function Clear-Kinit { & kdestroy 2>&1 | Out-Null }

function Get-SccmUrl([hashtable]$cfg, [string]$path) {
    $root = $cfg.base_url.TrimEnd('/')
    if (-not $root.ToLower().EndsWith('/adminservice')) { $root += '/AdminService' }
    return "$root/$path"
}

function Invoke-SccmRequest([hashtable]$cfg, [string]$method, [string]$path,
                            [hashtable]$query = $null, $body = $null) {
    $url = Get-SccmUrl $cfg $path
    if ($query) {
        $qs = @()
        foreach ($k in $query.Keys) { $qs += "$k=" + [uri]::EscapeDataString([string]$query[$k]) }
        if ($qs.Count -gt 0) { $url += '?' + ($qs -join '&') }
    }
    # Linux pwsh lacks Negotiate; use curl --negotiate against the Kerberos TGT.
    $curlArgs = @('-sS', '-w', '\nHTTP_STATUS:%{http_code}', '-X', $method.ToUpper(),
                  '--negotiate', '-u', ':', '-H', 'Accept: application/json')
    if ($cfg.verify_tls -eq 'false') { $curlArgs = @('-k') + $curlArgs }

    $bodyFile = $null
    if ($null -ne $body) {
        $json = ($body | ConvertTo-Json -Depth 8)
        $bodyFile = "/tmp/sccm_body_$PID.json"
        [IO.File]::WriteAllText($bodyFile, $json)
        $curlArgs += @('-H', 'Content-Type: application/json', '--data-binary', "@$bodyFile")
    }
    $curlArgs += $url

    try {
        $raw = & /usr/bin/curl @curlArgs 2>&1
        $exit = $LASTEXITCODE
    } finally {
        if ($bodyFile) { Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue }
    }
    $text = if ($raw -is [array]) { $raw -join "`n" } else { [string]$raw }
    $status = ''; $respBody = $text
    if ($text -match '(?s)(.*)\nHTTP_STATUS:(\d+)\s*$') { $respBody = $Matches[1]; $status = $Matches[2] }
    if ($exit -ne 0 -or $status -eq '' -or [int]$status -ge 400) {
        $snip = $respBody.Trim(); if ($snip.Length -gt 500) { $snip = $snip.Substring(0,500) }
        throw "$method $url failed (curl=$exit http=$status): $snip"
    }
    if ([string]::IsNullOrWhiteSpace($respBody)) { return $null }
    try { return $respBody | ConvertFrom-Json } catch { return $respBody }
}

function Convert-MacAddress([string]$mac) {
    if ([string]::IsNullOrWhiteSpace($mac)) { return $mac }
    $m = ($mac.Trim() -replace '[-\.]', ':' -replace '\s', '')
    if ($m -match '^[0-9A-Fa-f]{12}$') {
        $m = ($m.ToUpper() -split '(.{2})' | Where-Object { $_ }) -join ':'
    }
    return $m.ToUpper()
}

# --- main -------------------------------------------------------------------
try {
    $mac  = Convert-MacAddress $MACAddress
    $guid = $SCCMGuiD.Trim().ToUpper()

    $appCollections = @()
    if (-not [string]::IsNullOrWhiteSpace($AppCollectionIDs)) {
        $appCollections = ($AppCollectionIDs -split '[;,]' |
            ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }

    $cfg = Get-SccmConfig
    Invoke-Kinit $cfg

    # Uniqueness check
    $safeName = $VMName -replace "'", "''"
    $existing = Invoke-SccmRequest $cfg 'Get' 'wmi/SMS_R_System' @{
        '$filter' = "Name eq '$safeName'"; '$select' = 'ResourceID,Name'
    }
    $devices = @($existing.value)
    if ($devices.Count -gt 1) {
        throw "Multiple SCCM devices already exist for '$VMName' (count=$($devices.Count))."
    }

    $resourceId = $null
    if ($devices.Count -eq 1) {
        $resourceId = [int]$devices[0].ResourceID
        Write-Host "Device '$VMName' already exists (ResourceID=$resourceId)."
    } else {
        $body = @{
            NetbiosName = $VMName
            SMBiosGuid  = $guid
            MacAddress  = $mac
            OverwriteExistingRecord = $false
        }
        Invoke-SccmRequest $cfg 'Post' 'SMS_Site/ImportMachineEntry' $null $body | Out-Null
        Write-Host "Import submitted for '$VMName'. Polling for ResourceID..."

        for ($i = 1; $i -le $ResourceIdRetries; $i++) {
            Start-Sleep -Seconds 60
            $r = Invoke-SccmRequest $cfg 'Get' 'wmi/SMS_R_System' @{
                '$filter' = "Name eq '$safeName'"; '$select' = 'ResourceID,Name'
            }
            $d = @($r.value)
            if ($d.Count -gt 1) { throw "Multiple SCCM devices appeared during polling for '$VMName'." }
            if ($d.Count -eq 1) { $resourceId = [int]$d[0].ResourceID; break }
            Write-Host "  [poll $i/$ResourceIdRetries] ResourceID not yet available."
        }
        if (-not $resourceId) { throw "Timed out waiting for ResourceID for '$VMName'." }
    }

    # Direct membership rules for OS collection + any app collections
    $targets = @($OSCollectionID) + $appCollections
    foreach ($collId in $targets) {
        $body = @{
            CollectionID  = $collId
            ResourceID    = $resourceId
            RuleName      = "$VMName-$resourceId"
        }
        try {
            Invoke-SccmRequest $cfg 'Post' 'SMS_Collection/AddMembershipRule' $null $body | Out-Null
            Write-Host "Added ResourceID $resourceId to collection $collId."
        } catch {
            # AdminService returns an error if the rule already exists — log and continue
            Write-Host "AddMembershipRule($collId) non-fatal: $($_.Exception.Message)"
        }
        # Trigger collection refresh
        try {
            Invoke-SccmRequest $cfg 'Post' "SMS_Collection('$collId')/AdminService.RequestRefresh" $null $null | Out-Null
        } catch {
            Write-Host "RequestRefresh($collId) non-fatal: $($_.Exception.Message)"
        }
    }

    $global:SCCMResourceID     = $resourceId
    $global:SCCMImportStatus   = 'success'
    $global:SCCMAppCollections = $appCollections

    Write-Output (@{
        success         = $true
        resource_id     = $resourceId
        status          = 'success'
        os_collection   = $OSCollectionID
        app_collections = $appCollections
    } | ConvertTo-Json -Compress)
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
finally {
    Clear-Kinit
}
