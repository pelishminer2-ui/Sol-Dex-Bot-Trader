@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "from config import restore_config_bookmark; import json; print(json.dumps(restore_config_bookmark(), indent=2))"
) else (
  python -c "from config import restore_config_bookmark; import json; print(json.dumps(restore_config_bookmark(), indent=2))"
)
if errorlevel 1 exit /b 1
echo.
echo Bookmark restored. Restart the bot server if it is running.
endlocal
