# Controlli preliminari per Clipy su Windows. Uso:  .\scripts\check-windows.ps1 [-Port 8000]
# Non modifica nulla (tranne creare data\ se manca la verifica di scrittura). Exit code 0 = pronto, 1 = c'e' almeno un FAIL.
# NOTA: scritto e rivisto ma NON eseguito su Windows reale dallo sviluppo: al primo uso segnala qualsiasi anomalia.
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "clipy-common.ps1")

Write-Host "== Clipy - controllo ambiente Windows ==" -ForegroundColor Cyan
Write-Host "Cartella progetto: $Root"
if ($Root -match "\s") { Write-Status INFO "Il percorso contiene spazi: gli script lo gestiscono (tutti i percorsi sono tra virgolette)." }

# PowerShell
if ($PSVersionTable.PSVersion.Major -ge 5) { Write-Status OK "PowerShell $($PSVersionTable.PSVersion)" } else { Write-Status FAIL "PowerShell $($PSVersionTable.PSVersion) troppo vecchio" "Serve Windows PowerShell 5.1 o superiore." }

# Python
$py = Find-Python
if ($py) {
  Write-Status OK "Python $($py.Version) ($($py.Exe))"
  if ([version]$py.Version -gt [version]"3.13") { Write-Status WARN "Python $($py.Version) e' piu' recente di quelli testati (3.11-3.13)" "Se faster-whisper non si installa usa Python 3.12: winget install Python.Python.3.12" }
} else {
  Write-Status FAIL "Python 3.11+ non trovato (lo stub del Microsoft Store non vale)" "winget install Python.Python.3.12  (poi riapri il terminale)"
}

# Node / npm
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  $nv = (Invoke-Native "node" @("--version")).Output
  if ($nv -match "v(\d+)\." -and [int]$Matches[1] -ge 20) { Write-Status OK "Node $nv" } else { Write-Status FAIL "Node $nv troppo vecchio (serve >= 20)" "winget install OpenJS.NodeJS.LTS" }
} else { Write-Status FAIL "Node.js non trovato" "winget install OpenJS.NodeJS.LTS  (poi riapri il terminale)" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Write-Status FAIL "npm non trovato" "Reinstalla Node.js LTS." }

# FFmpeg
$ff = Find-Ffmpeg
if ($ff) {
  $ver = (Invoke-Native $ff.Ffmpeg @("-version")).Output -split "`r?`n" | Select-Object -First 1
  Write-Status OK "FFmpeg: $ver"
  if ($ff.Dir) { Write-Status WARN "ffmpeg/ffprobe non sono nel PATH ma sono stati trovati in $($ff.Dir)" "Clipy li usera' comunque (CLIPY_FFMPEG_DIR impostato dallo script di avvio)." }
  $filters = (Invoke-Native $ff.Ffmpeg @("-hide_banner", "-filters")).Output
  if ($filters -match "drawtext") { Write-Status OK "Filtro drawtext disponibile (testo overlay)" } else { Write-Status FAIL "Questo FFmpeg non ha il filtro drawtext" "Installa la build 'full' di Gyan: winget install Gyan.FFmpeg" }
} else { Write-Status FAIL "ffmpeg/ffprobe non trovati" "winget install Gyan.FFmpeg  (poi riapri il terminale)" }

# Porta
if (Test-PortFree $Port) { Write-Status OK "Porta $Port libera" } else { Write-Status FAIL "Porta $Port gia' in uso" "Chiudi l'altra istanza di Clipy oppure usa: .\scripts\start-dev.ps1 -Prod -Port 8001" }

# Disco e scrittura
$data = Join-Path $Root "data"
try {
  New-Item -ItemType Directory -Force -Path $data | Out-Null
  $probe = Join-Path $data ".write-test"; "ok" | Out-File -LiteralPath $probe -Encoding ascii; Remove-Item -LiteralPath $probe
  Write-Status OK "Cartella dati scrivibile: $data"
} catch { Write-Status FAIL "Cartella dati non scrivibile: $data" "Sposta il progetto in una cartella utente (es. C:\Users\<tu>\Clipy), non in Program Files." }
try {
  $drive = (Get-Item -LiteralPath $Root).PSDrive
  $freeGb = [math]::Round($drive.Free / 1GB, 1)
  if ($freeGb -lt 5) { Write-Status WARN "Spazio libero su $($drive.Name): $freeGb GB" "Servono alcuni GB per clip, render e modelli Whisper." } else { Write-Status OK "Spazio libero su $($drive.Name): $freeGb GB" }
} catch { Write-Status INFO "Spazio libero non determinabile" }

# Configurazione
$envFile = Join-Path $Root ".env"
if (Test-Path -LiteralPath $envFile) {
  Write-Status OK ".env presente"
  if ((Get-Content -LiteralPath $envFile -Raw) -match "(?m)^\s*CLIPY_HOST\s*=\s*(0\.0\.0\.0|::)") { Write-Status WARN "CLIPY_HOST espone l'API in rete" "Clipy non ha login: lascia 127.0.0.1." }
} else { Write-Status INFO ".env assente: verra' creato da .env.example al primo avvio (modalita' DEMO, nessun segreto)" }

# Dipendenze gia' installate (informativo)
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPy) {
  $r = Invoke-Native $venvPy @("-c", "import fastapi, sqlalchemy, httpx; print('ok')")
  if ($r.ExitCode -eq 0) { Write-Status OK "Ambiente Python (.venv) con dipendenze" } else { Write-Status WARN ".venv presente ma dipendenze mancanti" "Verranno installate da start-clipy.bat." }
  $w = Invoke-Native $venvPy @("-c", "import faster_whisper; print('ok')")
  if ($w.ExitCode -eq 0) { Write-Status OK "faster-whisper installato" } else { Write-Status INFO "faster-whisper non installato (opzionale)" "Per la trascrizione locale: .\.venv\Scripts\pip install faster-whisper" }
} else { Write-Status INFO ".venv non ancora creato (lo fa start-clipy.bat)" }
if (Test-Path -LiteralPath (Join-Path $Root "frontend\node_modules")) { Write-Status OK "Dipendenze npm installate" } else { Write-Status INFO "node_modules non ancora installato (lo fa start-clipy.bat)" }

Write-Host ""
if ($script:ClipyFailures -gt 0) {
  Write-Host "$($script:ClipyFailures) problema/i da risolvere prima di avviare Clipy." -ForegroundColor Red
  exit 1
}
Write-Host "Tutto pronto ($($script:ClipyWarnings) avvisi). Avvia con start-clipy.bat" -ForegroundColor Green
exit 0
