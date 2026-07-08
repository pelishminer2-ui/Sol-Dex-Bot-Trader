@echo off
REM install.bat - Create .venv and install requirements (safe to re-run)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
    echo.
    echo Install failed. See messages above.
    pause
    exit /b 1
)
echo.
echo Done. Double-click Start Bot.bat to start the bot.
pause
