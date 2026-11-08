@echo off
setlocal
cd /d "%~dp0"
title Witness Intel - Launcher
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. See docs\RUNBOOK.md for setup.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u "scripts\launch.py" %*
set "witness_exit=%errorlevel%"
if not "%witness_exit%"=="0" pause
endlocal & exit /b %witness_exit%
