$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv/Scripts/python.exe')) { throw 'Create the virtual environment and install requirements first; see docs/RUNBOOK.md' }
& .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
