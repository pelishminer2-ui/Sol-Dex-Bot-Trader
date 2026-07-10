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
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

Write-Host "== Sol Dex Bot Trader installer build ==" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host "Python: $Python"

function Find-ISCC {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

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
if (-not (Test-Path $PdfInstaller)) {
    throw "Missing PDF at $PdfInstaller - run generate_user_guide.py first"
}

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
    $Iscc = Find-ISCC
    if (-not $Iscc) {
        Write-Host "Inno Setup (ISCC.exe) not found." -ForegroundColor Yellow
        Write-Host "Install from https://jrsoftware.org/isdl.php then re-run:"
        Write-Host "  .\build.ps1 -SkipPdf -SkipPyInstaller"
        Write-Host "Frozen app is ready under setup-bot-installer\build\app\"
        exit 2
    }
    $OutDir = Join-Path $InstallerDir "output"
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    & $Iscc (Join-Path $InstallerDir "setup.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
    $Setup = Join-Path $OutDir "setup.exe"
    if (-not (Test-Path $Setup)) { throw "setup.exe not produced" }
    $sizeMb = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
    Write-Host ""
    Write-Host ("DONE: {0} ({1} MB)" -f $Setup, $sizeMb) -ForegroundColor Green
    Write-Host ("PDF:  {0}" -f $PdfDocs)
} else {
    Write-Host ""
    Write-Host "[4/4] Skipping Inno Setup"
}

Write-Host ""
Write-Host "Maintainer notes: see setup-bot-installer\README.md"
