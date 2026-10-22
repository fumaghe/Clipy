#!/usr/bin/env bash
# Esegue l'intero quality gate. Uso: scripts/run-tests.sh [--e2e]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/server"
echo "== ruff"; ../.venv/bin/ruff check app
echo "== pytest"; ../.venv/bin/pytest app/tests -q
cd "$ROOT/frontend"
echo "== typecheck"; npm run typecheck
echo "== lint"; npm run lint
echo "== vitest"; npm test
echo "== build"; npm run build
if [ "${1:-}" = "--e2e" ]; then echo "== playwright e2e"; npx playwright test; fi
echo "TUTTO OK"
