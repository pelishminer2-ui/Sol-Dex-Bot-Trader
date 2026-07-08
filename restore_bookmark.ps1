$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
& $python -c "from config import restore_config_bookmark; import json; print(json.dumps(restore_config_bookmark(), indent=2))"
Write-Host ""
Write-Host "Bookmark restored. Restart the bot server if it is running."
