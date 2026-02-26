# Mail - Send Mail Powershell - @DeletionDatePlan
# Sends deletion notification emails for VDI clients that have reached their deletion date and marks them for recycling

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
Write-Log "=== Starting VDI Deletion Notification Script ===" 'INFO'

# Set security protocols for SMTP communication
[System.Net.ServicePointManager]::SecurityProtocol = 'Tls,TLS11,TLS12'
Write-Log "Security protocols configured: TLS, TLS 1.1, TLS 1.2" 'INFO'

# Email configuration
$mailuser = "^[EmailUser]"
$mailpassword = ConvertTo-SecureString '^[EMailUserPW]' -AsPlainText -Force
$Credential = New-Object System.Management.Automation.PSCredential -ArgumentList $mailuser, $mailpassword
$bccuser = ^[EmailBcc]
$emailfrom = "^[EmailFrom]"
$smtpServer = "^[EmailSMTPServer]"

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

#region Database Query - Get VDI Assets Ready for Deletion
Write-Log "Querying database for VDI assets ready for deletion..." 'INFO'

$Query = @"
SELECT 
    A.UUID, 
    A.VMName, 
    A.UsecaseID, 
    A.Requestor, 
    A.Owner, 
    A.RDPUserIDs, 
    A.LocalAdmins,
    A.Snow_REQ, 
    A.Snow_RITM, 
    A.LifeCycle, 
    A.Deactivated, 
    A.DeactivationDatePlan, 
    A.CreationDate,
    P.VMName AS PoolVMName, 
    P.Status
FROM ${sqlAssetTable} A
INNER JOIN ${sqlPoolTable} P 
    ON A.VMName = P.VMName
WHERE 
    A.DeletionDatePlan <= GETDATE() 
    AND A.Deleted IS NULL 
    AND P.Status = 'Deactivated' 
    AND P.AssetUUID = A.UUID
"@

try {
    $Results = Invoke-Sqlcmd -Query $Query -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
    
    if ($Results) {
        Write-Log "Found $(@($Results).Count) VDI asset(s) ready for deletion" 'SUCCESS'
    } else {
        Write-Log "No VDI assets ready for deletion found" 'INFO'
        Write-Log "=== VDI Deletion Notification Script Completed ===" 'SUCCESS'
        exit 0
    }
} catch {
    Write-Log "Failed to query VDI assets for deletion: $_" 'ERROR'
    throw
}
#endregion

#region Process Each VDI Asset for Deletion
Write-Log "Processing VDI assets for deletion..." 'INFO'

foreach ($Result in $Results) {
    # Extract result data
    $UUID = $Result.UUID
    $VMName = $Result.VMName
    $UsecaseID = $Result.UsecaseID
    $CreationDate = $Result.CreationDate.ToString('dd.MM.yyyy')
    $DeactivationDatePlan = $Result.DeactivationDatePlan.ToString('dd.MM.yyyy')
    $Requestor = $Result.Requestor
    $Owner = $Result.Owner
    $RDPUserIDs = if ($Result.RDPUserIDs) { $Result.RDPUserIDs } else { "" }
    $LocalAdmins = if ($Result.LocalAdmins) { $Result.LocalAdmins } else { "" }
    $LifeCycle = $Result.LifeCycle
    $Snow_REQ = $Result.Snow_REQ
    $Snow_RITM = $Result.Snow_RITM

    Write-Log "========================================" 'INFO'
    Write-Log "Processing VM for deletion: ${VMName} (UUID: ${UUID})" 'INFO'
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
        $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - ${VMName} - Löschung / deletion"
        Write-Log "Email subject set for QS environment" 'INFO'
    } else {
        $subject = "V_Child1_Name VDI-Selfservice - ${VMName} - Löschung / deletion"
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
    <title>V_Child1_Name VDI-Selfservice - Systemnachricht Löschung</title>
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
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Systemnachricht - Löschung / System Message - Deletion</p>
                        </td>
                    </tr>
                    
                    <!-- System Warning - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <div style="background-color: #FFF3CD; border-left: 4px solid #BB0A30; padding: 15px; margin-bottom: 20px;">
                                <p style="margin: 0 0 10px 0; font-weight: bold; color: #BB0A30; font-size: 12pt;">ACHTUNG - Systemnachricht</p>
                                <p style="margin: 0; color: #856404;">
                                    Der VDI-Client <strong>${VMName}</strong> wurde für die Neuinstallation eingeplant, da die Mietperiode des dazugehörigen Asset <strong>${UUID}</strong> beendet ist.
                                </p>
                            </div>
                            <p style="margin: 0 0 10px 0;">
                                Die Neuinstallation der VM startet voraussichtlich <strong>heute 17:00 Uhr</strong> automatisch.
                            </p>
                            <p style="margin: 0 0 15px 0; font-size: 10pt; color: #666666; font-style: italic;">
                                Um diesen Vorgang abzubrechen, kann der Status der VM in der Tabelle <code>${sqlPoolTable}</code> der Datenbank <code>${sqlDatabase}</code> innerhalb der Instanz <code>${sqlServer}</code> auf einen anderen Wert als <strong>Recycle.Bin</strong> geändert werden.
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
                    
                    <!-- System Warning - English -->
                    <tr>
                        <td style="padding: 15px 30px;">
                            <div style="background-color: #FFF3CD; border-left: 4px solid #BB0A30; padding: 15px; margin-bottom: 20px;">
                                <p style="margin: 0 0 10px 0; font-weight: bold; color: #BB0A30; font-size: 12pt;">ATTENTION - System Message</p>
                                <p style="margin: 0; color: #856404;">
                                    The VDI client <strong>${VMName}</strong> has been scheduled for reinstallation as the rental period of the associated asset <strong>${UUID}</strong> has ended.
                                </p>
                            </div>
                            <p style="margin: 0 0 10px 0;">
                                The reinstallation of the VM will start automatically at approximately <strong>5:00 PM today</strong>.
                            </p>
                            <p style="margin: 0 0 15px 0; font-size: 10pt; color: #666666; font-style: italic;">
                                To cancel this process, the VM status in table <code>${sqlPoolTable}</code> of database <code>${sqlDatabase}</code> within instance <code>${sqlServer}</code> can be changed to a value other than <strong>Recycle.Bin</strong>.
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

    #region Send Email with Retry Logic
    Write-Log "Sending deletion notification email..." 'INFO'
    
    $maxEmailRetries = 3
    $emailRetryDelay = 5  # seconds
    $emailSent = $false
    
    for ($attempt = 1; $attempt -le $maxEmailRetries; $attempt++) {
        try {
            Write-Log "Email send attempt ${attempt} of ${maxEmailRetries}..." 'INFO'
            
            Send-MailMessage -UseSsl `
                -Port 25 `
                -To $bccuser `
                -From $emailfrom `
                -Subject $subject `
                -Body $EmailBody `
                -BodyAsHtml `
                -SmtpServer $smtpServer `
                -Credential $Credential `
                -Encoding ([System.Text.Encoding]::UTF8) `
                -ErrorAction Stop
            
            $emailSent = $true
            Write-Log "Email sent successfully to: ${bccuser}" 'SUCCESS'
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

    #region Update Database - Mark Asset for Deletion
    Write-Log "Updating database to mark asset for deletion..." 'INFO'
    
    try {
        # Update asset table - mark as deleted/expired
        $updateAssetQuery = "UPDATE ${sqlAssetTable} SET Deleted = '@[DATETIME(YYYY-MM-DD)]', AssetStatus = 'Expired' WHERE UUID = '${UUID}'"
        Invoke-Sqlcmd -Query $updateAssetQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
        Write-Log "Asset table updated successfully - marked as Expired for UUID: ${UUID}" 'SUCCESS'
        
        # Update pool table - mark VM for recycling
        $updatePoolQuery = "UPDATE ${sqlPoolTable} SET Status = 'Recycle.Bin', Snow_REQ = NULL, Snow_RITM = NULL WHERE AssetUUID = '${UUID}' AND VMName = '${VMName}' AND Status = 'Deactivated'"
        Invoke-Sqlcmd -Query $updatePoolQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
        Write-Log "Pool table updated successfully - marked for Recycle.Bin for VM: ${VMName}" 'SUCCESS'
        
    } catch {
        Write-Log "Failed to update database for UUID ${UUID}: $_" 'ERROR'
    }
    #endregion

    Write-Log "Completed processing for VM: ${VMName}" 'SUCCESS'
    Write-Log "========================================" 'INFO'
}
#endregion

#region Cleanup
Write-Log "=== VDI Deletion Notification Script Completed Successfully ===" 'SUCCESS'
#endregion