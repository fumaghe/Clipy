@echo off
REM Launcher Windows: controlla l'ambiente, avvia Clipy (backend + frontend compilato) e apre la dashboard.
REM La finestra NON si chiude da sola: in caso di errore resta visibile il messaggio.
setlocal
cd /d "%~dp0"
where powershell >nul 2>nul
if errorlevel 1 (
  echo ERRORE: PowerShell non trovato. Serve Windows PowerShell 5.1 o superiore.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" -Prod
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo Clipy si e' fermato con codice %RC%. Leggi il messaggio sopra e data\logs\clipy.log
) else (
  echo Clipy e' stato fermato.
)
pause
exit /b %RC%
