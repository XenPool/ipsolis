param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [int]$TimeoutMinutes = 360,
    [int]$PollSeconds = 60
)

# SCCM - Wait for Task Sequence (pure PS + Kerberos/GSSAPI).
# Polls SMS_DPMDeploymentAssetDetails (per-device StatusType) for the task sequence
# deployed to the given OS collection until it completes, fails, or times out.
# StatusType mapping: 1=Success, 2=InProgress, 3=Error.

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
function Invoke-SccmGet([hashtable]$cfg, [string]$path, [hashtable]$query) {
    $url = Get-SccmUrl $cfg $path
    if ($query) {
        $qs = @()
        foreach ($k in $query.Keys) { $qs += "$k=" + [uri]::EscapeDataString([string]$query[$k]) }
        if ($qs.Count -gt 0) { $url += '?' + ($qs -join '&') }
    }
    # Linux pwsh lacks Negotiate; use curl --negotiate against the Kerberos TGT.
    $curlArgs = @('-sS', '-w', '\nHTTP_STATUS:%{http_code}', '--negotiate', '-u', ':',
                  '-H', 'Accept: application/json', $url)
    if ($cfg.verify_tls -eq 'false') { $curlArgs = @('-k') + $curlArgs }
    $raw = & /usr/bin/curl @curlArgs 2>&1
    $exit = $LASTEXITCODE
    $text = if ($raw -is [array]) { $raw -join "`n" } else { [string]$raw }
    $status = ''; $body = $text
    if ($text -match '(?s)(.*)\nHTTP_STATUS:(\d+)\s*$') { $body = $Matches[1]; $status = $Matches[2] }
    if ($exit -ne 0 -or $status -eq '' -or [int]$status -ge 400) {
        $snip = $body.Trim(); if ($snip.Length -gt 500) { $snip = $snip.Substring(0,500) }
        throw "GET $url failed (curl=$exit http=$status): $snip"
    }
    if ([string]::IsNullOrWhiteSpace($body)) { return $null }
    try { return $body | ConvertFrom-Json } catch { return $body }
}

# StatusType -> (result, label) mapping for log-compatible output
$STATUS_LABEL = @{
    '1' = @{ result = 'Available';         label = 'The task sequence manager successfully completed execution of the task sequence' }
    '2' = @{ result = 'InProgress';        label = 'Task sequence is running'  }
    '3' = @{ result = 'TaskSeqRunError';   label = 'Task sequence failed'       }
}

# --- main -------------------------------------------------------------------
try {
    $cfg = Get-SccmConfig
    Invoke-Kinit $cfg

    # Find the deployment for the OS collection
    $dep = Invoke-SccmGet $cfg 'wmi/SMS_Deployment' @{
        '$filter' = "CollectionID eq '$OSCollectionID'"
        '$select' = 'DeploymentID,CollectionID,CollectionName'
        '$top'    = '1'
    }
    $depRow = @($dep.value)[0]
    if (-not $depRow) { throw "No deployment found for collection '$OSCollectionID'." }
    $deploymentId = $depRow.DeploymentID
    Write-Host "Tracking DeploymentID=$deploymentId on '$($depRow.CollectionName)'."

    $safeName = $VMName -replace "'", "''"
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $loop = 0
    $lastStatusType = $null
    $lastLabel = $null

    while ((Get-Date) -lt $deadline) {
        $loop++
        try {
            $resp = Invoke-SccmGet $cfg 'wmi/SMS_DPMDeploymentAssetDetails' @{
                '$filter' = "DeploymentID eq '$deploymentId' and DeviceName eq '$safeName'"
                '$select' = 'StatusType,StatusDescription,DeviceName'
                '$top'    = '1'
            }
            $asset = @($resp.value)[0]
        } catch {
            $asset = $null
            Write-Host "[poll $loop] asset query failed: $($_.Exception.Message)"
        }

        if ($asset) {
            $lastStatusType = [string]$asset.StatusType
            $lastLabel = $asset.StatusDescription
            Write-Host ("[poll {0}] StatusType={1} desc='{2}'" -f $loop, $lastStatusType, $lastLabel)

            if ($lastStatusType -eq '1' -or $lastStatusType -eq '3') { break }
        } else {
            Write-Host "[poll $loop] no asset status yet."
        }
        Start-Sleep -Seconds $PollSeconds
    }

    $mapping = $STATUS_LABEL[$lastStatusType]
    if (-not $mapping) { $mapping = @{ result = 'TaskSeqStartError'; label = ($lastLabel -replace '\s+',' ') } }

    $global:SCCMLastStatus     = if ($lastLabel) { $lastLabel } else { $mapping.label }
    $global:TaskSequenceResult = $mapping.result
    $global:DeploymentID       = $deploymentId

    $ok = ($mapping.result -eq 'Available')
    Write-Output (@{
        success             = $ok
        result              = $mapping.result
        status_type         = $lastStatusType
        status_description  = $global:SCCMLastStatus
        deployment_id       = $deploymentId
        polls               = $loop
    } | ConvertTo-Json -Compress)
    if (-not $ok) { exit 1 }
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
finally {
    Clear-Kinit
}
