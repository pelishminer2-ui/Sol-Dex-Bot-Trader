#Requires -Version 5.1
<#
.SYNOPSIS
  Build SolDexBotTrader (PyInstaller) + setup.exe (Inno Setup) under setup-bot-installer/.
#>
param(
    [switch]$SkipPdf,
    [switch]$SkipPyInstaller,
    [switch]$SkipInno
)

$ErrorActionPreference = "Stop"
$InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $InstallerDir

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

function Get-AppVersion {
    $vf = Join-Path $InstallerDir "version.txt"
    if (Test-Path $vf) {
        $v = (Get-Content -Raw $vf).Trim()
        if ($v) { return $v }
    }
    return "1.0.1"
}

function Write-BuildStamp {
    param([string]$Version)
    $now = Get-Date
    # Compact stamp avoids ISCC /D tokenization issues with spaces
    $stamp = $now.ToString("yyyy-MM-dd'T'HH:mm:sszzz")
    $dateOnly = $now.ToString("yyyy-MM-dd")
    $timeOnly = $now.ToString("HH:mm:ss")
    $infoPath = Join-Path $InstallerDir "BUILD_INFO.txt"
    $outDir = Join-Path $InstallerDir "output"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $body = @"
Sol Dex Bot Trader
Version: $Version
Built: $stamp
BuildDate: $dateOnly
BuildTime: $timeOnly
"@
    Set-Content -Path $infoPath -Value $body -Encoding UTF8
    Copy-Item -Force $infoPath (Join-Path $outDir "BUILD_INFO.txt")
    return [pscustomobject]@{
        Version = $Version
        Stamp = $stamp
        Date = $dateOnly
        Time = $timeOnly
        Path = $infoPath
    }
}

function Find-ISCC {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 7\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Install-InnoSetup {
    Write-Host "Attempting to install Inno Setup 6..." -ForegroundColor Yellow

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Using winget: JRSoftware.InnoSetup"
        & winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements --disable-interactivity
        $found = Find-ISCC
        if ($found) { return $found }
    }

    $choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($choco) {
        Write-Host "Using chocolatey: innosetup"
        & choco install innosetup -y
        $found = Find-ISCC
        if ($found) { return $found }
    }

    $tmp = Join-Path $env:TEMP "innosetup-install.exe"
    $url = "https://jrsoftware.org/download.php/is.exe"
    Write-Host "Downloading Inno Setup from $url ..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        Write-Host "Running silent install (per-user)..."
        $p = Start-Process -FilePath $tmp -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
        if ($null -ne $p.ExitCode -and $p.ExitCode -ne 0) {
            Write-Host "Inno installer exit code: $($p.ExitCode)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Auto-download/install failed: $_" -ForegroundColor Red
        return $null
    } finally {
        Remove-Item -Force $tmp -ErrorAction SilentlyContinue
    }

    return (Find-ISCC)
}

function Ensure-ISCC {
    $iscc = Find-ISCC
    if ($iscc) { return $iscc }
    Write-Host "Inno Setup (ISCC.exe) not found on PATH or standard locations." -ForegroundColor Yellow
    $iscc = Install-InnoSetup
    if ($iscc) {
        Write-Host "Inno Setup ready: $iscc" -ForegroundColor Green
        return $iscc
    }
    return $null
}

Push-Location $Root
try {
    $AppVersion = Get-AppVersion
    $Build = Write-BuildStamp -Version $AppVersion

    Write-Host "== Sol Dex Bot Trader installer build ==" -ForegroundColor Cyan
    Write-Host "Root: $Root"
    Write-Host "Python: $Python"
    Write-Host "Installer dir: $InstallerDir"
    Write-Host "Version: $($Build.Version)"
    Write-Host "Build stamp: $($Build.Stamp)"
    Write-Host "Expected setup.exe: $(Join-Path $InstallerDir 'output\setup.exe')"

    # Fail fast if packaging would miss the live/paper balance dropdown UX.
    $DashHtml = Join-Path $Root "static\index.html"
    $ConfigPy = Join-Path $Root "config.py"
    if (-not (Test-Path $DashHtml)) { throw "Missing dashboard: $DashHtml" }
    if (-not (Test-Path $ConfigPy)) { throw "Missing config: $ConfigPy" }
    $html = Get-Content -Raw -Path $DashHtml
    $cfg = Get-Content -Raw -Path $ConfigPy
    foreach ($marker in @(
        'id="paperBalanceInput"',
        'id="liveTradeableInput"',
        'liveTradeableTouched',
        'paperBalanceTouched',
        'setLiveTradeableSelect',
        'value="5.00"'
    )) {
        if ($html -notlike "*$marker*") {
            throw "Dashboard missing required marker '$marker' in $DashHtml"
        }
    }
    if ($html -match 'type="number"\s+id="liveTradeableInput"') {
        throw "Live tradeable control is still a number input; expected <select> dropdown in $DashHtml"
    }
    if ($cfg -notmatch 'MAX_LIVE_TRADEABLE_BALANCE_SOL\s*=\s*5\.0') {
        throw "config.py must set MAX_LIVE_TRADEABLE_BALANCE_SOL = 5.0 (found in $ConfigPy)"
    }
    Write-Host "Preflight OK: repo-root static/index.html + config.py include 0.75-5 SOL live/paper dropdowns" -ForegroundColor Green

    Write-Host ""
    Write-Host "[1/4] Ensuring build dependencies..."
    & $Python -m pip install --upgrade pip | Out-Null
    & $Python -m pip install -r (Join-Path $Root "requirements.txt") -r (Join-Path $InstallerDir "requirements-build.txt") | Out-Null

    if (-not $SkipPdf) {
        Write-Host ""
        Write-Host "[2/4] Generating user guide PDF..."
        & $Python (Join-Path $InstallerDir "generate_user_guide.py")
        if ($LASTEXITCODE -ne 0) { throw "PDF generation failed" }
    } else {
        Write-Host ""
        Write-Host "[2/4] Skipping PDF generation"
    }

    $PdfInstaller = Join-Path $InstallerDir "Sol-Dex-Bot-Trader-User-Guide.pdf"
    $PdfDocs = Join-Path $Root "docs\Sol-Dex-Bot-Trader-User-Guide.pdf"
    $OutDir = Join-Path $InstallerDir "output"
    $PdfOutput = Join-Path $OutDir "Sol-Dex-Bot-Trader-User-Guide.pdf"
    if (-not (Test-Path $PdfInstaller)) {
        throw "Missing PDF at $PdfInstaller - run generate_user_guide.py first"
    }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    Copy-Item -Force $PdfInstaller $PdfOutput
    Write-Host "PDF copied to: $PdfOutput"

    if (-not $SkipPyInstaller) {
        Write-Host ""
        Write-Host "[3/4] PyInstaller freeze..."
        if (-not (Test-Path (Join-Path $InstallerDir "BUILD_INFO.txt"))) {
            throw "BUILD_INFO.txt missing — build stamp step failed"
        }
        $DistPath = Join-Path $InstallerDir "build\app"
        $WorkPath = Join-Path $InstallerDir "build\pyi-work"
        New-Item -ItemType Directory -Force -Path $DistPath, $WorkPath | Out-Null
        & $Python -m PyInstaller `
            --noconfirm `
            --clean `
            --distpath $DistPath `
            --workpath $WorkPath `
            (Join-Path $InstallerDir "SolDexBotTrader.spec")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
        $Exe = Join-Path $DistPath "SolDexBotTrader\SolDexBotTrader.exe"
        if (-not (Test-Path $Exe)) { throw "Expected frozen exe missing: $Exe" }
        Write-Host "Frozen app: $Exe"
    } else {
        Write-Host ""
        Write-Host "[3/4] Skipping PyInstaller"
    }

    if (-not $SkipInno) {
        Write-Host ""
        Write-Host "[4/4] Compiling setup.exe with Inno Setup..."
        $Iscc = Ensure-ISCC
        if (-not $Iscc) {
            Write-Host "Inno Setup (ISCC.exe) still not found after auto-install attempt." -ForegroundColor Red
            Write-Host "Install manually from https://jrsoftware.org/isdl.php or:"
            Write-Host "  winget install --id JRSoftware.InnoSetup -e"
            Write-Host "Then re-run:"
            Write-Host "  .\build.ps1 -SkipPdf -SkipPyInstaller"
            Write-Host "Frozen app is ready under setup-bot-installer\build\app\"
            exit 2
        }
        Write-Host "Using ISCC: $Iscc"
        New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
        # Refresh stamp immediately before Inno so setup.exe VersionInfo matches compile time
        $Build = Write-BuildStamp -Version $AppVersion
        Write-Host "Inno VersionInfo stamp: $($Build.Stamp)"
        # ISCC resolves OutputDir relative to the .iss file location
        & $Iscc `
            "/DMyAppVersion=$($Build.Version)" `
            "/DMyAppBuildDate=$($Build.Date)" `
            "/DMyAppBuildTime=$($Build.Time)" `
            ("/DMyAppBuildStamp=" + $Build.Stamp) `
            (Join-Path $InstallerDir "setup.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
        $Setup = Join-Path $OutDir "setup.exe"
        if (-not (Test-Path $Setup)) { throw "setup.exe not produced at $Setup" }
        # Re-copy PDF + BUILD_INFO after Inno; keep end-user artifacts in output/
        Copy-Item -Force $PdfInstaller $PdfOutput
        Copy-Item -Force (Join-Path $InstallerDir "BUILD_INFO.txt") (Join-Path $OutDir "BUILD_INFO.txt")
        Copy-Item -Force (Join-Path $InstallerDir "version.txt") (Join-Path $OutDir "version.txt")
        $setupItem = Get-Item $Setup
        $sizeMb = [math]::Round($setupItem.Length / 1MB, 1)
        Write-Host ""
        Write-Host ("DONE: {0} ({1} MB)" -f $Setup, $sizeMb) -ForegroundColor Green
        Write-Host ("File timestamp: {0}" -f $setupItem.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"))
        Write-Host ("Version: {0}  Built: {1}" -f $Build.Version, $Build.Stamp)
        Write-Host ("PDF:  {0}" -f $PdfOutput)
        Write-Host ("PDF:  {0}" -f $PdfDocs)
        Write-Host ("BUILD_INFO: {0}" -f (Join-Path $OutDir "BUILD_INFO.txt"))
    } else {
        Write-Host ""
        Write-Host "[4/4] Skipping Inno Setup"
    }

    Write-Host ""
    Write-Host "Maintainer notes: see setup-bot-installer\README.md"
}
finally {
    Pop-Location
}
