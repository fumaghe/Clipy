# Clipy

Trasforma automaticamente le clip Twitch di **iDensTv** in video TikTok verticali: scopre le nuove clip, le scarica (portrait se disponibile), le trascrive in locale
(faster-whisper), genera titolo overlay e caption con un LLM, renderizza un MP4 1080×1920 con FFmpeg secondo un template, e le mette in una coda persistente
(fino a 4 post/giorno, configurabile) pubblicata via Buffer. Solo le clip **dubbie** vanno in revisione; le altre scorrono da sole.

> **Stato:** V1.1 (STEP 2: hardening integrazioni). Funziona subito in **modalità demo** (Twitch/AI/publisher simulati e marcati `SIMULATO`). La pubblicazione reale è **disattivata** (`DRY RUN`) finché non
> la abiliti tu da Settings. Vedi `IMPLEMENTATION_PROGRESS.md` e `docs/TEST_REPORT.md` per cosa è verificato e cosa è *NON VALIDATO LIVE*.

## Requisiti
- Python 3.11–3.13 (consigliato 3.12), Node ≥ 20, **FFmpeg + ffprobe** (build *full* con `drawtext`; trovati anche fuori dal `PATH`, vedi `CLIPY_FFMPEG_DIR`)
- (opzionale) `pip install faster-whisper` per la trascrizione locale reale; GPU CUDA facoltativa (fallback CPU)

## Avvio rapido
**Windows (PowerShell)**
```powershell
winget install Gyan.FFmpeg Python.Python.3.12 OpenJS.NodeJS.LTS   # una tantum
.\scripts\check-windows.ps1       # controlli preliminari (facoltativo, non modifica nulla)
start-clipy.bat                   # controlla l'ambiente, avvia backend + frontend compilato e apre il browser (la finestra resta aperta)
```
**Linux / macOS**
```bash
scripts/start-dev.sh --prod        # build + backend su http://127.0.0.1:8000
scripts/start-dev.sh               # sviluppo: backend :8000 + Vite :5173
```
Apri http://127.0.0.1:8000 (prod) o http://127.0.0.1:5173 (dev). Al primo avvio l'app è in **demo**:

1. Overview → **Run once**: importa le clip simulate delle ultime 24 h (la sesta, di 4 giorni fa, resta fuori).
2. Il worker scarica, analizza, renderizza e applica la review gate. Le clip dubbie compaiono in **Review Inbox**.
3. **Coda**: le clip idonee occupano gli slot 10:00 / 13:30 / 17:30 / 21:00 (Europe/Rome), max 4/giorno. In DRY RUN i job risultano «Simulato».
4. **Template Studio**: anteprima reale generata dallo stesso motore FFmpeg del render.

Singoli processi: backend `cd server && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (il worker parte nello stesso processo se
`CLIPY_RUN_WORKER=true`; un solo leader per volta grazie a un lease nel DB), frontend `cd frontend && npm run dev`.

## Passare al reale (in ordine)
**Guida passo-passo con risultati attesi e rimedi: [`docs/LIVE_TEST_CHECKLIST.md`](docs/LIVE_TEST_CHECKLIST.md).** Dettagli tecnici in `docs/INTEGRATIONS.md`. *Settings → Diagnostica integrazioni* mostra lo stato di ogni servizio (anche cosa è ancora simulato). Riassunto: app Twitch + Client ID/Secret → autorizza `iDensTv` → chiave LLM → (opz.) `faster-whisper` → account Buffer + TikTok +
API key → bucket R2/S3 con URL pubblico → Settings → **Pubblicazione reale** (frase di conferma). Nessun post reale parte prima di quest'ultimo passo.

## Test
```bash
scripts/run-tests.sh          # ruff + pytest + tsc + eslint + vitest + build
scripts/run-tests.sh --e2e    # + Playwright (backend reale in demo)
```

## Documentazione
`docs/STEP_2_1_WINDOWS_DEMO_FIXES.md` · `docs/LIVE_TEST_CHECKLIST.md` · `ARCHITECTURE.md` · `docs/DECISIONS.md` · `docs/INTEGRATIONS.md` · `docs/OPERATIONS.md` · `docs/TEST_REPORT.md` · `docs/TEMPLATE_REFERENCE.md` · `IMPLEMENTATION_PROGRESS.md`
