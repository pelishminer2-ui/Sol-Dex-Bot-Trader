# stop.ps1 - Stop the Flask dashboard server on the configured port
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Port = if ($env:GUI_PORT) { [int]$env:GUI_PORT } else { 5000 }
$StatusUrl = "http://127.0.0.1:$Port/api/bot/status"
$ServerLock = Join-Path $ProjectRoot ".flask.server.lock"
$LaunchLock = Join-Path $ProjectRoot ".flask.lock"

function Test-BotServerReady {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-PortListenerPids {
    param([int]$ListenPort)
    try {
        $listenerPids = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -in @("127.0.0.1", "0.0.0.0", "::") } |
            Select-Object -ExpandProperty OwningProcess -Unique
        return @($listenerPids | Where-Object { $_ -and $_ -gt 0 })
    } catch {
        return @()
    }
}

function Get-LockPid {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        # Locks may be single-line (server lock: "<pid>") or multi-line
        # (launch lock: "<pid>\n<iso-timestamp>\n<name>"). Use the first line.
        $lines = @(Get-Content $Path -ErrorAction Stop)
        if ($lines.Count -ge 1 -and $lines[0].Trim() -match '^\d+$') { return [int]$lines[0].Trim() }
    } catch { }
    return $null
}

function Remove-LockFiles {
    foreach ($lock in @($ServerLock, $LaunchLock)) {
        if (Test-Path $lock) {
            try { Remove-Item $lock -Force -ErrorAction Stop }
            catch { Write-Host "     Could not remove $(Split-Path $lock -Leaf): $_" }
        }
    }
}

Write-Host ""
Write-Host "Stopping Solana Mover Trading Bot server on port $Port ..."
Write-Host ""

$isOurServer = Test-BotServerReady -Url $StatusUrl
$listenerPids = Get-PortListenerPids -ListenPort $Port
$lockPid = Get-LockPid -Path $ServerLock
$launchPid = Get-LockPid -Path $LaunchLock

$targets = [System.Collections.Generic.HashSet[int]]::new()
foreach ($procId in $listenerPids) { [void]$targets.Add($procId) }
if ($lockPid) { [void]$targets.Add($lockPid) }

if ($targets.Count -eq 0) {
    Write-Host "[OK] No server process found on port $Port."
    Remove-LockFiles
    exit 0
}

if (-not $isOurServer) {
    Write-Host "[WARN] Port $Port is in use but /api/bot/status did not respond."
    Write-Host "       Stopping listener PIDs anyway: $($targets -join ', ')"
}

$stopped = 0
foreach ($procId in $targets) {
    if ($procId -eq $PID) { continue }
  try {
        $proc = Get-Process -Id $procId -ErrorAction Stop
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "     Stopped PID $procId ($($proc.ProcessName))"
        $stopped++
    } catch {
        Write-Host "     Could not stop PID $procId : $_"
    }
}

Start-Sleep -Milliseconds 500

if (Test-BotServerReady -Url $StatusUrl) {
    Write-Host "[ERROR] Server still responding on port $Port."
    # Server is still alive, so leave the server lock; but the launch lock is only a
    # launcher mutex and should never survive a stop attempt.
    if (Test-Path $LaunchLock) { Remove-Item $LaunchLock -Force -ErrorAction SilentlyContinue }
    exit 1
}

Remove-LockFiles

Write-Host ""
Write-Host "[OK] Server stopped ($stopped process(es))."
Write-Host ""
