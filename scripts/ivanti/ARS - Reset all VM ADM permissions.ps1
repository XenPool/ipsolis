# ARS - Reset all VM ADM permissions

# Secure password and create credential object
$secpasswd = ConvertTo-SecureString '^[QADPW]' -AsPlainText -Force      
$cred = New-Object System.Management.Automation.PSCredential("^[QADUser]", $secpasswd)  

# VM name
$VMName = "$[VMName]"

# Group name
$group = "G-V_Child1_Name-" + $VMName + "-ADM"

Try {
    # Connect to QAD service
    Connect-QADService -Service "^[QADServiceHostname]" -Proxy -Credential $cred

    # Check if group exists
    $userobj = Get-QADGroup -LDAPFilter "(SAMAccountName=${group})"
    if ($null -eq $userobj) {
        # Create new group if it doesn't exist
        New-QADGroup -Name $group -SamAccountName $group -GroupScope Global -ParentContainer "^[ADVDIGrpFldOU]" -Description "Mitglieder erhalten erweiterte VDI Admin-Rechte"
        Write-Host "Created new group $group"
    }

    # Remove all members from the group
    Get-QADGroupMember -Identity $group | Remove-QADGroupMember -Identity $group -Confirm:$false
    Write-Host "Removed all members from group $group"
} Catch {
    # Error handling
    Write-Error -Message "Error while resetting ADM permissions for ${group}: $_" -ErrorAction Stop
}