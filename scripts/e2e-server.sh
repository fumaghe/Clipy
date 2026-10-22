#!/usr/bin/env bash
# Backend per i test e2e: DB e media in una cartella temporanea, demo mode, tick rapidi.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CLIPY_DATA_DIR="$(mktemp -d)"
export CLIPY_PORT=8765 CLIPY_RUN_WORKER=true CLIPY_PIPELINE_TICK_SECONDS=2 CLIPY_PUBLISHER_TICK_SECONDS=5
cd "$ROOT/server"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
