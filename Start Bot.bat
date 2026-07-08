@echo off
REM Start Bot.bat - THE ONE FILE TO CLICK (Solana Mover Trading Bot)
REM Bootstraps .venv, starts Flask, opens http://127.0.0.1:5000, stops server when you close the browser or this window.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
if errorlevel 1 (
    echo.
    echo Launch failed. See messages above.
    pause
)
