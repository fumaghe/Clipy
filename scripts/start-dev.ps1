# Avvio locale Windows (PowerShell). Uso:  .\scripts\start-dev.ps1 [-Prod] [-Port 8000] [-NoBrowser] [-SkipChecks]
#   -Prod : backend che serve il frontend compilato su http://127.0.0.1:<Port>  (usato da start-clipy.bat)
#   senza -Prod : backend + Vite (sviluppo) su :5173
# NOTA: scritto per Windows PowerShell 5.1/7, NON eseguito su Windows reale dallo sviluppo (sandbox Linux).
param([switch]$Prod, [int]$Port = 8000, [switch]$NoBrowser, [switch]$SkipChecks)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
. (Join-Path $PSScriptRoot "clipy-common.ps1")
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"

try {
  Write-Host "== Clipy ==" -ForegroundColor Cyan

  # 1. prerequisiti (senza presumere PATH)
  $py = Find-Python
  $ff = Find-Ffmpeg
  if (-not $SkipChecks) {
    & (Join-Path $PSScriptRoot "check-windows.ps1") -Port $Port
    if ($LASTEXITCODE -ne 0) { throw "Controlli preliminari falliti: risolvi i punti [FAIL] sopra e rilancia." }
  }
  if (-not $py) { throw "Python 3.11+ non trovato." }
  if (-not $ff) { throw "ffmpeg/ffprobe non trovati." }
  if ($ff.Dir) { $env:CLIPY_FFMPEG_DIR = $ff.Dir }

  # 2. configurazione e cartelle dati
  if (-not (Test-Path -LiteralPath ".env")) { Copy-Item -LiteralPath ".env.example" -Destination ".env"; Write-Host "Creato .env da .env.example (nessun segreto: modalita' DEMO)" }
  foreach ($d in "originals", "rendered", "previews", "temp", "public", "fixtures", "logs", "models") { New-Item -ItemType Directory -Force -Path (Join-Path "data" $d) | Out-Null }

  # 3. ambiente Python (solo se serve)
  $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host "Creo l'ambiente Python (.venv) con Python $($py.Version)..."
    & $py.Exe -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Creazione .venv fallita." }
  }
  $reqFile = Join-Path $Root "server\requirements.txt"
  $reqStamp = Join-Path $Root ".venv\.clipy-requirements.sha"
  $reqHash = Get-FileSha $reqFile
  if ((-not (Test-Path -LiteralPath $reqStamp)) -or ((Get-Content -LiteralPath $reqStamp -Raw).Trim() -ne $reqHash)) {
    Write-Host "Installo le dipendenze Python (solo al primo avvio o dopo un aggiornamento)..."
    & $venvPy -m pip install --disable-pip-version-check -q -r $reqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install fallito (controlla la connessione)." }
    Set-Content -LiteralPath $reqStamp -Value $reqHash
  }

  # 4. frontend
  Push-Location -LiteralPath (Join-Path $Root "frontend")
  try {
    $lockStamp = Join-Path $Root "frontend\node_modules\.clipy-lock.sha"
    $lockHash = Get-FileSha (Join-Path $Root "frontend\package-lock.json")
    if ((-not (Test-Path -LiteralPath "node_modules")) -or (-not (Test-Path -LiteralPath $lockStamp)) -or ((Get-Content -LiteralPath $lockStamp -Raw).Trim() -ne $lockHash)) {
      Write-Host "Installo le dipendenze npm..."
      npm install --no-audit --no-fund
      if ($LASTEXITCODE -ne 0) { throw "npm install fallito." }
      Set-Content -LiteralPath $lockStamp -Value $lockHash
    }
    if ($Prod) {
      $dist = Join-Path $Root "frontend\dist\index.html"
      $newestSrc = Get-ChildItem -LiteralPath "src" -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
      $needBuild = (-not (Test-Path -LiteralPath $dist)) -or ($newestSrc -and $newestSrc.LastWriteTime -gt (Get-Item -LiteralPath $dist).LastWriteTime)
      if ($needBuild) {
        Write-Host "Compilo il frontend..."
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Build del frontend fallita." }
      }
    }
  } finally { Pop-Location }

  # 5. avvio
  $url = "http://127.0.0.1:$Port"
  if ($Prod) {
    if (-not $NoBrowser) {
      # apre il browser quando /health risponde (processo separato: non blocca il server)
      $opener = "for(`$i=0;`$i -lt 60;`$i++){try{Invoke-WebRequest -UseBasicParsing -Uri '$url/health' -TimeoutSec 2 | Out-Null; Start-Process '$url'; break}catch{Start-Sleep -Seconds 1}}"
      Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-Command", $opener) | Out-Null
    }
    Write-Host "Clipy in esecuzione su $url  (CTRL+C per fermare in modo pulito)" -ForegroundColor Green
    Write-Host "Modalita' predefinita: DEMO / DRY RUN - nessuna pubblicazione reale finche' non la abiliti in Settings."
    Set-Location -LiteralPath (Join-Path $Root "server")
    & $venvPy -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Il server si e' fermato con codice $code. Log: data\logs\clipy.log" }
  } else {
    $api = Start-Process -PassThru -WorkingDirectory (Join-Path $Root "server") -FilePath $venvPy -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port")
    Write-Host "Backend $url - Frontend http://127.0.0.1:5173 (CTRL+C per fermare)"
    try { Push-Location -LiteralPath (Join-Path $Root "frontend"); npm run dev } finally { Pop-Location; Stop-Process -Id $api.Id -ErrorAction SilentlyContinue }
  }
  exit 0
} catch {
  Write-Host ""
  Write-Host "ERRORE: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Se serve aiuto: docs\LIVE_TEST_CHECKLIST.md (sezione 1) e data\logs\clipy.log" -ForegroundColor DarkGray
  exit 1
}
