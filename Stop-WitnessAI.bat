@echo off
setlocal
cd /d "%~dp0"
title Witness Intel - Stop services
powershell.exe -NoProfile -File "%~dp0scripts\stop.ps1" %*
if errorlevel 1 (
  echo Shutdown needs attention. Read the message above.
  pause
  exit /b 1
)
endlocal
exit /b 0
