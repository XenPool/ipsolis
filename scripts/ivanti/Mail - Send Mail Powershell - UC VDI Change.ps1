# Mail - Send Mail Powershell - UC VDI Change
# Sends configuration change notification emails for VDI client modifications

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
Write-Log "=== Starting VDI Change Notification Script ===" 'INFO'

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
$Snow_REQ = "$[Snow_REQ]"
$Snow_RITM = "$[Snow_RITM]"
$OrderID = "$[OrderID]"

Write-Log "Processing change notification for VM: ${VMName} (UUID: ${UUID})" 'INFO'
Write-Log "Creation date: ${CreationDatePlan}, Deactivation date: ${DeactivationDatePlan}" 'INFO'
Write-Log "ServiceNow Request: ${Snow_REQ}, Item: ${Snow_RITM}" 'INFO'
Write-Log "Order ID: ${OrderID}" 'INFO'
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
        $col2 = if ($p.Count -ge 2) { $p[1].Trim() } else { "" }

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
    $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - ${VMName} - Bereitstellungsanpassung / change of assignment"
    Write-Log "Email subject set for QS environment" 'INFO'
} else {
    $subject = "V_Child1_Name VDI-Selfservice - ${VMName} - Bereitstellungsanpassung / change of assignment"
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
    Write-Log "Admin Users (validated in AD): $($ADMUserIDsUserOrGroupName.UserIDs -join ', ')" 'INFO'
} else {
    Write-Log "No Admin users could be validated in AD" 'INFO'
}

# Format Admin user list for email body - ALWAYS show what was requested
if ([string]::IsNullOrWhiteSpace($LocalAdmins)) {
    $ADMUserList = ""
    Write-Log "No Admin users requested" 'INFO'
} else {
    # Show the original requested list (not the validated list)
    $ADMUserList = $LocalAdmins -replace ";", "; "
    Write-Log "List of desired Admin users: ${ADMUserList}" 'INFO'
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
Write-Log "Building email body with dynamic content..." 'INFO'

$EmailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice - Bereitstellungsanpassung</title>
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
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Bereitstellungsanpassung / Change of Assignment</p>
                        </td>
                    </tr>
                    
                    <!-- Greeting - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Guten Tag $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 15px 0;">
                                die beantragte Änderung der Konfigurationsparameter des VDI-Clients <strong>${VMName}</strong> wurde erfolgreich bearbeitet. In der folgenden Tabelle finden Sie die aktualisierten Daten für diese Bereitstellung.
                            </p>
"@

# Add warning if there are permission issues (German)
if ($ADResultsADMHasIssues -or $ADResultsRDPHasIssues) {
    $EmailBody += @"
                            <p style="margin: 0 0 15px 0; padding: 10px; background-color: #FFF3CD; border-left: 4px solid #BB0A30; color: #856404;">
                                <strong style="color: #BB0A30;">&#9888; Achtung:</strong> Die Berechtigungszuweisung für die virtuelle Maschine erfordert Ihre Aufmerksamkeit. Bitte lesen Sie die detaillierten Hinweise am Ende der Tabelle.
                            </p>
"@
}

$EmailBody += @"
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
                        <td style="padding: 15px 30px 25px 30px;">
                            <p style="margin: 0 0 15px 0;">Good day $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 15px 0;">
                                the requested change to the configuration parameters of the VDI client <strong>${VMName}</strong> has been successfully processed. The updated provisioning settings can be found in the table above.
                            </p>
"@

# Add warning if there are permission issues (English)
if ($ADResultsADMHasIssues -or $ADResultsRDPHasIssues) {
    $EmailBody += @"
                            <p style="margin: 0 0 15px 0; padding: 10px; background-color: #FFF3CD; border-left: 4px solid #BB0A30; color: #856404;">
                                <strong style="color: #BB0A30;">&#9888; Attention:</strong> The permission assignment for the virtual machine requires your attention. Please read the detailed information at the end of the table.
                            </p>
"@
}

$EmailBody += @"
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
Write-Log "Sending change notification email..." 'INFO'

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
    throw "Failed to send change notification email"
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

Write-Log "=== VDI Change Notification Script Completed Successfully ===" 'SUCCESS'
#endregion