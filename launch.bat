@echo off
REM launch.bat - Wrapper for Start Bot.bat (use Start Bot.bat for Desktop shortcuts)
cd /d "%~dp0"
call "%~dp0Start Bot.bat" %*
