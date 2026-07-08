# install.ps1 - Create .venv and install Python dependencies (idempotent)
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$VenvDir = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$StampFile = Join-Path $VenvDir ".bootstrap_ok"

function Find-SystemPython {
    $candidates = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.File @($candidate.Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")) 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $version) { continue }
            $parts = $version.Trim().Split(".")
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
                Write-Host "[WARN] Found $($candidate.File) $version (Python 3.10+ recommended)"
            }
            return @{ File = $candidate.File; Args = $candidate.Args }
        } catch { }
    }
    return $null
}

function Test-VenvHealthy {
    if (-not (Test-Path $Python)) { return $false }
    try {
        & $Python -c "import flask, solana" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Invoke-Bootstrap {
    param([switch]$ForceInstall)

    if (-not $ForceInstall -and (Test-Path $StampFile) -and (Test-VenvHealthy)) {
        return $true
    }

    $systemPython = Find-SystemPython
    if (-not $systemPython) {
        Write-Host "[ERROR] Python 3 not found. Install from https://www.python.org/downloads/"
        Write-Host "        Check 'Add Python to PATH' during setup, then run install.bat again."
        return $false
    }

    if (-not (Test-Path $Python)) {
        Write-Host "[..] Creating virtual environment in .venv ..."
        $createArgs = @($systemPython.Args + @("-m", "venv", $VenvDir))
        & $systemPython.File @createArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Failed to create virtual environment."
            return $false
        }
    }

    if (-not (Test-Path $Requirements)) {
        Write-Host "[ERROR] requirements.txt not found at $Requirements"
        return $false
    }

    Write-Host "[..] Installing dependencies (first run may take a few minutes) ..."
    & $Python -m pip install --upgrade pip --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] pip upgrade failed."
        return $false
    }

    & $Python -m pip install -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] pip install failed."
        return $false
    }

    if (-not (Test-VenvHealthy)) {
        Write-Host "[ERROR] Dependencies installed but import check failed."
        return $false
    }

    try {
        (Get-Date).ToString("o") | Set-Content $StampFile -NoNewline
    } catch { }

    Write-Host "[OK] Environment ready: $Python"
    return $true
}

$ok = Invoke-Bootstrap -ForceInstall:$Force
if (-not $ok) { exit 1 }
exit 0
