<#
.SYNOPSIS
    ARS - Configure VM Local Permissions via SPML v2 / DSML v2.

.DESCRIPTION
    Synchronises Admin and RDP AD group memberships for a VDI using One Identity
    Active Roles 8.2 via SOAP SPML v2 Core Protocol + DSML v2 Profile.
    Replaces the legacy Ivanti/QAD-module-based version which required the
    One Identity PowerShell snap-in (not available on Linux Docker workers).

    All Active Roles operations are performed as raw SOAP/HTTP requests with
    Basic Authentication. The script runs on the Linux Celery worker and calls
    the AR Web Service on the Windows server directly.

.PARAMS (injected via $PARAMS hashtable by the XenPool dynamic runner)
    VMName       - Name of the VM (derives group names via pattern)
    VMDomain     - Domain key: 'OLDAD' or 'NEWAD'
    LocalAdmins  - Semicolon/comma-separated identities for Admin group
    RDPUserIDs   - Semicolon/comma-separated identities for RDP group
    OrderID      - XenPool order ID (numeric string)
    Snow_REQ     - ServiceNow request number (for AR operation reason)
    Snow_RITM    - ServiceNow RITM number (for AR operation reason)

.VARS (from global_vars DB, injected via $VARS hashtable)
    AR_OLD_SPML_ENDPOINT       - SPML endpoint URL for old/legacy domain AR server
    AR_OLD_SVC_USER            - Service account UPN for old domain
    AR_OLD_SVC_PW              - Service account password (secret)
    AR_NEW_SPML_ENDPOINT       - SPML endpoint URL for new domain AR server
    AR_NEW_SVC_USER            - Service account UPN for new domain
    AR_NEW_SVC_PW              - Service account password (secret)
    AR_OLD_DOMAIN_DN           - Base DN for old domain (e.g. DC=oldad,DC=corp,DC=com)
    AR_OLD_DOMAIN_FQDN         - FQDN for old domain (e.g. oldad.corp.com)
    AR_NEW_DOMAIN_DN           - Base DN for new domain
    AR_NEW_DOMAIN_FQDN         - FQDN for new domain
    AR_OLD_ADMIN_GROUP_PATTERN - Admin group pattern for old domain ({0} = VMName)
    AR_OLD_RDP_GROUP_PATTERN   - RDP group pattern for old domain ({0} = VMName)
    AR_NEW_ADMIN_GROUP_PATTERN - Admin group pattern for new domain ({0} = VMName)
    AR_NEW_RDP_GROUP_PATTERN   - Main RDP group pattern for new domain ({0} = VMName)
    AR_COMPANY_PREFIXES        - Comma-separated company prefixes (new domain only)
    AR_COMPANY_RDP_PATTERN     - Per-company RDP group pattern ({0}=company, {1}=VMName)
    AR_OPERATION_REASON_PREFIX - Prefix for AR operation reason (audit trail)
#>

Set-StrictMode -Off
$ErrorActionPreference = 'Stop'

# ── Parameters ─────────────────────────────────────────────────────────────────
$VMName          = $PARAMS['VMName']
$VMDomain        = $PARAMS['VMDomain']        # 'OLDAD' or 'NEWAD'
$OrderID         = $PARAMS['OrderID']
$Snow_REQ        = $PARAMS['Snow_REQ']
$Snow_RITM       = $PARAMS['Snow_RITM']
$AdmIdentityList = $PARAMS['LocalAdmins']
$RdpIdentityList = $PARAMS['RDPUserIDs']

# ── Domain config map ──────────────────────────────────────────────────────────
$DomainMap = @{
    OLDAD = @{
        SpmlUrl         = $VARS['AR_OLD_SPML_ENDPOINT']
        ServiceUser     = $VARS['AR_OLD_SVC_USER']
        ServicePassword = $VARS['AR_OLD_SVC_PW']
        DomainDN        = $VARS['AR_OLD_DOMAIN_DN']
        DomainFQDN      = $VARS['AR_OLD_DOMAIN_FQDN']
        AdminGroupPat   = $VARS['AR_OLD_ADMIN_GROUP_PATTERN']
        RdpGroupPat     = $VARS['AR_OLD_RDP_GROUP_PATTERN']
        CompanyPrefixes = @()
        CompanyRdpPat   = $null
    }
    NEWAD = @{
        SpmlUrl         = $VARS['AR_NEW_SPML_ENDPOINT']
        ServiceUser     = $VARS['AR_NEW_SVC_USER']
        ServicePassword = $VARS['AR_NEW_SVC_PW']
        DomainDN        = $VARS['AR_NEW_DOMAIN_DN']
        DomainFQDN      = $VARS['AR_NEW_DOMAIN_FQDN']
        AdminGroupPat   = $VARS['AR_NEW_ADMIN_GROUP_PATTERN']
        RdpGroupPat     = $VARS['AR_NEW_RDP_GROUP_PATTERN']
        CompanyPrefixes = @(($VARS['AR_COMPANY_PREFIXES'] -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        CompanyRdpPat   = $VARS['AR_COMPANY_RDP_PATTERN']
    }
}

$Log      = [System.Collections.Generic.List[string]]::new()
$Warnings = [System.Collections.Generic.List[string]]::new()

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $msg = "[$ts] [$Level] $Message"
    $Log.Add($msg)
    $color = switch ($Level) { 'SUCCESS'{'Green'} 'WARNING'{'Yellow'} 'ERROR'{'Red'} default{'White'} }
    Write-Host $msg -ForegroundColor $color
}

# ── SOAP / SPML helpers ────────────────────────────────────────────────────────

function Get-BasicAuthHeader {
    param([string]$User, [string]$Password)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("${User}:${Password}")
    return "Basic " + [Convert]::ToBase64String($bytes)
}

function Invoke-SpmlRequest {
    <#
    .SYNOPSIS Sends a SOAP SPML v2 request to the Active Roles Web Service endpoint.
    .RETURNS  [xml] parsed response document, or $null on hard failure.
    #>
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$SoapBody,          # Inner content of <soap:Body> (the SPML request element)
        [string]$SoapAction = 'urn:oasis:names:tc:SPML:2:0#request'
    )
    $envelope = @"
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope
    xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:spml="urn:oasis:names:tc:SPML:2:0"
    xmlns:dsml="urn:oasis:names:tc:DSML:2:0:core"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <soap:Header/>
  <soap:Body>
    $SoapBody
  </soap:Body>
</soap:Envelope>
"@
    try {
        $resp = Invoke-WebRequest `
            -Uri $SpmlUrl `
            -Method POST `
            -Body $envelope `
            -Headers @{
                'Authorization' = $AuthHeader
                'Content-Type'  = 'text/xml; charset=utf-8'
                'SOAPAction'    = $SoapAction
            } `
            -UseBasicParsing `
            -ErrorAction Stop
        return [xml]$resp.Content
    } catch {
        Write-Log "SPML HTTP error: $_" 'ERROR'
        return $null
    }
}

function Get-SpmlStatus {
    param([xml]$Response)
    if (-not $Response) { return 'failure' }
    # Status is in the response element's 'status' attribute
    $ns = @{ spml = 'urn:oasis:names:tc:SPML:2:0' }
    $node = $Response.SelectSingleNode('//*[@status]')
    if ($node) { return $node.GetAttribute('status') }
    return 'unknown'
}

# ── SPML: Search for a user or group by sAMAccountName ───────────────────────

function Search-SpmlObject {
    <#
    .SYNOPSIS Searches AR for an AD object by sAMAccountName.
    .RETURNS  Array of @{ DN; sAMAccountName; UPN; ObjectClass } or empty array.
    #>
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$BaseDN,
        [string]$SamAccountName,
        [ValidateSet('user','group','*')][string]$ObjectClass = '*',
        [int]$SizeLimit = 6
    )
    $classFilter = if ($ObjectClass -ne '*') {
        "<dsml:equalityMatch name=`"objectClass`"><dsml:value>$ObjectClass</dsml:value></dsml:equalityMatch>"
    } else { '' }

    $safeAccount = [System.Security.SecurityElement]::Escape($SamAccountName)
    $safeDN      = [System.Security.SecurityElement]::Escape($BaseDN)

    $soapBody = @"
<spml:searchRequest xmlns:spml="urn:oasis:names:tc:SPML:2:0" sizeLimit="$SizeLimit">
  <spml:query>
    <spml:basePsoID ID="$safeDN" targetID="urn:oasis:names:tc:SPML:2:0:DSML"/>
    <spml:scope>subTree</spml:scope>
    <spml:filter>
      <dsml:filter xmlns:dsml="urn:oasis:names:tc:DSML:2:0:core">
        <dsml:and>
          <dsml:equalityMatch name="sAMAccountName">
            <dsml:value>$safeAccount</dsml:value>
          </dsml:equalityMatch>
          $classFilter
        </dsml:and>
      </dsml:filter>
    </spml:filter>
    <spml:attributes>
      <spml:attribute name="distinguishedName"/>
      <spml:attribute name="sAMAccountName"/>
      <spml:attribute name="userPrincipalName"/>
      <spml:attribute name="objectClass"/>
    </spml:attributes>
  </spml:query>
</spml:searchRequest>
"@
    $respXml = Invoke-SpmlRequest -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -SoapBody $soapBody
    if (-not $respXml) { return @() }

    $results = @()
    $nsm = New-Object System.Xml.XmlNamespaceManager($respXml.NameTable)
    $nsm.AddNamespace('spml', 'urn:oasis:names:tc:SPML:2:0')
    $nsm.AddNamespace('dsml', 'urn:oasis:names:tc:DSML:2:0:core')

    foreach ($pso in $respXml.SelectNodes('//spml:pso', $nsm)) {
        $dn  = ($pso.SelectSingleNode('.//dsml:attr[@name="distinguishedName"]/dsml:value', $nsm)).'#text'
        $sam = ($pso.SelectSingleNode('.//dsml:attr[@name="sAMAccountName"]/dsml:value',   $nsm)).'#text'
        $upn = ($pso.SelectSingleNode('.//dsml:attr[@name="userPrincipalName"]/dsml:value', $nsm)).'#text'
        $cls = ($pso.SelectNodes('.//dsml:attr[@name="objectClass"]/dsml:value', $nsm) | ForEach-Object { $_.'#text' }) -join ','
        if ($dn) {
            $results += @{ DN = $dn; SamAccountName = $sam; UPN = $upn; ObjectClass = $cls }
        }
    }
    return $results
}

# ── SPML: Get group members (returns list of member DNs) ──────────────────────

function Get-SpmlGroupMembers {
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$GroupDN,
        [int]$SizeLimit = 200
    )
    $safeDN = [System.Security.SecurityElement]::Escape($GroupDN)
    $soapBody = @"
<spml:lookupRequest xmlns:spml="urn:oasis:names:tc:SPML:2:0">
  <spml:psoID ID="$safeDN" targetID="urn:oasis:names:tc:SPML:2:0:DSML"/>
  <spml:attributes>
    <spml:attribute name="member"/>
  </spml:attributes>
</spml:lookupRequest>
"@
    $respXml = Invoke-SpmlRequest -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -SoapBody $soapBody
    if (-not $respXml) { return @() }

    $nsm = New-Object System.Xml.XmlNamespaceManager($respXml.NameTable)
    $nsm.AddNamespace('spml', 'urn:oasis:names:tc:SPML:2:0')
    $nsm.AddNamespace('dsml', 'urn:oasis:names:tc:DSML:2:0:core')

    $members = @()
    foreach ($val in $respXml.SelectNodes('//dsml:attr[@name="member"]/dsml:value', $nsm)) {
        if ($val.'#text') { $members += $val.'#text' }
    }
    return $members
}

# ── SPML: Lookup a single object by DN ────────────────────────────────────────

function Get-SpmlObjectByDN {
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$DN
    )
    $safeDN = [System.Security.SecurityElement]::Escape($DN)
    $soapBody = @"
<spml:lookupRequest xmlns:spml="urn:oasis:names:tc:SPML:2:0">
  <spml:psoID ID="$safeDN" targetID="urn:oasis:names:tc:SPML:2:0:DSML"/>
  <spml:attributes>
    <spml:attribute name="distinguishedName"/>
    <spml:attribute name="sAMAccountName"/>
    <spml:attribute name="userPrincipalName"/>
    <spml:attribute name="objectClass"/>
    <spml:attribute name="groupType"/>
  </spml:attributes>
</spml:lookupRequest>
"@
    $respXml = Invoke-SpmlRequest -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -SoapBody $soapBody
    if (-not $respXml) { return $null }

    $nsm = New-Object System.Xml.XmlNamespaceManager($respXml.NameTable)
    $nsm.AddNamespace('spml', 'urn:oasis:names:tc:SPML:2:0')
    $nsm.AddNamespace('dsml', 'urn:oasis:names:tc:DSML:2:0:core')

    $dn       = ($respXml.SelectSingleNode('//dsml:attr[@name="distinguishedName"]/dsml:value', $nsm)).'#text'
    $sam      = ($respXml.SelectSingleNode('//dsml:attr[@name="sAMAccountName"]/dsml:value',   $nsm)).'#text'
    $upn      = ($respXml.SelectSingleNode('//dsml:attr[@name="userPrincipalName"]/dsml:value', $nsm)).'#text'
    $grpType  = ($respXml.SelectSingleNode('//dsml:attr[@name="groupType"]/dsml:value',         $nsm)).'#text'

    if (-not $dn) { return $null }
    return @{ DN = $dn; SamAccountName = $sam; UPN = $upn; GroupType = $grpType }
}

# ── SPML: Modify group membership (add or remove members) ─────────────────────

function Set-SpmlGroupMember {
    <#
    .SYNOPSIS Adds or removes a single member DN from an AD group via SPML modifyRequest.
    .PARAMETER Mode  'add' or 'delete'
    .RETURNS $true on success, $false on failure.
    #>
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$GroupDN,
        [string]$MemberDN,
        [ValidateSet('add','delete')][string]$Mode,
        [string]$OperationReason = ''
    )
    $safeGroupDN  = [System.Security.SecurityElement]::Escape($GroupDN)
    $safeMemberDN = [System.Security.SecurityElement]::Escape($MemberDN)
    $safeReason   = [System.Security.SecurityElement]::Escape($OperationReason)

    # AR 8.x supports an <spml:controlData> extension for operation reason (audit)
    $controlBlock = if ($OperationReason) {
        @"
  <spml:controlData>
    <ar:control xmlns:ar="urn:quest:active-roles:spml:1.0">
      <ar:operationReason>$safeReason</ar:operationReason>
    </ar:control>
  </spml:controlData>
"@
    } else { '' }

    $soapBody = @"
<spml:modifyRequest xmlns:spml="urn:oasis:names:tc:SPML:2:0">
  <spml:psoID ID="$safeGroupDN" targetID="urn:oasis:names:tc:SPML:2:0:DSML"/>
  $controlBlock
  <spml:modification modificationMode="$Mode">
    <spml:data>
      <dsml:attr name="member" xmlns:dsml="urn:oasis:names:tc:DSML:2:0:core">
        <dsml:value>$safeMemberDN</dsml:value>
      </dsml:attr>
    </spml:data>
  </spml:modification>
</spml:modifyRequest>
"@
    $respXml = Invoke-SpmlRequest -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -SoapBody $soapBody
    $status  = Get-SpmlStatus -Response $respXml
    return ($status -eq 'success')
}

# ── SPML: Change group scope (groupType attribute) ─────────────────────────────
# groupType bitmask: -2147483644 = DomainLocal Security, -2147483640 = Universal Security

function Set-SpmlGroupScope {
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$GroupDN,
        [ValidateSet('DomainLocal','Universal','Global')][string]$Scope,
        [string]$OperationReason = ''
    )
    # AD groupType bitmask values for security groups
    $scopeMap = @{ DomainLocal = '-2147483644'; Universal = '-2147483640'; Global = '-2147483646' }
    $groupTypeValue = $scopeMap[$Scope]
    $safeGroupDN    = [System.Security.SecurityElement]::Escape($GroupDN)
    $safeReason     = [System.Security.SecurityElement]::Escape($OperationReason)

    $controlBlock = if ($OperationReason) {
        @"
  <spml:controlData>
    <ar:control xmlns:ar="urn:quest:active-roles:spml:1.0">
      <ar:operationReason>$safeReason</ar:operationReason>
    </ar:control>
  </spml:controlData>
"@
    } else { '' }

    $soapBody = @"
<spml:modifyRequest xmlns:spml="urn:oasis:names:tc:SPML:2:0">
  <spml:psoID ID="$safeGroupDN" targetID="urn:oasis:names:tc:SPML:2:0:DSML"/>
  $controlBlock
  <spml:modification modificationMode="replace">
    <spml:data>
      <dsml:attr name="groupType" xmlns:dsml="urn:oasis:names:tc:DSML:2:0:core">
        <dsml:value>$groupTypeValue</dsml:value>
      </dsml:attr>
    </spml:data>
  </spml:modification>
</spml:modifyRequest>
"@
    $respXml = Invoke-SpmlRequest -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -SoapBody $soapBody
    $status  = Get-SpmlStatus -Response $respXml
    return ($status -eq 'success')
}

# ── Identity validation: resolves sAMAccountName to DN via SPML ───────────────

function Resolve-IdentityToDN {
    <#
    .SYNOPSIS Looks up a sam account name and returns its DN (user or group).
    .RETURNS  DN string or $null if not found / ambiguous.
    #>
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$Identity,
        [string]$BaseDN,
        [string]$DomainDN,
        [string]$DomainFQDN
    )
    # Strip domain prefix if present (e.g. DOMAIN\user → user)
    $sam = $Identity -replace '^[^\\]+\\', ''
    if ($sam.Length -lt 2) {
        Write-Log "Identity '$Identity' too short" 'WARNING'
        return $null
    }

    # Try user first
    $users = Search-SpmlObject -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
        -BaseDN $BaseDN -SamAccountName $sam -ObjectClass 'user'
    if ($users.Count -eq 1) {
        Write-Log "Resolved '$sam' → user DN: $($users[0].DN)" 'INFO'
        return $users[0].DN
    }
    if ($users.Count -gt 1) {
        # Filter to trusted domain
        $filtered = @($users | Where-Object { $_.DN -match [regex]::Escape($DomainDN) + '$' })
        if ($filtered.Count -eq 1) { return $filtered[0].DN }
        Write-Log "'$sam' is ambiguous ($($users.Count) users)" 'WARNING'
        return $null
    }

    # Try group
    $groups = Search-SpmlObject -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
        -BaseDN $BaseDN -SamAccountName $sam -ObjectClass 'group'
    if ($groups.Count -eq 1) {
        Write-Log "Resolved '$sam' → group DN: $($groups[0].DN)" 'INFO'
        return $groups[0].DN
    }
    if ($groups.Count -gt 1) {
        $filtered = @($groups | Where-Object { $_.DN -match [regex]::Escape($DomainDN) + '$' })
        if ($filtered.Count -eq 1) { return $filtered[0].DN }
        Write-Log "'$sam' is ambiguous ($($groups.Count) groups)" 'WARNING'
        return $null
    }

    Write-Log "'$sam' not found in AD (neither user nor group)" 'WARNING'
    return $null
}

# ── Core: Sync group membership (add missing, remove unlisted) ─────────────────

function Sync-GroupMembership {
    <#
    .SYNOPSIS Sets group membership to exactly the given list of member DNs.
    .RETURNS  @{ Added=@(); Removed=@(); Errors=@() }
    #>
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$GroupName,
        [string]$GroupDN,
        [string[]]$AllowedMemberDNs,
        [string]$OperationReason
    )
    $results = @{ Added = @(); Removed = @(); Errors = @() }
    $AllowedMemberDNs = @($AllowedMemberDNs | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique)

    # Get current membership
    $currentMembers = Get-SpmlGroupMembers -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -GroupDN $GroupDN

    # Add missing members
    foreach ($dn in $AllowedMemberDNs) {
        if (-not ($currentMembers -contains $dn)) {
            $ok = Set-SpmlGroupMember -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
                -GroupDN $GroupDN -MemberDN $dn -Mode 'add' -OperationReason $OperationReason
            if ($ok) {
                $results.Added += $dn
                Write-Log "Added '$dn' to '$GroupName'" 'SUCCESS'
            } else {
                $err = "Failed to add '$dn' to '$GroupName'"
                $results.Errors += $err
                Write-Log $err 'ERROR'
            }
        }
    }

    # Re-fetch after adds
    $currentMembers = Get-SpmlGroupMembers -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -GroupDN $GroupDN

    # Remove unlisted members
    foreach ($dn in $currentMembers) {
        if (-not ($AllowedMemberDNs -contains $dn)) {
            $ok = Set-SpmlGroupMember -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
                -GroupDN $GroupDN -MemberDN $dn -Mode 'delete' -OperationReason $OperationReason
            if ($ok) {
                $results.Removed += $dn
                Write-Log "Removed '$dn' from '$GroupName'" 'SUCCESS'
            } else {
                $err = "Failed to remove '$dn' from '$GroupName'"
                $results.Errors += $err
                Write-Log $err 'ERROR'
            }
        }
    }

    return $results
}

# ── Core: Configure Admin or RDP group access ──────────────────────────────────

function Set-ADGroupAccess {
    param(
        [string]$SpmlUrl,
        [string]$AuthHeader,
        [string]$GroupName,
        [string]$BaseDN,
        [string]$DomainDN,
        [string]$DomainFQDN,
        [string[]]$IdentityList,
        [string]$GroupType,        # 'Admin' or 'RDP'
        [string]$VMDomain,
        [string]$VMName,
        [string]$OperationReason,
        [hashtable]$DomainConfig
    )
    $report = @()

    # Resolve the group DN
    $groupObjs = Search-SpmlObject -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
        -BaseDN $BaseDN -SamAccountName $GroupName -ObjectClass 'group'

    if ($groupObjs.Count -eq 0) {
        $msg = "ERROR: Group '$GroupName' not found in AR."
        Write-Log $msg 'ERROR'
        $report += $msg
        return $report
    }
    $groupDN = $groupObjs[0].DN
    Write-Log "$GroupType group '$GroupName' → DN: $groupDN" 'INFO'

    # Resolve each identity to a DN
    $resolvedDNs = [System.Collections.Generic.List[string]]::new()
    foreach ($id in $IdentityList) {
        if (-not $id -or $id.Trim().Length -lt 2) { continue }
        $dn = Resolve-IdentityToDN -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
            -Identity $id -BaseDN $BaseDN -DomainDN $DomainDN -DomainFQDN $DomainFQDN
        if ($dn) {
            $resolvedDNs.Add($dn)
        } else {
            $warn = "WARNING: '$id' could not be resolved – skipped."
            $report  += $warn
            $Warnings.Add($warn)
        }
    }

    # For NEWAD RDP: also maintain per-company RDP sub-groups
    if ($GroupType -eq 'RDP' -and $VMDomain -eq 'NEWAD' -and $DomainConfig.CompanyRdpPat) {
        # Group company-specific members and sync company sub-groups
        $companyGroupDNs = @{}
        foreach ($dn in $resolvedDNs) {
            # Extract company DC component from DN (e.g. ...,DC=COMPANY,DC=parent,...)
            if ($dn -match ',DC=([^,]+),DC=[^,]+,DC=') {
                $company = $matches[1].ToUpper()
                if ($DomainConfig.CompanyPrefixes -contains $company) {
                    $cgName = $DomainConfig.CompanyRdpPat -f $company, $VMName
                    if (-not $companyGroupDNs.ContainsKey($cgName)) { $companyGroupDNs[$cgName] = @() }
                    $companyGroupDNs[$cgName] += $dn
                }
            }
        }
        foreach ($cgName in $companyGroupDNs.Keys) {
            $cgObjs = Search-SpmlObject -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
                -BaseDN $BaseDN -SamAccountName $cgName -ObjectClass 'group'
            if ($cgObjs.Count -eq 0) {
                Write-Log "Company RDP group '$cgName' not found – skipping" 'WARNING'
                continue
            }
            $cgDN = $cgObjs[0].DN
            $cgSync = Sync-GroupMembership -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
                -GroupName $cgName -GroupDN $cgDN `
                -AllowedMemberDNs $companyGroupDNs[$cgName] `
                -OperationReason $OperationReason
            $cgSync.Added   | ForEach-Object { $report += "Granted access (company group $cgName): $((($_ -split ',')[0]) -replace 'CN=','')" }
            $cgSync.Removed | ForEach-Object { $report += "Revoked access (company group $cgName): $((($_ -split ',')[0]) -replace 'CN=','')" }
            $report += $cgSync.Errors
        }
    }

    # Ensure group scope is DomainLocal for cross-domain membership (OLDAD only)
    if ($VMDomain -eq 'OLDAD') {
        $groupInfo = Get-SpmlObjectByDN -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader -DN $groupDN
        # groupType -2147483644 = DomainLocal security; others need conversion
        if ($groupInfo -and $groupInfo.GroupType -and $groupInfo.GroupType -ne '-2147483644') {
            Write-Log "Converting '$GroupName' scope to DomainLocal" 'INFO'
            $ok = Set-SpmlGroupScope -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
                -GroupDN $groupDN -Scope 'DomainLocal' -OperationReason $OperationReason
            if (-not $ok) {
                Write-Log "Scope change for '$GroupName' failed – continuing anyway" 'WARNING'
            }
        }
    }

    # Sync membership
    $sync = Sync-GroupMembership -SpmlUrl $SpmlUrl -AuthHeader $AuthHeader `
        -GroupName $GroupName -GroupDN $groupDN `
        -AllowedMemberDNs $resolvedDNs.ToArray() `
        -OperationReason $OperationReason

    $sync.Added   | ForEach-Object { $report += "Granted $GroupType access: $((($_ -split ',')[0]) -replace 'CN=','')." }
    $sync.Removed | ForEach-Object { $report += "Revoked $GroupType access: $((($_ -split ',')[0]) -replace 'CN=','')." }
    $report += $sync.Errors

    Write-Log "$GroupType sync for '$GroupName' done. +$($sync.Added.Count) / -$($sync.Removed.Count)" 'SUCCESS'
    return $report
}

# ── Main ───────────────────────────────────────────────────────────────────────

try {
    Write-Log "=== ARS - Configure VM Local Permissions (SPML v2) ===" 'INFO'
    Write-Log "VM: $VMName | Domain: $VMDomain | Order: $OrderID" 'INFO'

    # Validate required params
    foreach ($p in @('VMName', 'VMDomain', 'OrderID')) {
        $v = $PARAMS[$p]
        if (-not $v -or $v.Trim() -eq '') { throw "Required parameter '$p' is missing or empty." }
    }
    if ($OrderID -notmatch '^\d+$') { throw "OrderID must be numeric. Got: $OrderID" }
    if (-not $DomainMap.ContainsKey($VMDomain)) {
        throw "VMDomain '$VMDomain' is not supported. Use 'OLDAD' or 'NEWAD'."
    }

    $cfg        = $DomainMap[$VMDomain]
    $authHeader = Get-BasicAuthHeader -User $cfg.ServiceUser -Password $cfg.ServicePassword
    $baseDN     = $cfg.DomainDN

    $operationReason = "$($VARS['AR_OPERATION_REASON_PREFIX']) SNow REQ $Snow_REQ RITM $Snow_RITM Order $OrderID"

    # Parse identity lists
    $AdmList = if ([string]::IsNullOrWhiteSpace($AdmIdentityList)) { @() } else {
        @($AdmIdentityList -split '[;,]' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
    }
    $RdpList = if ([string]::IsNullOrWhiteSpace($RdpIdentityList)) { @() } else {
        @($RdpIdentityList -split '[;,]' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
    }
    Write-Log "Admin identities: $(if ($AdmList) { $AdmList -join ', ' } else { '(none – will clear group)' })" 'INFO'
    Write-Log "RDP identities:   $(if ($RdpList) { $RdpList -join ', ' } else { '(none – will clear group)' })" 'INFO'

    # Derive group names
    $admGroupName = $cfg.AdminGroupPat -f $VMName
    $rdpGroupName = $cfg.RdpGroupPat   -f $VMName
    Write-Log "Admin group: $admGroupName" 'INFO'
    Write-Log "RDP group:   $rdpGroupName" 'INFO'

    # Configure Admin group
    $admResults = Set-ADGroupAccess `
        -SpmlUrl $cfg.SpmlUrl -AuthHeader $authHeader `
        -GroupName $admGroupName -BaseDN $baseDN `
        -DomainDN $cfg.DomainDN -DomainFQDN $cfg.DomainFQDN `
        -IdentityList $AdmList -GroupType 'Admin' `
        -VMDomain $VMDomain -VMName $VMName `
        -OperationReason $operationReason `
        -DomainConfig $cfg

    # Configure RDP group
    $rdpResults = Set-ADGroupAccess `
        -SpmlUrl $cfg.SpmlUrl -AuthHeader $authHeader `
        -GroupName $rdpGroupName -BaseDN $baseDN `
        -DomainDN $cfg.DomainDN -DomainFQDN $cfg.DomainFQDN `
        -IdentityList $RdpList -GroupType 'RDP' `
        -VMDomain $VMDomain -VMName $VMName `
        -OperationReason $operationReason `
        -DomainConfig $cfg

    $admSummary = if ($admResults) { $admResults -join '; ' } else { 'OK - no changes' }
    $rdpSummary = if ($rdpResults) { $rdpResults -join '; ' } else { 'OK - no changes' }

    Write-Log "=== Completed ===" 'SUCCESS'

    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    @{
        success       = $true
        ADResultsADM  = $admSummary
        ADResultsRDP  = $rdpSummary
        warnings      = $Warnings.ToArray()
        log_lines     = $Log.Count
    } | ConvertTo-Json -Compress
    exit 0

} catch {
    $errMsg = $_.Exception.Message
    Write-Log "CRITICAL: $errMsg" 'ERROR'
    Write-Log $_.ScriptStackTrace 'ERROR'

    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    @{
        success  = $false
        error    = $errMsg
        log_lines = $Log.Count
    } | ConvertTo-Json -Compress
    exit 1
}
