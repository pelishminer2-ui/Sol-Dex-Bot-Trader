# Optional Docker-assisted build wrapper.
# Prefer host build.ps1 on Windows; this script documents / attempts a containerized path.

$ErrorActionPreference = "Stop"
$InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $InstallerDir

Write-Host "Docker build helper for Sol Dex Bot Trader installer" -ForegroundColor Cyan
Write-Host "Primary path: run setup-bot-installer\build.ps1 on a Windows host with Inno Setup."

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Host "Docker not found — use .\build.ps1 instead."
    exit 1
}

# Linux containers cannot run Inno Setup / Windows PE reliably.
# If the engine is Windows containers, mount the repo and run PowerShell build.
$info = docker version --format "{{.Server.Os}}" 2>$null
Write-Host "Docker server OS: $info"

if ($info -eq "windows") {
    Write-Host "Running build inside Windows container..."
    docker build -f (Join-Path $InstallerDir "Dockerfile.windows") -t sol-dex-bot-installer $Root
    docker run --rm -v "${Root}:C:\src" sol-dex-bot-installer
} else {
    Write-Host @"

Linux Docker engine detected. Inno Setup requires Windows.
Use the host build instead:

  cd $InstallerDir
  .\build.ps1

Or switch Docker Desktop to Windows containers and re-run this script.

"@
    exit 0
}
