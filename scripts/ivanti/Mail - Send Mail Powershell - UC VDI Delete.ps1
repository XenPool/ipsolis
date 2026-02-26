# Mail - Send Mail Powershell - UC VDI Delete
# Sends deletion notification emails for VDI client decommissioning

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

# ARS credentials
$arsuser = "^[QADUser]"
$arspassword = ConvertTo-SecureString '^[QADPW]' -AsPlainText -Force
$arsCredential = New-Object System.Management.Automation.PSCredential -ArgumentList $arsuser, $arspassword

Write-Log "Configuration loaded successfully" 'SUCCESS'
#endregion

#region Input Parameters
$UUID = "$[UUID]"
$Requestor = "$[Requestor]"
$Owner = "$[Owner]"
$VMName = "$[VMName]"
$DeactivationDatePlan = ([datetime]'$[DeactivationDatePlan]').ToString('dd.MM.yyyy')
$Snow_REQ = "$[Snow_REQ]"
$Snow_RITM = "$[Snow_RITM]"

Write-Log "Processing deletion notification for VM: ${VMName} (UUID: ${UUID})" 'INFO'
Write-Log "Deactivation date: ${DeactivationDatePlan}" 'INFO'
Write-Log "ServiceNow Request: ${Snow_REQ}, Item: ${Snow_RITM}" 'INFO'
#endregion

#region Database Query - Asset Information
Write-Log "Querying database for asset information..." 'INFO'

$assetQuery = @"
SELECT * FROM ${sqlAssetTable}
WHERE UUID = '${UUID}'
"@

try {
    $DBQueryResult = Invoke-Sqlcmd -Query $assetQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
    
    if ($null -eq $DBQueryResult) {
        throw "No asset found with UUID: ${UUID}"
    }
    
    $RDPUserIDs = $DBQueryResult.RDPUserIDs
    $LocalAdmins = $DBQueryResult.LocalAdmins
    $lifecycle = $DBQueryResult.LifeCycle
    $CreationDatePlan = ([datetime]$DBQueryResult.CreationDate).ToString('dd.MM.yyyy')
    
    Write-Log "Asset information retrieved successfully" 'SUCCESS'
    Write-Log "Creation date: ${CreationDatePlan}, Lifecycle: ${lifecycle} days" 'INFO'
} catch {
    Write-Log "Failed to query asset information: $_" 'ERROR'
    throw
}
#endregion

#region Email Subject Configuration
# Set subject based on environment (QS vs Production)
if ([System.Environment]::MachineName -eq "V_Child1_NameINSA5118") {
    $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - ${VMName} - Bereitstellungsbeendigung / end of assignment"
    Write-Log "Email subject set for QS environment" 'INFO'
} else {
    $subject = "V_Child1_Name VDI-Selfservice - ${VMName} - Bereitstellungsbeendigung / end of assignment"
    Write-Log "Email subject set for Production environment" 'INFO'
}
#endregion

#region Database Query - Use Case Information
Write-Log "Querying database for use case information..." 'INFO'

# Import SQLServer module
try {
    Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
    Write-Log "SQLServer module imported successfully" 'SUCCESS'
} catch {
    Write-Log "Failed to import SQLServer module: $_" 'WARNING'
}

$useCaseQuery = @"
SELECT ${sqlUseCaseTable}.UsecaseName, ${sqlUseCaseTable}.UsecaseDescription
FROM ${sqlAssetTable} CROSS JOIN ${sqlUseCaseTable}
WHERE ${sqlAssetTable}.UUID = '${UUID}' AND ${sqlUseCaseTable}.UsecaseID = ${sqlAssetTable}.UsecaseID
"@

try {
    $Result = Invoke-Sqlcmd -Query $useCaseQuery -ServerInstance $sqlServer -Username $sqlUsername -Password $sqlPassword -Database $sqlDatabase -ErrorAction Stop
    
    $usecaseid = $Result.UsecaseID
    $usecasename = $Result.UsecaseName
    $usecasedescription = $Result.UsecaseDescription
    
    Write-Log "Use case information retrieved: ${usecasename}" 'SUCCESS'
} catch {
    Write-Log "Failed to query use case information: $_" 'ERROR'
    $usecaseid = "N/A"
    $usecasename = "N/A"
    $usecasedescription = "N/A"
}
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

# Service Portal link will be replaced by deployment system
$ServicePortalLink = '^[ServicePortalURL]'

$EmailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice - Bereitstellungsbeendigung</title>
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
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Bereitstellungsbeendigung / End of Assignment</p>
                        </td>
                    </tr>
                    
                    <!-- Greeting - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Guten Tag $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 10px 0;">
                                die beantragte Beendigung der Bereitstellung und Löschung des VDI Client <strong>${VMName}</strong> wurde vermerkt. Am <strong>${DeactivationDatePlan}</strong> werden alle Zugriffsberechtigungen auf den VDI Client deaktiviert; ein Zugriff ist danach nicht mehr möglich.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                Sofern Sie den VDI Client länger benötigen, können Sie die Bereitstellung noch bis <strong>${DeactivationDatePlan}</strong> im ${ServicePortalLink} verlängern. Wählen Sie bitte hierzu die Antragsart <strong>Ändern</strong> und selektieren Sie aus der Liste den VDI Client <strong>${VMName}</strong> und legen Sie eine neue <strong>Mietdauer</strong> in Tagen fest.
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
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${usecaseid} ${usecasename}</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">VDI Beschreibung / description</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${usecasedescription}</td>
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
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${lifecycle} Tage / Days</td>
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
                                the requested termination and deletion of the VDI Client <strong>${VMName}</strong> has been recorded. On <strong>${DeactivationDatePlan}</strong>, all access permissions to the VDI Client will be deactivated, and access will no longer be possible thereafter.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                If you require the VDI Client for a longer period, you can extend the provision until <strong>${DeactivationDatePlan}</strong> via the ${ServicePortalLink}. Please select the <strong>Change</strong> request type, choose the VDI Client <strong>${VMName}</strong> from the list, and specify a new <strong>rental duration</strong> in days.
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
Write-Log "Sending deletion notification email..." 'INFO'

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
            throw "Unable to send email after ${maxEmailRetries} attempts: $_"
        }
    }
}

if (-not $emailSent) {
    throw "Failed to send deletion notification email"
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

Write-Log "=== VDI Deletion Notification Script Completed Successfully ===" 'SUCCESS'
#endregion