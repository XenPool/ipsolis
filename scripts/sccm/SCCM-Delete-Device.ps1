param(
    [Parameter(Mandatory=$true)][string]$VMName
)

# SCCM - Delete Device (pure PowerShell + Kerberos/GSSAPI).
# Requires: krb5-user / libgssapi-krb5-2 in the worker image (baked via Dockerfile).
# Reads sccm.* config from app_config (via db_query.py) — no secrets in-script.

# --- helpers ---------------------------------------------------------------
function Get-SccmConfig {
    $json = python /app/tasks/utils/db_query.py `
        "SELECT key, value FROM app_config WHERE key LIKE %s" "sccm.%"
    $rows = $json | ConvertFrom-Json
    $cfg = @{}
    foreach ($r in $rows) {
        $short = $r.key.Substring(5)   # strip 'sccm.'
        $cfg[$short] = $r.value
    }
    foreach ($k in 'base_url','username','password','realm','kdc') {
        if ([string]::IsNullOrWhiteSpace($cfg[$k])) { throw "app_config key 'sccm.$k' is empty" }
    }
    if (-not $cfg.ContainsKey('verify_tls')) { $cfg['verify_tls'] = 'true' }
    return $cfg
}

function Invoke-Kinit([hashtable]$cfg) {
    $krb5conf = "[libdefaults]`n" +
                "    default_realm = $($cfg.realm)`n" +
                "    dns_lookup_kdc = false`n" +
                "    dns_lookup_realm = false`n" +
                "[realms]`n" +
                "    $($cfg.realm) = {`n" +
                "        kdc = $($cfg.kdc)`n" +
                "        admin_server = $($cfg.kdc)`n" +
                "    }`n"
    $krbPath = "/tmp/krb5_xp_$PID.conf"
    [IO.File]::WriteAllText($krbPath, $krb5conf)
    $env:KRB5_CONFIG = $krbPath
    $env:KRB5CCNAME  = "/tmp/krb5cc_xp_$PID"

    # Kerberos principals are user@REALM. Strip any NT-style DOMAIN\ prefix
    # (e.g. 'XENPOOL\Administrator' -> 'Administrator').
    $principal = $cfg.username
    if ($principal -match '\\') { $principal = ($principal -split '\\')[-1] }
    if ($principal -notmatch '@') { $principal = "$principal@$($cfg.realm)" }

    $pwFile = "/tmp/kinit_pw_$PID"
    [IO.File]::WriteAllText($pwFile, $cfg.password)
    try {
        $out = bash -c "kinit -V '$principal' < '$pwFile' 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "kinit failed ($LASTEXITCODE): $out" }
    } finally {
        Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
    }
}

function Clear-Kinit { & kdestroy 2>&1 | Out-Null }

function Get-SccmUrl([hashtable]$cfg, [string]$path) {
    $root = $cfg.base_url.TrimEnd('/')
    if (-not $root.ToLower().EndsWith('/adminservice')) { $root += '/AdminService' }
    return "$root/$path"
}

function Invoke-SccmRequest([hashtable]$cfg, [string]$method, [string]$path, [hashtable]$query) {
    $url = Get-SccmUrl $cfg $path
    if ($query) {
        $qs = @()
        foreach ($k in $query.Keys) { $qs += "$k=" + [uri]::EscapeDataString([string]$query[$k]) }
        if ($qs.Count -gt 0) { $url += '?' + ($qs -join '&') }
    }
    # Linux pwsh lacks Negotiate auth; use curl --negotiate against the Kerberos TGT.
    $curlArgs = @('-sS', '-w', '\nHTTP_STATUS:%{http_code}', '-X', $method.ToUpper(),
                  '--negotiate', '-u', ':', '-H', 'Accept: application/json', $url)
    if ($cfg.verify_tls -eq 'false') { $curlArgs = @('-k') + $curlArgs }

    $raw = & /usr/bin/curl @curlArgs 2>&1
    $exit = $LASTEXITCODE
    $text = if ($raw -is [array]) { $raw -join "`n" } else { [string]$raw }
    $status = ''
    $body = $text
    if ($text -match '(?s)(.*)\nHTTP_STATUS:(\d+)\s*$') { $body = $Matches[1]; $status = $Matches[2] }
    if ($exit -ne 0 -or $status -eq '' -or [int]$status -ge 400) {
        $snip = $body.Trim(); if ($snip.Length -gt 500) { $snip = $snip.Substring(0,500) }
        throw "$method $url failed (curl=$exit http=$status): $snip"
    }
    if ([string]::IsNullOrWhiteSpace($body)) { return $null }
    try { return $body | ConvertFrom-Json } catch { return $body }
}

# --- main -------------------------------------------------------------------
try {
    if ([string]::IsNullOrWhiteSpace($VMName)) {
        Write-Output (@{ success = $false; error = 'VMName is empty' } | ConvertTo-Json -Compress)
        exit 1
    }

    $cfg = Get-SccmConfig
    Invoke-Kinit $cfg

    $safeName = $VMName -replace "'", "''"
    $resp = Invoke-SccmRequest $cfg 'Get' 'wmi/SMS_R_System' @{
        '$filter' = "Name eq '$safeName'"
        '$select' = 'ResourceID,Name'
    }
    $devices = @($resp.value)

    if ($devices.Count -eq 0) {
        $global:SCCMDeleteCount = 0
        Write-Output (@{ success = $true; deleted = 0; message = "No device named '$VMName' in SCCM." } | ConvertTo-Json -Compress)
        exit 0
    }
    if ($devices.Count -gt 1) {
        Write-Output (@{ success = $false; error = "Multiple devices match '$VMName'"; count = $devices.Count } | ConvertTo-Json -Compress)
        exit 1
    }

    $rid = $devices[0].ResourceID
    Invoke-SccmRequest $cfg 'Delete' "wmi/SMS_R_System($rid)" $null | Out-Null

    $global:SCCMDeleteResourceID = $rid
    $global:SCCMDeleteCount      = 1
    Write-Output (@{ success = $true; deleted = 1; resource_id = $rid } | ConvertTo-Json -Compress)
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
finally {
    Clear-Kinit
}
