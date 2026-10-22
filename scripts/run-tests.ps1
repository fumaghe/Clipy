# Quality gate completo (Windows). Uso: .\scripts\run-tests.ps1 [-E2E]
param([switch]$E2E)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location "$Root\server"
Write-Host "== ruff"; & ..\.venv\Scripts\ruff.exe check app
Write-Host "== pytest"; & ..\.venv\Scripts\pytest.exe app/tests -q
Set-Location "$Root\frontend"
Write-Host "== typecheck"; npm run typecheck
Write-Host "== lint"; npm run lint
Write-Host "== vitest"; npm test
Write-Host "== build"; npm run build
if ($E2E) { Write-Host "== playwright"; npx playwright test }
Write-Host "TUTTO OK"
