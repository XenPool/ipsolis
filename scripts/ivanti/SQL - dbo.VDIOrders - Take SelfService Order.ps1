# SQL - dbo.VDIOrders - Take SelfService Order
# Processes VDI-Selfservice orders from ServiceNow and creates database entries

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
Write-Log "=== Starting VDI-Selfservice Order Processing Script ===" 'INFO'

# Centralized configuration
$config = @{
    # Email settings
    Email = @{
        User       = "^[EmailUser]"
        Password   = '^[EMailUserPW]'
        From       = "^[EmailFrom]"
        BCC        = ^[EmailBcc]
        SMTPServer = "^[EmailSMTPServer]"
        Port       = 25
    }
    
    # SQL settings
    SQL = @{
        Server         = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        Username       = "^[SQLVDILoginUser]"
        Password       = "^[SQLVDILoginPW]"
        OrderTable     = "^[SQLVDIOrderTable]"
        UseCaseTable   = "^[SQLVDIUseCaseTable]"
    }
    
    # Active Directory settings
    AD = @{
        ServiceHostname = '^[NewADQADServiceHostname]'
        Domain          = '${COMPANY}'
        Username        = '^[NewADVDIR99SvcAcc]'
        Password        = '^[NewADVDIR99SvcAccPW]'
    }
    
    # Environment detection
    Environment = @{
        QSMachineName = "${COMPANY}1234"
    }
}

Write-Log "Configuration loaded successfully" 'SUCCESS'

# Import required modules
try {
    Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
    Write-Log "SQLServer module imported successfully" 'SUCCESS'
} catch {
    Write-Log "Failed to import SQLServer module: $_" 'ERROR'
    throw
}

# Set security protocols for SMTP communication
[System.Net.ServicePointManager]::SecurityProtocol = 'Tls,TLS11,TLS12'
Write-Log "Security protocols configured: TLS, TLS 1.1, TLS 1.2" 'INFO'

# Create email credential
$mailpassword = ConvertTo-SecureString $config.Email.Password -AsPlainText -Force
$EmailCredential = New-Object System.Management.Automation.PSCredential -ArgumentList $config.Email.User, $mailpassword
Write-Log "Email credentials prepared" 'INFO'

# Create AD credential
$adPassword = ConvertTo-SecureString $config.AD.Password -AsPlainText -Force
$ADCredential = New-Object System.Management.Automation.PSCredential -ArgumentList $config.AD.Username, $adPassword
Write-Log "AD credentials prepared" 'INFO'
#endregion

#region Active Directory Connection with Retry Logic
Write-Log "Connecting to Active Directory service..." 'INFO'

$maxRetries = 3
$retryDelay = 10  # seconds
$connected = $false

for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
    try {
        Write-Log "QAD connection attempt ${attempt} of ${maxRetries} (Domain: $($config.AD.Domain))..." 'INFO'
        Connect-QADService -Service $config.AD.ServiceHostname -Proxy -Credential $ADCredential -ErrorAction Stop | Out-Null
        $connected = $true
        Write-Log "Successfully connected to QAD service for domain: $($config.AD.Domain)" 'SUCCESS'
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

#region Input Parameters
$Action = "$[Action]"
$UsecaseID = "$[UsecaseID]"
$Requestor = "$[Requestor]"
$Owner = "$[Owner]"
$RDPUserIDs = "$[RDPUserIDs]"
$LocalAdmins = "$[LocalAdmins]"
$CreationDatePlan = "$[CreationDatePlan]"
$DeactivationDatePlan = "$[DeactivationDatePlan]"
$LifeCycle = "$[LifeCycle]"
$Snow_REQ = "$[Snow_REQ]"
$Snow_RITM = "$[Snow_RITM]"
$CostCenter = "$[CostCenter]"
$PricePerDay = "$[PricePerDay]"
$AssetUUID = "$[AssetUUID]"

Write-Log "Processing order - Action: ${Action}, Use Case: ${UsecaseID}" 'INFO'
Write-Log "ServiceNow Request: ${Snow_REQ}, Item: ${Snow_RITM}" 'INFO'
#endregion

#region Validate DeactivationDatePlan
# Cancel the order if DeactivationDatePlan was not correctly transmitted by ServiceNow
if ([string]::IsNullOrEmpty($DeactivationDatePlan)) {
    Write-Log "DeactivationDatePlan is missing or empty - Order will be marked as ERROR" 'ERROR'
    $Status = "!ERROR!"
} else {
    Write-Log "DeactivationDatePlan validated: ${DeactivationDatePlan}" 'SUCCESS'
    $Status = "Pending"
}
#endregion

#region Clean User ID Lists
# Remove double semicolons from RDPUserIDs
$RDPUserIDs = $RDPUserIDs.Replace(';;', ';')
Write-Log "RDP User IDs cleaned: ${RDPUserIDs}" 'INFO'

# Remove double semicolons from LocalAdmins
$LocalAdmins = $LocalAdmins.Replace(';;', ';')
Write-Log "Local Admin IDs cleaned: ${LocalAdmins}" 'INFO'
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
        $adUser = Get-QADUser -Identity $Identity -Properties lastname, givenname, department, mail -ErrorAction Stop
        
        if ($adUser) {
            # Safely extract properties with null-check and ensure single values
            $userInfo['Surname'] = if ($adUser.lastname) { 
                if ($adUser.lastname -is [array]) { $adUser.lastname[0] } else { $adUser.lastname }
            } else { "" }
            
            $userInfo['Givenname'] = if ($adUser.givenname) { 
                if ($adUser.givenname -is [array]) { $adUser.givenname[0] } else { $adUser.givenname }
            } else { "" }
            
            $userInfo['Department'] = if ($adUser.department) { 
                if ($adUser.department -is [array]) { $adUser.department[0] } else { $adUser.department }
            } else { "" }
            
            $userInfo['Email'] = if ($adUser.mail) { 
                if ($adUser.mail -is [array]) { $adUser.mail[0] } else { $adUser.mail }
            } else { "" }
            
            Write-Log "${Role} information retrieved: $($userInfo['Givenname']) $($userInfo['Surname']) ($($userInfo['Email']))" 'SUCCESS'
        } else {
            Write-Log "${Role} '${Identity}' not found in Active Directory" 'WARNING'
        }
    } catch {
        Write-Log "Failed to retrieve ${Role} information: $_" 'ERROR'
    }
    
    return $userInfo
}
#endregion

#region Retrieve User Information
Write-Log "Retrieving requestor and owner information from Active Directory..." 'INFO'

$RequestorInfo = Get-UserInformation -Identity $Requestor -Role "Requestor"
$OwnerInfo = Get-UserInformation -Identity $Owner -Role "Owner"
#endregion

#region Get Use Case Information
Write-Log "Retrieving use case information for Use Case ID: ${UsecaseID}..." 'INFO'

$useCaseQuery = "SELECT UsecaseID, UsecaseName, UsecaseDescription FROM $($config.SQL.UseCaseTable) WHERE UsecaseID = '${UsecaseID}'"

try {
    $useCaseResult = Invoke-Sqlcmd -Query $useCaseQuery -ServerInstance $config.SQL.Server -Username $config.SQL.Username -Password $config.SQL.Password -Database $config.SQL.Database -ErrorAction Stop
    
    if ($useCaseResult) {
        $UsecaseName = $useCaseResult.UsecaseName
        $UsecaseDescription = $useCaseResult.UsecaseDescription
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

#region Format Dates and Calculate Days Until Provisioning
# Format dates for email display
try {
    $CreationDatePlanFormatted = ([DateTime]$CreationDatePlan).ToString('dd.MM.yyyy')
    $DeactivationDatePlanFormatted = ([DateTime]$DeactivationDatePlan).ToString('dd.MM.yyyy')
    Write-Log "Dates formatted - Creation: ${CreationDatePlanFormatted}, Deactivation: ${DeactivationDatePlanFormatted}" 'INFO'
} catch {
    Write-Log "Failed to format dates: $_" 'ERROR'
    $CreationDatePlanFormatted = $CreationDatePlan
    $DeactivationDatePlanFormatted = $DeactivationDatePlan
}

# Calculate days until provisioning
try {
    $currentDate = (Get-Date).Date
    $creationDate = ([DateTime]$CreationDatePlan).Date
    $daysUntilProvisioning = ($creationDate - $currentDate).Days
    
    Write-Log "Days until provisioning: ${daysUntilProvisioning}" 'INFO'
    
    # Build provisioning text based on days
    if ($daysUntilProvisioning -eq 0) {
        # Provisioning is today
        $ProvisioningText_DE = "Die Bereitstellung Ihres VDI Clients erfolgt gemäß Ihrer Bestellung <strong>heute</strong> am <strong>${CreationDatePlanFormatted}</strong>."
        $ProvisioningText_EN = "The provision of your VDI Client will be completed according to your order <strong>today</strong> on <strong>${CreationDatePlanFormatted}</strong>."
        Write-Log "Provisioning scheduled for today" 'INFO'
    } elseif ($daysUntilProvisioning -eq 1) {
        # Provisioning is tomorrow
        $ProvisioningText_DE = "Die Bereitstellung Ihres VDI Clients erfolgt gemäß Ihrer Bestellung <strong>morgen</strong> am <strong>${CreationDatePlanFormatted}</strong>."
        $ProvisioningText_EN = "The provision of your VDI Client will be completed according to your order <strong>tomorrow</strong> on <strong>${CreationDatePlanFormatted}</strong>."
        Write-Log "Provisioning scheduled for tomorrow" 'INFO'
    } elseif ($daysUntilProvisioning -gt 1) {
        # Provisioning is in multiple days
        $ProvisioningText_DE = "Die Bereitstellung Ihres VDI Clients erfolgt gemäß Ihrer Bestellung in <strong>${daysUntilProvisioning} Tagen</strong> am <strong>${CreationDatePlanFormatted}</strong>."
        $ProvisioningText_EN = "The provision of your VDI Client will be completed according to your order in <strong>${daysUntilProvisioning} days</strong> on <strong>${CreationDatePlanFormatted}</strong>."
        Write-Log "Provisioning scheduled in ${daysUntilProvisioning} days" 'INFO'
    } else {
        # Provisioning date is in the past
        $ProvisioningText_DE = "Die Bereitstellung Ihres VDI Clients erfolgt gemäß Ihrer Bestellung schnellstmöglich am <strong>${CreationDatePlanFormatted}</strong>."
        $ProvisioningText_EN = "The provision of your VDI Client will be completed according to your order as soon as possible on <strong>${CreationDatePlanFormatted}</strong>."
        Write-Log "Provisioning date is in the past (${daysUntilProvisioning} days ago)" 'WARNING'
    }
    
} catch {
    Write-Log "Failed to calculate days until provisioning: $_" 'ERROR'
    # Fallback text if calculation fails
    $ProvisioningText_DE = "Die Bereitstellung Ihres VDI Clients erfolgt gemäß Ihrer Bestellung am <strong>${CreationDatePlanFormatted}</strong>."
    $ProvisioningText_EN = "The provision of your VDI Client will be completed according to your order on <strong>${CreationDatePlanFormatted}</strong>."
}
#endregion

#region Email Body Template
Write-Log "Building email body with dynamic content..." 'INFO'

$EmailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>${COMPANY}} VDI-Selfservice - Bestellbestätigung</title>
</head>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000000; margin: 0; padding: 0;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px;">
                <table width="814" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border: 1px solid #e0e0e0;">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #BB0A30; padding: 20px; text-align: center;">
                            <h1 style="color: #ffffff; font-size: 18pt; margin: 0; font-weight: bold;">${COMPANY} VDI-Selfservice</h1>
                            <p style="color: #ffffff; font-size: 12pt; margin: 5px 0 0 0;">Bestellbestätigung / Order Confirmation</p>
                        </td>
                    </tr>
                    
                    <!-- Greeting - German -->
                    <tr>
                        <td style="padding: 25px 30px 15px 30px;">
                            <p style="margin: 0 0 15px 0;">Guten Tag $($RequestorInfo['Givenname']) $($RequestorInfo['Surname']),</p>
                            <p style="margin: 0 0 10px 0;">
                                vielen Dank, dass Sie das VDI-Selfservice System im ${COMPANY} Service Portal nutzen. Wir bestätigen den Eingang Ihrer Bestellung für eine Virtuelle Maschine (VDI Client) und werden diese nun bearbeiten.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                ${ProvisioningText_DE} Nach erfolgreicher Bereitstellung erhalten Sie von uns eine weitere E-Mail mit allen relevanten Informationen.
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
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; width: 45%; color: #666666;">VDI Variante / variant</td>
                                    <td style="border-bottom: 1px solid #666666; width: 55%; color: #666666;">${UsecaseID} ${UsecaseName}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">VDI Beschreibung / description</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${UsecaseDescription}</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">${COMPANY} MyServe REQ ID</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${Snow_REQ}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">${COMPANY} MyServe RITM ID</td>
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
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${CreationDatePlanFormatted}</td>
                                </tr>
                                <tr style="background-color: #ffffff;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Bereitstellungszeitraum / Provision Period</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${LifeCycle} Tage / Days</td>
                                </tr>
                                <tr style="background-color: #E5E5E5;">
                                    <td style="border-bottom: 1px solid #666666; font-weight: bold; color: #666666;">Deaktivierungsdatum / Date of Deactivation</td>
                                    <td style="border-bottom: 1px solid #666666; color: #666666;">${DeactivationDatePlanFormatted}</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer German -->
                    <tr>
                        <td style="padding: 15px 30px 25px 30px; border-bottom: 2px solid #e0e0e0;">
                            <p style="margin: 0;">Mit freundlichen Grüßen</p>
                            <p style="margin: 5px 0 0 0; font-weight: bold;">${COMPANY} Mitarbeiter IT</p>
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
                                thank you for using the VDI-Selfservice System in the ${COMPANY} Service Portal. We confirm the receipt of your order for a virtual machine (VDI Client) and will now process it.
                            </p>
                            <p style="margin: 0 0 15px 0;">
                                ${ProvisioningText_EN} After successful provisioning, you will receive another email from us with all relevant information.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer English -->
                    <tr>
                        <td style="padding: 15px 30px 25px 30px;">
                            <p style="margin: 0;">Kind regards</p>
                            <p style="margin: 5px 0 0 0; font-weight: bold;">${COMPANY} Mitarbeiter IT</p>
                            <p style="margin: 0; color: #666666;">Client Services</p>
                        </td>
                    </tr>
                    
                    <!-- Bottom Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 15px; text-align: center; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; font-size: 9pt; color: #666666;">${COMPANY} VDI-Selfservice | ${COMPANY} Mitarbeiter IT | Client Services</p>
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

#region Build SQL Insert Query
Write-Log "Building SQL insert query for action: ${Action}" 'INFO'

if ($Action -eq 'New') {
    # Insert query for new VDI order
    $insertQuery = @"
INSERT INTO $($config.SQL.OrderTable)
(OrderDate
,Action
,UsecaseID
,Requestor
,Owner
,SecondOwner
,RDPUserIDs
,LocalAdmins
,CreationDatePlan
,DeactivationDatePlan
,LifeCycle
,Snow_REQ
,Snow_RITM
,CostCenter
,PricePerDay
,Status
)
VALUES
('@[DATETIME(YYYY-MM-DD)]'
,'${Action}'
,'${UsecaseID}'
,'${Requestor}'
,'${Owner}'
,'${Requestor}'
,'${RDPUserIDs}'
,'${LocalAdmins}'
,'${CreationDatePlan}'
,'${DeactivationDatePlan}'
,'${LifeCycle}'
,'${Snow_REQ}'
,'${Snow_RITM}'
,'${CostCenter}'
,'${PricePerDay}'
,'${Status}'
);
SELECT SCOPE_IDENTITY() AS NewID;
"@
    Write-Log "SQL query prepared for new VDI order" 'INFO'
    
} else {
    # Insert query for change/modify order with existing asset
    $insertQuery = @"
INSERT INTO $($config.SQL.OrderTable)
(OrderDate
,Action
,AssetUUID
,Requestor
,Owner
,SecondOwner
,RDPUserIDs
,LocalAdmins
,CreationDatePlan
,DeactivationDatePlan
,LifeCycle
,Snow_REQ
,Snow_RITM
,CostCenter
,PricePerDay
,Status
)
VALUES
('@[DATETIME(YYYY-MM-DD)]'
,'${Action}'
,'${AssetUUID}'
,'${Requestor}'
,'${Owner}'
,'${Requestor}'
,'${RDPUserIDs}'
,'${LocalAdmins}'
,'${CreationDatePlan}'
,'${DeactivationDatePlan}'
,'${LifeCycle}'
,'${Snow_REQ}'
,'${Snow_RITM}'
,'${CostCenter}'
,'${PricePerDay}'
,'${Status}'
);
SELECT SCOPE_IDENTITY() AS NewID;
"@
    Write-Log "SQL query prepared for change order (Asset UUID: ${AssetUUID})" 'INFO'
}
#endregion

#region Insert Order into Database
Write-Log "Inserting order into database..." 'INFO'

try {
    $result = Invoke-Sqlcmd -Query $insertQuery -ServerInstance $config.SQL.Server -Username $config.SQL.Username -Password $config.SQL.Password -Database $config.SQL.Database -ErrorAction Stop
    
    $newOrderID = $result.NewID
    Write-Log "Order successfully inserted into database with ID: ${newOrderID}" 'SUCCESS'
    
} catch {
    Write-Log "Failed to insert order into database: $_" 'ERROR'
    throw
}
#endregion

#region Send Order Confirmation Email
# Only send confirmation email for 'New' orders
if ($Action -eq 'New') {
    Write-Log "Preparing to send order confirmation email..." 'INFO'
    
    # Set email subject based on environment (QS vs Production)
    if ([System.Environment]::MachineName -eq $config.Environment.QSMachineName) {
        $subject = "${COMPANY} VDI-Selfservice (QS-Instance) - VDI Client - Bestellbestätigung / Order confirmation"
        Write-Log "Email subject set for QS environment" 'INFO'
    } else {
        $subject = "${COMPANY} VDI-Selfservice - VDI Client - Bestellbestätigung / Order confirmation"
        Write-Log "Email subject set for Production environment" 'INFO'
    }
    
    # Determine email recipients - filter out empty strings and null values
    $To = @($RequestorInfo['Email'], $OwnerInfo['Email']) | 
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | 
        Select-Object -Unique
    
    # Fallback: If no valid recipient emails found, use BCC
    if ($To.Count -eq 0) {
        Write-Log "No valid recipient email addresses found. Using BCC recipients as fallback." 'WARNING'
        $To = $config.Email.BCC
        $EmailBody = "<p><strong><span style='color: #ff0000;'>Achtung, Requestor UND Owner E-Mail Adresse konnte im AD nicht gefunden werden.</span></strong></p>" + $EmailBody
    } else {
        Write-Log "Email recipients: $($To -join ', ')" 'INFO'
    }
    
    # Send email with retry logic
    $maxEmailRetries = 3
    $emailRetryDelay = 5  # seconds
    $emailSent = $false
    
    for ($attempt = 1; $attempt -le $maxEmailRetries; $attempt++) {
        try {
            Write-Log "Email send attempt ${attempt} of ${maxEmailRetries}..." 'INFO'
            
            Send-MailMessage -UseSsl `
                -Port $config.Email.Port `
                -To $To `
                -Bcc $config.Email.BCC `
                -From $config.Email.From `
                -Subject $subject `
                -Body $EmailBody `
                -BodyAsHtml `
                -SmtpServer $config.Email.SMTPServer `
                -Credential $EmailCredential `
                -Encoding ([System.Text.Encoding]::UTF8) `
                -ErrorAction Stop
            
            $emailSent = $true
            Write-Log "Order confirmation email sent successfully to: $($To -join ', ')" 'SUCCESS'
            Write-Log "Subject: ${subject}" 'INFO'
            break
            
        } catch {
            $errorMsg = "Failed to send email: $_"
            
            if ($attempt -lt $maxEmailRetries) {
                Write-Log "${errorMsg} - Retrying in ${emailRetryDelay} seconds..." 'WARNING'
                Start-Sleep -Seconds $emailRetryDelay
            } else {
                Write-Log "${errorMsg} - Max retries reached" 'ERROR'
            }
        }
    }
    
    if (-not $emailSent) {
        Write-Log "Failed to send order confirmation email after ${maxEmailRetries} attempts" 'ERROR'
    }
} else {
    Write-Log "Action is '${Action}' - No confirmation email sent (only sent for 'New' orders)" 'INFO'
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

Write-Log "=== VDI-Selfservice Order Processing Script Completed Successfully ===" 'SUCCESS'
Write-Log "Order ID: ${newOrderID}, Status: ${Status}" 'INFO'
#endregion
