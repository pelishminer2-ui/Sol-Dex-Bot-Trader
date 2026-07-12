@echo off
REM Stop the packaged Sol Dex Bot Trader process (no console required while running).
REM Prefer the tray icon "Quit" when available; this is a fallback.
taskkill /IM SolDexBotTrader.exe /F >nul 2>&1
if errorlevel 1 (
  echo SolDexBotTrader.exe is not running.
) else (
  echo Sol Dex Bot Trader stopped.
)
exit /b 0
