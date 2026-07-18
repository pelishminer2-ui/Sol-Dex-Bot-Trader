@echo off
REM Start Bot.bat - THE ONE FILE TO CLICK (Solana Mover Trading Bot)
REM Bootstraps .venv, starts Flask in the background, opens http://127.0.0.1:5000.
REM Server keeps running after you close the browser (use stop.bat / tray Quit to stop).
REM Optional: starts watchdog.py so Flask auto-restarts if the process dies.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" -Detach %*
if errorlevel 1 (
    echo.
    echo Launch failed. See messages above.
    pause
)
