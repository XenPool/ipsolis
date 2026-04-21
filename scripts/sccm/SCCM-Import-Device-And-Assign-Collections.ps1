param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [Parameter(Mandatory=$true)][string]$MACAddress,
    [Parameter(Mandatory=$true)][string]$SCCMGuiD,
    [string]$AppCollectionIDs = "",
    [int]$ResourceIdRetries = 60,
    [int]$MembershipRetries = 20,
    [int]$MembershipRetryDelaySec = 30
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
    } elseif ($method.ToUpper() -eq 'POST') {
        # IIS rejects bodyless POSTs with HTTP 411 (Length Required) unless a
        # Content-Length header is present. AdminService parameter-less actions
        # (RequestRefresh, etc.) fall into this category.
        $curlArgs += @('-H', 'Content-Length: 0')
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
        # ConfigMgr 2509 AdminService: ImportMachineEntry is an OData
        # ActionImport on the SMS_Site entity set — URL uses a dot, not a
        # slash, and body property names are ALL CAPS (SMBIOSGUID, MACAddress).
        $body = @{
            NetbiosName = $VMName
            SMBIOSGUID  = $guid
            MACAddress  = $mac
            OverwriteExistingRecord = $false
        }
        Invoke-SccmRequest $cfg 'Post' 'wmi/SMS_Site.ImportMachineEntry' $null $body | Out-Null
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

    # Direct membership rules for OS collection + any app collections.
    # ConfigMgr 2509 AdminService: both AddMembershipRule and RequestRefresh
    # are bound OData actions on SMS_Collection instances — URLs use the
    # `wmi/SMS_Collection('<id>')/AdminService.<Action>` form.
    $targets = @($OSCollectionID) + $appCollections
    $membershipStatus = @{}
    foreach ($collId in $targets) {
        # AdminService metadata (wmi/$metadata): AddMembershipRule is a bound
        # action on SMS_Collection taking a single `collectionRule` parameter
        # of abstract type SMS_CollectionRule. The concrete direct-membership
        # subtype is SMS_CollectionRuleDirect, selected via @odata.type.
        $body = @{
            collectionRule = @{
                '@odata.type'     = '#AdminService.SMS_CollectionRuleDirect'
                ResourceClassName = 'SMS_R_System'
                ResourceID        = $resourceId
                RuleName          = "$VMName-$resourceId"
            }
        }
        try {
            Invoke-SccmRequest $cfg 'Post' "wmi/SMS_Collection('$collId')/AdminService.AddMembershipRule" $null $body | Out-Null
            Write-Host "Added ResourceID $resourceId to collection $collId."
        } catch {
            # AdminService returns an error if the rule already exists — log and continue
            Write-Host "AddMembershipRule($collId) non-fatal: $($_.Exception.Message)"
        }
        # Trigger collection refresh. AddMembershipRule only stages the rule;
        # membership is not materialised until the collection is evaluated.
        try {
            Invoke-SccmRequest $cfg 'Post' "wmi/SMS_Collection('$collId')/AdminService.RequestRefresh" $null $null | Out-Null
            Write-Host "RequestRefresh submitted for collection $collId."
        } catch {
            Write-Host "RequestRefresh($collId) non-fatal: $($_.Exception.Message)"
        }

        # Poll SMS_FullCollectionMembership until ResourceID shows up in the
        # collection (or timeout). Collection eval is async — downstream steps
        # (PXE boot, task-sequence deployment) assume the device is a member.
        $isMember = $false
        for ($j = 1; $j -le $MembershipRetries; $j++) {
            try {
                $mResp = Invoke-SccmRequest $cfg 'Get' 'wmi/SMS_FullCollectionMembership' @{
                    '$filter' = "CollectionID eq '$collId' and ResourceID eq $resourceId"
                    '$select' = 'ResourceID,CollectionID,Name'
                }
                $members = @($mResp.value)
                if ($members.Count -ge 1) { $isMember = $true; break }
            } catch {
                Write-Host "  [membership poll $j/$MembershipRetries] query error: $($_.Exception.Message)"
            }
            Write-Host "  [membership poll $j/$MembershipRetries] ResourceID $resourceId not yet in $collId; re-triggering refresh."
            try { Invoke-SccmRequest $cfg 'Post' "wmi/SMS_Collection('$collId')/AdminService.RequestRefresh" $null $null | Out-Null } catch {}
            Start-Sleep -Seconds $MembershipRetryDelaySec
        }
        if ($isMember) {
            Write-Host "Confirmed ResourceID $resourceId is a member of $collId."
            $membershipStatus[$collId] = $true
        } else {
            Write-Host "WARNING: ResourceID $resourceId not confirmed in $collId within $($MembershipRetries * $MembershipRetryDelaySec)s."
            $membershipStatus[$collId] = $false
            if ($collId -eq $OSCollectionID) {
                throw "Timed out waiting for ResourceID $resourceId to appear in OS collection $collId."
            }
        }
    }

    $global:SCCMResourceID     = $resourceId
    $global:SCCMImportStatus   = 'success'
    $global:SCCMAppCollections = $appCollections

    Write-Output (@{
        success           = $true
        resource_id       = $resourceId
        status            = 'success'
        os_collection     = $OSCollectionID
        app_collections   = $appCollections
        membership_status = $membershipStatus
    } | ConvertTo-Json -Compress)
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
finally {
    Clear-Kinit
}
