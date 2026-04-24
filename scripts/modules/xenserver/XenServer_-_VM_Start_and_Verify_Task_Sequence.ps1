# NAME: XenServer - VM Start and Verify Task Sequence
# DESC: Starts a XenServer VM so PXE fires, then verifies the SCCM OSD task sequence actually began via status messages.
param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [int]$StartupTimeoutSeconds   = 120,
    [int]$TaskSequenceWaitSeconds = 900,
    [int]$TaskSequenceCheckSec    = 30
)

# XenServer - VM Start and Verify Task Sequence
#   1. Starts the halted VM (or confirms it is already Running) so PXE can fire.
#   2. Polls SCCM AdminService for status messages from the device that prove
#      the OSD task sequence has actually started (Task Sequence Engine
#      messages). If nothing shows up within the timeout window, the step
#      throws so the dynamic runner aborts the remaining critical steps.

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [ValidateSet('INFO','WARNING','ERROR','SUCCESS')][string]$Level='INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] [$Level] $Message"
}

# --- SCCM helpers (pure PS + Kerberos/GSSAPI) ------------------------------
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

function Invoke-SccmRequest([hashtable]$cfg, [string]$method, [string]$path, [hashtable]$query = $null) {
    $url = Get-SccmUrl $cfg $path
    if ($query) {
        $qs = @()
        foreach ($k in $query.Keys) { $qs += "$k=" + [uri]::EscapeDataString([string]$query[$k]) }
        if ($qs.Count -gt 0) { $url += '?' + ($qs -join '&') }
    }
    $curlArgs = @('-sS', '-w', '\nHTTP_STATUS:%{http_code}', '-X', $method.ToUpper(),
                  '--negotiate', '-u', ':', '-H', 'Accept: application/json')
    if ($cfg.verify_tls -eq 'false') { $curlArgs = @('-k') + $curlArgs }
    $curlArgs += $url
    $raw = & /usr/bin/curl @curlArgs 2>&1
    $exit = $LASTEXITCODE
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

# --- XenServer helpers ------------------------------------------------------
function Get-XenVMFresh([string]$n) {
    $vms = Get-XenVM -Name $n -ErrorAction Stop |
           Where-Object { -not $_.is_a_template -and -not $_.is_control_domain }
    if (-not $vms) { throw "VM '$n' not found on XenServer" }
    return $vms | Select-Object -First 1
}

function Wait-XenVMRunning([string]$n, [int]$timeout, [int]$interval = 3) {
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        $vm = Get-XenVMFresh $n
        $state = $vm.power_state.ToString()
        Write-Log "VM '$n' power state: $state ($elapsed/$timeout s)"
        if ($state -eq 'Running') { return $true }
        Start-Sleep -Seconds $interval
        $elapsed += $interval
    }
    return $false
}

# --- main -------------------------------------------------------------------
$xenConnected = $false
try {
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

    Import-Module XenServerPSModule -ErrorAction Stop
    Write-Log "XenServerPSModule loaded" 'SUCCESS'

    Connect-XenServer -Server   $VARS.'xenserver.host' `
                      -UserName $VARS.'xenserver.username' `
                      -Password $VARS.'xenserver.password' `
                      -NoWarnCertificates `
                      -SetDefaultSession `
                      -ErrorAction Stop | Out-Null
    $xenConnected = $true
    Write-Log "Connected to XenServer" 'SUCCESS'

    # Record wall-clock power-on reference BEFORE issuing Start so we can
    # filter out any stale TS messages from previous lifecycles on the MP.
    # Pad 30s backwards to tolerate small client/server clock skew.
    $poweredOnAt = (Get-Date).ToUniversalTime().AddSeconds(-30)
    $poweredOnIso = $poweredOnAt.ToString("yyyy-MM-ddTHH:mm:ssZ")

    $vm = Get-XenVMFresh $VMName
    $state = $vm.power_state.ToString()
    Write-Log "Initial power state of '$VMName': $state" 'INFO'

    if ($state -eq 'Running') {
        Write-Log "VM is already Running - skipping Start action." 'WARNING'
    } else {
        Invoke-XenVM -VM $vm -XenAction Start -ErrorAction Stop | Out-Null
        Write-Log "Start command issued; waiting up to $StartupTimeoutSeconds s for Running state." 'INFO'
        $ok = Wait-XenVMRunning $VMName $StartupTimeoutSeconds 3
        if (-not $ok) { throw "VM '$VMName' did not reach Running state within $StartupTimeoutSeconds s." }
        Write-Log "VM '$VMName' is Running." 'SUCCESS'
    }

    # --- Verify task sequence is actually executing -------------------------
    $cfg = Get-SccmConfig
    Invoke-Kinit $cfg

    # Component 'Task Sequence Engine' is emitted by smsts.exe inside WinPE
    # once the OSD TS has started. If we see any such message newer than our
    # power-on reference, the sequence is running. MessageID-based filtering
    # is intentionally broad so ID-specific locale/version drift doesn't
    # hide real progress.
    $safeName = $VMName -replace "'", "''"
    $filter = "MachineName eq '$safeName' and Component eq 'Task Sequence Engine' and Time gt $poweredOnIso"

    Write-Log "Polling SCCM status messages (timeout $TaskSequenceWaitSeconds s, interval $TaskSequenceCheckSec s)..." 'INFO'
    $elapsed = 0
    $tsStarted = $false
    $lastMsg = $null
    while ($elapsed -lt $TaskSequenceWaitSeconds) {
        try {
            $resp = Invoke-SccmRequest $cfg 'Get' 'wmi/SMS_StatMsgWithInsStrings' @{
                '$filter'  = $filter
                '$select'  = 'RecordID,MachineName,Component,MessageID,Time'
                '$orderby' = 'Time desc'
                '$top'     = '1'
            }
            $msgs = @($resp.value)
            if ($msgs.Count -ge 1) {
                $lastMsg = $msgs[0]
                $tsStarted = $true
                Write-Log "Task Sequence message observed: MessageID=$($lastMsg.MessageID) at $($lastMsg.Time)" 'SUCCESS'
                break
            }
        } catch {
            Write-Log "Status message query error: $($_.Exception.Message)" 'WARNING'
        }
        Write-Log "  [ts poll $elapsed/$TaskSequenceWaitSeconds s] no TS engine messages yet for '$VMName'." 'INFO'
        Start-Sleep -Seconds $TaskSequenceCheckSec
        $elapsed += $TaskSequenceCheckSec
    }

    if (-not $tsStarted) {
        throw "Task sequence did not start for '$VMName' within $TaskSequenceWaitSeconds seconds (no 'Task Sequence Engine' status messages observed)."
    }

    $global:VMStartStatus    = 'started'
    $global:TaskSequenceRunning = $true
    $global:TaskSequenceFirstMessageID = [string]$lastMsg.MessageID
    $global:TaskSequenceFirstMessageTime = [string]$lastMsg.Time

    Write-Output (@{
        success              = $true
        vm_name              = $VMName
        vm_started           = ($state -ne 'Running')
        task_sequence_running = $true
        first_ts_message_id  = [string]$lastMsg.MessageID
        first_ts_message_time = [string]$lastMsg.Time
    } | ConvertTo-Json -Compress)
}
catch {
    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)
    exit 1
}
finally {
    Clear-Kinit
    if ($xenConnected) {
        try { Disconnect-XenServer -ErrorAction SilentlyContinue } catch {}
    }
}
