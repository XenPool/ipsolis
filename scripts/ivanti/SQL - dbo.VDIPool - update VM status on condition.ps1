# SQL - dbo.VDIPool - update VM status on condition

# Import the SQLServer module for running SQL queries
Import-Module -Name SQLServer -Scope Local

# Set the security protocol for network communications
[System.Net.ServicePointManager]::SecurityProtocol = 'Tls,TLS11,TLS12'

# Define email credentials for sending notifications
$mailuser     = "^[EmailUser]"
$mailpassword = ConvertTo-SecureString  '^[EMailUserPW]'  -AsPlainText -Force
$Credential   = New-Object System.Management.Automation.PSCredential -ArgumentList $mailuser,$mailpassword
$bccuser      = ^[EmailBcc]
$emailfrom    = "^[EmailFrom]"
$smtpServer   = "^[EmailSMTPServer]"

# Define the email subject based on the machine environment
if ( [system.environment]::MachineName -eq "V_Child1_NameINSA5118") {
    # If the machine is the test instance, adjust the subject line accordingly
    $subject = "V_Child1_Name VDI-Selfservice (QS-Instance) - SCCM Neuinstallation von $VMName nicht erfolgreich..."
} else {
    # Production environment subject line
    $subject = "V_Child1_Name VDI-Selfservice - SCCM Neuinstallation von $VMName nicht erfolgreich..."
}

# Define the VM name (used in multiple operations)
$VMName = "$[VMName]"

# Set up credentials for vSphere server access
$secpasswd      = ConvertTo-SecureString '^[vSphereServerAdminPW]' -AsPlainText -Force      
$cred           = New-Object System.Management.Automation.PSCredential("^[vSphereServerAdminUser]",$secpasswd)  

# Connect to the vSphere server
Connect-VIServer -Server "^[vSphereServerHost]" -Credential $cred

# Retrieve VM information using its name
$vm = Get-VM -Name "$[VMName]"
$vmId = $vm.ExtensionData.MoRef.Value  # Get the VM's unique managed object reference ID

# Retrieve the VM's UUID and other details
Get-VM -Name "$[VMName]" | Select-Object Name, Id, @{Name="UUID";Expression={$_.ExtensionData.Config.InstanceUuid}}
$InstanceUUID = (Get-View ServiceInstance | Select-Object -ExpandProperty Content | Select-Object -ExpandProperty About | Select-Object InstanceUuid).InstanceUUID

# Define the vCenter URL
$vcenterUrl = "https://^[vSphereServerHost]"

# Construct the link to the VM's console in the vCenter UI
$linkToConsole = $($vcenterUrl) + '/ui/app/vm;nav=v/urn:vmomi:VirtualMachine:' + $vmId + ':' + $InstanceUUID + '/summary'

# Check the task sequence result and send email notifications if there's an error
if ("$[TaskSequenceResult]" -eq "TaskSeqRunError" -or "$[TaskSequenceResult]" -eq "TaskSeqStartError") {
    # Construct a failure comment for logging
    $NewComment = "$(Get-Date -Format 'dd.MM.yyyy HH:mm'): SCCM deployment $[DeploymentID] failed"

    # Build the email body in HTML format for detailed error reporting
    $EmailBody = @"
<head>
    <style>
        body { font-family: Arial, sans-serif; color: #333333; }
        h1 { color: #D9534F; font-size: 14px; }
        .highlight { color: #D9534F; font-weight: bold; }
        .content { padding: 15px; border: 1px solid #D9534F; background-color: #F2DEDE; }
        .footer { font-size: 12px; color: #999999; margin-top: 20px; }
    </style>
</head>
<div>
    <div>
        <h1>VDI-SelfService System</h1>
    </div>
</div>
<div>
    <div class="content">
        <p>VDI-SelfService: The automatic reinstallation of the VM <strong>$VMName</strong> has failed.</p>
        <p><strong>Error:</strong> <span style="color: #ff0000;">$[TaskSequenceResult]</span></p>
        <p><strong>Possible actions:   </strong>Check the status of the VM at the <a title="VCenter" href="$linkToConsole">console</a> and manually restart the VM. Then check if the installation completes correctly. Alternatively, you can retry the installation.</p>
        <p>If the installation completes successfully, set the status of the VM in VDIPool to <strong>Available</strong>.</p>
        <blockquote>
            <p>UPDATE VDIPool<br />SET<br />AssetUUID = NULL,<br />Snow_REQ = NULL,<br />Snow_RITM = NULL,<br />Status = 'Available',<br />HardwareNotes = 'manually recycled by ...'<br />Where (VMName = '$VMName')</p>
        </blockquote>
        <p>Otherwise, repeat the installation by setting the VM status in VDIPool to <strong>Recycle.Bin</strong>.</p>
        <blockquote>
            <p>UPDATE VDIPool<br />SET<br />AssetUUID = NULL,<br />Snow_REQ = NULL,<br />Snow_RITM = NULL,<br />Status = 'Recycle.Bin',<br />HardwareNotes = 'rescheduled for recycling by ...'<br />Where (VMName = '$VMName')</p>
        </blockquote>
        <p>If the problem persists, there is likely an issue with the configuration of the virtual hardware of the VM.</p>
    </div>
</div>
<div>
    <div class="footer">
        <p>Best regards,<br />Your VDISelfService</p>
    </div>
</div>
"@
    # Send the notification email with the error details
    Send-MailMessage -UseSsl -Port 25 -To $bccuser -From $emailfrom -Subject $Subject -Body $EmailBody -BodyAsHtml -SmtpServer $smtpServer -Credential $Credential -encoding ([System.Text.Encoding]::UTF8)
} else {
    # If no error occurred, log a successful deployment comment
    $NewComment = "$(Get-Date -Format 'dd.MM.yyyy HH:mm'): SCCM deployment $[DeploymentID] successful"
}

# Update the SQL database with the deployment result and comment
$Query = "UPDATE ^[SQLVDIPoolTable]
SET
[AssetUUID] = NULL, 
[Snow_REQ] = NULL, 
[Snow_RITM] = NULL, 
[Status] = '$[TaskSequenceResult]',
[LastIAEvent] = '$NewComment'
Where (VMName = '$VMName')"

# Execute the SQL query on the specified database
Invoke-Sqlcmd -Query $Query -ServerInstance "^[SQLVDIServerInstance]" -Username "^[SQLVDILoginUser]" -Password "^[SQLVDILoginPW]" -Database "^[SQLVDIDatabase]"
