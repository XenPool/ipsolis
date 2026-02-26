# ARS - Reset all RDP permissions for the VM

# Secure password and create credential object
$secpasswd = ConvertTo-SecureString '^[QADPW]' -AsPlainText -Force      
$cred = New-Object System.Management.Automation.PSCredential("^[QADUser]", $secpasswd)  

# VM name
$VMName = "$[VMName]"

# Group name
$group = "G-V_Child1_Name-"+$VMName+"-VDI"

Try {
    # Connect to QAD service
    Connect-QADService -Service "^[QADServiceHostname]" -Proxy -Credential $cred

    # Check if group exists
    $userobj = Get-QADGroup -LDAPFilter "(SAMAccountName=$group)"
    if ($null -eq $userobj) {
        # Create new group if it doesn't exist
        New-QADGroup -Name $group -SamAccountName $group -GroupScope Global -ParentContainer "^[ADVDIGrpFldOU]" -Description "Mitglieder erhalten VDI Basis-Rolle"
        Write-Host "Created new group $group"
    }

    # Remove all members from the group
    Get-QADGroupMember -Identity $group | Remove-QADGroupMember -Identity $group -Confirm:$false
    Write-Host "Removed all members from group $group"

    # Ensure group scope is set to Global
    if ((Get-QADGroup -LDAPFilter "(SAMAccountName=$group)").GroupScope -ne "Global") {
        Write-Host "Changing group scope for $group to Global..."
        Set-QADGroup -Identity $group -GroupScope Universal
        Set-QADGroup -Identity $group -GroupScope Global
        Write-Host "Group scope for $group changed to Global"
    }
} Catch {
    # Error handling
    Write-Error -Message "Error while resetting RDP permissions for ${group}: $_" -ErrorAction Stop
}