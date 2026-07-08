# install_startup.ps1 - Add/remove Windows login auto-start shortcut
param(
    [ValidateSet("install", "remove")]
    [string]$Action = "install"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$ShortcutPath = Join-Path $StartupFolder "Solana Mover Trading Bot.lnk"
$TargetPath = Join-Path $ProjectRoot "run_hidden.vbs"

function Set-StartupShortcut {
    if (-not (Test-Path $TargetPath)) {
        throw "Missing launcher: $TargetPath"
    }
    if (-not (Test-Path $StartupFolder)) {
        New-Item -ItemType Directory -Path $StartupFolder -Force | Out-Null
    }

    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = "Start Solana Mover Trading Bot dashboard at login"
    $Shortcut.Save()

    Write-Host "[OK] Auto-start enabled:"
    Write-Host "     $ShortcutPath"
    Write-Host "     -> $TargetPath"
}

function Remove-StartupShortcut {
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "[OK] Removed auto-start shortcut."
    } else {
        Write-Host "[OK] No auto-start shortcut found."
    }
}

Write-Host ""
if ($Action -eq "install") {
    Set-StartupShortcut
    Write-Host ""
    Write-Host "The bot will start in the background at Windows login."
    Write-Host "Use uninstall_startup.bat to remove it."
} else {
    Remove-StartupShortcut
}
Write-Host ""
