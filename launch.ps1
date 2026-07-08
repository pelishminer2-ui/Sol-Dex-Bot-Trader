# launch.ps1 v2026-07-06 - unified session launcher
# Solana Mover Trading Bot - bootstrap, start Flask, open dashboard, stop when session ends.

param(
    [switch]$NoPause,
    [switch]$Detach
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$SessionMode = -not $Detach
$script:SessionMode = $SessionMode

$InstallScript = Join-Path $ProjectRoot "install.ps1"
if (-not (Test-Path $InstallScript)) {
    Write-Host "[ERROR] Missing install.ps1 in $ProjectRoot"
    exit 1
}
& $InstallScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Environment setup failed. Run install.bat manually."
    exit 1
}

$Port = if ($env:GUI_PORT) { [int]$env:GUI_PORT } else { 5000 }
$BaseUrl = "http://127.0.0.1:$Port"
$StatusUrl = "$BaseUrl/api/bot/status"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$AppPy = Join-Path $ProjectRoot "app.py"
$LockFile = Join-Path $ProjectRoot ".flask.lock"
$BrowserStampFile = Join-Path $ProjectRoot ".flask.browser"
$StopScript = Join-Path $ProjectRoot "stop.ps1"
$BrowserCooldownSec = 15
$ServerWaitSec = 90

$script:LockStream = $null
$script:ServerStopped = $false
$script:WeStartedServer = $false
$script:SessionCleanupRegistered = $false

if (-not ([System.Management.Automation.PSTypeName]'ConsoleCtrl').Type) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ConsoleCtrl {
    public delegate bool Handler(int eventType);
    [DllImport("Kernel32", SetLastError = true)]
    public static extern bool SetConsoleCtrlHandler(Handler handler, bool add);
}
'@
}

function Get-StaleLockPid {
    if (-not (Test-Path $LockFile)) { return $null }
    try {
        $raw = (Get-Content $LockFile -Raw -ErrorAction Stop).Trim()
        if ($raw -match '^\d+$') { return [int]$raw }
    } catch { }
    return $null
}

function Clear-StaleLaunchLock {
    $staleLockPid = Get-StaleLockPid
    if ($null -ne $staleLockPid) {
        if (Get-Process -Id $staleLockPid -ErrorAction SilentlyContinue) { return $false }
    }
    Release-LaunchLock
    return $true
}

function Release-LaunchLock {
    if ($script:LockStream) {
        try { $script:LockStream.Close() } catch { }
        $script:LockStream = $null
    }
    if (Test-Path $LockFile) {
        try { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue } catch { }
    }
}

function Acquire-LaunchLock {
    $dir = Split-Path $LockFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        try {
            $script:LockStream = [System.IO.File]::Open(
                $LockFile,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            $script:LockStream.SetLength(0)
            $writer = New-Object System.IO.StreamWriter($script:LockStream, [System.Text.Encoding]::ASCII, 32, $true)
            $launchLockPid = $PID
            $writer.Write([string]$launchLockPid)
            $writer.Flush()
            return $true
        } catch {
            if ($attempt -eq 0 -and (Clear-StaleLaunchLock)) { continue }

            Write-Host '[..] Another launch is in progress - waiting for it to finish...'
            $deadline = (Get-Date).AddSeconds($ServerWaitSec)
            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Milliseconds 400
                if (Test-BotServerReady -StatusUrl $StatusUrl) {
                    Write-Host '[OK] Server ready (started by another launch).'
                    return $false
                }
            }
            throw 'Could not acquire launch lock (.flask.lock). Close other launch windows and retry.'
        }
    }
    throw 'Could not acquire launch lock (.flask.lock).'
}

function Test-PortListening {
    param([int]$ListenPort)
    try {
        $conn = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -in @("127.0.0.1", "0.0.0.0", "::") }
        return $null -ne $conn
    } catch {
        $tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port $ListenPort -WarningAction SilentlyContinue
        return $tcp.TcpTestSucceeded
    }
}

function Test-BotServerReady {
    param(
        [string]$StatusUrl
    )
    try {
        $resp = Invoke-WebRequest -Uri $StatusUrl -UseBasicParsing -TimeoutSec 3
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

function Stop-DuplicateListeners {
    param([int]$ListenPort)
    $listenerPids = Get-PortListenerPids -ListenPort $ListenPort
    if ($listenerPids.Count -le 1) { return }

    Write-Host "[..] Found $($listenerPids.Count) processes on port $ListenPort - keeping newest, stopping duplicates..."
    $keepPid = ($listenerPids | Sort-Object -Descending | Select-Object -First 1)
    foreach ($procId in $listenerPids) {
        if ($procId -eq $keepPid) { continue }
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "     Stopped PID $procId"
        } catch {
            Write-Host "     Could not stop PID $procId : $_"
        }
    }
    Start-Sleep -Milliseconds 800
}

function Wait-ForServer {
    param(
        [string]$Url,
        [int]$TimeoutSec = 90
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-BotServerReady -StatusUrl $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Ensure-SingleHealthyListener {
    param([int]$ListenPort)

    if (Test-BotServerReady -StatusUrl $StatusUrl) {
        Stop-DuplicateListeners -ListenPort $ListenPort
        return
    }

    if (Test-PortListening -ListenPort $ListenPort) {
        Write-Host "[..] Port $ListenPort is in use but bot is not ready - cleaning up stale listeners..."
        Stop-DuplicateListeners -ListenPort $ListenPort
    }
}

function Start-BotServerIfNeeded {
    if (Test-BotServerReady -StatusUrl $StatusUrl) {
        return $false
    }

    Ensure-SingleHealthyListener -ListenPort $Port
    if (Test-BotServerReady -StatusUrl $StatusUrl) {
        return $false
    }

    for ($raceGuard = 0; $raceGuard -lt 8; $raceGuard++) {
        if (Test-BotServerReady -StatusUrl $StatusUrl) {
            return $false
        }
        Start-Sleep -Milliseconds 400
    }

    if (-not (Test-Path $Python)) {
        Write-Host '[ERROR] Python not found at:' $Python
        Write-Host "        Run install.bat first."
        exit 1
    }

    Write-Host '[..] Starting server in background...'
    $env:SOLANA_AUTO_OPEN_BROWSER = "0"
    $env:SOLANA_LAUNCHED_BY = "launcher"
    Start-Process -FilePath $Python -ArgumentList "`"$AppPy`"" -WorkingDirectory $ProjectRoot -WindowStyle Hidden
    return $true
}

function Open-DashboardBrowser {
    param([string]$Url)
    $recent = $false
    if (Test-Path $BrowserStampFile) {
        try {
            $last = [datetime]::Parse((Get-Content $BrowserStampFile -Raw).Trim())
            if (((Get-Date) - $last).TotalSeconds -lt $BrowserCooldownSec) {
                $recent = $true
            }
        } catch { }
    }
    if ($recent) {
        Write-Host '[OK] Browser was opened recently - reusing existing tab.'
        return
    }
    Write-Host "[..] Opening browser at $Url"
    Start-Process $Url
    try {
        (Get-Date).ToString("o") | Set-Content $BrowserStampFile -NoNewline
    } catch { }
}

function Get-BrowserPidsForPort {
    param([int]$ListenPort)

    $patterns = @(
        "127.0.0.1:$ListenPort",
        "localhost:$ListenPort"
    )
    $browserNames = @('chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe', 'opera.exe', 'vivaldi.exe')
    $pids = [System.Collections.Generic.HashSet[int]]::new()

    foreach ($name in $browserNames) {
        try {
            $procs = Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue
            foreach ($proc in $procs) {
                $cmd = $proc.CommandLine
                if (-not $cmd) { continue }
                foreach ($pat in $patterns) {
                    if ($cmd -like "*$pat*") {
                        [void]$pids.Add([int]$proc.ProcessId)
                        break
                    }
                }
            }
        } catch { }
    }

    return @($pids)
}

function Wait-BrowserSessionEnd {
    param([int]$ListenPort)

    $emptyStreak = 0
    $requiredEmpty = 3
    $browserEverSeen = $false
    $hintShown = $false

    while ($true) {
        $pids = Get-BrowserPidsForPort -ListenPort $ListenPort
        if ($pids.Count -gt 0) {
            $browserEverSeen = $true
            $emptyStreak = 0
        } elseif ($browserEverSeen) {
            $emptyStreak++
            if ($emptyStreak -ge $requiredEmpty) { return }
        } elseif (-not $hintShown) {
            $hintShown = $true
            Write-Host '[..] Close the dashboard browser tab/window, or close this window to stop.'
        }

        Start-Sleep -Seconds 2
    }
}

function Stop-BotServerIfOwned {
    param(
        [bool]$WeStarted,
        [switch]$Quiet
    )

    if ($script:ServerStopped) { return }
    if (-not $WeStarted) {
        if (-not $Quiet) {
            Write-Host '[OK] Server was already running - left it running. Use stop.bat to stop manually.'
        }
        return
    }

    if (-not (Test-Path $StopScript)) {
        Write-Host "[ERROR] Missing stop.ps1 at $StopScript"
        return
    }

    if (-not $Quiet) {
        Write-Host '[..] Stopping server...'
    }
    & $StopScript
    $script:ServerStopped = $true
}

function Register-SessionCleanup {
    if ($script:SessionCleanupRegistered) { return }
    $script:SessionCleanupRegistered = $true

    $cleanup = {
        if ($script:SessionMode -and $script:WeStartedServer -and -not $script:ServerStopped) {
            Stop-BotServerIfOwned -WeStarted $true -Quiet
        }
        Release-LaunchLock
    }
    $script:SessionCleanup = $cleanup

    Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action $cleanup | Out-Null

    $handler = [ConsoleCtrl+Handler]{
        param([int]$eventType)
        if ($eventType -in 0, 1, 2, 5, 6) {
            & $script:SessionCleanup
        }
        return $false
    }
    [ConsoleCtrl]::SetConsoleCtrlHandler($handler, $true) | Out-Null
}

function Show-SessionBanner {
    param(
        [string]$Url,
        [bool]$WeStarted
    )

    Write-Host ''
    Write-Host '============================================================'
    Write-Host '  Solana Mover Trading Bot'
    Write-Host '============================================================'
    Write-Host "  Dashboard: $Url"
    if ($WeStarted) {
        Write-Host '  Close the browser tab/window OR close this window to stop the server.'
    } else {
        Write-Host '  Connected to an already-running server.'
        Write-Host '  Close the browser when finished. Server stays running (use stop.bat to stop).'
    }
    Write-Host '============================================================'
    Write-Host ''
}

try {
    $hasLock = Acquire-LaunchLock
    if (-not $hasLock) {
        Open-DashboardBrowser -Url $BaseUrl
        if ($SessionMode) {
            Show-SessionBanner -Url $BaseUrl -WeStarted $false
            Wait-BrowserSessionEnd -ListenPort $Port
            Write-Host '[OK] Browser session ended.'
        } else {
            Write-Host '[OK] Connected to server started by another launch.'
            Write-Host "Dashboard: $BaseUrl"
            Write-Host ''
        }
        exit 0
    }

    Write-Host ''
    Write-Host '============================================================'
    Write-Host '  Solana Mover Trading Bot'
    Write-Host '============================================================'
    Write-Host ''

    $started = $false
    if (Test-BotServerReady -StatusUrl $StatusUrl) {
        Write-Host "[OK] Bot server already running on port $Port - reusing it."
        Stop-DuplicateListeners -ListenPort $Port
    } else {
        $started = Start-BotServerIfNeeded
        if (-not $started -and (Test-BotServerReady -StatusUrl $StatusUrl)) {
            Write-Host "[OK] Bot server already running on port $Port - reusing it."
        }
    }

    $script:WeStartedServer = $started
    if ($SessionMode -and $started) {
        Register-SessionCleanup
    }

    Write-Host "[..] Waiting for $StatusUrl ..."
    if (-not (Wait-ForServer -Url $StatusUrl -TimeoutSec $ServerWaitSec)) {
        Write-Host '[ERROR] Server did not respond within 90 seconds.'
        exit 1
    }

    Stop-DuplicateListeners -ListenPort $Port

    Write-Host '[OK] Dashboard ready.'
    Open-DashboardBrowser -Url $BaseUrl

    if ($SessionMode) {
        Show-SessionBanner -Url $BaseUrl -WeStarted $started
        Wait-BrowserSessionEnd -ListenPort $Port
        Stop-BotServerIfOwned -WeStarted $started
        Write-Host '[OK] Session ended.'
        Write-Host ''
    } else {
        Write-Host ''
        if ($started) {
            Write-Host 'Server started in the background. Close this window anytime.'
        } else {
            Write-Host 'Connected to an already-running server.'
        }
        Write-Host "Dashboard: $BaseUrl"
        Write-Host ''
    }
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
} finally {
    if ($script:SessionCleanupRegistered) {
        & $script:SessionCleanup
    } elseif ($SessionMode -and $script:WeStartedServer -and -not $script:ServerStopped) {
        Stop-BotServerIfOwned -WeStarted $true -Quiet
        Release-LaunchLock
    } else {
        Release-LaunchLock
    }
}
