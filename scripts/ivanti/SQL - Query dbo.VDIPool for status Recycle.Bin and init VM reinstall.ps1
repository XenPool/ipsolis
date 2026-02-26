# SQL - Query dbo.VDIPool for status Recycle.Bin and init VM reinstall
# Convert password to secure string
$SecPasswd = ConvertTo-SecureString  "^[IAAPIPW]" -AsPlainText -Force
# Create credentials object
$MyCreds = New-Object System.Management.Automation.PSCredential ( "^[IAAPIUser]" , $SecPasswd)
# Set API URL
$url = "^[IAAPIUrl]/Dispatcher/SchedulingService/jobs"

# Import SQLServer module
Import-Module -Name SQLServer -Scope Local
 
# Define SQL query to get VMs in 'Recycle.Bin' status
$Query =   "SELECT TOP 1 
    vp.*, 
    va.AssetStatus AS VA_AssetStatus, 
    va.UUID AS VA_UUID
FROM dbo.VDIPool vp
LEFT JOIN VDIAssets va ON vp.AssetUUID = va.UUID
WHERE vp.Status = 'Recycle.Bin'
  AND vp.Snow_REQ IS NULL 
  AND vp.Snow_RITM IS NULL
  AND (va.AssetStatus = 'Expired' OR va.UUID IS NULL OR vp.AssetUUID IS NULL)
ORDER BY NEWID();
"

# Execute SQL query
$Results = Invoke-Sqlcmd -Query $Query -ServerInstance "^[SQLVDIServerInstance]" -Username "^[SQLVDILoginUser]" -Password "^[SQLVDILoginPW]" -Database "^[SQLVDIDatabase]"

# Loop through each result
ForEach($Result  in $Results){
    $VMName = $Result.VMName
    # Update VM status to 'Recycling'
    $Query = "UPDATE       ^[SQLVDIPoolTable]
    SET                Status = 'Recycling'
    WHERE        ^[SQLVDIPoolTable].VMName = '$VMName'
    "
    Invoke-Sqlcmd -Query $Query -ServerInstance "^[SQLVDIServerInstance]" -Username "^[SQLVDILoginUser]" -Password "^[SQLVDILoginPW]" -Database "^[SQLVDIDatabase]"

    # Define request body for API call
    $body = '{
        "Description": " Reinstall VM '+$Result.VMName+' and return to available pool ",
        "When": {
            "Immediate": true,
            "IsLocalTime": true,
            "UseWakeOnLAN": false
        },
        "What": [
            {
                "ID": " ^[GUID-Runbook-4] ",
                "Type": 2,
                "Name": " Reinstall VM '+$Result.VMName+' and return to available pool "
            }
        ],
        "Who": [
            {
                "ID": " ^[GUID-Dispatcher] ",
                "Type": 1,
            }
        ],
        "Parameters": [
            {
                "Identifier": "",
                "Type": 2,
                "TaskContainerGuid": " ^[GUID-Runbook-4] ",
                "TaskContainerName": " IA - Assign Stock VDI '+$Result.Hostname+' By SN Request ",
                "JobGuid": "{00000000-0000-0000-0000-000000000000}",
                "JobName": "",
                "JobParameters": [
                    {
                        "Name": "VMName",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.VMName+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "UsecaseID",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.UsecaseID+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "OSCollectionID",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.OSCollectionID+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "AppCollectionIDs",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.AppCollectionIDs+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "MACAddress",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.MACAddress+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "CPU",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.CPU+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "RAM",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.RAM+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "HDD",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.HDD+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    },
                    {
                        "Name": "SCCMGuiD",
                        "Type": 0,
                        "Description": "",
                        "Value1": "'+$Result.SCCMGuiD+'",
                        "Value2": "",
                        "Value3": "",
                        "Hint": "Please provide the necessary input",
                        "Selection": ""
                    }
                ]
            }
        ],
        "ScheduleInParallel": "true"
    }
    '

    # Make API call to dispatch the job
    Invoke-WebRequest -Uri $url -Method Post -Credential $MyCreds -ContentType "application/json" -Body $body
}