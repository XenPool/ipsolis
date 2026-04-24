# NAME: Test - Show all variables
# DESC: Diagnostic: outputs all param, context and global variables
# Test - Show all variables
# Diagnostic script that outputs all available param and global variables
# Use this via the module editor test runner to verify variable injection

param(
    [Parameter(Mandatory=$true)]
    [string]$VMName,

    [string]$asset_name,
    [string]$asset_id,
    [string]$order_id,
    [string]$user_email,
    [string]$user_name,
    [string]$owner_email,
    [string]$owner_name,
    [string]$rdp_users,
    [string]$admin_users,
    [string]$expires_at,
    [string]$requested_from,
    [string]$asset_type_id,
    [string]$asset_type_name,
    [string]$snow_req,
    [string]$snow_ritm
)

Write-Host "=============================================="
Write-Host "  XenPool Variable Diagnostic Test"
Write-Host "=============================================="
Write-Host ""

Write-Host "--- PARAM (script parameters) ---"
Write-Host "  VMName              = $VMName"
Write-Host "  asset_name          = $asset_name"
Write-Host "  asset_id            = $asset_id"
Write-Host "  order_id            = $order_id"
Write-Host "  user_email          = $user_email"
Write-Host "  user_name           = $user_name"
Write-Host "  owner_email         = $owner_email"
Write-Host "  owner_name          = $owner_name"
Write-Host "  rdp_users           = $rdp_users"
Write-Host "  admin_users         = $admin_users"
Write-Host "  expires_at          = $expires_at"
Write-Host "  requested_from      = $requested_from"
Write-Host "  asset_type_id       = $asset_type_id"
Write-Host "  asset_type_name     = $asset_type_name"
Write-Host "  snow_req            = $snow_req"
Write-Host "  snow_ritm           = $snow_ritm"
Write-Host ""

Write-Host "--- VARS (global / hosting) ---"
Write-Host "  xenserver.host      = $($VARS.'xenserver.host')"
Write-Host "  xenserver.username  = $($VARS.'xenserver.username')"
Write-Host "  xenserver.password  = $(if ($VARS.'xenserver.password') { '***SET***' } else { '(empty)' })"
Write-Host "  vsphere.host        = $($VARS.'vsphere.host')"
Write-Host "  vsphere.username    = $($VARS.'vsphere.username')"
Write-Host "  vsphere.password    = $(if ($VARS.'vsphere.password') { '***SET***' } else { '(empty)' })"
Write-Host ""

Write-Host "=============================================="
Write-Host "  Test completed successfully"
Write-Host "=============================================="
