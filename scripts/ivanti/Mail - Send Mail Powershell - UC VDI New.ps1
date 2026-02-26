# Mail - Send Mail Powershell - UC VDI New
# Sends provisioning notification emails for new VDI client assignments

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
Write-Log "=== Starting VDI New Assignment Notification Script ===" 'INFO'

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

# SQL configuration
$sqlServer = "^[SQLVDIServerInstance]"
$sqlDatabase = "^[SQLVDIDatabase]"
$sqlUsername = "^[SQLVDILoginUser]"
$sqlPassword = "^[SQLVDILoginPW]"
$sqlAssetTable = "^[SQLVDIAssetTable]"
$sqlUseCaseTable = "^[SQLVDIUseCaseTable]"

Write-Log "Configuration loaded successfully" 'SUCCESS'
#endregion

#region Input Parameters
$UUID = "$[UUID]"
$Requestor = "$[Requestor]"
$Owner = "$[Owner]"
$RDPUserIDs = "$[RDPUserIDs]"
$LocalAdmins = "$[LocalAdmins]"
$VMName = "$[VMName]"
$CreationDatePlan = ([DateTime]'$[CreationDatePlan]').ToString('dd.MM.yyyy')
$DeactivationDatePlan = ([DateTime]'$[DeactivationDatePlan]').ToString('dd.MM.yyyy')
$DeletionDatePlan = ([DateTime]'$[DeactivationDatePlan]').AddDays(7)
$DeletionDatePlan = ([DateTime]${DeletionDatePlan}).ToString('dd.MM.yyyy')
$UsecaseID = "$[UsecaseID]"
$UsecaseName = "$[UseCaseName]"
$UsecaseDescription = "$[UseCaseDescription]"
$LifeCycle = "$[LifeCycle]"
$Snow_REQ = "$[Snow_REQ]"
$Snow_RITM = "$[Snow_RITM]"
$OrderID = "$[OrderID]"

Write-Log "Processing new VDI assignment notification for VM: ${VMName} (UUID: ${UUID})" 'INFO'
Write-Log "Creation date: ${CreationDatePlan}, Deactivation date: ${DeactivationDatePlan}" 'INFO'
Write-Log "ServiceNow Request: ${Snow_REQ}, Item: ${Snow_RITM}" 'INFO'
Write-Log "Order ID: ${OrderID}" 'INFO'
#endregion

#region Active Directory Connection - Not Required
# Note: AD connection removed as we only count parameters without validation
Write-Log "Skipping AD connection - using parameter count only" 'INFO'
#endregion

#region Function: Convert Log to HTML Table
function Convert-LogToHtmlTable {
    param (
        [Parameter(Mandatory)]
        [string]$InputText,

        [Parameter(Mandatory)]
        [string]$Headline
    )
    
    Write-Log "Converting log entries to HTML table: ${Headline}" 'INFO'
    
    # Split input text into rows and create HTML table rows
    $rows = (($InputText -split "`r?`n") | Where-Object { $_.Trim() -ne "" } | ForEach-Object {
        $p = $_.Split(' ', 2)

        $col1 = if ($p.Count -ge 1) { $p[0] } else { "" }
        $col2 = if ($p.Count -ge 2) { $p[1] } else { "" }

        "<tr>" +
        "<td style='vertical-align:top;padding-top:0;margin-top:0;'>${col1}</td>" +
        "<td style='vertical-align:top;padding-top:0;margin-top:0;'>${col2}</td>" +
        "</tr>"
    }) -join ""

    # Build complete HTML table
    $html = "<h4><strong><span style='color:#bb0a30;'>${Headline}&nbsp;</span></strong></h4>" +
            "<table style='width: 729px; border-collapse:collapse;border:none;font-family:""Arial"",monospace;font-size:11px;white-space:pre;'>" +
            $rows +
            "</table>"

    return $html
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

#region Function: Count User IDs and Groups
function Get-UserIDCount {
    param (
        [string]$UserIDs
    )
    
    if ([string]::IsNullOrWhiteSpace($UserIDs)) {
        Write-Log "No user/group entries provided" 'INFO'
        return @{
            Count = 0
            UserIDs = @()
        }
    }
    
    # Split on semicolon and filter empty entries
    $UserIDsList = @($UserIDs -split '[;,]' | ForEach-Object { ($_ -replace '.*\\','').Trim() } | Where-Object { $_ })
    
    Write-Log "Counted $($UserIDsList.Count) user/group entries" 'INFO'
    
    return @{
        Count = $UserIDsList.Count
        UserIDs = $UserIDsList
    }
}
#endregion

#region Function: Create RDP File
function New-RDPFile {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory = $true, Position = 0)]
        [System.String]$target,
        
        [Parameter(Mandatory = $false, Position = 1)]
        [System.String]$outputdirectory = 'C:\users\Public\Desktop\'
    )
    
    Write-Log "Creating RDP file for target: ${target}" 'INFO'
    
    # Define RDP file content with optimal settings
    $rdp = @"
screen mode id:i:1
use multimon:i:0
desktopwidth:i:1680
desktopheight:i:1050
session bpp:i:32
winposstr:s:0,3,44,161,1403,1050
compression:i:1
keyboardhook:i:2
V_Child1_Nameocapturemode:i:0
videoplaybackmode:i:1
connection type:i:7
networkautodetect:i:1
bandwidthautodetect:i:1
displayconnectionbar:i:1
enableworkspacereconnect:i:0
disable wallpaper:i:0
allow font smoothing:i:0
allow desktop composition:i:0
disable full window drag:i:1
disable menu anims:i:1
disable themes:i:0
disable cursor setting:i:0
bitmapcachepersistenable:i:1
full address:s:${target}
V_Child1_Nameomode:i:0
redirectprinters:i:1
redirectcomports:i:0
redirectsmartcards:i:1
redirectclipboard:i:1
redirectposdevices:i:0
autoreconnection enabled:i:1
authentication level:i:2
prompt for credentials:i:0
negotiate security layer:i:1
remoteapplicationmode:i:0
alternate shell:s:
shell working directory:s:
gatewayhostname:s:
gatewayusagemethod:i:4
gatewaycredentialssource:i:4
gatewayprofileusagemethod:i:0
promptcredentialonce:i:0
gatewaybrokeringtype:i:0
use redirection server name:i:0
rdgiskdcproxy:i:0
kdcproxyname:s:
smart sizing:i:1
drivestoredirect:s:
"@
    
    try {
        # Create RDP file with the specified target and output directory
        $rdpFilePath = Join-Path $outputdirectory "${target}.rdp"
        $rdp | Out-File -FilePath $rdpFilePath -Encoding ASCII -Force
        Write-Log "RDP file created successfully: ${rdpFilePath}" 'SUCCESS'
    } catch {
        Write-Log "Failed to create RDP file: $_" 'ERROR'
        throw
    }
}
#endregion

#region Database Query - AD Results
Write-Log "Querying database for AD configuration results (Order ID: ${OrderID})..." 'INFO'

try {
    $readQuery = "SELECT ADResultsADM, ADResultsRDP FROM VDIOrders WHERE ID = ${OrderID}"
    $dbRes = Invoke-Sqlcmd -Query $readQuery -ServerInstance $sqlServer -Username $sqlUsername -Password $sqlPassword -Database $sqlDatabase -ErrorAction Stop

    if ($null -ne $dbRes) {
        # Get results and trim trailing semicolons
        $ADResultsADM = ($dbRes.ADResultsADM -as [string]).TrimEnd(';').Trim()
        $ADResultsRDP = ($dbRes.ADResultsRDP -as [string]).TrimEnd(';').Trim()
        Write-Log "AD results retrieved from database" 'SUCCESS'
    } else {
        Write-Log "No AD results found for Order ID: ${OrderID}" 'WARNING'
        $ADResultsADM = ""
        $ADResultsRDP = ""
    }
} catch {
    Write-Log "Failed to read AD results for Order ${OrderID}: $_" 'ERROR'
    $ADResultsADM = ""
    $ADResultsRDP = ""
}
#endregion

#region Database Query - Use Case and Asset Information
Write-Log "Querying database for VDI asset and use case information..." 'INFO'

$assetQuery = @"
SELECT 
    A.*, 
    U.UsecaseName,
    U.UsecaseDescription
FROM ${sqlAssetTable} A
LEFT JOIN ${sqlUseCaseTable} U 
    ON A.UsecaseID = U.UsecaseID
WHERE A.UUID = '${UUID}'
"@

try {
    $Result = Invoke-Sqlcmd -Query $assetQuery -ServerInstance $sqlServer -Database $sqlDatabase -Username $sqlUsername -Password $sqlPassword -ErrorAction Stop
    
    if ($Result) {
        # Direct value assignment from query result
        $LifeCycle = $Result.LifeCycle
        $UseCaseID = $Result.UsecaseID
        $UseCaseName = $Result.UsecaseName
        $UseCaseDescription = $Result.UsecaseDescription
        
        Write-Log "Use case information retrieved: ${UseCaseName} (ID: ${UseCaseID})" 'SUCCESS'
        Write-Log "Lifecycle: ${LifeCycle} days" 'INFO'
    } else {
        Write-Log "No asset information found for UUID: ${UUID}" 'WARNING'
    }
} catch {
    Write-Log "Failed to query asset/use case information: $_" 'ERROR'
    throw
}
#endregion

#region Email Subject Configuration
# Set subject based on environment (QS vs Production)
if ([System.Environment]::MachineName -eq "V_Child1_NameINSA5118") {
    $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - ${VMName} - Neue Bereitstellung / new assignment"
    Write-Log "Email subject set for QS environment" 'INFO'
} else {
    $subject = "V_Child1_Name VDI-Selfservice - ${VMName} - Neue Bereitstellung / new assignment"
    Write-Log "Email subject set for Production environment" 'INFO'
}
#endregion

#region Retrieve User Information
Write-Log "Retrieving requestor and owner information from Active Directory..." 'INFO'

$RequestorInfo = Get-UserInformation -Identity $Requestor -Role "Requestor"
$OwnerInfo = Get-UserInformation -Identity $Owner -Role "Owner"
#endregion

#region Count RDP Users
Write-Log "Counting RDP users..." 'INFO'
$RDPUserIDsUserOrGroupName = Get-UserIDCount -UserIDs $RDPUserIDs

if ($RDPUserIDsUserOrGroupName.Count -gt 0) {
    Write-Log "RDP Users: $($RDPUserIDsUserOrGroupName.UserIDs -join ', ')" 'INFO'
    $RDPUserList = $RDPUserIDs -replace ";", "; "
} else {
    $RDPUserList = ""
    Write-Log "No RDP users to display" 'INFO'
}
#endregion

#region Count Admin Users
Write-Log "Counting Admin users..." 'INFO'
$ADMUserIDsUserOrGroupName = Get-UserIDCount -UserIDs $LocalAdmins

if ($ADMUserIDsUserOrGroupName.Count -gt 0) {
    Write-Log "Admin Users: $($ADMUserIDsUserOrGroupName.UserIDs -join ', ')" 'INFO'
    $ADMUserList = $LocalAdmins -replace ";", "; "
} else {
    $ADMUserList = ""
    Write-Log "No Admin users to display" 'INFO'
}
#endregion

#region Process AD Results - Admin Permissions
$ADResultsADMHasIssues = $false
$ADResultsADMArray = @()

if (-not [string]::IsNullOrWhiteSpace($ADResultsADM)) {
    Write-Log "Processing AD results for Admin permissions..." 'INFO'
    
    # Split on semicolon and filter out empty entries
    $ADResultsADMArray = @($ADResultsADM -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    
    if ($ADResultsADMArray.Count -gt 0) {
        # Check if there are any unusual entries (not just "Granted" or "Revoked")
        foreach ($entry in $ADResultsADMArray) {
            $entryLower = $entry.ToLower()
            # Check if entry contains ONLY normal operations (granted, revoked, or already assigned)
            if (-not ($entryLower -match 'granted access' -or $entryLower -match 'revoked access' -or $entryLower -match 'is already assigned' -or $entryLower -match '^ok -')) {
                $ADResultsADMHasIssues = $true
                Write-Log "Unusual Admin permission entry detected: ${entry}" 'WARNING'
                break
            }
        }
        
        if ($ADResultsADMHasIssues) {
            Write-Log "Admin change journal contains unusual entries - flagged for attention" 'WARNING'
        } else {
            Write-Log "Admin change journal contains only normal operations (granted/revoked)" 'SUCCESS'
        }
    }
} else {
    Write-Log "No Admin AD results available" 'INFO'
}
#endregion

#region Process AD Results - RDP Permissions
$ADResultsRDPHasIssues = $false
$ADResultsRDPArray = @()

if (-not [string]::IsNullOrWhiteSpace($ADResultsRDP)) {
    Write-Log "Processing AD results for RDP permissions..." 'INFO'
    
    # Split on semicolon and filter out empty entries
    $ADResultsRDPArray = @($ADResultsRDP -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    
    if ($ADResultsRDPArray.Count -gt 0) {
        # Check if there are any unusual entries (not just "Granted" or "Revoked")
        foreach ($entry in $ADResultsRDPArray) {
            $entryLower = $entry.ToLower()
            # Check if entry contains ONLY normal operations (granted, revoked, or already assigned)
            if (-not ($entryLower -match 'granted access' -or $entryLower -match 'revoked access' -or $entryLower -match 'is already assigned' -or $entryLower -match '^ok -')) {
                $ADResultsRDPHasIssues = $true
                Write-Log "Unusual RDP permission entry detected: ${entry}" 'WARNING'
                break
            }
        }
        
        if ($ADResultsRDPHasIssues) {
            Write-Log "RDP change journal contains unusual entries - flagged for attention" 'WARNING'
        } else {
            Write-Log "RDP change journal contains only normal operations (granted/revoked)" 'SUCCESS'
        }
    }
} else {
    Write-Log "No RDP AD results available" 'INFO'
}
#endregion

#region Email Body Template
$EmailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice - Neue Bereitstellung</title>
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
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Neue Bereitstellung / New Assignment</p>
                        </td>
                    </tr>
                    
                    <!-- Greeting - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Guten Tag $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 10px 0;">
                                die V_Child1_Name Service Portal Bestellung <strong>${Snow_RITM}</strong> (${UsecaseName}) wurde bearbeitet, und der bestellte VDI-Client <strong>${VMName}</strong> steht Ihnen ab sofort zur Verfügung.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                Für den Zugriff auf den VDI-Client nutzen Sie bitte die App Remotedesktopverbindung (mstsc.exe) oder öffnen Sie die RDP-Verbindungsdatei im Anhang dieser E-Mail.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Usage Instructions - German -->
                    <tr>
                        <td style="padding: 0 30px 15px 30px;">
                            <p style="margin: 0 0 10px 0; font-weight: bold;">Bitte beachten Sie die folgenden Nutzungshinweise:</p>
"@

# Add warning if there are permission issues
if ($ADResultsADMHasIssues -or $ADResultsRDPHasIssues) {
    $EmailBody += @"
                            <p style="margin: 0 0 15px 0; padding: 10px; background-color: #FFF3CD; border-left: 4px solid #BB0A30; color: #856404;">
                                <strong style="color: #BB0A30;">&#9888; Achtung:</strong> Die Berechtigungszuweisung für die virtuelle Maschine erfordert Ihre Aufmerksamkeit. Bitte lesen Sie die detaillierten Hinweise am Ende der Tabelle.
                            </p>
"@
}

$EmailBody += @"
                            <p style="margin: 0 0 5px 0;">Der VDI-Client …</p>
                            <ul style="margin: 5px 0 15px 20px; padding: 0; line-height: 1.6;">
                                <li style="margin-bottom: 5px;">ist Ihnen für einen Bereitstellungszeitraum von <strong>${LifeCycle} Tagen</strong> zugeteilt</li>
                                <li style="margin-bottom: 5px;">wurde für die Gruppe mit RDP-Berechtigung aus dem Intranet oder über VPN freigeschaltet</li>
                                <li style="margin-bottom: 5px;">unterliegt denselben Regelungen (insbesondere den <a href="https://volkswagengroup.sharepoint.com/sites/V_Child1_NameMynet-ITSecurity/" target="_blank" style="color: #BB0A30; text-decoration: none;">IT-Sicherheitsbestimmungen</a>) wie Notebooks und Desktops</li>
                                <li style="margin-bottom: 5px;">wird nicht regelmäßig gesichert; eine Wiederherstellung von Dateien ist nicht möglich</li>
                                <li style="margin-bottom: 5px;">am <strong>${DeactivationDatePlan}</strong> werden alle Zugriffsberechtigungen deaktiviert</li>
                                <li style="margin-bottom: 5px;">sofern Sie den VDI-Client länger benötigen, können Sie die Bereitstellung bis <strong>${DeactivationDatePlan}</strong> im ${ServicePortalLink} verlängern</li>
                            </ul>
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
"@

# Add RDP Change Journal if exists
if (-not [string]::IsNullOrWhiteSpace($ADResultsRDP)) {
    # Determine background color based on issues
    $rdpBackgroundColor = if ($ADResultsRDPHasIssues) { '#FFF3CD' } else { '#D4EDDA' }
    $rdpTextColor = if ($ADResultsRDPHasIssues) { '#856404' } else { '#155724' }
    $rdpBorderColor = if ($ADResultsRDPHasIssues) { '#856404' } else { '#28a745' }
    $rdpHeaderColor = if ($ADResultsRDPHasIssues) { '#BB0A30' } else { '#28a745' }
    $rdpStatusIcon = if ($ADResultsRDPHasIssues) { '&#9888;' } else { '&#10004;' }  # ⚠ or ✔
    $rdpStatusText = if ($ADResultsRDPHasIssues) { 'Change Journal - Remote Desktop Access (Attention Required)' } else { 'Change Journal - Remote Desktop Access (OK)' }
    
    $EmailBody += @"
                                <tr style="background-color: ${rdpBackgroundColor};">
                                    <td colspan="2" style="border-bottom: 1px solid #666666; padding: 12px;">
                                        <p style="margin: 0 0 8px 0; font-weight: bold; color: ${rdpHeaderColor};"><span style="font-size: 14pt;">${rdpStatusIcon}</span> ${rdpStatusText}</p>
                                        <div style="font-size: 10pt; color: ${rdpTextColor}; border-left: 3px solid ${rdpBorderColor}; padding-left: 10px;">
"@
    
    # Add each RDP change entry
    for ($i = 0; $i -lt $ADResultsRDPArray.Count; $i++) {
        $EmailBody += "                                            <p style='margin: 3px 0;'>$($i + 1). $($ADResultsRDPArray[$i])</p>`n"
    }
    
    $EmailBody += @"
                                        </div>
                                    </td>
                                </tr>
"@
}

# Add Admin Change Journal if exists
if (-not [string]::IsNullOrWhiteSpace($ADResultsADM)) {
    # Determine background color based on issues
    $admBackgroundColor = if ($ADResultsADMHasIssues) { '#FFF3CD' } else { '#D4EDDA' }
    $admTextColor = if ($ADResultsADMHasIssues) { '#856404' } else { '#155724' }
    $admBorderColor = if ($ADResultsADMHasIssues) { '#856404' } else { '#28a745' }
    $admHeaderColor = if ($ADResultsADMHasIssues) { '#BB0A30' } else { '#28a745' }
    $admStatusIcon = if ($ADResultsADMHasIssues) { '&#9888;' } else { '&#10004;' }  # ⚠ or ✔
    $admStatusText = if ($ADResultsADMHasIssues) { 'Change Journal - Local Administrator Privileges (Attention Required)' } else { 'Change Journal - Local Administrator Privileges (OK)' }
    
    $EmailBody += @"
                                <tr style="background-color: ${admBackgroundColor};">
                                    <td colspan="2" style="border-bottom: 1px solid #666666; padding: 12px;">
                                        <p style="margin: 0 0 8px 0; font-weight: bold; color: ${admHeaderColor};"><span style="font-size: 14pt;">${admStatusIcon}</span> ${admStatusText}</p>
                                        <div style="font-size: 10pt; color: ${admTextColor}; border-left: 3px solid ${admBorderColor}; padding-left: 10px;">
"@
    
    # Add each Admin change entry
    for ($i = 0; $i -lt $ADResultsADMArray.Count; $i++) {
        $EmailBody += "                                            <p style='margin: 3px 0;'>$($i + 1). $($ADResultsADMArray[$i])</p>`n"
    }
    
    $EmailBody += @"
                                        </div>
                                    </td>
                                </tr>
"@
}

$EmailBody += @"
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Additional Resources - German -->
                    <tr>
                        <td style="padding: 15px 30px;">
                            <p style="margin: 0; font-size: 10pt; color: #666666;">
                                Allgemeine Informationen zu unseren <a href="https://volkswagengroup.sharepoint.com/sites/V_Child1_NameMynet-WissensweltMitarbeiterIT/SitePages/Virtuelle-L%C3%B6sungen.aspx" target="_blank" style="color: #BB0A30; text-decoration: none;">virtuellen Lösungen</a> sowie spezielle Informationen zu <a href="https://volkswagengroup.sharepoint.com/sites/V_Child1_NameMynet-Services/Freigegebene%20Dokumente/Forms/AllItems.aspx?id=%2Fsites%2FV_Child1_NameMynet%2DServices%2FFreigegebene%20Dokumente%2FVDI%2Fvirtuelle%5Floesungen%5Fquick%5Freference%5Fguide%5Fvdi%2Epdf&parent=%2Fsites%2FV_Child1_NameMynet%2DServices%2FFreigegebene%20Dokumente%2FVDI" target="_blank" style="color: #BB0A30; text-decoration: none;">VDI-Clients</a> finden Sie im V_Child1_Name mynet.
                            </p>
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
                                The V_Child1_Name Service Portal order <strong>${Snow_RITM}</strong> (${UsecaseName}) has been processed, and the ordered VDI client <strong>${VMName}</strong> is now available to you.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                To access the VDI client, please use the Remote Desktop Connection app (mstsc.exe) or open the RDP connection file attached to this email.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Usage Instructions - English -->
                    <tr>
                        <td style="padding: 0 30px 25px 30px;">
                            <p style="margin: 0 0 10px 0; font-weight: bold;">Please note the following usage instructions:</p>
"@

# Add warning if there are permission issues (English version)
if ($ADResultsADMHasIssues -or $ADResultsRDPHasIssues) {
    $EmailBody += @"
                            <p style="margin: 0 0 15px 0; padding: 10px; background-color: #FFF3CD; border-left: 4px solid #BB0A30; color: #856404;">
                                <strong style="color: #BB0A30;">&#9888; Attention:</strong> The permission assignment for the virtual machine requires your attention. Please read the detailed information at the end of the table.
                            </p>
"@
}

$EmailBody += @"
                            <p style="margin: 0 0 5px 0;">The VDI client …</p>
                            <ul style="margin: 5px 0 15px 20px; padding: 0; line-height: 1.6;">
                                <li style="margin-bottom: 5px;">is assigned to you for a provisioning period of <strong>${LifeCycle} days</strong></li>
                                <li style="margin-bottom: 5px;">has been enabled for the group with RDP permissions via intranet or VPN</li>
                                <li style="margin-bottom: 5px;">is subject to the same rules (especially the <a href="https://volkswagengroup.sharepoint.com/sites/V_Child1_NameMynet-ITSecurity/" target="_blank" style="color: #BB0A30; text-decoration: none;">IT security regulations</a>) as notebooks and desktops</li>
                                <li style="margin-bottom: 5px;">is not backed up regularly; recovery of files is not possible</li>
                                <li style="margin-bottom: 5px;">on <strong>${DeactivationDatePlan}</strong>, all access permissions will be deactivated</li>
                                <li style="margin-bottom: 5px;">if you need the VDI client for a longer period, you can extend the provisioning until <strong>${DeactivationDatePlan}</strong> in the Service Portal</li>
                            </ul>

                    <!-- Additional Resources - English -->
                    <tr>
                        <td style="padding: 15px 30px;">
                            <p style="margin: 0; font-size: 10pt; color: #666666;">
                                General information about our <a href="https://volkswagengroup.sharepoint.com/sites/V_Child1_NameMynet-WissensweltMitarbeiterIT/SitePages/Virtuelle-L%C3%B6sungen.aspx" target="_blank" style="color: #BB0A30; text-decoration: none;">virtual solutions</a> and specific information about <a href="https://volkswagengroup.sharepoint.com/sites/V_Child1_NameMynet-Services/Freigegebene%20Dokumente/Forms/AllItems.aspx?id=%2Fsites%2FV_Child1_NameMynet%2DServices%2FFreigegebene%20Dokumente%2FVDI%2Fvirtuelle%5Floesungen%5Fquick%5Freference%5Fguide%5Fvdi%2Epdf&parent=%2Fsites%2FV_Child1_NameMynet%2DServices%2FFreigegebene%20Dokumente%2FVDI" target="_blank" style="color: #BB0A30; text-decoration: none;">VDI clients</a> can be found on V_Child1_Name mynet.
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
#endregion

#region Create and Prepare RDP File
Write-Log "Creating RDP connection file..." 'INFO'

try {
    # Create RDP file in temp directory
    New-RDPFile -target "${VMName}.V_Child1_DNS_FQDN_old_sublocation" -outputdirectory $Env:Temp
    
    # Rename file to remove .V_Child1_DNS_FQDN_old_sublocation from filename
    $sourceRdp = Join-Path $Env:Temp "${VMName}.V_Child1_DNS_FQDN_old_sublocation.rdp"
    $targetRdp = Join-Path $Env:Temp "${VMName}.rdp"
    
    Move-Item -Path $sourceRdp -Destination $targetRdp -Force
    Write-Log "RDP file prepared for attachment: ${targetRdp}" 'SUCCESS'
} catch {
    Write-Log "Failed to create/prepare RDP file: $_" 'ERROR'
    throw
}
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
Write-Log "Sending new assignment notification email..." 'INFO'

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
            -Attachments $targetRdp `
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
    throw "Failed to send new assignment notification email"
}
#endregion

#region Cleanup
Write-Log "Performing cleanup..." 'INFO'

# Remove temporary RDP file
try {
    if (Test-Path $targetRdp) {
        Remove-Item -Path $targetRdp -Force -ErrorAction SilentlyContinue
        Write-Log "Temporary RDP file removed" 'SUCCESS'
    }
} catch {
    Write-Log "Failed to remove temporary RDP file: $_" 'WARNING'
}

# Disconnect from QAD service
Write-Log "Disconnecting from QAD service..." 'INFO'
try {
    Disconnect-QADService -ErrorAction SilentlyContinue
    Write-Log "Disconnected from QAD service" 'SUCCESS'
} catch {
    Write-Log "Error during QAD disconnect: $_" 'WARNING'
}

Write-Log "=== VDI New Assignment Notification Script Completed Successfully ===" 'SUCCESS'
#endregion