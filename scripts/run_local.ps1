# =============================================================================
# Axion by 4Labs — Local Launcher (Windows / PowerShell)
#
# Stages:
#   1/7  Detect Python 3.11+
#   2/7  Create/use .venv
#   3/7  Install/update dependencies
#   4/7  Prepare the data directory + rotate logs
#   5/7  Run migrations (scripts\migrate.py owns customer-facing messages)
#   6/7  Check port 7777 (or AXION_PORT) and show PID/process on conflict
#   7/7  Start uvicorn on 127.0.0.1:${PORT}, open dashboard, mirror to
#        axion-server.log
#
# To run from PowerShell:
#   PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
# Or from cmd.exe:
#   powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1
# =============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# ── Resolve project root ────────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $ProjectRoot

# ── Configuration ───────────────────────────────────────────────────────────
$Port = if ($env:AXION_PORT) { $env:AXION_PORT }
        elseif ($env:KLEITOS_PORT) { $env:KLEITOS_PORT }
        else { '7777' }
$VHost = '127.0.0.1'
$VenvDir = Join-Path $ProjectRoot '.venv'

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

$LogDir       = Join-Path $DataDir 'logs'
$LauncherLog  = Join-Path $LogDir 'axion-launcher.log'
$ServerLog    = Join-Path $LogDir 'axion-server.log'
$MigrateLog   = Join-Path $LogDir 'axion-migration.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ── Output helpers (console + axion-launcher.log) ───────────────────────────
function Write-LogLine([string]$msg) {
    Add-Content -Path $LauncherLog -Value $msg
}
function Write-Stage([string]$n, [string]$msg) {
    Write-Host "[$n]    $msg" -ForegroundColor Blue
    Write-LogLine "[$n]    $msg"
}
function Write-Info([string]$msg) {
    Write-Host "[INFO]  $msg" -ForegroundColor Blue
    Write-LogLine "[INFO]  $msg"
}
function Write-Ok([string]$msg) {
    Write-Host "[OK]    $msg" -ForegroundColor Green
    Write-LogLine "[OK]    $msg"
}
function Write-Warn([string]$msg) {
    Write-Host "[WARN]  $msg" -ForegroundColor Yellow
    Write-LogLine "[WARN]  $msg"
}
function Write-Fail([string]$msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
    Write-LogLine "[ERROR] $msg"
}
function Write-FailureHint() {
    Write-Host ""
    Write-Host "  For diagnostics:" -ForegroundColor DarkGray
    Write-Host "    $VenvDir\Scripts\python.exe scripts\support_bundle.py" -ForegroundColor DarkGray
    Write-Host "    (creates a redacted zip at $DataDir\support\)" -ForegroundColor DarkGray
    Write-Host "  Launcher log: $LauncherLog" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Axion by 4Labs - Local Launcher" -ForegroundColor Cyan
Write-Host ""
Write-Info "Project root : $ProjectRoot"
Write-Info "Data dir     : $DataDir"
Write-Info "Logs         : $LogDir"
Write-Info "Port         : $Port"
Write-Host ""

Write-LogLine ""
Write-LogLine "=== launcher run $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ') ==="
Write-LogLine "  PROJECT_ROOT=$ProjectRoot"
Write-LogLine "  DATA_DIR=$DataDir"
Write-LogLine "  PORT=$Port"

# Pre-rotate logs so growing files don't accumulate forever.
$preVenvPy = Join-Path $VenvDir 'Scripts\python.exe'
if (Test-Path $preVenvPy) {
    try { & $preVenvPy (Join-Path $ProjectRoot 'scripts\rotate_logs.py') $LogDir | Out-Null } catch { }
}

# 1/7. Find Python 3.11+
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
    Write-FailureHint
    exit 1
}
Write-Stage "1/7" ("Python    : $($Py.Cmd) $($Py.Args -join ' ') ($($Py.Version))")

# 2/7. Virtual environment
if (-not (Test-Path $VenvDir)) {
    Write-Info "First run detected - setting up a fresh virtual environment ..."
    $venvArgs = $Py.Args + @('-m', 'venv', $VenvDir)
    & $Py.Cmd @venvArgs
    if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to create venv."; Write-FailureHint; exit 1 }
    Write-Stage "2/7" "Virtual env: created at .venv"
} else {
    Write-Stage "2/7" "Virtual env: reusing existing .venv"
}

$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Write-Fail "Virtual environment is broken (missing $VenvPy). Delete .venv and retry."
    Write-FailureHint
    exit 1
}

# 3/7. Dependencies
$Marker = Join-Path $VenvDir '.deps-installed'
$ReqFile = Join-Path $ProjectRoot 'requirements.txt'
$needInstall = $true
if ((Test-Path $Marker) -and (Test-Path $ReqFile)) {
    if ((Get-Item $Marker).LastWriteTime -ge (Get-Item $ReqFile).LastWriteTime) {
        $needInstall = $false
    }
}
if ($needInstall) {
    Write-Info "Installing dependencies (first run can take 1-2 minutes) ..."
    & $VenvPy -m pip install --upgrade pip --quiet
    & $VenvPy -m pip install -r $ReqFile --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed. Check your internet connection."
        Write-FailureHint
        exit 1
    }
    Set-Content -Path $Marker -Value (Get-Date).ToString('o')
    Write-Stage "3/7" "Deps      : installed"
} else {
    Write-Stage "3/7" "Deps      : up to date"
}

# 4/7. Data directory + log rotation
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'db')      | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'logs')    | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'backups') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'exports') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'support') | Out-Null
try { & $VenvPy (Join-Path $ProjectRoot 'scripts\rotate_logs.py') $LogDir | Out-Null } catch { }
Write-Stage "4/7" "Data dir  : $DataDir"

# 5/7. Migrations
Write-Info "Running migrations ..."
$migrateOutput = & $VenvPy (Join-Path $ProjectRoot 'scripts\migrate.py') 2>&1
$migrateRc = $LASTEXITCODE
$migrateOutput | ForEach-Object { Write-Host $_ }
$migrateOutput | ForEach-Object { Add-Content -Path $MigrateLog -Value $_ }
switch ($migrateRc) {
    0 {
        Write-Stage "5/7" "Database  : at schema head"
    }
    2 {
        Write-Host ""
        Write-Fail "Cannot start: database is newer than this version of Axion."
        Write-Host "    See the message above for recovery steps."
        Write-FailureHint
        exit 2
    }
    3 {
        Write-Host ""
        Write-Fail "Cannot start: database is corrupt or unreadable."
        Write-Host "    See the message above for recovery steps."
        Write-FailureHint
        exit 3
    }
    4 {
        Write-Host ""
        Write-Fail "Cannot start: pre-migration backup failed."
        Write-Host "    See the message above for recovery steps."
        Write-FailureHint
        exit 4
    }
    default {
        Write-Host ""
        Write-Fail "Migrations failed (see above)."
        Write-FailureHint
        exit 1
    }
}

# 6/7. Port conflict check + PID/process info on collision
$portInUse = $false
$portOwner = $null
try {
    $tcp = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
    if ($tcp) {
        $portInUse = $true
        try {
            $proc = Get-Process -Id ($tcp | Select-Object -First 1).OwningProcess -ErrorAction SilentlyContinue
            if ($proc) {
                $portOwner = "$($proc.ProcessName) (pid $($proc.Id))"
            }
        } catch { }
    }
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
    if ($portOwner) {
        Write-Host "  Owner   : $portOwner"
    }
    Write-Host ""
    Write-Host "  Options:"
    Write-Host "    1. Close the other application."
    Write-Host "    2. Or run Axion on a different port:"
    Write-Host "         `$env:AXION_PORT='7778'; PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1"
    Write-Host ""
    Write-FailureHint
    exit 2
}
Write-Stage "6/7" "Port      : $Port free"

# 7/7. Start uvicorn (mirror server output to axion-server.log)
Write-Host ""
Write-Info "Starting Axion on http://${VHost}:${Port} ..."
Write-Host ""

$proc = Start-Process -FilePath $VenvPy `
    -ArgumentList @('-m','uvicorn','src.main:app','--host',$VHost,'--port',$Port,'--log-level','info') `
    -WorkingDirectory $ProjectRoot `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $ServerLog `
    -RedirectStandardError $ServerLog

# Wait up to 30s for health
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://${VHost}:${Port}/api/v1/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    if ($proc.HasExited) {
        Write-Fail "uvicorn exited unexpectedly. See $ServerLog"
        Write-FailureHint
        exit 1
    }
}

if (-not $healthy) {
    Write-Fail "Axion did not become healthy within 30 seconds. See $ServerLog"
    if (-not $proc.HasExited) { try { $proc.Kill() } catch { } }
    Write-FailureHint
    exit 1
}

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "    Axion is running." -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "    Dashboard : http://${VHost}:${Port}" -ForegroundColor White
Write-Host "    API docs  : http://${VHost}:${Port}/docs"
Write-Host "    Data      : $DataDir"
Write-Host "    Logs      : $ServerLog"
Write-Host "    Support   : $VenvPy scripts\support_bundle.py"
Write-Host "    Stop      : Ctrl+C in this window, or close the window"
Write-Host ""

Start-Process "http://${VHost}:${Port}/dashboard/"

Wait-Process -Id $proc.Id
