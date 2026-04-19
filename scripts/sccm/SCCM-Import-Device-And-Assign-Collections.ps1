param(
    [Parameter(Mandatory=$true)][string]$VMName,
    [Parameter(Mandatory=$true)][string]$OSCollectionID,
    [Parameter(Mandatory=$true)][string]$MACAddress,
    [Parameter(Mandatory=$true)][string]$SCCMGuiD,
    [string]$AppCollectionIDs = "",
    [int]$ResourceIdRetries = 60
)

# SCCM - Import Device and Assign Collections
# Wraps tasks/utils/sccm_admin.py import-machine (Admin Service, NTLM).
# Replaces Import-CMComputerInformation + Add-CMDeviceCollectionDirectMembershipRule
# loop from the legacy "Create Device in SCCM and boot to PXE" script.
#
# App collections are passed as a semicolon-separated string (legacy format).

$args = @(
    "import-machine",
    "--name",            $VMName,
    "--os-collection",   $OSCollectionID,
    "--mac",             $MACAddress,
    "--guid",            $SCCMGuiD,
    "--resource-id-retries", "$ResourceIdRetries"
)

if (-not [string]::IsNullOrWhiteSpace($AppCollectionIDs)) {
    # Normalise legacy ';' separator to the ',' the Python CLI expects
    $normalised = ($AppCollectionIDs -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ','
    if ($normalised) {
        $args += @("--app-collections", $normalised)
    }
}

$json = python /app/tasks/utils/sccm_admin.py @args
$exit = $LASTEXITCODE

Write-Output $json
if ($exit -ne 0) { exit $exit }

try {
    $parsed = $json | ConvertFrom-Json
    $global:SCCMResourceID      = $parsed.resource_id
    $global:SCCMImportStatus    = $parsed.status
    $global:SCCMAppCollections  = $parsed.app_collections
} catch { }
