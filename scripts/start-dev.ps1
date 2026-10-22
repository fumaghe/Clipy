# Avvio locale Windows (PowerShell). Uso:  .\scripts\start-dev.ps1 [-Prod]
param([switch]$Prod)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
foreach ($b in "ffmpeg", "ffprobe", "python", "node") {
  if (-not (Get-Command $b -ErrorAction SilentlyContinue)) { Write-Error "ERRORE: '$b' non trovato nel PATH. Vedi README (winget install Gyan.FFmpeg Python.Python.3.12 OpenJS.NodeJS.LTS)"; exit 1 }
}
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Creato .env da .env.example (nessun segreto: modalita DEMO)" }
foreach ($d in "originals","rendered","previews","temp","public","fixtures") { New-Item -ItemType Directory -Force -Path "data\$d" | Out-Null }
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q -r server\requirements.txt
if (-not (Test-Path frontend\node_modules)) { Push-Location frontend; npm install --no-audit --no-fund; Pop-Location }
if ($Prod) {
  Push-Location frontend; npm run build; Pop-Location
  Write-Host "Clipy su http://127.0.0.1:8000"
  Set-Location server
  & ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} else {
  $api = Start-Process -PassThru -WorkingDirectory "$Root\server" -FilePath "$Root\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000"
  Write-Host "Backend http://127.0.0.1:8000 - Frontend http://127.0.0.1:5173 (CTRL+C per fermare)"
  try { Push-Location frontend; npm run dev } finally { Stop-Process -Id $api.Id -ErrorAction SilentlyContinue }
}
