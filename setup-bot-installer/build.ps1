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
    Write-Host "== Sol Dex Bot Trader installer build ==" -ForegroundColor Cyan
    Write-Host "Root: $Root"
    Write-Host "Python: $Python"
    Write-Host "Installer dir: $InstallerDir"
    Write-Host "Expected setup.exe: $(Join-Path $InstallerDir 'output\setup.exe')"

    Write-Host ""
    Write-Host "[1/4] Ensuring build dependencies..."
    & $Python -m pip install --upgrade pip | Out-Null
    & $Python -m pip install -r (Join-Path $Root "requirements.txt") "pyinstaller>=6.0" "reportlab>=4.0" "Pillow>=10.0" | Out-Null

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
        # ISCC resolves OutputDir relative to the .iss file location
        & $Iscc (Join-Path $InstallerDir "setup.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
        $Setup = Join-Path $OutDir "setup.exe"
        if (-not (Test-Path $Setup)) { throw "setup.exe not produced at $Setup" }
        # Re-copy PDF after Inno in case OutputDir was cleaned; keep end-user PDF in output/
        Copy-Item -Force $PdfInstaller $PdfOutput
        $sizeMb = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
        Write-Host ""
        Write-Host ("DONE: {0} ({1} MB)" -f $Setup, $sizeMb) -ForegroundColor Green
        Write-Host ("PDF:  {0}" -f $PdfOutput)
        Write-Host ("PDF:  {0}" -f $PdfDocs)
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
