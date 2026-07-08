@echo off
setlocal
cd /d "%~dp0"
echo Reverting regime-aware entry tuning to the OLD bottom line...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" restore_regime_tuning.py
) else (
  python restore_regime_tuning.py
)
if errorlevel 1 exit /b 1
echo.
echo Regime entry tuning reverted. Restart the bot server so config reloads.
endlocal
