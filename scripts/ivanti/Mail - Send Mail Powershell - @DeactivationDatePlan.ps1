# Mail - Send Mail Powershell - @DeactivationDatePlan
# Sends deactivation notification emails for VDI clients that have reached their end-of-life date

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
Write-Log "=== Starting VDI Deactivation Notification Script ===" 'INFO'

# Set security protocols for SMTP communication
[System.Net.ServicePointManager]::SecurityProtocol = 'Tls,TLS11,TLS12'
Write-Log "Security protocols configured: TLS, TLS 1.1, TLS 1.2" 'INFO'

# Service Portal link will be replaced by deployment system
$ServicePortalLink = '^[ServicePortalURL]'

# Domain and service configuration
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
        LoginUser      = '^[SQLVDILoginUser]'
        LoginPW        = '^[SQLVDILoginPW]'
        AssetTable     = '^[SQLVDIAssetTable]'
        UseCaseTable   = '^[SQLVDIUseCaseTable]'
        PoolTable      = '^[SQLVDIPoolTable]'
    }
    Email = @{
        SmtpServer = '^[EmailSMTPServer]'
        From       = '^[EmailFrom]'
        BccUser    = ^[EmailBcc]  # Already an array from deployment system
        User       = '^[EmailUser]'
        Password   = '^[EMailUserPW]'
    }
}

# Email credentials
$mailpassword = ConvertTo-SecureString $Config.Email.Password -AsPlainText -Force
$Credential = New-Object System.Management.Automation.PSCredential -ArgumentList $Config.Email.User, $mailpassword

Write-Log "Configuration loaded successfully" 'SUCCESS'
#endregion

#region Active Directory Connection with Retry Logic
Write-Log "Connecting to Active Directory service..." 'INFO'

$maxRetries = 3
$retryDelay = 10  # seconds
$connected = $false

for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
    try {
        Write-Log "QAD connection attempt ${attempt} of ${maxRetries}..." 'INFO'
        
        # Initially connect to V_Child1_NetBIOS_old for queries
        $arsuser = $Config.V_Child1_NetBIOS_old.QADUser
        $arspassword = ConvertTo-SecureString $Config.V_Child1_NetBIOS_old.QADPW -AsPlainText -Force
        $arsCredential = New-Object System.Management.Automation.PSCredential -ArgumentList $arsuser, $arspassword
        
        Connect-QADService -Service $Config.V_Child1_NetBIOS_old.QADServiceHostname -Proxy -Credential $arsCredential -ErrorAction Stop | Out-Null
        $connected = $true
        Write-Log "Successfully connected to QAD service (V_Child1_NetBIOS_old)" 'SUCCESS'
        break
    } catch {
        $errorMsg = "Failed to connect to QAD service: $_"
        
        if ($attempt -lt $maxRetries) {
            Write-Log "${errorMsg} - Retrying in ${retryDelay} seconds..." 'WARNING'
            Start-Sleep -Seconds $retryDelay
        } else {
            Write-Log "${errorMsg} - Max retries reached" 'ERROR'
            throw "Unable to connect to QAD service after ${maxRetries} attempts: $_"
        }
    }
}

if (-not $connected) {
    throw "Failed to establish QAD service connection"
}
#endregion

#region Function: Get User Information from AD
function Get-UserInformation {
    param(
        [string]$Identity,
        [string]$Role,  # "Requestor" or "Owner"
        [string]$VMDomain  # Current VM domain to determine correct AD
    )
    
    # Initialize user information hashtable
    $userInfo = @{
        Surname = ""
        Givenname = ""
        Department = ""
        Email = ""
    }
    
    if (-not $Identity) {
        Write-Log "${Role} is not specified" 'WARNING'
        return $userInfo
    }
    
    # Get domain configuration for filtering
    $domainConfig = if ($Config.ContainsKey($VMDomain)) { $Config[$VMDomain] } else { $Config.V_Child1_NetBIOS_old }
    $trustedDnSuffix = $domainConfig.TrustedDnSuffix
    
    try {
        Write-Log "Retrieving AD information for ${Role}: ${Identity} (Domain: ${VMDomain})" 'INFO'
        $adUsers = @(Get-QADUser -Identity $Identity -Properties * -ErrorAction Stop)
        
        if ($adUsers.Count -eq 0) {
            throw "${Role} '${Identity}' not found in Active Directory"
        }
        
        # Filter users by DN suffix to get the correct domain
        $adUser = $adUsers | Where-Object { $_.DN -like "*,$trustedDnSuffix" } | Select-Object -First 1
        
        if (-not $adUser) {
            Write-Log "User '${Identity}' found but not in domain ${VMDomain} (${trustedDnSuffix})" 'WARNING'
            Write-Log "Available DNs: $($adUsers | ForEach-Object { $_.DN } | Out-String)" 'INFO'
            # Fallback to first user if domain filter doesn't match
            $adUser = $adUsers[0]
            Write-Log "Using fallback user from: $($adUser.DN)" 'WARNING'
        } else {
            Write-Log "User found in correct domain: $($adUser.DN)" 'SUCCESS'
        }
        
        if ($adUser) {
            $userInfo['Surname'] = $adUser.lastname
            $userInfo['Givenname'] = $adUser.givenname
            $userInfo['Department'] = $adUser.department
            $userInfo['Email'] = $adUser.mail
            
            Write-Log "${Role} information retrieved: $($userInfo['Givenname']) $($userInfo['Surname']) ($($userInfo['Email']))" 'SUCCESS'
        } else {
            throw "${Role} '${Identity}' could not be resolved"
        }
    } catch {
        Write-Log "Failed to retrieve ${Role} information: $_" 'ERROR'
    }
    
    return $userInfo
}
#endregion

#region Function: Get User IDs and Resolve Groups
function Get-UserIDs {
    param (
        [string]$UserIDs
    )
    
    # Initialize list for resolved users
    $UserIDsUserOrGroupName = New-Object System.Collections.Generic.List[System.Object]
    $UserIDsList = $UserIDs.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries)
    
    Write-Log "Resolving $($UserIDsList.Count) user/group entries..." 'INFO'
    
    # Resolve each user or group
    foreach ($UserID in $UserIDsList) {
        $trimmedID = $UserID.Trim()
        if (-not $trimmedID) { continue }
        
        Write-Log "Searching for user: ${trimmedID}" 'INFO'
        
        try {
            # Try to resolve as user
            $userinformation = Get-QADUser -Identity $trimmedID -Properties * -ErrorAction SilentlyContinue
            
            if ($userinformation) {
                $UserIDsUserOrGroupName.Add($trimmedID)
                Write-Log "Resolved as user: ${trimmedID}" 'SUCCESS'
            } else {
                # Try to resolve as group
                Write-Log "User not found, searching for group: ${trimmedID}" 'INFO'
                
                try {
                    $GroupMembers = @(Get-QADGroupMember -Indirect -Identity $trimmedID -ErrorAction Stop)
                    
                    if ($GroupMembers.Count -gt 0) {
                        foreach ($GroupMember in $GroupMembers) {
                            $UserIDsUserOrGroupName.Add($GroupMember.Name)
                        }
                        Write-Log "Resolved as group with $($GroupMembers.Count) members: ${trimmedID}" 'SUCCESS'
                    } else {
                        Write-Log "Group found but has no members: ${trimmedID}" 'WARNING'
                    }
                } catch {
                    Write-Log "Could not find group: ${trimmedID} - $_" 'WARNING'
                }
            }
        } catch {
            Write-Log "Error resolving identity '${trimmedID}': $_" 'ERROR'
        }
    }
    
    # Remove duplicates
    $UserIDsUserOrGroupName = $UserIDsUserOrGroupName | Select-Object -Unique
    
    Write-Log "Total unique users resolved: $($UserIDsUserOrGroupName.Count)" 'INFO'
    
    return @{
        Count = $UserIDsUserOrGroupName.Count
        UserIDs = $UserIDsUserOrGroupName
    }
}
#endregion

#region Database Query - Get Expired VDI Assets
Write-Log "Querying database for expired VDI assets..." 'INFO'

$Query = @"
SELECT 
    A.UUID, 
    A.VMName, 
    A.UsecaseID, 
    A.Requestor, 
    A.Owner, 
    A.Snow_REQ, 
    A.Snow_RITM, 
    A.LifeCycle, 
    A.Deactivated, 
    A.DeactivationDatePlan, 
    A.CreationDate,
    P.VMName AS PoolVMName, 
    P.Status,
    P.Domain
FROM $($Config.SQL.AssetTable) A
INNER JOIN $($Config.SQL.PoolTable) P 
    ON A.VMName = P.VMName
WHERE 
    A.DeactivationDatePlan < GETDATE() 
    AND A.Deactivated IS NULL 
    AND P.Status = 'Notified' 
    AND P.AssetUUID = A.UUID
"@

try {
    $Results = Invoke-Sqlcmd -Query $Query `
        -ServerInstance $Config.SQL.ServerInstance `
        -Database $Config.SQL.Database `
        -Username $Config.SQL.LoginUser `
        -Password $Config.SQL.LoginPW `
        -ErrorAction Stop
    
    if ($Results) {
        Write-Log "Found $(@($Results).Count) expired VDI asset(s) to process" 'SUCCESS'
    } else {
        Write-Log "No expired VDI assets found" 'INFO'
        Disconnect-QADService -ErrorAction SilentlyContinue
        Write-Log "=== VDI Deactivation Notification Script Completed ===" 'SUCCESS'
        exit 0
    }
} catch {
    Write-Log "Failed to query expired VDI assets: $_" 'ERROR'
    throw
}
#endregion

#region Process Each Expired VDI Asset
Write-Log "Processing expired VDI assets..." 'INFO'

foreach ($Result in $Results) {
    # Extract result data
    $UUID = $Result.UUID
    $VMName = $Result.VMName
    $UsecaseID = $Result.UsecaseID
    $CreationDate = $Result.CreationDate.ToString('dd.MM.yyyy')
    $DeactivationDatePlan = $Result.DeactivationDatePlan.ToString('dd.MM.yyyy')
    $Requestor = $Result.Requestor
    $Owner = $Result.Owner
    $LifeCycle = $Result.LifeCycle
    $Snow_REQ = $Result.Snow_REQ
    $Snow_RITM = $Result.Snow_RITM
    $VMDomain = $Result.Domain

    Write-Log "========================================" 'INFO'
    Write-Log "Processing VM: ${VMName} (UUID: ${UUID})" 'INFO'
    Write-Log "Domain: ${VMDomain}" 'INFO'
    Write-Log "Deactivation date: ${DeactivationDatePlan}" 'INFO'

    #region Determine Domain Configuration and Connect
    Write-Log "Determining domain-specific configuration for: ${VMDomain}..." 'INFO'
    
    $domainConfig = $null
    if ($Config.ContainsKey($VMDomain)) {
        $domainConfig = $Config[$VMDomain]
        Write-Log "Using ${VMDomain} domain configuration" 'INFO'
    } else {
        Write-Log "Unknown domain '${VMDomain}' - defaulting to V_Child1_NetBIOS_old configuration" 'WARNING'
        $domainConfig = $Config.V_Child1_NetBIOS_old
        $VMDomain = 'V_Child1_NetBIOS_old'
    }
    
    # Reconnect to the correct domain service if needed
    try {
        Write-Log "Connecting to ${VMDomain} QAD service..." 'INFO'
        Disconnect-QADService -ErrorAction SilentlyContinue
        
        $arsuser = $domainConfig.QADUser
        $arspassword = ConvertTo-SecureString $domainConfig.QADPW -AsPlainText -Force
        $arsCredential = New-Object System.Management.Automation.PSCredential -ArgumentList $arsuser, $arspassword
        
        Connect-QADService -Service $domainConfig.QADServiceHostname -Proxy -Credential $arsCredential -ErrorAction Stop | Out-Null
        Write-Log "Successfully connected to ${VMDomain} QAD service" 'SUCCESS'
    } catch {
        Write-Log "Failed to connect to ${VMDomain} QAD service: $_" 'ERROR'
        Write-Log "Skipping VM: ${VMName}" 'WARNING'
        continue
    }
    #endregion

    #region Determine Group Names Based on Domain
    Write-Log "Determining AD group names for domain: ${VMDomain}..." 'INFO'
    
    # Admin group
    $GroupADM = $domainConfig.AdminGroupPattern -f $VMName
    Write-Log "Admin Group: ${GroupADM}" 'INFO'
    
    # RDP groups (including company-specific groups for V_Child1_Name domain)
    $RdpGroups = @()
    
    # Main RDP group
    $GroupVDI = $domainConfig.RdpGroupPattern -f $VMName
    $RdpGroups += $GroupVDI
    Write-Log "Main RDP Group: ${GroupVDI}" 'INFO'
    
    # For V_Child1_Name domain, add company-specific RDP groups
    if ($VMDomain -eq 'V_Child1_Name' -and $domainConfig.ContainsKey('CompanyRdpPattern')) {
        foreach ($company in $domainConfig.CompanyPrefixes) {
            $companyRdpGroup = $domainConfig.CompanyRdpPattern -f $company, $VMName
            $RdpGroups += $companyRdpGroup
            Write-Log "Company RDP Group (${company}): ${companyRdpGroup}" 'INFO'
        }
    }
    #endregion

    #region Get Use Case Information
    Write-Log "Retrieving use case information for Use Case ID: ${UsecaseID}..." 'INFO'
    
    try {
        $useCaseQuery = "SELECT * FROM $($Config.SQL.UseCaseTable) WHERE UsecaseID = ${UsecaseID}"
        $TypeResult = Invoke-Sqlcmd -Query $useCaseQuery `
            -ServerInstance $Config.SQL.ServerInstance `
            -Database $Config.SQL.Database `
            -Username $Config.SQL.LoginUser `
            -Password $Config.SQL.LoginPW `
            -ErrorAction Stop
        
        if ($TypeResult) {
            $UsecaseName = $TypeResult.UsecaseName
            $UsecaseDescription = $TypeResult.UsecaseDescription
            Write-Log "Use case information retrieved: ${UsecaseName}" 'SUCCESS'
        } else {
            Write-Log "No use case found for ID: ${UsecaseID}" 'WARNING'
            $UsecaseName = "N/A"
            $UsecaseDescription = "N/A"
        }
    } catch {
        Write-Log "Failed to retrieve use case information: $_" 'ERROR'
        $UsecaseName = "N/A"
        $UsecaseDescription = "N/A"
    }
    #endregion

    #region Email Subject Configuration
    # Set subject based on environment (QS vs Production)
    if ([System.Environment]::MachineName -eq "V_Child1_NameINSA5118") {
        $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - ${VMName} - Deaktivierung und Deprovisionierung / deactivation and deprovisioning"
        Write-Log "Email subject set for QS environment" 'INFO'
    } else {
        $subject = "V_Child1_Name VDI-Selfservice - ${VMName} - Deaktivierung und Deprovisionierung / deactivation and deprovisioning"
        Write-Log "Email subject set for Production environment" 'INFO'
    }
    #endregion

    #region Retrieve User Information
    Write-Log "Retrieving requestor and owner information from Active Directory..." 'INFO'
    
    $RequestorInfo = Get-UserInformation -Identity $Requestor -Role "Requestor" -VMDomain $VMDomain
    $OwnerInfo = Get-UserInformation -Identity $Owner -Role "Owner" -VMDomain $VMDomain
    #endregion

    #region Email Body Template
    Write-Log "Building email body with dynamic content..." 'INFO'
    
    $EmailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice - Deaktivierung</title>
</head>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000000; margin: 0; padding: 0;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px;">
                <table width="814" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border: 1px solid #e0e0e0;">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #BB0A30; padding: 20px; text-align: center;">
                            <h1 style="color: #ffffff; font-size: 18pt; margin: 0; font-weight: bold;">V_Child1_Name VDI-Selfservice</h1>
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Deaktivierung und Deprovisionierung / Deactivation and Deprovisioning</p>
                        </td>
                    </tr>
                    
                    <!-- Greeting - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Guten Tag $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 10px 0;">
                                der Bereitstellungszeitraum des VDI Client <strong>${VMName}</strong> ist beendet. Die Berechtigungen wurden (wie in unserer letzten E-Mail angekündigt) deaktiviert.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                Sie können im ${ServicePortalLink} einen neuen VDI Client beantragen. Wählen Sie hierzu bitte die Antragsart <strong>Neu</strong>.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Details Table Header -->
                    <tr>
                        <td style="padding: 10px 30px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #E5E5E5;">
                                <tr>
                                    <td style="padding: 12px; text-align: center;">
                                        <h2 style="margin: 0; font-size: 14pt; color: #BB0A30; font-weight: bold;">ZUSAMMENFASSUNG / SUMMARY</h2>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Details Table Content -->
                    <tr>
                        <td style="padding: 0 30px 20px 30px;">
                            <table width="100%" cellpadding="8" cellspacing="0" border="0" style="border-collapse: collapse; border: 2px solid #666666;">
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; width: 45%; color: #666666;">Computername</td>
                                    <td style="border-bottom: 1px solid #666666; width: 55%; color: #666666;">${VMName}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">VDI Variante / variant</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${UsecaseID} ${UsecaseName}</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">VDI Beschreibung / description</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${UsecaseDescription}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Bereitstellungs ID / Provisioning ID</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${UUID}</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">V_Child1_Name MyServe REQ ID</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${Snow_REQ}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">V_Child1_Name MyServe RITM ID</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${Snow_RITM}</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Besteller / Customer</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">$($RequestorInfo['Surname']), $($RequestorInfo['Givenname']) ($($RequestorInfo['Department']))</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Nutzende / Recipient</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">$($OwnerInfo['Surname']), $($OwnerInfo['Givenname']) ($($OwnerInfo['Department']))</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Bereitstellungsdatum / Date of Provisioning</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${CreationDate}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Bereitstellungszeitraum / Provision Period</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${LifeCycle} Tage / Days</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Deaktivierungsdatum / Date of Deactivation</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${DeactivationDatePlan}</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer German -->
                    <tr>
                        <td style="padding: 15px 30px 25px 30px; border-bottom: 2px solid #e0e0e0;">
                            <p style="margin: 0;">Mit freundlichen Grüßen</p>
                            <p style="margin: 5px 0 0 0; font-weight: bold;">V_Child1_Name Mitarbeiter IT</p>
                            <p style="margin: 0; color: #666666;">Client Services</p>
                        </td>
                    </tr>
                    
                    <!-- Divider -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <hr style="border: none; border-top: 2px solid #BB0A30; margin: 0;" />
                        </td>
                    </tr>
                    
                    <!-- Greeting - English -->
                    <tr>
                        <td style="padding: 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Good day $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 10px 0;">
                                your access to the VDI client <strong>${VMName}</strong> has been deactivated as planned.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                You can request a new VDI in the ${ServicePortalLink}. To do this, select the request type <strong>New</strong>.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer English -->
                    <tr>
                        <td style="padding: 15px 30px 25px 30px;">
                            <p style="margin: 0;">Kind regards</p>
                            <p style="margin: 5px 0 0 0; font-weight: bold;">V_Child1_Name Mitarbeiter IT</p>
                            <p style="margin: 0; color: #666666;">Client Services</p>
                        </td>
                    </tr>
                    
                    <!-- Bottom Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 15px; text-align: center; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; font-size: 9pt; color: #666666;">V_Child1_Name VDI-Selfservice | V_Child1_Name Mitarbeiter IT | Client Services</p>
                            <p style="margin: 5px 0 0 0; font-size: 9pt; color: #999999;">$(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')</p>
                        </td>
                    </tr>
                    
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"@
    
    Write-Log "Email body prepared successfully" 'SUCCESS'
    #endregion

    #region Determine Email Recipients
    Write-Log "Determining email recipients..." 'INFO'
    
    # Collect unique recipient email addresses
    $To = @($RequestorInfo['Email'], $OwnerInfo['Email']) | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique
    
    # Ensure $To is always an array
    if ($To -isnot [array]) {
        $To = @($To)
    }
    
    # Fallback: If no valid recipient emails found, use BCC and add warning to email body
    if ($To.Count -eq 0) {
        Write-Log "No valid recipient email addresses found. Using BCC recipients as fallback." 'WARNING'
        $To = $Config.Email.BccUser
        $EmailBody = "<p><strong><span style='color: #ff0000;'>Achtung, Requestor UND Owner E-Mail Adresse konnte im AD nicht gefunden werden.</span></strong></p>" + $EmailBody
    } else {
        Write-Log "Email recipients: $($To -join ', ')" 'INFO'
        Write-Log "Recipient count: $($To.Count)" 'INFO'
    }
    #endregion

    #region Send Email with Retry Logic
    Write-Log "Sending deactivation notification email..." 'INFO'
    
    $maxEmailRetries = 3
    $emailRetryDelay = 5  # seconds
    $emailSent = $false
    
    for ($attempt = 1; $attempt -le $maxEmailRetries; $attempt++) {
        try {
            Write-Log "Email send attempt ${attempt} of ${maxEmailRetries}..." 'INFO'
            Write-Log "To: $($To -join ', ')" 'INFO'
            Write-Log "Bcc: $($Config.Email.BccUser -join ', ')" 'INFO'
            
            Send-MailMessage -UseSsl `
                -Port 25 `
                -To $To `
                -Bcc $Config.Email.BccUser `
                -From $Config.Email.From `
                -Subject $subject `
                -Body $EmailBody `
                -BodyAsHtml `
                -SmtpServer $Config.Email.SmtpServer `
                -Credential $Credential `
                -Encoding ([System.Text.Encoding]::UTF8) `
                -ErrorAction Stop
            
            $emailSent = $true
            Write-Log "Email sent successfully to: $($To -join ', ')" 'SUCCESS'
            Write-Log "Subject: ${subject}" 'INFO'
            break
            
        } catch {
            $errorMsg = "Failed to send email: $_"
            
            if ($attempt -lt $maxEmailRetries) {
                Write-Log "${errorMsg} - Retrying in ${emailRetryDelay} seconds..." 'WARNING'
                Start-Sleep -Seconds $emailRetryDelay
            } else {
                Write-Log "${errorMsg} - Max retries reached" 'ERROR'
                Write-Log "Skipping database update and group cleanup for VM: ${VMName}" 'WARNING'
                continue  # Skip to next VDI asset
            }
        }
    }
    
    if (-not $emailSent) {
        Write-Log "Failed to send email for VM: ${VMName}. Skipping this asset." 'ERROR'
        continue  # Skip to next VDI asset
    }
    #endregion

    #region Update Database - Mark Asset as Deactivated
    Write-Log "Updating database to mark asset as deactivated..." 'INFO'
    
    try {
        # Update asset table
        $updateAssetQuery = "UPDATE $($Config.SQL.AssetTable) SET Deactivated = '@[DATETIME(YYYY-MM-DD)]', AssetStatus = 'Deactivated' WHERE UUID = '${UUID}'"
        Invoke-Sqlcmd -Query $updateAssetQuery `
            -ServerInstance $Config.SQL.ServerInstance `
            -Database $Config.SQL.Database `
            -Username $Config.SQL.LoginUser `
            -Password $Config.SQL.LoginPW `
            -ErrorAction Stop
        Write-Log "Asset table updated successfully for UUID: ${UUID}" 'SUCCESS'
        
        # Update pool table
        $updatePoolQuery = "UPDATE $($Config.SQL.PoolTable) SET Status = 'Deactivated' WHERE AssetUUID = '${UUID}' AND VMName = '${VMName}' AND Status = 'Notified'"
        Invoke-Sqlcmd -Query $updatePoolQuery `
            -ServerInstance $Config.SQL.ServerInstance `
            -Database $Config.SQL.Database `
            -Username $Config.SQL.LoginUser `
            -Password $Config.SQL.LoginPW `
            -ErrorAction Stop
        Write-Log "Pool table updated successfully for VM: ${VMName}" 'SUCCESS'
        
    } catch {
        Write-Log "Failed to update database for UUID ${UUID}: $_" 'ERROR'
    }
    #endregion

    #region Remove AD Group Members
    Write-Log "Removing all members from AD groups for VM: ${VMName}..." 'INFO'
    
    # Define operation reason for V_Child1_Namet trail
    $Reason = "VDI-SelfService provisioning of asset ${UUID} has ended according to plan. VDI access has been deactivated."
    $Control = @{}
    $Control.Add("OperationReason", $Reason)
    
    # Remove members from all RDP groups
    foreach ($RdpGroup in $RdpGroups) {
        try {
            Write-Log "Checking RDP group: ${RdpGroup}..." 'INFO'
            $rdpMembers = @(Get-QADGroupMember -Identity $RdpGroup -ErrorAction SilentlyContinue)
            
            if ($rdpMembers.Count -gt 0) {
                Write-Log "Removing $($rdpMembers.Count) member(s) from RDP group: ${RdpGroup}..." 'INFO'
                $rdpMembers | Remove-QADGroupMember -Identity $RdpGroup -Control $Control -Confirm:$false -ErrorAction Stop | Out-Null
                Write-Log "Successfully removed all members from RDP group: ${RdpGroup}" 'SUCCESS'
            } else {
                Write-Log "No members found in RDP group: ${RdpGroup}" 'INFO'
            }
        } catch {
            if ($_.Exception.Message -like "*not found*") {
                Write-Log "RDP group does not exist: ${RdpGroup}" 'INFO'
            } else {
                Write-Log "Failed to remove members from RDP group ${RdpGroup}: $_" 'ERROR'
            }
        }
    }
    
    # Remove members from Admin group
    try {
        Write-Log "Checking Admin group: ${GroupADM}..." 'INFO'
        $admMembers = @(Get-QADGroupMember -Identity $GroupADM -ErrorAction SilentlyContinue)
        
        if ($admMembers.Count -gt 0) {
            Write-Log "Removing $($admMembers.Count) member(s) from Admin group: ${GroupADM}..." 'INFO'
            $admMembers | Remove-QADGroupMember -Identity $GroupADM -Control $Control -Confirm:$false -ErrorAction Stop | Out-Null
            Write-Log "Successfully removed all members from Admin group: ${GroupADM}" 'SUCCESS'
        } else {
            Write-Log "No members found in Admin group: ${GroupADM}" 'INFO'
        }
    } catch {
        if ($_.Exception.Message -like "*not found*") {
            Write-Log "Admin group does not exist: ${GroupADM}" 'INFO'
        } else {
            Write-Log "Failed to remove members from Admin group ${GroupADM}: $_" 'ERROR'
        }
    }
    
    Write-Log "Completed AD group cleanup for VM: ${VMName}" 'SUCCESS'
    #endregion

    Write-Log "Completed processing for VM: ${VMName}" 'SUCCESS'
    Write-Log "========================================" 'INFO'
}
#endregion

#region Cleanup
Write-Log "Disconnecting from QAD service..." 'INFO'
try {
    Disconnect-QADService -ErrorAction SilentlyContinue
    Write-Log "Disconnected from QAD service" 'SUCCESS'
} catch {
    Write-Log "Error during QAD disconnect: $_" 'WARNING'
}

Write-Log "=== VDI Deactivation Notification Script Completed Successfully ===" 'SUCCESS'
#endregion