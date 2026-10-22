#!/usr/bin/env bash
# Avvio locale Linux/macOS. Uso:  scripts/start-dev.sh [--prod]
#   (default) backend :8000 + frontend dev (Vite) :5173     --prod  = solo backend che serve la build su :8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || { echo "ERRORE: ffmpeg/ffprobe non trovati nel PATH (apt install ffmpeg / brew install ffmpeg)"; exit 1; }
command -v python3 >/dev/null || { echo "ERRORE: Python >= 3.11 richiesto"; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "Creato .env da .env.example (nessun segreto: modalità DEMO)"; }
mkdir -p data/{originals,rendered,previews,temp,public,fixtures}
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r server/requirements.txt
[ -d frontend/node_modules ] || (cd frontend && npm install --no-audit --no-fund)
if [ "${1:-}" = "--prod" ]; then
  (cd frontend && npm run build)
  echo "Clipy su http://127.0.0.1:8000"
  cd server && exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
fi
trap 'kill 0' EXIT
(cd server && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
(cd frontend && npm run dev) &
echo "Backend http://127.0.0.1:8000  ·  Frontend http://127.0.0.1:5173"
wait
