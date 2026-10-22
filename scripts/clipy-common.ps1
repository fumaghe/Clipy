# Funzioni condivise da start-dev.ps1 e check-windows.ps1 (dot-source: . "$PSScriptRoot\clipy-common.ps1").
# Compatibile con Windows PowerShell 5.1 e PowerShell 7. NON TESTATO su Windows reale dallo sviluppo (sandbox Linux).

$script:ClipyFailures = 0
$script:ClipyWarnings = 0

function Write-Status {
  param([ValidateSet("OK", "WARN", "FAIL", "INFO")][string]$Level, [string]$Message, [string]$Hint = "")
  $color = @{ OK = "Green"; WARN = "Yellow"; FAIL = "Red"; INFO = "Cyan" }[$Level]
  Write-Host ("[{0,-4}] {1}" -f $Level, $Message) -ForegroundColor $color
  if ($Hint) { Write-Host ("       -> {0}" -f $Hint) -ForegroundColor DarkGray }
  if ($Level -eq "FAIL") { $script:ClipyFailures++ }
  if ($Level -eq "WARN") { $script:ClipyWarnings++ }
}

# Esegue un comando nativo e restituisce l'output (stdout+stderr) senza far fallire lo script se il comando manca.
function Invoke-Native {
  param([string]$File, [string[]]$Arguments = @())
  try {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $out = & $File @Arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return @{ Output = $out.Trim(); ExitCode = $code }
  } catch {
    $ErrorActionPreference = $prev
    return @{ Output = $_.Exception.Message; ExitCode = 9009 }
  }
}

# Trova un Python >= 3.11 (preferisce 3.12/3.13). Scarta lo "stub" del Microsoft Store (python.exe senza interprete reale).
function Find-Python {
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in "3.12", "3.13", "3.11") { $candidates += ,@("py", @("-$v")) }
  }
  foreach ($n in "python", "python3") {
    $cmd = Get-Command $n -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $candidates += ,@($cmd.Source, @()) }
  }
  foreach ($c in $candidates) {
    $r = Invoke-Native $c[0] ($c[1] + @("-c", "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)"))
    if ($r.ExitCode -eq 0 -and $r.Output -match "^(\d+)\.(\d+)") {
      $maj = [int]$Matches[1]; $min = [int]$Matches[2]
      if ($maj -eq 3 -and $min -ge 11) {
        $exe = ($r.Output -split "`r?`n")[1]
        return @{ Exe = $exe; Version = "$maj.$min" }
      }
    }
  }
  return $null
}

# Cerca ffmpeg/ffprobe nel PATH e nelle cartelle tipiche (winget, Chocolatey, C:\ffmpeg). Imposta CLIPY_FFMPEG_DIR se trovato fuori PATH.
function Find-Ffmpeg {
  $dirs = @()
  if ($env:CLIPY_FFMPEG_DIR) { $dirs += $env:CLIPY_FFMPEG_DIR }
  if ($env:LOCALAPPDATA) { $dirs += (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links") }
  foreach ($base in $env:ProgramFiles, $env:ProgramData, "C:\") {
    if ($base) { $dirs += (Join-Path $base "ffmpeg\bin") }
  }
  if ($env:ChocolateyInstall) { $dirs += (Join-Path $env:ChocolateyInstall "bin") }
  $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
  $fp = Get-Command ffprobe -ErrorAction SilentlyContinue
  if ($ff -and $fp) { return @{ Ffmpeg = $ff.Source; Ffprobe = $fp.Source; Dir = "" } }
  foreach ($d in $dirs) {
    $f1 = Join-Path $d "ffmpeg.exe"; $f2 = Join-Path $d "ffprobe.exe"
    if ((Test-Path -LiteralPath $f1) -and (Test-Path -LiteralPath $f2)) { return @{ Ffmpeg = $f1; Ffprobe = $f2; Dir = $d } }
  }
  return $null
}

function Test-PortFree {
  param([int]$Port)
  try {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
    $l.Start(); $l.Stop(); return $true
  } catch { return $false }
}

function Get-FileSha {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return "" }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}
