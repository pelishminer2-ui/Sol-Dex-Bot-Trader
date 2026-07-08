@echo off
REM uninstall_startup.bat - Remove Windows login auto-start
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_startup.ps1" -Action remove
pause
