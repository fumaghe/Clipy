@echo off
REM Launcher Windows: avvia Clipy in modalita produzione locale (http://127.0.0.1:8000)
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" -Prod
