# launch.ps1 v2026-07-06 - unified session launcher
# Solana Mover Trading Bot - bootstrap, start Flask, open dashboard, stop when session ends.

param(
    [switch]$NoPause,
    [switch]$Detach,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$SessionMode = -not $Detach
$script:SessionMode = $SessionMode

$InstallScript = Join-Path $ProjectRoot "install.ps1"
if (-not $SelfTest) {
    if (-not (Test-Path $InstallScript)) {
        Write-Host "[ERROR] Missing install.ps1 in $ProjectRoot"
        exit 1
    }
    & $InstallScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Environment setup failed. Run install.bat manually."
        exit 1
    }
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
# If a launch lock is owned by a still-alive launcher but no server is serving and the
# lock is older than this, treat it as an orphaned launcher window and reclaim it.
$LockStaleGraceSec = 120

$script:HeldLock = $false
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

# Process names that legitimately own the launch lock: a launcher (PowerShell) or the
# server itself (Python). Anything else owning the recorded PID means the PID was reused
# by an unrelated program, so the lock is stale.
$script:LaunchLockOwnerNames = @(
    'powershell', 'pwsh', 'powershell_ise',
    'python', 'pythonw', 'python3', 'python3.13', 'python3.12', 'python3.11'
)

function Read-LaunchLockInfo {
    # Returns $null when no lock file exists. Otherwise a hashtable with:
    #   Pid (int?), Time (datetime?), Name (string?), Unreadable (bool)
    if (-not (Test-Path $LockFile)) { return $null }
    $lines = $null
    try {
        $lines = @(Get-Content $LockFile -ErrorAction Stop)
    } catch {
        # File exists but is held with an exclusive share (legacy launcher still running).
        return @{ Pid = $null; Time = $null; Name = $null; Unreadable = $true }
    }
    $lockPid = $null; $lockTime = $null; $lockName = $null
    if ($lines.Count -ge 1 -and $lines[0].Trim() -match '^\d+$') { $lockPid = [int]$lines[0].Trim() }
    if ($lines.Count -ge 2) { try { $lockTime = [datetime]::Parse($lines[1].Trim()) } catch { } }
    if ($lines.Count -ge 3) { $lockName = $lines[2].Trim() }
    return @{ Pid = $lockPid; Time = $lockTime; Name = $lockName; Unreadable = $false }
}

function Test-LaunchLockActive {
    # $true  => an existing lock represents a genuine, in-progress launch/server we must respect.
    # $false => no lock, or the lock is stale and may be reclaimed.
    $info = Read-LaunchLockInfo
    if ($null -eq $info) { return $false }

    if ($info.Unreadable) {
        # Held exclusively by another process (legacy launcher). Only treat as active if a
        # server is actually serving; otherwise it is an orphan we cannot read but should not
        # block indefinitely on.
        if (Test-BotServerReady -StatusUrl $StatusUrl) { return $true }
        Write-Host '[..] Launch lock is held but unreadable and no server is serving - treating as stale.'
        return $false
    }

    $lockPid = $info.Pid
    if ($null -eq $lockPid) {
        Write-Host '[..] Stale launch lock (missing/invalid owner PID) - clearing.'
        return $false
    }

    $proc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Host "[..] Stale launch lock (owner PID $lockPid is not running) - clearing."
        return $false
    }

    if ($proc.ProcessName -notin $script:LaunchLockOwnerNames) {
        Write-Host "[..] Stale launch lock (PID $lockPid is '$($proc.ProcessName)', not a launcher/server - PID reused) - clearing."
        return $false
    }

    # Owner is alive and a plausible launcher/server. If the server is up, this is an active
    # session -> respect it (caller will connect instead of starting a duplicate).
    if (Test-BotServerReady -StatusUrl $StatusUrl) { return $true }

    # Owner alive but no server responding: distinguish "just starting" from "orphaned window".
    if ($info.Time) {
        $ageSec = ((Get-Date) - $info.Time).TotalSeconds
        if ($ageSec -gt $LockStaleGraceSec) {
            Write-Host ("[..] Stale launch lock (owner PID {0} alive but no server; lock age {1:N0}s > {2}s grace) - clearing." -f $lockPid, $ageSec, $LockStaleGraceSec)
            return $false
        }
        return $true
    }

    # No timestamp (legacy lock) and no server -> assume orphaned rather than block forever.
    Write-Host "[..] Stale launch lock (owner PID $lockPid alive, no server, no timestamp) - clearing."
    return $false
}

function Remove-StaleLaunchLock {
    # Force-remove the lock file. Only call after Test-LaunchLockActive returned $false.
    if (-not (Test-Path $LockFile)) { return $true }
    try {
        Remove-Item $LockFile -Force -ErrorAction Stop
        return $true
    } catch {
        return (-not (Test-Path $LockFile))
    }
}

function Release-LaunchLock {
    # Only delete the lock if it currently records OUR PID, so we never stomp a lock that
    # another launcher has since reclaimed.
    $script:HeldLock = $false
    if (-not (Test-Path $LockFile)) { return }
    $info = Read-LaunchLockInfo
    if ($null -ne $info -and $info.Pid -eq $PID) {
        try { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue } catch { }
    }
}

function Write-LaunchLock {
    param([System.IO.FileStream]$Stream)
    # Line 1: owning PID, Line 2: ISO-8601 timestamp, Line 3: process name.
    $payload = "{0}`n{1}`n{2}" -f $PID, ((Get-Date).ToString("o")), 'powershell'
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($payload)
    $Stream.Write($bytes, 0, $bytes.Length)
    $Stream.Flush()
}

function Acquire-LaunchLock {
    $dir = Split-Path $LockFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        try {
            # CreateNew is atomic: exactly one racing launcher wins. FileShare.Read lets others
            # READ the PID/timestamp for staleness checks. We close the handle immediately so a
            # lingering launcher can never keep the file un-deletable.
            $stream = [System.IO.File]::Open(
                $LockFile,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::Read
            )
            try {
                Write-LaunchLock -Stream $stream
            } finally {
                $stream.Close()
            }
            $script:HeldLock = $true
            return $true
        } catch [System.IO.IOException] {
            if (Test-LaunchLockActive) {
                if (Test-BotServerReady -StatusUrl $StatusUrl) {
                    Write-Host '[OK] Server already running (started by another launch) - connecting.'
                    return $false
                }
                Write-Host '[..] Another launch is in progress - waiting for it to finish...'
                $deadline = (Get-Date).AddSeconds($ServerWaitSec)
                while ((Get-Date) -lt $deadline) {
                    Start-Sleep -Milliseconds 400
                    if (Test-BotServerReady -StatusUrl $StatusUrl) {
                        Write-Host '[OK] Server ready (started by another launch).'
                        return $false
                    }
                    if (-not (Test-LaunchLockActive)) { break }
                }
                if (Test-BotServerReady -StatusUrl $StatusUrl) { return $false }
                Write-Host '[..] Concurrent launch produced no server in time - reclaiming lock.'
            }
            if (-not (Remove-StaleLaunchLock)) {
                throw 'Could not remove stale launch lock (.flask.lock). It may be held by a live process - close other launch windows and retry.'
            }
        }
    }
    throw 'Could not acquire launch lock (.flask.lock) after retries.'
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

    # Continuous-run: never stop Flask from launcher cleanup / Ctrl+C.
    # Use stop.bat (or tray Quit) for intentional shutdown.
    $cleanup = {
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

if ($SelfTest) {
    # Safe verification of stale-lock recovery. Uses a temp lock file so the real .flask.lock
    # and the running server are never touched.
    $realLockFile = $LockFile
    $LockFile = Join-Path ([System.IO.Path]::GetTempPath()) (".flask.selftest.{0}.lock" -f $PID)
    if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }

    $pass = 0; $fail = 0
    function Assert-Test {
        param([string]$Name, [bool]$Condition)
        if ($Condition) { Write-Host "  [PASS] $Name"; $script:pass++ }
        else { Write-Host "  [FAIL] $Name"; $script:fail++ }
    }

    Write-Host 'Running launch-lock self-test (temp lock, real .flask.lock untouched)...'
    Write-Host "  Temp lock: $LockFile"

    # Find a definitely-dead PID.
    $deadPid = 999999
    while (Get-Process -Id $deadPid -ErrorAction SilentlyContinue) { $deadPid++ }

    # Scenario 1: dead owner PID -> stale -> reclaimable.
    "{0}`n{1}`n{2}" -f $deadPid, ((Get-Date).AddDays(-2).ToString("o")), 'powershell' | Set-Content $LockFile -NoNewline
    Assert-Test 'Dead owner PID is detected as stale (not active)' (-not (Test-LaunchLockActive))
    Assert-Test 'Stale lock is removable' (Remove-StaleLaunchLock)
    Assert-Test 'Lock file gone after reclaim' (-not (Test-Path $LockFile))

    # Scenario 2: alive but non-launcher PID (reused) -> stale.
    $alienProc = Get-Process | Where-Object { $script:LaunchLockOwnerNames -notcontains $_.ProcessName } | Select-Object -First 1
    if ($alienProc) {
        "{0}`n{1}`n{2}" -f $alienProc.Id, ((Get-Date).ToString("o")), $alienProc.ProcessName | Set-Content $LockFile -NoNewline
        Assert-Test "Alive non-launcher PID ($($alienProc.ProcessName)) is stale (PID reuse)" (-not (Test-LaunchLockActive))
        Remove-StaleLaunchLock | Out-Null
    } else {
        Write-Host '  [SKIP] No non-launcher process available for PID-reuse test'
    }

    # Scenario 3: garbage / no PID -> stale.
    'not-a-pid' | Set-Content $LockFile -NoNewline
    Assert-Test 'Non-numeric lock content is stale' (-not (Test-LaunchLockActive))
    Remove-StaleLaunchLock | Out-Null

    # Scenario 4: alive launcher owner (this process) with OLD timestamp and no server on the
    # temp status URL -> orphaned launcher -> stale.
    $savedStatusUrl = $StatusUrl
    $StatusUrl = 'http://127.0.0.1:1/api/bot/status'  # nothing serves here
    "{0}`n{1}`n{2}" -f $PID, ((Get-Date).AddHours(-3).ToString("o")), 'powershell' | Set-Content $LockFile -NoNewline
    Assert-Test 'Alive launcher owner + old lock + no server is stale (orphaned window)' (-not (Test-LaunchLockActive))

    # Scenario 5: alive launcher owner with RECENT timestamp and no server -> genuine startup -> active (must NOT reclaim).
    "{0}`n{1}`n{2}" -f $PID, ((Get-Date).ToString("o")), 'powershell' | Set-Content $LockFile -NoNewline
    Assert-Test 'Alive launcher owner + recent lock is treated as active (double-launch guard)' (Test-LaunchLockActive)
    $StatusUrl = $savedStatusUrl

    # Scenario 6: full acquire on a stale (dead-PID) lock should SUCCEED and rewrite ownership.
    "{0}`n{1}`n{2}" -f $deadPid, ((Get-Date).AddDays(-2).ToString("o")), 'powershell' | Set-Content $LockFile -NoNewline
    $StatusUrl = 'http://127.0.0.1:1/api/bot/status'
    $acquired = Acquire-LaunchLock
    $StatusUrl = $savedStatusUrl
    Assert-Test 'Acquire-LaunchLock reclaims a stale lock and returns $true' ($acquired -eq $true)
    $after = Read-LaunchLockInfo
    Assert-Test 'Reclaimed lock now records our PID' ($null -ne $after -and $after.Pid -eq $PID)
    Release-LaunchLock
    Assert-Test 'Release-LaunchLock removes our own lock' (-not (Test-Path $LockFile))

    if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
    $LockFile = $realLockFile

    Write-Host ''
    Write-Host ("Self-test complete: {0} passed, {1} failed." -f $pass, $fail)
    if ($fail -gt 0) { exit 1 }
    exit 0
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

    # Keep Flask alive for long runs: spawn watchdog if not already monitoring.
    $WatchdogPy = Join-Path $ProjectRoot "watchdog.py"
    if ((Test-Path $WatchdogPy) -and (Test-Path $Python)) {
        $wdRunning = $false
        try {
            $wdProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
            foreach ($proc in $wdProcs) {
                if ($proc.CommandLine -and ($proc.CommandLine -like "*watchdog.py*")) {
                    $wdRunning = $true
                    break
                }
            }
        } catch { }
        if (-not $wdRunning) {
            Write-Host '[..] Starting watchdog (auto-restart Flask if it dies)...'
            $env:SOLANA_LAUNCHED_BY = "launcher"
            Start-Process -FilePath $Python -ArgumentList "`"$WatchdogPy`"" -WorkingDirectory $ProjectRoot -WindowStyle Hidden
        } else {
            Write-Host '[OK] Watchdog already running.'
        }
    }

    if ($SessionMode) {
        Show-SessionBanner -Url $BaseUrl -WeStarted $started
        Wait-BrowserSessionEnd -ListenPort $Port
        # Continuous-run: leave the server up when the browser closes.
        # Use stop.bat to shut down Flask intentionally.
        Write-Host '[OK] Browser session ended — server left running (stop.bat to shut down).'
        Write-Host ''
    } else {
        Write-Host ''
        if ($started) {
            Write-Host 'Server started in the background. Close this window anytime.'
        } else {
            Write-Host 'Connected to an already-running server.'
        }
        Write-Host "Dashboard: $BaseUrl"
        Write-Host 'Server stays up until stop.bat (watchdog restarts it if it crashes).'
        Write-Host ''
    }
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
} finally {
    # Detach / continuous: never kill the server from launcher cleanup.
    # Only release our launch lock; stop.bat / tray Quit own shutdown.
    if ($script:SessionCleanupRegistered) {
        # Override: release lock only — do not stop server on browser/window close.
        Release-LaunchLock
    } else {
        Release-LaunchLock
    }
}
