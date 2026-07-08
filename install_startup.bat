@echo off
REM install_startup.bat - Start bot automatically at Windows login (no console)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_startup.ps1" -Action install
pause
