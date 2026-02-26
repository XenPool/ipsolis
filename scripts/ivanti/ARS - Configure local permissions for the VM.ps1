# ARS - Configure local permissions for the VM
$VMName = "$[VMName]"
$VMDomain = "$[VMDomain]"
$OrderID = "$[OrderID]"
$Snow_REQ = "$[Snow_REQ]"
$Snow_RITM = "$[Snow_RITM]"
$AdmIdentityList = "$[LocalAdmins]"
$RdpIdentityList = "$[RDPUserIDs]"

$Config = @{
    V_Child1_NetBIOS_old = @{
        QADServiceHostname = '^[QADServiceHostname]'
        QADUser            = '^[QADUser]'
        QADPW              = '^[QADPW]'
        TrustedDnSuffix    = "V_Child1_DN_Old"
        TrustedDomainName  = "V_Child1_DNS_FQDN_old"
        AdminGroupPattern  = "G-V_Child1_Name-{0}-ADM"
        RdpGroupPattern    = "G-V_Child1_Name-{0}-VDI"
        CompanyPrefixes    = @('V_Child2_Name', 'V_Child1_Name', 'V_Child3_Name', 'V_Child4_Name')
    }
    V_Child1_Name = @{
        QADServiceHostname = '^[NewADQADServiceHostname]'
        QADUser            = '^[NewADVDIR99SvcAcc]'
        QADPW              = '^[NewADVDIR99SvcAccPW]'
        TrustedDnSuffix    = "V_Parent_DN"
        TrustedDomainName  = "V_Parent_DNS_FQDN"
        AdminGroupPattern  = "GLCS-PERMS-L-0200-SDA-{0}"
        RdpGroupPattern    = "V_Child1_Name-0200-G-V_Child1_Name-{0}-VDIRDPUsers"
        CompanyPrefixes    = @('V_Child2_Name', 'V_Child1_Name', 'V_Child3_Name', 'V_Child4_Name')
        CompanyRdpPattern  = "V_Child1_Name-0200-G-{0}-{1}-VDIRDPUsers"
    }
    SQL = @{
        ServerInstance = '^[SQLVDIServerInstance]'
        Database       = '^[SQLVDIDatabase]'
        Username       = '^[SQLVDILoginUser]'
        Password       = '^[SQLVDILoginPW]'
    }
    Email = @{
        SmtpServer = '^[EmailSMTPServer]'
        From       = '^[EmailFrom]'
        BccUser    = '^[EmailBcc]'
        User       = '^[EmailUser]'
        Password   = '^[EMailUserPW]'
    }
}

$logFile = "C:\Logs\VDI-ARS-$VMName-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet('INFO', 'SUCCESS', 'WARNING', 'ERROR')]
        [string]$Level = 'INFO'
    )
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $logMsg = "[$timestamp] [$Level] $Message"
    $color = switch ($Level) {
        'SUCCESS' { 'Green' }
        'WARNING' { 'Yellow' }
        'ERROR'   { 'Red' }
        default   { 'White' }
    }
    Write-Host $logMsg -ForegroundColor $color
    Add-Content -Path $logFile -Value $logMsg -ErrorAction SilentlyContinue
}

function Invoke-SafeSqlCommand {
    param(
        [string]$Query,
        [hashtable]$SqlConfig,
        [string]$OperationName
    )
    try {
        $sqlParams = @{
            ServerInstance = $SqlConfig.ServerInstance
            Database       = $SqlConfig.Database
            Username       = $SqlConfig.Username
            Password       = $SqlConfig.Password
            Query          = $Query
            ErrorAction    = 'Stop'
        }
        Invoke-Sqlcmd @sqlParams
    } catch {
        Write-Log "$OperationName failed: $_" 'ERROR'
        throw $_
    }
}

function Get-UserPrincipalName {
    param([string]$Identity)
    if ($Identity -match '@') { return $Identity }
    if ($Identity -match '^SIDUSER:(.+)$') { return $matches[1] }
    if ($Identity -match '^SID:(S-1-5[0-9\-]+)$') {
        $sid = $matches[1]
        try {
            $objSID = New-Object System.Security.Principal.SecurityIdentifier($sid)
            $objUser = $objSID.Translate([System.Security.Principal.NTAccount])
            $username = $objUser.Value -replace '^[^\\]+\\', ''
            return $username
        } catch {
            return 'user'
        }
    }
    if ($Identity -match 'CN=([^,]+)') { return $matches[1] }
    try {
        $obj = Get-QADObject -Identity $Identity -ErrorAction SilentlyContinue
        if ($obj) {
            if ($obj.UserPrincipalName) { return $obj.UserPrincipalName }
            elseif ($obj.Name) { return $obj.Name }
        }
    } catch { }
    return $Identity
}

function Resolve-SIDToUsername {
    param([string]$Identity)
    if ($Identity -match 'CN=(S-1-5-[0-9\-]+)') {
        $sid = $matches[1]
        try {
            $objSID = New-Object System.Security.Principal.SecurityIdentifier($sid)
            $objUser = $objSID.Translate([System.Security.Principal.NTAccount])
            $username = $objUser.Value -replace '^[^\\]+\\', ''
            return $username
        } catch { }
    }
    return $null
}

function Send-NotificationEmail {
    param(
        [string]$Subject,
        [string]$Body,
        [string]$To,
        [hashtable]$EmailConfig,
        [PSCredential]$Credential
    )
    try {
        $mailSplat = @{
            UseSsl      = $true
            Port        = 25
            To          = $To
            From        = $EmailConfig.From
            Subject     = $Subject
            Body        = $Body
            BodyAsHtml  = $true
            SmtpServer  = $EmailConfig.SmtpServer
            Credential  = $Credential
            Encoding    = [System.Text.Encoding]::UTF8
            ErrorAction = 'Stop'
        }
        
        if ($EmailConfig.BccUser -and $EmailConfig.BccUser.Trim()) {
            try {
                $bccString = $EmailConfig.BccUser.Trim() -replace '^\@\(', '' -replace '\)$', ''
                $bccArray = @($bccString -split ',' | ForEach-Object {
                    $cleaned = $_.Trim().Trim('"').Trim("'").Trim()
                    if ($cleaned -and $cleaned -match '\S+@\S+\.\S+') { $cleaned }
                })
                if ($bccArray.Count -gt 0) {
                    $mailSplat['Bcc'] = $bccArray
                    Write-Log "BCC recipients: $($bccArray -join ', ')" 'INFO'
                }
            } catch { Write-Log "Failed to parse BCC: $_. Sending without BCC." 'WARNING' }
        }
        Send-MailMessage @mailSplat
        Write-Log "Email sent: $Subject" 'SUCCESS'
    } catch {
        Write-Log "Failed to send email: $_" 'ERROR'
    }
}

function Find-UserInNewADBySID {
    param(
        [string]$UserName,
        [hashtable]$DomainConfig
    )
    $result = @{ Found = $false; SID = $null; Domain = $null }
    try {
        $nt = New-Object System.Security.Principal.NTAccount("V_Child1_Name", "$UserName")
        $sidObj = $nt.Translate([System.Security.Principal.SecurityIdentifier])
        return @{ Found = $true; SID = $sidObj.Value; Domain = "V_Child1_NetBIOS" }
    } catch { }
    if ($DomainConfig -and $DomainConfig.CompanyPrefixes) {
        foreach ($company in $DomainConfig.CompanyPrefixes) {
            if ($company -eq 'V_Child1_Name') { continue }
            try {
                $nt = New-Object System.Security.Principal.NTAccount($company, "$UserName")
                $sidObj = $nt.Translate([System.Security.Principal.SecurityIdentifier])
                return @{ Found = $true; SID = $sidObj.Value; Domain = $company }
            } catch { }
        }
    }
    return $result
}

function Add-UserToGroupBySID {
    param(
        [string]$UserName,
        [string]$SID,
        [string]$Domain,
        [string]$GroupName,
        [hashtable]$Control,
        [string]$VMName = $null,
        [string]$OrderID = $null,
        [hashtable]$EmailConfig = $null,
        [PSCredential]$EmailCredential = $null,
        [bool]$SendEmailOnFailure = $false
    )
    try {
        [void](New-Object System.Security.Principal.SecurityIdentifier($SID))
        return @{ Success = $true; SIDFormat = "SID:$SID" }
    } catch {
        Write-Log "Failed validating SID for '$Domain\$UserName': $_" 'ERROR'
        if ($SendEmailOnFailure -and $EmailConfig -and $EmailCredential) {
            $qsSuffix = if ($env:COMPUTERNAME -eq 'V_Child1_NameINSA5118') { ' (QS)' } else { '' }
            $subject = "Order $OrderID needs manual action. The user $UserName has no OldAD shadow account"
            $groupLink = '<a title="Active Roles Webconsole" href="https://ars.V_Child1_DNS_FQDN_old_sublocation/ARWebAdmin/" target="_blank" style="color: #BB0A30; text-decoration: none; font-weight: bold;">' + $GroupName + '</a>'
            
            $emailBody = @"
<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><title>V_Child1_Name VDI WatchDog Alert$qsSuffix</title></head>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000000; margin: 0; padding: 0;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px;">
                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff;">
                    <tr>
                        <td style="background-color: #BB0A30; padding: 20px; text-align: center;">
                            <h1 style="color: #ffffff; font-size: 20pt; margin: 0; font-weight: bold;">V_Child1_Name VDI-Selfservice WatchDog Alert$qsSuffix</h1>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background: #FFF3CD; border-left: 4px solid #BB0A30;">
                                <tr>
                                    <td>
                                        <p style="color: #BB0A30; font-size: 14pt; font-weight: bold; margin: 0 0 10px 0;">ACHTUNG: Manuelle Aktion erforderlich</p>
                                        <p style="margin: 0;">Order <strong style="color: #BB0A30;">$OrderID</strong> für VM <strong style="color: #BB0A30;">$VMName</strong> muss manuell geprüft werden.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <h2 style="color: #BB0A30; font-size: 14pt; margin: 0 0 10px 0;">Problembeschreibung</h2>
                            <table width="100%" cellpadding="4" cellspacing="0" border="0" style="border-collapse: collapse;">
                                <tr>
                                    <td width="40%" style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Benutzer-ID</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;"><strong style="color: #BB0A30;">$Domain\$UserName</strong></td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Zielgruppe</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$groupLink</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">VM Name</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$VMName</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Order ID</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$OrderID</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Problem</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">Der Benutzer existiert nur in NewAD und hat kein Shadow-Konto in OldAD</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background-color: #E8F4F8; border-left: 4px solid #0066CC;">
                                <tr>
                                    <td>
                                        <h3 style="color: #0066CC; margin: 0 0 10px 0; font-size: 12pt;">Erforderliche Maßnahmen</h3>
                                        <p style="margin: 0 0 10px 0;"><strong>Ursache:</strong></p>
                                        <p style="margin: 0 0 15px 0;">Active Roles kann diesen Benutzer nicht verarbeiten (kein Shadow-Konto in OldAD).</p>
                                        <p style="margin: 0 0 10px 0;"><strong>Lösung:</strong></p>
                                        <p style="margin: 0;">Benutzer <strong style="color: #0066CC;">$Domain\$UserName</strong> manuell via Active Roles MMC hinzufügen.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 15px; text-align: center; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; font-size: 10pt;"><strong>V_Child1_Name VDI Watchdog</strong></p>
                            <p style="margin: 5px 0; font-size: 9pt; color: #666666;">Automatische Überwachung | $(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')</p>
                            <p style="margin: 10px 0 0 0; font-size: 9pt; color: #999999;">
                                Support: <a href="mailto:V_Snap-Provision_EMailFromAddress" style="color: #BB0A30; text-decoration: none;">V_Snap-Provision_EMailFromAddress</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"@
            $toRecipient = $EmailConfig.From
            if ($EmailConfig.BccUser) {
                $bccParsed = $EmailConfig.BccUser.Trim() -replace '^\@\(', '' -replace '\)$', ''
                $firstBcc = ($bccParsed -split ',' | ForEach-Object { $_.Trim().Trim('"').Trim("'").Trim() } | Where-Object { $_ -match '\S+@\S+\.\S+' } | Select-Object -First 1)
                if ($firstBcc) { $toRecipient = $firstBcc }
            }
            Send-NotificationEmail -Subject $subject -Body $emailBody -To $toRecipient -EmailConfig $EmailConfig -Credential $EmailCredential
        }
        return @{ Success = $false; SIDFormat = $null }
    }
}

function Get-ValidatedIdentityList {
    param(
        [string[]]$Entries,
        [string]$TrustedDnSuffix,
        [string]$TrustedDomainName,
        [string]$GroupName = $null,
        [string]$VMDomain = $null,
        [string]$VMName = $null,
        [hashtable]$EmailConfig = $null,
        [PSCredential]$EmailCredential = $null,
        [hashtable]$DomainConfig = $null
    )

    $validated = @()
    $errors = @()
    $notFoundUsers = @()
    $wrongDomainUsers = @()
    foreach ($e in $Entries) {
        if (-not $e -or $e.Length -lt 6) {
            if ($e) {
                Write-Log "'$e' too short (min 6)" 'WARNING'
                $errors += "Identity $($e.ToUpper()) too short (min 6 chars)."
            }
            continue
        }
        $matchedObject = $null
        $objectType = $null
        $u = @(Get-QADUser -Identity $e -SizeLimit 6 -ErrorAction SilentlyContinue)
        if ($u.Count -gt 0) {
            $objectType = 'User'
            if ($u.Count -gt 1) {
                if ($VMDomain -eq 'V_Child1_Name') {
                    $filtered = @()
                    foreach ($usr in $u) {
                        if ($usr.DN -match ',DC=([^,]+),DC=.+?,V_Parent_DN$') {
                            $userCompany = $matches[1].ToUpper()
                            if ($DomainConfig.CompanyPrefixes -contains $userCompany) {
                                $filtered += $usr
                            }
                        }
                    }
                } elseif ($VMDomain -eq 'V_Child1_NetBIOS_old') {
                    $filtered = @($u | Where-Object { $_.DN -match 'V_Child1_DN_Old$' })
                } else {
                    $filtered = @()
                }
                
                if ($filtered.Count -eq 1) {
                    $matchedObject = $filtered[0]
                } elseif ($filtered.Count -gt 1) {
                    $errors += "$($e.ToUpper()) ambiguous ($($filtered.Count) matches)."
                    Write-Log "'$e' ambiguous after filter" 'WARNING'; continue
                } else {
                    $errors += "$($e.ToUpper()) - $($u.Count) matches, none in allowed domains."
                    Write-Log "'$e' - no matches in allowed domains" 'WARNING'; continue
                }
            } else {
                $matchedObject = $u[0]
            }
        }
        
        if (-not $matchedObject) {
            $g = @(Get-QADGroup -Identity $e -SizeLimit 6 -ErrorAction SilentlyContinue)
            if ($g.Count -gt 0) {
                $objectType = 'Group'
                if ($g.Count -gt 1) {
                    Write-Log "'$e' has $($g.Count) groups. Filtering by '$VMDomain'" 'INFO'
                    
                    if ($VMDomain -eq 'V_Child1_Name') {
                        $filtered = @()
                        foreach ($grp in $g) {
                            if ($grp.DN -match ',DC=([^,]+),DC=.+?,V_Parent_DN$') {
                                $groupCompany = $matches[1].ToUpper()
                                if ($DomainConfig.CompanyPrefixes -contains $groupCompany) {
                                    $filtered += $grp
                                }
                            }
                        }
                    } elseif ($VMDomain -eq 'V_Child1_NetBIOS_old') {
                        $filtered = @($g | Where-Object { $_.DN -match 'V_Child1_DN_Old$' })
                    } else {
                        $filtered = @()
                    }
                    
                    if ($filtered.Count -eq 1) {
                        $matchedObject = $filtered[0]
                    } elseif ($filtered.Count -gt 1) {
                        $errors += "$($e.ToUpper()) ambiguous ($($filtered.Count) groups)."
                        Write-Log "'$e' - ambiguous group" 'WARNING'
                        continue
                    } else {
                        $errors += "$($e.ToUpper()) - $($g.Count) groups, none in allowed domains."
                        Write-Log "'$e' - no groups in allowed domains" 'WARNING'
                        continue
                    }
                } else {
                    $matchedObject = $g[0]
                }
            }
        }
        
        if ($matchedObject) {
            $inTrustedDomain = ($matchedObject.DN -match [regex]::Escape($TrustedDnSuffix) + '$') -or ($matchedObject.UserPrincipalName -and $matchedObject.UserPrincipalName.ToLower().EndsWith("@"+$TrustedDomainName.ToLower()))
            if ($inTrustedDomain) {
                if ($VMDomain -eq 'V_Child1_NetBIOS_old') { $validated += $e }
                else {
                    if ($matchedObject.DN) { $validated += $matchedObject.DN }
                    elseif ($matchedObject.UserPrincipalName) { $validated += $matchedObject.UserPrincipalName }
                    else { $errors += "$objectType '$e' matched but has no DN or UPN" }
                }
            } else {
                $userDomain = "unknown"
                if ($matchedObject.DN -match 'DC=([^,]+),DC=([^,]+)$') { $userDomain = "$($matches[1]).$($matches[2])" }
                if ($VMDomain -eq 'V_Child1_NetBIOS_old') { $wrongDomainUsers += @{ Identity = $e; Domain = $userDomain } }
                else {
                    $errors += "$objectType '$e' in OldAD ($userDomain). VDI $VMName in NewAD - cannot assign."
                    Write-Log "$objectType '$e' from '$userDomain' not allowed for NewAD VDIs" 'WARNING'
                }
            }
            continue
        }
        if ($VMDomain -eq 'V_Child1_NetBIOS_old') { $notFoundUsers += $e }
        else { $errors += "$($e.ToUpper()) not found in AD. Verify identity." }
    }

    if ($VMDomain -eq 'V_Child1_NetBIOS_old' -and $wrongDomainUsers.Count -gt 0) {
        foreach ($wrongDomainUser in $wrongDomainUsers) {
            $userName = $wrongDomainUser.Identity
            $userDomain = $wrongDomainUser.Domain
            $sidLookup = Find-UserInNewADBySID -UserName $userName -DomainConfig $DomainConfig
            if ($sidLookup.Found) {
                $addResult = Add-UserToGroupBySID -UserName $userName `
                                                   -SID $sidLookup.SID `
                                                   -Domain $sidLookup.Domain `
                                                   -GroupName $GroupName `
                                                   -Control $Control `
                                                   -SendEmailOnFailure $false
                
                if ($addResult.Success) {
                    $validated += $addResult.SIDFormat
                } else {
                    $notFoundUsers += $userName
                }
            } else {
                $errors += "User $($userName.ToUpper()) from domain '$userDomain' not found in OldAD or NewAD."
                Write-Log "'$userName' from '$userDomain' not allowed and not found in NewAD" 'WARNING'
            }
        }
    }

    if ($VMDomain -eq 'V_Child1_NetBIOS_old' -and $notFoundUsers.Count -gt 0 -and $EmailConfig -and $EmailCredential) {
        foreach ($notFoundUser in $notFoundUsers) {
            $sidLookup = Find-UserInNewADBySID -UserName $notFoundUser -DomainConfig $DomainConfig
            if ($sidLookup.Found) {
                $addResult = Add-UserToGroupBySID -UserName $notFoundUser `
                                                   -SID $sidLookup.SID `
                                                   -Domain $sidLookup.Domain `
                                                   -GroupName $GroupName `
                                                   -Control $Control `
                                                   -VMName $VMName `
                                                   -OrderID $OrderID `
                                                   -EmailConfig $EmailConfig `
                                                   -EmailCredential $EmailCredential `
                                                   -SendEmailOnFailure $true
                
                if ($addResult.Success) {
                    $validated += $addResult.SIDFormat
                } else {
                    $errors += "User '$notFoundUser' in NewAD ($($sidLookup.Domain)). VDI $VMName in OldAD - auto-assign failed. Manual assignment by VDI Support needed. Contact if no access after 3 days."
                }
            } else {
                $errors += "User or group $($notFoundUser.ToUpper()) not found in AD, please check."
                Write-Log "'$notFoundUser' not found in OldAD or NewAD" 'WARNING'
            }
        }
    } elseif ($VMDomain -eq 'V_Child1_NetBIOS_old' -and $notFoundUsers.Count -gt 0) {
        foreach ($notFoundUser in $notFoundUsers) {
            $errors += "User or group $($notFoundUser.ToUpper()) not found in AD, please check."
            Write-Log "'$notFoundUser' not found in AD" 'WARNING'
        }
    }
    return @{ Validated = $validated; Errors = $errors }
}
function Get-ForeignSecurityPrincipalDN {
    param(
        [string]$SID,
        [string]$GroupDN
    )
    if ($GroupDN -match '(DC=.+)$') {
        $domainDN = $matches[1]
        return "CN=$SID,CN=ForeignSecurityPrincipals,$domainDN"
    }
    Write-Log "Could not extract domain DN from group DN: $GroupDN" 'WARNING'
    return $null
}
function Sync-ADGroupMembership {
    param(
        [string]$GroupName,
        [string[]]$AllowedMembers,
        [hashtable]$Control,
        [string]$VMDomain
    )
    $results = @{ Added = @(); Removed = @(); AlreadyAssigned = @(); Errors = @() }
    try {
        $groupObj = Get-QADGroup -Identity $GroupName -ErrorAction Stop
    } catch {
        $results.Errors += "Group '$GroupName' not found: $_"
        Write-Log "Group '$GroupName' not found: $_" 'ERROR'
        return $results
    }
    
    if ($VMDomain -eq 'V_Child1_NetBIOS_old' -and $groupObj.GroupScope -ne "DomainLocal") {
        try {
            $members = @(Get-QADGroupMember -Identity $GroupName -SizeLimit 100 -ErrorAction SilentlyContinue)
            
            $members | ForEach-Object { 
                Remove-QADGroupMember -Identity $GroupName -Member $_ -Confirm:$false -Control $Control -ErrorAction Stop 
            }
            
            Set-QADGroup -Identity $GroupName -GroupScope Universal -ErrorAction Stop -Control $Control
            Set-QADGroup -Identity $GroupName -GroupScope DomainLocal -ErrorAction Stop -Control $Control
            
            $members | ForEach-Object { 
                Add-QADGroupMember -Identity $GroupName -Member $_ -Control $Control -ErrorAction Stop 
            }
            
            Write-Log "Changed '$GroupName' to DomainLocal." 'SUCCESS'
        } catch {
            Write-Log "Failed to change scope for '$GroupName': $_" 'ERROR'
            return $results
        }
    }
    
    $currentMembers = @(Get-QADGroupMember -Identity $GroupName -SizeLimit 100 -ErrorAction SilentlyContinue | ForEach-Object { 
        if ($_ | Get-Member -Name DN -MemberType NoteProperty,Property) {
            $_.DN
        }
    }) | Where-Object { $_ -and $_.Trim() }
    
    $AllowedMembers = @($AllowedMembers | Where-Object { $_ -and $_.Trim() })
    
    if ($VMDomain -eq 'V_Child1_NetBIOS_old') {
        $resolvedAllowedMembers = @()
        foreach ($member in $AllowedMembers) {
            if ($member -match '^SID(USER)?:(.+)$') {
                $sidValue = $matches[2]
                $foreignSecPrincipalDN = Get-ForeignSecurityPrincipalDN -SID $sidValue -GroupDN $groupObj.DN
                if ($foreignSecPrincipalDN) {
                    $resolvedAllowedMembers += $foreignSecPrincipalDN
                } else {
                    $resolvedAllowedMembers += $member
                }
            } else {
                $memberObj = Get-QADObject -Identity $member -ErrorAction SilentlyContinue
                if ($memberObj -and $memberObj.DN) {
                    $resolvedAllowedMembers += $memberObj.DN
                } else {
                    $resolvedAllowedMembers += $member
                }
            }
        }
        $AllowedMembersForComparison = $resolvedAllowedMembers
    } else {
        $AllowedMembersForComparison = $AllowedMembers
    }
    
    foreach ($member in $AllowedMembers) {
        if ($member -and $member.Trim()) {
            $memberToCompare = $member
            if ($VMDomain -eq 'V_Child1_NetBIOS_old') {
                if ($member -match '^SID(USER)?:(.+)$') {
                    $sidValue = $matches[2]
                    $foreignSecPrincipalDN = Get-ForeignSecurityPrincipalDN -SID $sidValue -GroupDN $groupObj.DN
                    if ($foreignSecPrincipalDN) {
                        $memberToCompare = $foreignSecPrincipalDN
                    }
                } else {
                    try {
                        $memberObj = Get-QADObject -Identity $member -ErrorAction SilentlyContinue
                        if ($memberObj -and $memberObj.DN) {
                            $memberToCompare = $memberObj.DN
                        }
                    } catch {
                    }
                }
            }
            
            if (-not ($currentMembers -contains $memberToCompare)) {
                try {
                    $memberToAdd = $member
                    if ($member -match '^SID(USER)?:(.+)$') {
                        $memberToAdd = $matches[2]
                    }
                    Add-QADGroupMember -Identity $GroupName -Member $memberToAdd -ErrorAction Stop -Control $Control
                    $results.Added += $member
                    Write-Log "Added '$(Get-UserPrincipalName $member)' to '$GroupName'" 'SUCCESS'
                } catch {
                    $errorMsg = "Failed to add '$member': $_"
                    $results.Errors += $errorMsg
                    Write-Log $errorMsg 'ERROR'
                }
            } else {
                $results.AlreadyAssigned += $member
            }
        }
    }
    
    $currentMembers = @(Get-QADGroupMember -Identity $GroupName -SizeLimit 100 -ErrorAction SilentlyContinue | ForEach-Object { 
        if ($_ | Get-Member -Name DN -MemberType NoteProperty,Property) {
            $_.DN
        }
    }) | Where-Object { $_ -and $_.Trim() }
    
    foreach ($member in $currentMembers) {
        if ($member -and $member.Trim() -and -not ($AllowedMembersForComparison -contains $member)) {
            try {
                $displayName = Resolve-SIDToUsername -Identity $member
                if (-not $displayName) { $displayName = Get-UserPrincipalName $member }
                
                Remove-QADGroupMember -Identity $GroupName -Member $member -Confirm:$false -ErrorAction Stop -Control $Control
                $results.Removed += $displayName
                Write-Log "Removed '$displayName' from '$GroupName'" 'SUCCESS'
            } catch {
                $errorMsg = "Failed to remove '$member': $_"
                $results.Errors += $errorMsg
                Write-Log $errorMsg 'ERROR'
            }
        }
    }
    
    return $results
}

function Sync-CompanySpecificRdpGroups {
    param(
        [string]$VMName,
        [string[]]$AllowedRdpDNs,
        [hashtable]$Control,
        [hashtable]$DomainConfig
    )
    $results = @()
    if (-not $DomainConfig.CompanyPrefixes) { return $results }
    foreach ($company in $DomainConfig.CompanyPrefixes) {
        $companyGroupName = $DomainConfig.CompanyRdpPattern -f $company, $VMName
        
        $compGroupObj = Get-QADGroup -Identity $companyGroupName -ErrorAction SilentlyContinue
        if (-not $compGroupObj) {
            continue
        }
        
        $compMembers = @(Get-QADGroupMember -Identity $companyGroupName -SizeLimit 100 -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ | Get-Member -Name DN -MemberType NoteProperty,Property) {
                $_.DN
            }
        }) | Where-Object { $_ }
        
        foreach ($member in $compMembers) {
            if ($member -and -not ($AllowedRdpDNs -contains $member)) {
                try {
                    Remove-QADGroupMember -Identity $companyGroupName -Member $member -Confirm:$false -ErrorAction Stop -Control $Control
                    $results += "Revoked access for $((Get-UserPrincipalName $member).ToUpper())."
                    Write-Log "Removed '$(Get-UserPrincipalName $member)' from '$companyGroupName'" 'SUCCESS'
                } catch {
                    $errorMsg = "Failed to revoke access for '$(Get-UserPrincipalName $member)': $_"
                    $results += $errorMsg
                    Write-Log $errorMsg 'ERROR'
                }
            }
        }
    }
    
    return $results
}

function Add-MemberToCompanyRdpGroup {
    param(
        [string]$UserOrGroup,
        [string]$VMName,
        [hashtable]$Control,
        [hashtable]$DomainConfig
    )
    try {
        $userObj = Get-QADObject -Identity $UserOrGroup -ErrorAction Stop
        if ($userObj -and $userObj.DN) {
            $dnParts = $userObj.DN -split ','
            if ($dnParts.Count -ge 4) {
                $companyDC = $dnParts[-4] -replace 'DC=', ''
                $companyName = $companyDC.ToUpper()
                
                $customRdpGroupName = $DomainConfig.CompanyRdpPattern -f $companyName, $VMName
                
                $customGroupObj = Get-QADGroup -Identity $customRdpGroupName -ErrorAction SilentlyContinue
                if (-not $customGroupObj) {
                    Write-Log "Group '$customRdpGroupName' not found. Skipping member '$(Get-UserPrincipalName $UserOrGroup)'." 'WARNING'
                    return @{
                        Success = $false
                        Message = "RDP group for $companyName not found. Skipping '$(Get-UserPrincipalName $UserOrGroup)'."
                        CompanyGroup = $null
                    }
                }
                
                Add-QADGroupMember -Identity $customRdpGroupName -Member $UserOrGroup -ErrorAction Stop -Control $Control
                Write-Log "Added '$(Get-UserPrincipalName $UserOrGroup)' to '$customRdpGroupName'" 'SUCCESS'
                return @{
                    Success = $true
                    Message = "Granted access for $((Get-UserPrincipalName $UserOrGroup).ToUpper())."
                    CompanyGroup = $customRdpGroupName
                }
            } else {
                Write-Log "Could not determine company for '$(Get-UserPrincipalName $UserOrGroup)'" 'WARNING'
                return @{
                    Success = $false
                    Message = "Cannot grant access for '$(Get-UserPrincipalName $UserOrGroup)' - company unknown."
                    CompanyGroup = $null
                }
            }
        } else {
            Write-Log "Could not retrieve DN for '$(Get-UserPrincipalName $UserOrGroup)'" 'WARNING'
            return @{
                Success = $false
                Message = "Cannot grant access for '$(Get-UserPrincipalName $UserOrGroup)' - no DN."
                CompanyGroup = $null
            }
        }
    } catch {
        Write-Log "Failed to add member '$(Get-UserPrincipalName $UserOrGroup)' to company group: $_" 'ERROR'
        
        try {
            $obj = New-Object System.Security.Principal.NTAccount("V_Child1_Name", "$UserOrGroup")
            $sid = $obj.Translate([System.Security.Principal.SecurityIdentifier])
            $sidValue = $sid.Value
            
            $userObjForSid = Get-QADObject -Identity $UserOrGroup -ErrorAction SilentlyContinue
            if ($userObjForSid -and $userObjForSid.DN) {
                $dnParts = $userObjForSid.DN -split ','
                if ($dnParts.Count -ge 4) {
                    $companyDC = $dnParts[-4] -replace 'DC=', ''
                    $companyName = $companyDC.ToUpper()
                    $targetGroupForSid = $DomainConfig.CompanyRdpPattern -f $companyName, $VMName
                } else {
                    $targetGroupForSid = $DomainConfig.RdpGroupPattern -f $VMName
                }
            } else {
                $targetGroupForSid = $DomainConfig.RdpGroupPattern -f $VMName
            }
            
            Add-QADGroupMember -Identity $targetGroupForSid -Member "$sidValue" -ErrorAction Stop -Control $Control
            Write-Log "Added via SID to '$targetGroupForSid'" 'SUCCESS'
            return @{
                Success = $true
                Message = "Added member '$(Get-UserPrincipalName $UserOrGroup)' (via SID: $sidValue) to group '$targetGroupForSid'."
                CompanyGroup = $targetGroupForSid
            }
        } catch {
            Write-Log "Fallback SID conversion failed: $_" 'ERROR'
            return @{
                Success = $false
                Message = "Fallback SID conversion for '$(Get-UserPrincipalName $UserOrGroup)' failed: $_"
                CompanyGroup = $null
            }
        }
    }
}

function Set-ADGroupAccess {
    param(
        [string]$GroupName,
        [string[]]$IdentityList,
        [string]$GroupType,
        [string]$VMDomain,
        [string]$VMName,
        [hashtable]$Control,
        [string]$TrustedDnSuffix,
        [string]$TrustedDomainName,
        [hashtable]$EmailConfig,
        [PSCredential]$EmailCredential,
        [hashtable]$DomainConfig
    )
    $results = @()
    Write-Log "Configuring $GroupType for '$GroupName'..." 'INFO'
    $groupObj = Get-QADGroup -Identity $GroupName -ErrorAction SilentlyContinue
    if (-not $groupObj) {
        $errorMsg = "ERROR: Group '$GroupName' not found."
        $results += $errorMsg
        Write-Log $errorMsg 'ERROR'
        return $results
    }
    
    $validationParams = @{
        Entries            = $IdentityList
        TrustedDnSuffix    = $TrustedDnSuffix
        TrustedDomainName  = $TrustedDomainName
        GroupName          = $GroupName
        VMDomain           = $VMDomain
        VMName             = $VMName
        EmailConfig        = $EmailConfig
        EmailCredential    = $EmailCredential
        DomainConfig       = $DomainConfig
    }
    $validationResult = Get-ValidatedIdentityList @validationParams
    $validatedMembers = $validationResult.Validated
    $results += $validationResult.Errors
    
    if ($GroupType -eq 'RDP' -and $VMDomain -eq 'V_Child1_Name') {
        $companyGroupMembers = @{}
        $newMembers = @()
        
        foreach ($UserOrGroup in $validatedMembers) {
            if ($UserOrGroup) {
                $addResult = Add-MemberToCompanyRdpGroup -UserOrGroup $UserOrGroup `
                                                          -VMName $VMName `
                                                          -Control $Control `
                                                          -DomainConfig $DomainConfig
                
                if ($addResult.Success) {
                    $results += $addResult.Message
                    $newMembers += $UserOrGroup
                    
                    if ($addResult.CompanyGroup) {
                        if (-not $companyGroupMembers.ContainsKey($addResult.CompanyGroup)) {
                            $companyGroupMembers[$addResult.CompanyGroup] = @()
                        }
                        
                        try {
                            $memberObj = Get-QADObject -Identity $UserOrGroup -ErrorAction SilentlyContinue
                            if ($memberObj -and $memberObj.DN) {
                                $companyGroupMembers[$addResult.CompanyGroup] += $memberObj.DN
                            }
                        } catch {
                            $companyGroupMembers[$addResult.CompanyGroup] += $UserOrGroup
                        }
                    }
                } else {
                    $results += $addResult.Message
                }
            }
        }
        
        $AllowedMainGroupDNs = @()
        $AllowedAllCompanyDNs = @()
        
        foreach ($entry in $validatedMembers) {
            if (-not $entry) { continue }
            
            $resolved = $null
            try {
                $resolved = Get-QADObject -Identity $entry -ErrorAction SilentlyContinue
            } catch {
                $resolved = $null
            }
            
            if ($resolved) {
                $memberDNs = @()
                if ($resolved -is [System.Array]) {
                    foreach ($r in $resolved) { 
                        if ($r.DN) { 
                            $memberDNs += $r.DN
                        } 
                    }
                } else {
                    if ($resolved.DN) { 
                        $memberDNs += $resolved.DN
                    }
                }
                
                $AllowedAllCompanyDNs += $memberDNs
                
                foreach ($dn in $memberDNs) {
                    $dnParts = $dn -split ','
                    if ($dnParts.Count -ge 4) {
                        $companyDC = $dnParts[-4] -replace 'DC=', ''
                        if ($companyDC.ToUpper() -eq 'V_Child1_Name') {
                            $AllowedMainGroupDNs += $dn
                        }
                    }
                }
            } else {
                if ($entry -match 'DC=') { 
                    $AllowedAllCompanyDNs += $entry
                    
                    $dnParts = $entry -split ','
                    if ($dnParts.Count -ge 4) {
                        $companyDC = $dnParts[-4] -replace 'DC=', ''
                        if ($companyDC.ToUpper() -eq 'V_Child1_Name') {
                            $AllowedMainGroupDNs += $entry
                        }
                    }
                }
            }
        }
        
        $AllowedMainGroupDNs = $AllowedMainGroupDNs | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique
        $AllowedAllCompanyDNs = $AllowedAllCompanyDNs | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique
        
        $syncResults = Sync-ADGroupMembership -GroupName $GroupName `
                                               -AllowedMembers $AllowedMainGroupDNs `
                                               -Control $Control `
                                               -VMDomain $VMDomain
        
        $syncResults.Added | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "Added $((Get-UserPrincipalName $_).ToUpper()) to RDP" 
        }
        $syncResults.Removed | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "Revoked access for $((Get-UserPrincipalName $_).ToUpper())."
        }
        $syncResults.AlreadyAssigned | Where-Object { $_ -and $_.Trim() } | ForEach-Object {
            if ($newMembers -notcontains $_) {
                $results += "User or group $((Get-UserPrincipalName $_).ToUpper()) is already assigned."
            }
        }
        $results += $syncResults.Errors
        
        foreach ($companyGroupName in $companyGroupMembers.Keys) {
            $allowedCompanyMembers = $companyGroupMembers[$companyGroupName] | Where-Object { $_ -and $_.Trim() }
            
            Write-Log "Sync RDP: $companyGroupName" 'INFO'
            
            $compGroupObj = Get-QADGroup -Identity $companyGroupName -ErrorAction SilentlyContinue
            if (-not $compGroupObj) {
                Write-Log "Group '$companyGroupName' not found" 'WARNING'
                continue
            }
            
            $compCurrentMembers = @(Get-QADGroupMember -Identity $companyGroupName -SizeLimit 100 -ErrorAction SilentlyContinue | ForEach-Object {
                if ($_ | Get-Member -Name DN -MemberType NoteProperty,Property) {
                    $_.DN
                }
            }) | Where-Object { $_ -and $_.Trim() }
            
            foreach ($member in $compCurrentMembers) {
                if ($member -and $member.Trim() -and -not ($allowedCompanyMembers -contains $member)) {
                    try {
                        $displayName = Resolve-SIDToUsername -Identity $member
                        if (-not $displayName) { $displayName = Get-UserPrincipalName $member }
                        
                        Remove-QADGroupMember -Identity $companyGroupName -Member $member -Confirm:$false -ErrorAction Stop -Control $Control
                        $results += "Revoked access for $($displayName.ToUpper())."
                        Write-Log "Removed '$displayName' from '$companyGroupName'" 'SUCCESS'
                    } catch {
                        $displayName = Resolve-SIDToUsername -Identity $member
                        if (-not $displayName) { $displayName = Get-UserPrincipalName $member }
                        $errorMsg = "Failed to remove '$displayName' from '$companyGroupName': $_"
                        $results += $errorMsg
                        Write-Log $errorMsg 'ERROR'
                    }
                }
            }
        }
        
        if ($DomainConfig.CompanyPrefixes) {
            foreach ($company in $DomainConfig.CompanyPrefixes) {
                $companyGroupName = $DomainConfig.CompanyRdpPattern -f $company, $VMName
                
                if ($companyGroupMembers.ContainsKey($companyGroupName)) {
                    continue
                }
                
                $compGroupObj = Get-QADGroup -Identity $companyGroupName -ErrorAction SilentlyContinue
                if (-not $compGroupObj) {
                    Write-Log "Group '$companyGroupName' not found" 'INFO'
                    continue
                }
                
                Write-Log "Sync RDP cleanup: $companyGroupName" 'INFO'
                
                $compMembers = @(Get-QADGroupMember -Identity $companyGroupName -SizeLimit 100 -ErrorAction SilentlyContinue | ForEach-Object {
                    if ($_ | Get-Member -Name DN -MemberType NoteProperty,Property) {
                        $_.DN
                    }
                }) | Where-Object { $_ -and $_.Trim() }
                
                foreach ($member in $compMembers) {
                    if ($member -and $member.Trim() -and -not ($AllowedAllCompanyDNs -contains $member)) {
                        try {
                            $displayName = Resolve-SIDToUsername -Identity $member
                            if (-not $displayName) { $displayName = Get-UserPrincipalName $member }
                            
                            Remove-QADGroupMember -Identity $companyGroupName -Member $member -Confirm:$false -ErrorAction Stop -Control $Control
                            $results += "Revoked access for $($displayName.ToUpper())."
                            Write-Log "Removed '$displayName' from '$companyGroupName'" 'SUCCESS'
                        } catch {
                            $displayName = Resolve-SIDToUsername -Identity $member
                            if (-not $displayName) { $displayName = Get-UserPrincipalName $member }
                            $errorMsg = "Failed to revoke access for '$displayName': $_"
                            $results += $errorMsg
                            Write-Log $errorMsg 'ERROR'
                        }
                    }
                }
            }
        }
        
    } elseif ($GroupType -eq 'RDP' -and $VMDomain -eq 'V_Child1_NetBIOS_old') {
        $syncResults = Sync-ADGroupMembership -GroupName $GroupName `
                                               -AllowedMembers $validatedMembers `
                                               -Control $Control `
                                               -VMDomain $VMDomain
        
        $syncResults.Added | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "Granted access for $((Get-UserPrincipalName $_).ToUpper())." 
        }
        $syncResults.Removed | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "Revoked access for $((Get-UserPrincipalName $_).ToUpper())." 
        }
        $syncResults.AlreadyAssigned | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "User or group $((Get-UserPrincipalName $_).ToUpper()) is already assigned." 
        }
        $results += $syncResults.Errors
        
    } else {
        $syncResults = Sync-ADGroupMembership -GroupName $GroupName `
                                               -AllowedMembers $validatedMembers `
                                               -Control $Control `
                                               -VMDomain $VMDomain
        
        $syncResults.Added | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "Granted access for $((Get-UserPrincipalName $_).ToUpper())." 
        }
        $syncResults.Removed | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "Revoked access for $((Get-UserPrincipalName $_).ToUpper())." 
        }
        $syncResults.AlreadyAssigned | Where-Object { $_ -and $_.Trim() } | ForEach-Object { 
            $results += "User or group $((Get-UserPrincipalName $_).ToUpper()) is already assigned." 
        }
        $results += $syncResults.Errors
    }
    
    Write-Log "$GroupType access config for '$GroupName' completed." 'SUCCESS'
    
    return $results
}

try {
    Write-Log "=== Starting AD Permission Config ===" 'INFO'
    Write-Log "VM: $VMName | Domain: $VMDomain | Order: $OrderID" 'INFO'
    @('VMName', 'VMDomain', 'OrderID') | ForEach-Object {
        if (-not (Get-Variable -Name $_ -ErrorAction SilentlyContinue).Value) {
            throw "CRITICAL: Required parameter '$_' is missing or empty."
        }
    }
    if (-not (Get-Variable -Name 'AdmIdentityList' -ErrorAction SilentlyContinue)) { $AdmIdentityList = "" }
    if (-not (Get-Variable -Name 'RdpIdentityList' -ErrorAction SilentlyContinue)) { $RdpIdentityList = "" }
    if ($OrderID -notmatch '^\d+$') { throw "CRITICAL: OrderID must be numeric. Received: $OrderID" }
    if (-not $Config.ContainsKey($VMDomain)) { throw "ERROR: Domain '$VMDomain' not supported or empty." }
    $DomainConfig = $Config[$VMDomain]
    $trustedDnSuffix = $DomainConfig.TrustedDnSuffix
    $trustedDomainName = $DomainConfig.TrustedDomainName
    $ADSecPasswd = ConvertTo-SecureString $DomainConfig.QADPW -AsPlainText -Force
    $ADCred = New-Object PSCredential($DomainConfig.QADUser, $ADSecPasswd)
    $MailSecPasswd = ConvertTo-SecureString $Config.Email.Password -AsPlainText -Force
    $MailCred = New-Object PSCredential($Config.Email.User, $MailSecPasswd)
    $maxRetries = 3
    $retryDelaySeconds = 5
    $connected = $false
    Write-Log "Connecting to AD: $($DomainConfig.QADServiceHostname)" 'INFO'
    for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
        try {
            Connect-QADService -Service $DomainConfig.QADServiceHostname -Credential $ADCred -Proxy -ErrorAction Stop | Out-Null
            $connected = $true
            Write-Log "Connected to AD (attempt $attempt/$maxRetries)" 'SUCCESS'
            break
        } catch {
            $errorMsg = "Failed to connect to Active Directory (attempt $attempt/$maxRetries): $_"
            
            if ($attempt -lt $maxRetries) {
                Write-Log "$errorMsg - Retrying in $retryDelaySeconds seconds..." 'WARNING'
                Start-Sleep -Seconds $retryDelaySeconds
            } else {
                Write-Log "$errorMsg - Max retries reached. Aborting." 'ERROR'
                throw "Unable to connect to AD service after $maxRetries attempts: $_"
            }
        }
    }
    
    if (-not $connected) {
        throw "Failed to establish AD connection."
    }
    
    $Reason = "V_Child1_Name Service Portal req $Snow_REQ item $Snow_RITM by VDI-SelfService order $OrderID."
    
    $Control = @{ }
    $Control.Add("OperationReason", $Reason)
    
    Write-Log "V_Child1_Namet Reason: $Reason" 'INFO'
    
    $admGroupName = $DomainConfig.AdminGroupPattern -f $VMName
    $rdpGroupName = $DomainConfig.RdpGroupPattern -f $VMName
    
    Write-Log "Admin Group: $admGroupName" 'INFO'
    Write-Log "RDP Group: $rdpGroupName" 'INFO'
    
    if ([string]::IsNullOrWhiteSpace($AdmIdentityList)) {
        $AdmCleanIdentityList = @()
        Write-Log "Admin Identities: (empty - will remove all)" 'WARNING'
    } else {
        $AdmCleanIdentityList = @($AdmIdentityList -split '[;,]' | ForEach-Object { ($_ -replace '.*\\','').Trim() } | Where-Object { $_ }) | Select-Object -Unique
        Write-Log "Admin Identities: $($AdmCleanIdentityList -join ', ')" 'INFO'
    }
    
    if ([string]::IsNullOrWhiteSpace($RdpIdentityList)) {
        $RdpCleanIdentityList = @()
        Write-Log "RDP Identities: (empty - will remove all)" 'WARNING'
    } else {
        $RdpCleanIdentityList = @($RdpIdentityList -split '[;,]' | ForEach-Object { ($_ -replace '.*\\','').Trim() } | Where-Object { $_ }) | Select-Object -Unique
        Write-Log "RDP Identities: $($RdpCleanIdentityList -join ', ')" 'INFO'
    }
    
    $ADResultsADM = Set-ADGroupAccess -GroupName $admGroupName `
                                      -IdentityList $AdmCleanIdentityList `
                                      -GroupType 'Admin' `
                                      -VMDomain $VMDomain `
                                      -VMName $VMName `
                                      -Control $Control `
                                      -TrustedDnSuffix $trustedDnSuffix `
                                      -TrustedDomainName $trustedDomainName `
                                      -EmailConfig $Config.Email `
                                      -EmailCredential $MailCred `
                                      -DomainConfig $DomainConfig
    
    $ADResultsRDP = Set-ADGroupAccess -GroupName $rdpGroupName `
                                      -IdentityList $RdpCleanIdentityList `
                                      -GroupType 'RDP' `
                                      -VMDomain $VMDomain `
                                      -VMName $VMName `
                                      -Control $Control `
                                      -TrustedDnSuffix $trustedDnSuffix `
                                      -TrustedDomainName $trustedDomainName `
                                      -EmailConfig $Config.Email `
                                      -EmailCredential $MailCred `
                                      -DomainConfig $DomainConfig
    
    $ADResultsADM = $ADResultsADM -replace '"', '' -replace "'", ''
    if ($ADResultsADM -and !($ADResultsADM | Where-Object { $_ -notmatch 'is already assigned' })) {
        $global:ADResultsADM = "OK - " + ($ADResultsADM -join '; ')
    } else { $global:ADResultsADM = $ADResultsADM -join '; ' }
    
    $ADResultsRDP = $ADResultsRDP -replace '"', '' -replace "'", ''
    if ($ADResultsRDP -and !($ADResultsRDP | Where-Object { $_ -notmatch 'is already assigned' })) {
        $global:ADResultsRDP = "OK - " + ($ADResultsRDP -join '; ')
    } else { $global:ADResultsRDP = $ADResultsRDP -join '; ' }
    
    Write-Log "Admin Results: $global:ADResultsADM" 'INFO'
    Write-Log "RDP Results: $global:ADResultsRDP" 'INFO'
    
    try {
        $safeADM = ($global:ADResultsADM -replace "'''","'").Replace("'", "''")
        $safeRDP = ($global:ADResultsRDP -replace "'''","'").Replace("'", "''")
        
        $updateQuery = @"
UPDATE VDIOrders
SET ADResultsADM = N'$safeADM',
    ADResultsRDP = N'$safeRDP'
WHERE ID = ${OrderID}
"@
        
        Invoke-SafeSqlCommand -Query $updateQuery -SqlConfig $Config.SQL -OperationName "Update VDIOrders with AD results"
        Write-Log "Stored ADResults for Order $OrderID in VDIOrders." 'SUCCESS'
    } catch {
        Write-Log "Failed to store ADResults for Order ${OrderID}: $_" 'ERROR'
    }
    
    Disconnect-QADService
    
    Write-Log "=== AD Permission Config Completed ===" 'SUCCESS'
    
} catch {
    $criticalError = $_
    $errorMessage = $criticalError.Exception.Message
    $errorStackTrace = $criticalError.ScriptStackTrace
    
    Write-Log "CRITICAL ERROR: $errorMessage" 'ERROR'
    Write-Log $errorStackTrace 'ERROR'
    
    try {
        $qsSuffix = if ($env:COMPUTERNAME -eq 'V_Child1_NameINSA5118') { ' (QS)' } else { '' }
        $subject = "Order $OrderID - CRITICAL: VDI Permission Config Failed"
        
        $emailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice WatchDog Alert$qsSuffix</title>
</head>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000000; margin: 0; padding: 0;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px;">
                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background: #fff;">
                    <tr>
                        <td style="background: #BB0A30; padding: 20px; text-align: center;">
                            <h1 style="color: #fff; font-size: 20pt; margin: 0; font-weight: bold;">V_Child1_Name VDI WatchDog Alert$qsSuffix</h1>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background: #F8D7DA; border-left: 4px solid #BB0A30;">
                                <tr>
                                    <td>
                                        <p style="color: #BB0A30; font-size: 14pt; font-weight: bold; margin: 0 0 10px 0;">⚠ KRITISCHER FEHLER</p>
                                        <p style="margin: 0;">Berechtigungskonfiguration für VM <strong style="color: #BB0A30;">$VMName</strong> fehlgeschlagen.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <h2 style="color: #BB0A30; font-size: 14pt; margin: 0 0 10px 0;">Fehlerdetails</h2>
                            <table width="100%" cellpadding="4" cellspacing="0" border="0" style="border-collapse: collapse;">
                                <tr>
                                    <td width="40%" style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Order ID</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;"><strong style="color: #BB0A30;">$OrderID</strong></td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">VM Name</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$VMName</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Domain</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$VMDomain</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">SNOW Request</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$Snow_REQ</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">SNOW RITM</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$Snow_RITM</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Zeitstempel</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')</td>
                                </tr>
                                <tr>
                                    <td style="padding: 4px; font-weight: bold; color: #666666; vertical-align: top;">Fehlermeldung</td>
                                    <td style="padding: 4px; color: #721c24; font-family: 'Courier New', monospace; font-size: 10pt; background-color: #f8f9fa;">$($errorMessage -replace '<', '&lt;' -replace '>', '&gt;')</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <h3 style="color: #666666; font-size: 12pt; margin: 0 0 10px 0;">Stack Trace</h3>
                            <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px; font-family: 'Courier New', monospace; font-size: 9pt; color: #495057; overflow-x: auto; max-height: 200px; overflow-y: auto;">
                                <pre style="margin: 0; white-space: pre-wrap; word-wrap: break-word;">$($errorStackTrace -replace '<', '&lt;' -replace '>', '&gt;')</pre>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background-color: #FFF3CD; border-left: 4px solid #856404;">
                                <tr>
                                    <td>
                                        <h3 style="color: #856404; margin: 0 0 10px 0; font-size: 12pt;">Erforderliche Maßnahmen</h3>
                                        <ul style="margin: 0; padding-left: 20px; color: #856404;">
                                            <li style="margin-bottom: 5px;">Überprüfen Sie Fehlermeldung und Stack Trace</li>
                                            <li style="margin-bottom: 5px;">Prüfen Sie AD Services Konnektivität</li>
                                            <li style="margin-bottom: 5px;">Führen Sie Konfiguration manuell durch</li>
                                            <li style="margin-bottom: 5px;">Aktualisieren Sie Order-Status in DB</li>
                                            <li>Informieren Sie Anforderer über Verzögerung</li>
                                        </ul>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background-color: #E8F4F8; border-left: 4px solid #0066CC;">
                                <tr>
                                    <td>
                                        <h3 style="color: #0066CC; margin: 0 0 10px 0; font-size: 12pt;">Log-Datei</h3>
                                        <p style="margin: 0; font-family: 'Courier New', monospace; font-size: 10pt; color: #495057;">
                                            $logFile
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 15px; text-align: center; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; font-size: 10pt;"><strong>V_Child1_Name VDI Watchdog</strong></p>
                            <p style="margin: 5px 0; font-size: 9pt; color: #666666;">Automatische Überwachung | Kritischer Fehler</p>
                            <p style="margin: 10px 0 0 0; font-size: 9pt; color: #999999;">
                                Support: <a href="mailto:V_Snap-Provision_EMailFromAddress" style="color: #BB0A30; text-decoration: none;">V_Snap-Provision_EMailFromAddress</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"@
        
        $toRecipient = $Config.Email.From
        if ($Config.Email.BccUser) {
            $bccParsed = $Config.Email.BccUser.Trim() -replace '^\@\(', '' -replace '\)$', ''
            $firstBcc = ($bccParsed -split ',' | ForEach-Object { 
                $_.Trim().Trim('"').Trim("'").Trim() 
            } | Where-Object { $_ -match '\S+@\S+\.\S+' } | Select-Object -First 1)
            
            if ($firstBcc) {
                $toRecipient = $firstBcc
            }
        }
        
        Send-NotificationEmail -Subject $subject -Body $emailBody -To $toRecipient -EmailConfig $Config.Email -Credential $MailCred
        Write-Log "Watchdog alert sent." 'INFO'
    } catch {
        Write-Log "Failed to send Watchdog alert: $_" 'WARNING'
    }
    
    try { Disconnect-QADService -ErrorAction SilentlyContinue } catch { }
    
    throw $criticalError
}

