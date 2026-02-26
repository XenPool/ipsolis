# ARS - Create AD ComputerAccount

# Secure password and create credential object
$secpasswd = ConvertTo-SecureString '^[QADPW]' -AsPlainText -Force      
$cred = New-Object System.Management.Automation.PSCredential("^[QADUser]", $secpasswd)  

# VM name
$VMName = "$[VMName]"

Try {
    # Connect to QAD service
    Connect-QADService -Service "^[QADServiceHostname]" -Proxy -Credential $cred

    # Create new AD computer account
    New-QADComputer -Name $VMName -ParentContainer '^[ADVDICmpFldOU]'
    Write-Host "Successfully created ComputerObject $VMName in '^[ADVDICmpFldOU]'"
} Catch {
    # Error handling
    Write-Error -Message "Error while trying to create ComputerObject $VMName in '^[ADVDICmpFldOU]': $_" -ErrorAction Stop
}