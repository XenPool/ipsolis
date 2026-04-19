param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [int]$TimeoutMinutes = 360,
    [int]$PollSeconds = 60
)

# SCCM - Wait for Task Sequence
# Wraps tasks/utils/sccm_admin.py wait-task-sequence (Admin Service, NTLM).
# Polls SMS_DPMDeploymentAssetDetails (per-device StatusType) and the
# SMS_StatMsgWithInsStrings view to surface the human-readable
# StatusDescription strings the legacy script relied on.
#
# StatusType mapping (Admin Service):
#   1 = Success, 2 = InProgress, 3 = Error

$json = python /app/tasks/utils/sccm_admin.py wait-task-sequence `
    --name "$VMName" `
    --os-collection "$OSCollectionID" `
    --timeout-minutes "$TimeoutMinutes" `
    --poll-seconds "$PollSeconds"
$exit = $LASTEXITCODE

Write-Output $json
if ($exit -ne 0) { exit $exit }

try {
    $parsed = $json | ConvertFrom-Json
    $global:SCCMLastStatus       = $parsed.status_description
    $global:TaskSequenceResult   = $parsed.result        # Available / TaskSeqRunError / TaskSeqStartError
    $global:DeploymentID         = $parsed.deployment_id
} catch { }
