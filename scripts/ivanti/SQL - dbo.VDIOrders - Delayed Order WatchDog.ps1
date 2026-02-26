# SQL - dbo.VDIOrders - Delayed Order WatchDog
# Monitors VDI orders and alerts administrators about delayed orders that cannot be processed

#region Logging Function
function Write-Log {
    <#
    .SYNOPSIS
    Writes formatted log messages to console with color coding
    
    .PARAMETER Message
    The message to log
    
    .PARAMETER Level
    Log level (INFO, WARNING, ERROR, SUCCESS)
    #>
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
Write-Log "=== Starting VDI Order WatchDog ===" 'INFO'

# Set ErrorActionPreference to Stop to catch all errors
$ErrorActionPreference = "Stop"

# Set security protocols for secure email communication
try {
    Write-Log "Configuring security protocols..." 'INFO'
    [System.Net.ServicePointManager]::SecurityProtocol = 'Tls,TLS11,TLS12'
    Write-Log "Security protocols configured: TLS 1.0, 1.1, 1.2" 'SUCCESS'
} catch {
    Write-Log "Failed to configure security protocols: $_" 'ERROR'
    throw
}

# Configuration for email notifications
$config = @{
    Email = @{
        User       = "^[EmailUser]"
        Password   = "^[EMailUserPW]"
        Bcc        = ^[EmailBcc]
        From       = "^[EmailFrom]"
        SMTPServer = "^[EmailSMTPServer]"
        Port       = 25
    }
    
    SQL = @{
        ServerInstance = "^[SQLVDIServerInstance]"
        Database       = "^[SQLVDIDatabase]"
        LoginUser      = "^[SQLVDILoginUser]"
        LoginPW        = "^[SQLVDILoginPW]"
        OrderTable     = "^[SQLVDIOrderTable]"
    }
    
    # Watchdog settings
    WatchDog = @{
        DelayThresholdDays = 3  # Alert if order is delayed more than this many days
    }
}

Write-Log "Email Server: $($config.Email.SMTPServer)" 'INFO'
Write-Log "SQL Server: $($config.SQL.ServerInstance)" 'INFO'
Write-Log "Database: $($config.SQL.Database)" 'INFO'
Write-Log "Delay Threshold: $($config.WatchDog.DelayThresholdDays) days" 'INFO'
#endregion

#region Email Credentials
try {
    Write-Log "Setting up email credentials..." 'INFO'
    
    # Convert password to secure string and create credential object
    $securePassword = ConvertTo-SecureString $config.Email.Password -AsPlainText -Force
    $emailCredential = New-Object System.Management.Automation.PSCredential(
        $config.Email.User,
        $securePassword
    )
    
    Write-Log "Email credentials configured for: $($config.Email.User)" 'SUCCESS'
    
} catch {
    Write-Log "Failed to configure email credentials: $_" 'ERROR'
    throw
}
#endregion

#region SQL Server Module
try {
    Write-Log "Loading SQLServer module..." 'INFO'
    
    # Import SQLServer module with error handling
    Import-Module -Name SQLServer -Scope Local -ErrorAction Stop
    
    Write-Log "SQLServer module loaded successfully" 'SUCCESS'
    
} catch {
    Write-Log "Failed to load SQLServer module: $_" 'ERROR'
    Write-Log "Please install the module using: Install-Module -Name SqlServer" 'ERROR'
    throw
}
#endregion

#region Database Connection Test
try {
    Write-Log "Testing database connection..." 'INFO'
    
    # Test connection with a simple query
    $testQuery = "SELECT 1 AS TestConnection"
    $testResult = Invoke-Sqlcmd -ServerInstance $config.SQL.ServerInstance `
                                -Database $config.SQL.Database `
                                -Username $config.SQL.LoginUser `
                                -Password $config.SQL.LoginPW `
                                -Query $testQuery `
                                -QueryTimeout 5 `
                                -ErrorAction Stop
    
    if ($testResult.TestConnection -eq 1) {
        Write-Log "Database connection successful" 'SUCCESS'
    } else {
        throw "Database connection test returned unexpected result"
    }
    
} catch {
    Write-Log "Database connection test failed: $_" 'ERROR'
    throw
}
#endregion

#region Query Delayed Orders
try {
    Write-Log "Querying database for delayed orders..." 'INFO'
    
    # SQL query to retrieve all non-completed orders that should have been processed by now
    $query = @"
SELECT 
    VDIOrders.ID,
    VDIOrders.OrderDate,
    VDIOrders.Action,
    VDIOrders.AssetUUID,
    VDIOrders.Status,
    VDIOrders.UsecaseID,
    VDIOrders.Requestor,
    VDIOrders.Owner,
    VDIOrders.SecondOwner,
    VDIOrders.RDPUserIDs,
    VDIOrders.LocalAdmins,
    CAST(VDIOrders.CreationDatePlan AS date) AS CreationDatePlan,
    CAST(VDIOrders.DeactivationDatePlan AS date) AS DeactivationDatePlan,
    VDIOrders.LifeCycle,
    VDIOrders.Snow_REQ,
    VDIOrders.Snow_RITM,
    VDIOrders.CostCenter,
    VDIOrders.PricePerDay,
    VDIOrders.WatchDogAlert,
    VDIAssets.VMName
FROM VDIOrders
LEFT JOIN VDIAssets ON VDIOrders.AssetUUID = VDIAssets.UUID
WHERE VDIOrders.Status <> 'Completed'
    AND VDIOrders.CreationDatePlan <= GETDATE()
ORDER BY VDIOrders.OrderDate
"@
    
    # Execute the query
    $delayedOrders = Invoke-Sqlcmd -Query $query `
                                   -ServerInstance $config.SQL.ServerInstance `
                                   -Database $config.SQL.Database `
                                   -Username $config.SQL.LoginUser `
                                   -Password $config.SQL.LoginPW `
                                   -QueryTimeout 30 `
                                   -ErrorAction Stop
    
    # Check if any delayed orders were found
    if ($null -eq $delayedOrders -or $delayedOrders.Count -eq 0) {
        Write-Log "No delayed orders found - all orders are processing normally" 'SUCCESS'
        Write-Log "=== VDI Order WatchDog Completed Successfully ===" 'SUCCESS'
        exit 0
    }
    
    Write-Log "Found $($delayedOrders.Count) order(s) to check" 'INFO'
    
} catch {
    Write-Log "Failed to query delayed orders: $_" 'ERROR'
    throw
}
#endregion

#region Process Delayed Orders
try {
    Write-Log "Processing delayed orders..." 'INFO'
    
    # Initialize counters for statistics
    $stats = @{
        TotalOrders       = $delayedOrders.Count
        DelayedOrders     = 0
        AlertsSent        = 0
        AlertsSkipped     = 0
        Errors            = 0
    }
    
    # Loop through each delayed order
    foreach ($order in $delayedOrders) {
        try {
            Write-Log "Checking Order ID: $($order.ID)" 'INFO'
            
            # Calculate how many days the order has been pending
            $orderAge = (New-TimeSpan -Start $order.OrderDate -End (Get-Date)).Days
            Write-Log "  Order Age: ${orderAge} days (Status: $($order.Status))" 'INFO'
            
            # Check if the order has been pending longer than the threshold
            if ($orderAge -gt $config.WatchDog.DelayThresholdDays) {
                Write-Log "  Order has been delayed for more than $($config.WatchDog.DelayThresholdDays) days" 'WARNING'
                $stats.DelayedOrders++
                
                # Check if we have already sent an alert for this order
                if ([string]::IsNullOrWhiteSpace($order.WatchDogAlert)) {
                    Write-Log "  No previous alert sent - sending notification..." 'WARNING'
                    
                    # Determine email subject based on environment
                    $environment = if ([System.Environment]::MachineName -eq "V_Child1_NameINSA5118") {
                        "QS-Instance"
                    } else {
                        "Production"
                    }
                    
                    $emailSubject = if ($environment -eq "QS-Instance") {
                        "V_Child1_Name VDI-Selfservice (QS-Instance) - Order $($order.ID) kann nicht bearbeitet werden!"
                    } else {
                        "V_Child1_Name VDI-Selfservice - Order $($order.ID) kann nicht bearbeitet werden!"
                    }
                    
                    Write-Log "  Environment: ${environment}" 'INFO'
                    Write-Log "  Email Subject: ${emailSubject}" 'INFO'
                    
                    # Build optional table rows only if data exists
                    $optionalRows = ""
                    
                    if (-not [string]::IsNullOrWhiteSpace($order.VMName)) {
                        $optionalRows += "<tr><td>VM Name</td><td>$($order.VMName)</td></tr>`n"
                    }
                    
                    if (-not [string]::IsNullOrWhiteSpace($order.Snow_REQ)) {
                        $optionalRows += "<tr><td>ServiceNow REQ</td><td>$($order.Snow_REQ)</td></tr>`n"
                    }
                    
                    if (-not [string]::IsNullOrWhiteSpace($order.Snow_RITM)) {
                        $optionalRows += "<tr><td>ServiceNow RITM</td><td>$($order.Snow_RITM)</td></tr>`n"
                    }
                    
                    # Construct HTML email body optimized for Outlook Word engine
                    $emailBody = @"
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>V_Child1_Name VDI-Selfservice Alert</title>
</head>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000000; margin: 0; padding: 0;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px;">
                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff;">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #BB0A30; padding: 20px; text-align: center;">
                            <h1 style="color: #ffffff; font-size: 20pt; margin: 0; font-weight: bold;">V_Child1_Name VDI-Selfservice WatchDog Alert</h1>
                        </td>
                    </tr>
                    
                    <!-- Alert Box -->
                    <tr>
                        <td style="padding: 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background-color: #FFF3CD; border-left: 4px solid #BB0A30;">
                                <tr>
                                    <td>
                                        <p style="color: #BB0A30; font-size: 14pt; font-weight: bold; margin: 0 0 10px 0;">WARNUNG: Verzögerter Auftrag erkannt</p>
                                        <p style="margin: 0;">Ein VDI-SelfService Auftrag konnte seit mehr als <strong style="color: #BB0A30;">$($config.WatchDog.DelayThresholdDays) Tagen</strong> nicht verarbeitet werden.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Order Details -->
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <h2 style="color: #BB0A30; font-size: 14pt; margin: 0 0 10px 0;">Auftragsdetails</h2>
                            <table width="100%" cellpadding="4" cellspacing="0" border="0" style="border-collapse: collapse;">
                                <tr>
                                    <td width="40%" style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Auftrags-ID</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;"><strong>$($order.ID)</strong></td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Aktion</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$($order.Action)</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Status</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;"><strong style="color: #BB0A30;">$($order.Status)</strong></td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">UseCase ID</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$($order.UsecaseID)</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Auftragsdatum</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$(Get-Date $order.OrderDate -Format 'dd.MM.yyyy HH:mm')</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Verzögerung</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;"><strong style="color: #BB0A30;">${orderAge} Tage</strong></td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Anforderer</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$($order.Requestor)</td>
                                </tr>
                                <tr>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px; font-weight: bold; color: #666666;">Eigentümer</td>
                                    <td style="border-bottom: 1px solid #e0e0e0; padding: 4px;">$($order.Owner)</td>
                                </tr>
${optionalRows}                            </table>
                        </td>
                    </tr>
                    
                    <!-- Recommendations -->
                    <tr>
                        <td style="padding: 0 20px 20px 20px;">
                            <table width="100%" cellpadding="10" cellspacing="0" border="0" style="background-color: #E8F4F8; border-left: 4px solid #0066CC;">
                                <tr>
                                    <td>
                                        <h3 style="color: #0066CC; margin: 0 0 10px 0; font-size: 12pt;">Empfohlene Massnahmen</h3>
                                        <p style="margin: 0 0 10px 0;"><strong>Bitte überprüfen Sie folgende Punkte:</strong></p>
                                        <ul style="margin: 0; padding-left: 20px;">
                                            <li>Sind genügend VDIs mit Status <strong>"Verfügbar"</strong> im Pool vorhanden?</li>
                                            <li>Ist die UseCase ID <strong>$($order.UsecaseID)</strong> korrekt konfiguriert?</li>
                                            <li>Gibt es technische Probleme mit der VDI-Infrastruktur?</li>
                                            <li>Wurden alle erforderlichen Ressourcen bereitgestellt?</li>
                                        </ul>
                                        <p style="margin: 15px 0 0 0;">
                                            <strong>Nächste Schritte:</strong><br>
                                            Prüfen Sie den Auftrag im V_Child1_Name VDI-Selfservice Portal und beheben Sie eventuelle Blockaden.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 15px; text-align: center; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; font-size: 10pt;"><strong>V_Child1_Name VDI-Selfservice WatchDog</strong></p>
                            <p style="margin: 5px 0; font-size: 9pt; color: #666666;">Automatische Systemüberwachung | $(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')</p>
                            <p style="margin: 10px 0 0 0; font-size: 9pt; color: #999999;">
                                Diese E-Mail wurde automatisch generiert. Bitte nicht auf diese E-Mail antworten.
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
                    
                    # Send the alert email
                    try {
                        Write-Log "  Sending alert email..." 'INFO'
                        
                        Send-MailMessage -UseSsl `
                                       -Port $config.Email.Port `
                                       -To $config.Email.Bcc `
                                       -From $config.Email.From `
                                       -Subject $emailSubject `
                                       -Body $emailBody `
                                       -BodyAsHtml `
                                       -SmtpServer $config.Email.SMTPServer `
                                       -Credential $emailCredential `
                                       -Encoding ([System.Text.Encoding]::UTF8) `
                                       -ErrorAction Stop
                        
                        Write-Log "  Alert email sent successfully" 'SUCCESS'
                        $stats.AlertsSent++
                        
                        # Update WatchDogAlert timestamp in database
                        try {
                            Write-Log "  Updating WatchDogAlert timestamp in database..." 'INFO'
                            
                            $currentDate = Get-Date -Format "yyyy-MM-dd"
                            $updateQuery = @"
UPDATE $($config.SQL.OrderTable)
SET WatchDogAlert = '${currentDate}'
WHERE ID = '$($order.ID)'
"@
                            
                            Invoke-Sqlcmd -Query $updateQuery `
                                        -ServerInstance $config.SQL.ServerInstance `
                                        -Database $config.SQL.Database `
                                        -Username $config.SQL.LoginUser `
                                        -Password $config.SQL.LoginPW `
                                        -QueryTimeout 30 `
                                        -ErrorAction Stop
                            
                            Write-Log "  WatchDogAlert timestamp updated successfully" 'SUCCESS'
                            
                        } catch {
                            Write-Log "  Failed to update WatchDogAlert timestamp: $_" 'ERROR'
                            Write-Log "  Alert was sent but database update failed" 'WARNING'
                            $stats.Errors++
                        }
                        
                    } catch {
                        Write-Log "  Failed to send alert email: $_" 'ERROR'
                        $stats.Errors++
                    }
                    
                } else {
                    Write-Log "  Alert already sent on: $($order.WatchDogAlert)" 'INFO'
                    Write-Log "  Skipping duplicate alert" 'INFO'
                    $stats.AlertsSkipped++
                }
                
            } else {
                Write-Log "  Order age (${orderAge} days) is within acceptable threshold" 'INFO'
            }
            
        } catch {
            Write-Log "  Error processing order $($order.ID): $_" 'ERROR'
            $stats.Errors++
        }
    }
    
} catch {
    Write-Log "Critical error during order processing: $_" 'ERROR'
    throw
}
#endregion

#region Completion Summary
Write-Log "=== VDI Order WatchDog Completed ===" 'SUCCESS'
Write-Log "Processing Summary:" 'INFO'
Write-Log "  Total Orders Checked: $($stats.TotalOrders)" 'INFO'
Write-Log "  Delayed Orders Found: $($stats.DelayedOrders)" 'INFO'
Write-Log "  Alerts Sent: $($stats.AlertsSent)" 'SUCCESS'
Write-Log "  Alerts Skipped (already sent): $($stats.AlertsSkipped)" 'INFO'

if ($stats.Errors -gt 0) {
    Write-Log "  Errors Encountered: $($stats.Errors)" 'ERROR'
} else {
    Write-Log "  Errors Encountered: 0" 'SUCCESS'
}

Write-Log "WatchDog run completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'INFO'
#endregion