# Quality gate completo (Windows). Uso: .\scripts\run-tests.ps1 [-E2E]
param([switch]$E2E)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
function Step($name, [scriptblock]$cmd) { Write-Host "== $name"; & $cmd; if ($LASTEXITCODE -ne 0) { throw "Fallito: $name" } }
Set-Location -LiteralPath "$Root\server"
Step "ruff" { & ..\.venv\Scripts\ruff.exe check app }
Step "pytest" { & ..\.venv\Scripts\pytest.exe app/tests -q }
Set-Location -LiteralPath "$Root\frontend"
Step "typecheck" { npm run typecheck }
Step "lint" { npm run lint }
Step "vitest" { npm test }
Step "build" { npm run build }
if ($E2E) { Step "playwright" { npx playwright test } }
Write-Host "TUTTO OK"
