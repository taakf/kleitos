# =============================================================================
# Axion by 4Labs — Local Launcher (Windows / PowerShell)
#
# One-command "double-click and go" path for Windows end users.
#
#   1. Finds Python 3.11+ (python, python3, py -3)
#   2. Creates .venv if missing
#   3. Installs requirements.txt
#   4. Creates the data directory (%USERPROFILE%\axion-data or AXION_DATA_DIR)
#   5. Runs migrations on the SQLite database
#   6. Starts uvicorn on 127.0.0.1:${env:AXION_PORT or 7777}
#   7. Opens the dashboard in the default browser
#
# Exits cleanly on port conflicts, missing Python, dep failures.
# No Docker required. Pure local install.
#
# To run from PowerShell:
#   PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
# Or from cmd.exe:
#   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1
# =============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Write-Info($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Blue }
function Write-Ok($msg)   { Write-Host "[OK]    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

# Resolve project root (this script lives in scripts\)
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $ProjectRoot

# Configuration
$Port = if ($env:AXION_PORT) { $env:AXION_PORT }
        elseif ($env:KLEITOS_PORT) { $env:KLEITOS_PORT }
        else { '7777' }
$VHost = '127.0.0.1'
$VenvDir = Join-Path $ProjectRoot '.venv'

# Data dir: prefer existing kleitos-data for back-compat, else axion-data
$DataDir = if ($env:AXION_DATA_DIR) { $env:AXION_DATA_DIR }
           elseif ($env:KLEITOS_DATA_DIR) { $env:KLEITOS_DATA_DIR }
           else {
               $klei = Join-Path $env:USERPROFILE 'kleitos-data'
               $axn  = Join-Path $env:USERPROFILE 'axion-data'
               if ((Test-Path $klei) -and -not (Test-Path $axn)) { $klei } else { $axn }
           }

$env:AXION_DATA_DIR   = $DataDir
$env:AXION_DB_PATH    = Join-Path $DataDir 'db\kleitos.db'
$env:KLEITOS_DATA_DIR = $DataDir
$env:KLEITOS_DB_PATH  = Join-Path $DataDir 'db\kleitos.db'

Write-Host ""
Write-Host "  Axion by 4Labs - Local Launcher" -ForegroundColor Cyan
Write-Host ""
Write-Info "Project root : $ProjectRoot"
Write-Info "Data dir     : $DataDir"
Write-Info "Port         : $Port"
Write-Host ""

# 1. Find Python 3.11+
function Find-Python {
    $candidates = @(
        @{ cmd = 'python3.13'; args = @() },
        @{ cmd = 'python3.12'; args = @() },
        @{ cmd = 'python3.11'; args = @() },
        @{ cmd = 'python';     args = @() },
        @{ cmd = 'python3';    args = @() },
        @{ cmd = 'py';         args = @('-3.13') },
        @{ cmd = 'py';         args = @('-3.12') },
        @{ cmd = 'py';         args = @('-3.11') },
        @{ cmd = 'py';         args = @('-3') }
    )
    foreach ($c in $candidates) {
        $exe = Get-Command $c.cmd -ErrorAction SilentlyContinue
        if ($null -eq $exe) { continue }
        try {
            $allArgs = $c.args + @('-c', 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            $verOut = & $c.cmd @allArgs 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $verOut) { continue }
            $parts = $verOut.Trim().Split('.')
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -ge 3 -and $minor -ge 11) {
                return [pscustomobject]@{ Cmd = $c.cmd; Args = $c.args; Version = "$major.$minor" }
            }
        } catch { continue }
    }
    return $null
}

$Py = Find-Python
if ($null -eq $Py) {
    Write-Fail "Python 3.11 or newer is required but was not found on PATH."
    Write-Host ""
    Write-Host "  Install from https://www.python.org/downloads/"
    Write-Host "  IMPORTANT: tick 'Add Python to PATH' during install."
    Write-Host ""
    exit 1
}
Write-Ok ("Python $($Py.Version) ($($Py.Cmd) $($Py.Args -join ' '))")

# 2. Virtual environment
if (-not (Test-Path $VenvDir)) {
    Write-Info "Creating virtual environment at .venv ..."
    $venvArgs = $Py.Args + @('-m', 'venv', $VenvDir)
    & $Py.Cmd @venvArgs
    if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to create venv."; exit 1 }
    Write-Ok "Virtual environment created"
} else {
    Write-Ok "Virtual environment exists"
}

$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Write-Fail "Virtual environment is broken (missing $VenvPy). Delete .venv and retry."
    exit 1
}

# 3. Dependencies (skip if marker is newer than requirements.txt)
$Marker = Join-Path $VenvDir '.deps-installed'
$ReqFile = Join-Path $ProjectRoot 'requirements.txt'
$needInstall = $true
if ((Test-Path $Marker) -and (Test-Path $ReqFile)) {
    if ((Get-Item $Marker).LastWriteTime -ge (Get-Item $ReqFile).LastWriteTime) {
        $needInstall = $false
    }
}
if ($needInstall) {
    Write-Info "Installing dependencies (this can take 1-2 minutes on first run) ..."
    & $VenvPy -m pip install --upgrade pip --quiet
    & $VenvPy -m pip install -r $ReqFile --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed. Check your internet connection."
        exit 1
    }
    Set-Content -Path $Marker -Value (Get-Date).ToString('o')
    Write-Ok "Dependencies installed"
} else {
    Write-Ok "Dependencies up to date"
}

# 4. Data directory
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'db')      | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'logs')    | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'backups') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'exports') | Out-Null
Write-Ok "Data dir ready ($DataDir)"

# 5. Migrations
# scripts\migrate.py prints clean, customer-facing output and returns a
# structured exit code so this launcher doesn't have to format messages
# itself. Exit codes are documented at the top of that script.
Write-Info "Running migrations ..."
& $VenvPy (Join-Path $ProjectRoot 'scripts\migrate.py')
switch ($LASTEXITCODE) {
    0 {
        Write-Ok "Database is at schema head"
    }
    2 {
        Write-Host ""
        Write-Fail "Cannot start: database is newer than this version of Axion."
        Write-Host "    See the message above for recovery steps."
        exit 2
    }
    3 {
        Write-Host ""
        Write-Fail "Cannot start: database is corrupt or unreadable."
        Write-Host "    See the message above for recovery steps."
        exit 3
    }
    4 {
        Write-Host ""
        Write-Fail "Cannot start: pre-migration backup failed."
        Write-Host "    See the message above for recovery steps."
        exit 4
    }
    default {
        Write-Host ""
        Write-Fail "Migrations failed (see above)."
        exit 1
    }
}

# 6. Port check — if Axion already running, open the dashboard and exit
$portInUse = $false
try {
    $tcp = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
    if ($tcp) { $portInUse = $true }
} catch { }

if ($portInUse) {
    try {
        $r = Invoke-WebRequest -Uri "http://${VHost}:${Port}/api/v1/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            Write-Ok "Axion is already running at http://${VHost}:${Port}"
            Start-Process "http://${VHost}:${Port}/dashboard/"
            exit 0
        }
    } catch { }
    Write-Fail "Port $Port is in use by another application."
    Write-Host ""
    Write-Host "  Close the other application, or set AXION_PORT to a different port:"
    Write-Host "    $env:AXION_PORT='7778'; .\scripts\run_local.ps1"
    Write-Host ""
    exit 2
}

# 7. Start uvicorn in background, wait for health, open browser, then attach
Write-Host ""
Write-Info "Starting Axion on http://${VHost}:${Port} ..."
Write-Host ""

# Start uvicorn as a background job so we can monitor health, then surface logs.
$LogStdout = Join-Path $DataDir 'logs\axion-stdout.log'
$LogStderr = Join-Path $DataDir 'logs\axion-stderr.log'

$proc = Start-Process -FilePath $VenvPy `
    -ArgumentList @('-m','uvicorn','src.main:app','--host',$VHost,'--port',$Port,'--log-level','info') `
    -WorkingDirectory $ProjectRoot `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $LogStdout `
    -RedirectStandardError $LogStderr

# Wait up to 30s for /api/v1/health
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://${VHost}:${Port}/api/v1/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    if ($proc.HasExited) {
        Write-Fail "uvicorn exited unexpectedly. See $LogStderr"
        exit 1
    }
}

if (-not $healthy) {
    Write-Fail "Axion did not become healthy within 30 seconds. See $LogStderr"
    if (-not $proc.HasExited) { try { $proc.Kill() } catch { } }
    exit 1
}

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "    Axion is running." -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "    Dashboard : http://${VHost}:${Port}" -ForegroundColor White
Write-Host "    API docs  : http://${VHost}:${Port}/docs"
Write-Host "    Data      : $DataDir"
Write-Host "    Logs      : $LogStdout"
Write-Host "    Stop      : Ctrl+C in this window, or close the window"
Write-Host ""

Start-Process "http://${VHost}:${Port}/dashboard/"

# Wait for the uvicorn process — Ctrl+C will propagate.
Wait-Process -Id $proc.Id
