# ARS - Delete AD ComputerAccount

# Secure password and create credential object
$secpasswd = ConvertTo-SecureString '^[QADPW]' -AsPlainText -Force      
$cred = New-Object System.Management.Automation.PSCredential("^[QADUser]", $secpasswd)  

# VM name
$VMName = "$[VMName]"

Try {
    # Connect to QAD service
    Connect-QADService -Service "^[QADServiceHostname]" -Proxy -Credential $cred

    # Delete AD computer account
    Get-QADComputer -SearchRoot "^[SCCMOSDDomainOUName]" -Name "$[VMName]"| Select-Object -Last 1 | Remove-QADObject -Force -Confirm:$false -DeleteTree
    Write-Host "Successfully deleted ComputerObject $VMName"
} Catch {
    # Error handling
    Write-Error -Message "Error while trying to delete ComputerObject ${VMName}: $_" -ErrorAction Stop
}