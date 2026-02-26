# Mail - Send Mail Powershell - @NotificationDatePlan
# Sends upcoming deactivation notification emails for VDI clients approaching their end-of-life date

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
Write-Log "=== Starting VDI Notification Script ===" 'INFO'

# Set security protocols for SMTP communication
[System.Net.ServicePointManager]::SecurityProtocol = 'Tls,TLS11,TLS12'
Write-Log "Security protocols configured: TLS, TLS 1.1, TLS 1.2" 'INFO'
    
# Service Portal link will be replaced by deployment system
$ServicePortalLink = '^[ServicePortalURL]'

# Email configuration
$mailuser = "^[EmailUser]"
$mailpassword = ConvertTo-SecureString '^[EMailUserPW]' -AsPlainText -Force
$Credential = New-Object System.Management.Automation.PSCredential -ArgumentList $mailuser, $mailpassword
$bccuser = ^[EmailBcc]
$emailfrom = "^[EmailFrom]"
$smtpServer = "^[EmailSMTPServer]"

# Active Directory service credentials
$arsuser = "^[QADUser]"
$arspassword = ConvertTo-SecureString '^[QADPW]' -AsPlainText -Force
$arsCredential = New-Object System.Management.Automation.PSCredential -ArgumentList $arsuser, $arspassword

# SQL configuration
$sqlServer = "^[SQLVDIServerInstance]"
$sqlDatabase = "^[SQLVDIDatabase]"
$sqlUsername = "^[SQLVDILoginUser]"
$sqlPassword = "^[SQLVDILoginPW]"
$sqlAssetTable = "^[SQLVDIAssetTable]"
$sqlUseCaseTable = "^[SQLVDIUseCaseTable]"
$sqlPoolTable = "^[SQLVDIPoolTable]"

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
        Connect-QADService -Service "^[QADServiceHostname]" -Proxy -Credential $arsCredential -ErrorAction Stop | Out-Null
        $connected = $true
        Write-Log "Successfully connected to QAD service" 'SUCCESS'
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
        [string]$Role  # "Requestor" or "Owner"
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
    
    try {
        Write-Log "Retrieving AD information for ${Role}: ${Identity}" 'INFO'
        $adUser = Get-QADUser -Identity $Identity -Properties * -ErrorAction Stop
        
        if ($adUser) {
            $userInfo['Surname'] = $adUser.lastname
            $userInfo['Givenname'] = $adUser.givenname
            $userInfo['Department'] = $adUser.department
            $userInfo['Email'] = $adUser.mail
            
            Write-Log "${Role} information retrieved: $($userInfo['Givenname']) $($userInfo['Surname']) ($($userInfo['Email']))" 'SUCCESS'
        } else {
            throw "${Role} '${Identity}' not found in Active Directory"
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

#region Database Query - Get VDI Assets Requiring Notification
Write-Log "Querying database for VDI assets requiring notification..." 'INFO'

$Query = "SELECT * FROM ${sqlAssetTable} WHERE (NotificationDatePlan <= GETDATE() AND Notified IS NULL)"

try {
    $Results = Invoke-Sqlcmd -Query $Query -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
    
    if ($Results) {
        Write-Log "Found $(@($Results).Count) VDI asset(s) requiring notification" 'SUCCESS'
    } else {
        Write-Log "No VDI assets requiring notification found" 'INFO'
        Disconnect-QADService -ErrorAction SilentlyContinue
        Write-Log "=== VDI Notification Script Completed ===" 'SUCCESS'
        exit 0
    }
} catch {
    Write-Log "Failed to query VDI assets for notification: $_" 'ERROR'
    throw
}
#endregion

#region Process Each VDI Asset for Notification
Write-Log "Processing VDI assets for notification..." 'INFO'

foreach ($Result in $Results) {
    # Extract result data
    $UUID = $Result.UUID
    $VMName = $Result.VMName
    $UsecaseID = $Result.UsecaseID
    $CreationDatePlan = $Result.CreationDate.ToString('dd.MM.yyyy')
    $DeactivationDatePlan = $Result.DeactivationDatePlan.ToString('dd.MM.yyyy')
    $Requestor = $Result.Requestor
    $Owner = $Result.Owner
    $RDPUserIDs = if ($Result.RDPUserIDs) { $Result.RDPUserIDs } else { "" }
    $LocalAdmins = if ($Result.LocalAdmins) { $Result.LocalAdmins } else { "" }
    $LifeCycle = $Result.LifeCycle
    $Snow_REQ = $Result.Snow_REQ
    $Snow_RITM = $Result.Snow_RITM

    Write-Log "========================================" 'INFO'
    Write-Log "Processing VM for notification: ${VMName} (UUID: ${UUID})" 'INFO'
    Write-Log "Deactivation date: ${DeactivationDatePlan}" 'INFO'

    #region Get Use Case Information
    Write-Log "Retrieving use case information for Use Case ID: ${UsecaseID}..." 'INFO'
    
    try {
        $useCaseQuery = "SELECT * FROM ${sqlUseCaseTable} WHERE UsecaseID = ${UsecaseID}"
        $TypeResult = Invoke-Sqlcmd -Query $useCaseQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
        
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
        $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - ${VMName} - Ablauf der Bereitstellung / End of provision period"
        Write-Log "Email subject set for QS environment" 'INFO'
    } else {
        $subject = "V_Child1_Name VDI-Selfservice - ${VMName} - Ablauf der Bereitstellung / End of provision period"
        Write-Log "Email subject set for Production environment" 'INFO'
    }
    #endregion

    #region Retrieve User Information
    Write-Log "Retrieving requestor and owner information from Active Directory..." 'INFO'
    
    $RequestorInfo = Get-UserInformation -Identity $Requestor -Role "Requestor"
    $OwnerInfo = Get-UserInformation -Identity $Owner -Role "Owner"
    #endregion

    #region Resolve RDP Users
    Write-Log "Resolving RDP users..." 'INFO'
    $RDPUserIDsUserOrGroupName = Get-UserIDs -UserIDs $RDPUserIDs
    
    if ($RDPUserIDsUserOrGroupName.Count -gt 0) {
        Write-Log "RDP Users: $($RDPUserIDsUserOrGroupName.UserIDs -join ', ')" 'INFO'
    }
    
    # Format RDP user list for email body
    if ([string]::IsNullOrWhiteSpace($RDPUserIDs)) {
        $RDPUserList = ""
        Write-Log "No RDP users to display" 'INFO'
    } else {
        $RDPUserList = $RDPUserIDs -replace ";", "; "
        Write-Log "RDP user list prepared: ${RDPUserList}" 'INFO'
    }
    #endregion

    #region Resolve Admin Users
    Write-Log "Resolving Admin users..." 'INFO'
    $ADMUserIDsUserOrGroupName = Get-UserIDs -UserIDs $LocalAdmins
    
    if ($ADMUserIDsUserOrGroupName.Count -gt 0) {
        Write-Log "Admin Users: $($ADMUserIDsUserOrGroupName.UserIDs -join ', ')" 'INFO'
    }
    
    # Format Admin user list for email body
    if ([string]::IsNullOrWhiteSpace($LocalAdmins)) {
        $ADMUserList = ""
        Write-Log "No Admin users to display" 'INFO'
    } else {
        $ADMUserList = $LocalAdmins -replace ";", "; "
        Write-Log "Admin user list prepared: ${ADMUserList}" 'INFO'
    }
    #endregion

    #region Email Body Template
    Write-Log "Building email body with dynamic content..." 'INFO'
    
    $EmailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice - Bevorstehende Beendigung</title>
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
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Ablauf der Bereitstellung / End of provision period</p>
                        </td>
                    </tr>
                    
                    <!-- Greeting - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Guten Tag $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 10px 0;">
                                der Bereitstellungszeitraum des VDI Client <strong>${VMName}</strong> endet in Kürze. Die aktuell vorhandenen RDP Zugriffsberechtigungen und die administrativen Berechtigungen auf den VDI Client werden daher <strong>planmäßig</strong> am <strong>${DeactivationDatePlan}</strong> deaktiviert.
                            </p>
                            <p style="margin: 0 0 10px 0;">
                                Sie können im ${ServicePortalLink} der Bereitstellungszeitraum des VDI Client verlängern. Wählen Sie hierzu bitte die Antragsart <strong>Ändern</strong> und selektieren Sie in der Liste den VDI Client <strong>${VMName}</strong> und ändern Sie die Miet-Dauer.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                <strong>Beachten Sie:</strong> Wird die Bereitstellung vor dem o.g. Deaktivierungsdatum nicht verlängert, erfolgt im Nachgang die automatische dauerhafte Löschung des VDI Client mit allen Programmen und Dateien.
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
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">RDP Berechtigung / Permissions</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${RDPUserList}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Admin Berechtigung / Permissions</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${ADMUserList}</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Bereitstellungsdatum / Date of Provisioning</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${CreationDatePlan}</td>
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
                                the rental period of the VDI Client <strong>${VMName}</strong> will soon expire. The current RDP access permissions, as well as the administrative rights for the VDI Client, will therefore be deactivated as <strong>scheduled</strong> on <strong>${DeactivationDatePlan}</strong>.
                            </p>
                            <p style="margin: 0 0 10px 0;">
                                You can extend the rental period of the VDI Client via the ${ServicePortalLink}. Please select the <strong>Change</strong> request type, choose the VDI Client <strong>${VMName}</strong> from the list, and adjust the rental duration.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                <strong>Please note:</strong> If the provisioning is not extended before the above-mentioned deactivation date, the VDI Client, along with all programs and files, will be permanently deleted automatically thereafter.
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
    
    # Fallback: If no valid recipient emails found, use BCC and add warning to email body
    if ($To.Count -eq 0) {
        Write-Log "No valid recipient email addresses found. Using BCC recipients as fallback." 'WARNING'
        $To = $bccuser
        $EmailBody = "<p><strong><span style='color: #ff0000;'>Achtung, Requestor UND Owner E-Mail Adresse konnte im AD nicht gefunden werden.</span></strong></p>" + $EmailBody
    } else {
        Write-Log "Email recipients: $($To -join ', ')" 'INFO'
    }
    #endregion

    #region Send Email with Retry Logic
    Write-Log "Sending upcoming deactivation notification email..." 'INFO'
    
    $maxEmailRetries = 3
    $emailRetryDelay = 5  # seconds
    $emailSent = $false
    
    for ($attempt = 1; $attempt -le $maxEmailRetries; $attempt++) {
        try {
            Write-Log "Email send attempt ${attempt} of ${maxEmailRetries}..." 'INFO'
            
            Send-MailMessage -UseSsl `
                -Port 25 `
                -To $To `
                -Bcc $bccuser `
                -From $emailfrom `
                -Subject $subject `
                -Body $EmailBody `
                -BodyAsHtml `
                -SmtpServer $smtpServer `
                -Credential $Credential `
                -Encoding ([System.Text.Encoding]::UTF8) `
                -Priority High `
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
                Write-Log "Skipping database update for VM: ${VMName}" 'WARNING'
                continue  # Skip to next VDI asset
            }
        }
    }
    
    if (-not $emailSent) {
        Write-Log "Failed to send email for VM: ${VMName}. Skipping this asset." 'ERROR'
        continue  # Skip to next VDI asset
    }
    #endregion

    #region Update Database - Mark Asset as Notified
    Write-Log "Updating database to mark asset as notified..." 'INFO'
    
    try {
        # Get current date for notification timestamp
        $NotifiedDate = Get-Date -Format "yyyy-MM-dd"
        
        # Update asset table - mark as notified
        $updateAssetQuery = "UPDATE ${sqlAssetTable} SET Notified = '${NotifiedDate}' WHERE UUID = '${UUID}'"
        Invoke-Sqlcmd -Query $updateAssetQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
        Write-Log "Asset table updated successfully - marked as Notified for UUID: ${UUID}" 'SUCCESS'
        
        # Update pool table - mark VM as notified
        $updatePoolQuery = "UPDATE ${sqlPoolTable} SET Status = 'Notified' WHERE AssetUUID = '${UUID}' AND VMName = '${VMName}' AND Status = 'Occupied'"
        Invoke-Sqlcmd -Query $updatePoolQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
        Write-Log "Pool table updated successfully - marked as Notified for VM: ${VMName}" 'SUCCESS'
        
    } catch {
        Write-Log "Failed to update database for UUID ${UUID}: $_" 'ERROR'
    }
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

Write-Log "=== VDI Notification Script Completed Successfully ===" 'SUCCESS'
#endregion