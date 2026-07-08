# Optional Windows firewall helper — block inbound TCP on the GUI port except loopback.
# Run PowerShell as Administrator. Adjust $Port if you use a non-default GUI_PORT.
#
# This script adds a Windows Defender Firewall rule that denies inbound connections
# to the bot dashboard port from non-local addresses. The Flask app already binds
# to 127.0.0.1 by default; this is defense-in-depth if FLASK_HOST is misconfigured.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\allow_localhost_only.ps1
#
# To remove the rule later:
#   Remove-NetFirewallRule -DisplayName "Solana Mover Bot - Block External GUI"

param(
    [int]$Port = 5000
)

$RuleName = "Solana Mover Bot - Block External GUI"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Run this script as Administrator to modify Windows Firewall rules."
    Write-Host "Documentation only — no changes were made."
    exit 1
}

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Rule already exists: $RuleName"
    exit 0
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Action Block `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress Any `
    -Profile Any `
    -Description "Blocks external inbound access to Solana Mover Trading Bot GUI on port $Port. Localhost access is unaffected."

Write-Host "Created firewall rule: $RuleName (blocks inbound TCP $Port from remote addresses)"
Write-Host "Flask still binds to 127.0.0.1 by default — keep FLASK_HOST=127.0.0.1."
