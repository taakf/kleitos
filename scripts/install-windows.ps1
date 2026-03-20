# ============================================================================
# Kleitos — Windows Installer (Premium Edition)
#
# NOTE: This is OPTIONAL. Just double-click Kleitos.bat and everything
# sets itself up automatically. This script is for the full premium install:
#   - Builds Kleitos.exe (native system tray app)
#   - Custom branded icon on Desktop and Start Menu
#   - Silent auto-start on login via the .exe
#   - Proper Add/Remove Programs entry
#
# Run on the target Windows machine:
#   cd C:\path\to\kleitos
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

$AppName = "Kleitos"
$AppVersion = "1.0.0"
$Port = if ($env:KLEITOS_PORT) { [int]$env:KLEITOS_PORT } else { 7777 }
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DataDir = Join-Path $env:USERPROFILE "kleitos-data"
$VenvDir = Join-Path $ProjectDir ".venv"
$AssetsDir = Join-Path $ProjectDir "assets"
$TaskName = "Kleitos Auto-Start"

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "--- $num/$total  $msg ---" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "[+] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "[!] $msg" -ForegroundColor Yellow
}

function Write-Fail($msg) {
    Write-Host "[x] $msg" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Banner
Write-Host ""
Write-Host "  _  ___     _ _            " -ForegroundColor White
Write-Host " | |/ / |   (_) |           " -ForegroundColor White
Write-Host " | ' /| | ___| | |_ ___  ___" -ForegroundColor White
Write-Host " |  < | |/ _ \ | __/ _ \/ __|" -ForegroundColor White
Write-Host " | . \| |  __/ | || (_) \__ \" -ForegroundColor White
Write-Host " |_|\_\_|\___|_|\__\___/|___/" -ForegroundColor White
Write-Host ""
Write-Host "  Portfolio Intelligence System - Premium Installer" -ForegroundColor White
Write-Host ""

# --------------------------------------------------------------------------
Write-Step 1 9 "Checking prerequisites"
# --------------------------------------------------------------------------

# Find Python
$PythonExe = $null
$candidates = @("python3.12", "python3.11", "python3", "python", "py")

foreach ($cmd in $candidates) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $PythonExe = $cmd
                break
            }
        }
    } catch { }
}

# Try py launcher with version flag
if (-not $PythonExe) {
    try {
        $ver = & py -3.12 --version 2>&1
        if ($ver -match "Python 3\.1[2-9]") {
            $PythonExe = "py -3.12"
        }
    } catch { }
}

if (-not $PythonExe) {
    Write-Host ""
    Write-Host "  Python 3.11+ is required but not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install Python from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  IMPORTANT: Check 'Add Python to PATH' during install!" -ForegroundColor Yellow
    Write-Host ""

    $install = Read-Host "  Would you like to open the Python download page? (Y/n)"
    if ($install -ne "n") {
        Start-Process "https://www.python.org/downloads/"
    }
    Write-Fail "Please install Python 3.11+ and run this installer again."
}

$pyVer = & $PythonExe --version 2>&1
Write-OK "Python: $pyVer"

# --------------------------------------------------------------------------
Write-Step 2 9 "Creating virtual environment"
# --------------------------------------------------------------------------

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonW = Join-Path $VenvDir "Scripts\pythonw.exe"

if (Test-Path $VenvDir) {
    Write-OK "Virtual environment already exists, updating..."
} else {
    Write-Host "  Creating venv at $VenvDir ..."
    if ($PythonExe -eq "py -3.12") {
        & py -3.12 -m venv $VenvDir
    } else {
        & $PythonExe -m venv $VenvDir
    }
    Write-OK "Created virtual environment"
}

# Upgrade pip
& $VenvPython -m pip install --upgrade pip -q 2>&1 | Out-Null

# --------------------------------------------------------------------------
Write-Step 3 9 "Installing server dependencies"
# --------------------------------------------------------------------------

& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt") -q
Write-OK "All Python packages installed"

# Verify critical imports
& $VenvPython -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, apscheduler; print('All imports OK')"

# --------------------------------------------------------------------------
Write-Step 4 9 "Installing tray app dependencies"
# --------------------------------------------------------------------------

& $VenvPython -m pip install pystray Pillow requests pyinstaller -q
Write-OK "Tray app + build dependencies installed"

# --------------------------------------------------------------------------
Write-Step 5 9 "Generating icons"
# --------------------------------------------------------------------------

$IconFile = Join-Path $AssetsDir "kleitos.ico"
if (-not (Test-Path $IconFile)) {
    & $VenvPython (Join-Path $ProjectDir "scripts\generate-icons.py")
    Write-OK "Icons generated"
} else {
    Write-OK "Icons already exist"
}

# --------------------------------------------------------------------------
Write-Step 6 9 "Building Kleitos.exe"
# --------------------------------------------------------------------------

$ExePath = Join-Path $ProjectDir "dist\Kleitos.exe"
$BuildExe = $true

if (Test-Path $ExePath) {
    Write-OK "Kleitos.exe already exists"
    $rebuild = Read-Host "  Rebuild? (y/N)"
    if ($rebuild -ne "y") { $BuildExe = $false }
}

if ($BuildExe) {
    Write-Host "  Building Kleitos.exe (this takes 1-2 minutes)..."
    & $VenvPython (Join-Path $ProjectDir "scripts\build-exe.py")
    if (Test-Path $ExePath) {
        $size = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
        Write-OK "Kleitos.exe built ($size MB)"
    } else {
        Write-Warn "Build failed — falling back to tray script mode"
    }
}

# --------------------------------------------------------------------------
Write-Step 7 9 "Creating data directories"
# --------------------------------------------------------------------------

$dirs = @("db", "logs", "backups")
foreach ($d in $dirs) {
    $p = Join-Path $DataDir $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}
Write-OK "Data directory: $DataDir"

# Create .env if needed
$EnvFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $EnvFile)) {
    @"
# Kleitos Environment Configuration
# -----------------------------------
# Anthropic API key (optional - system works without it using rule-based fallbacks)
# ANTHROPIC_API_KEY=sk-ant-...

# NewsAPI key (optional - for news collection from newsapi.org)
# NEWSAPI_KEY=...
"@ | Set-Content -Path $EnvFile -Encoding UTF8
    Write-OK "Created .env file - edit to add API keys (optional)"
} else {
    Write-OK ".env already exists, keeping it"
}

# --------------------------------------------------------------------------
Write-Step 8 9 "Creating shortcuts"
# --------------------------------------------------------------------------

# Determine the best target for shortcuts
if (Test-Path $ExePath) {
    $LaunchTarget = $ExePath
} else {
    $LaunchTarget = Join-Path $ProjectDir "Kleitos.bat"
}

# Desktop shortcut
$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Kleitos.lnk"
try {
    $WScriptShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $LaunchTarget
    $Shortcut.WorkingDirectory = $ProjectDir
    $Shortcut.Description = "Kleitos Portfolio Intelligence System"
    if (-not (Test-Path $ExePath) -and (Test-Path $IconFile)) {
        $Shortcut.IconLocation = $IconFile
    }
    $Shortcut.Save()
    Write-OK "Desktop shortcut created"
} catch {
    Write-Warn "Could not create desktop shortcut: $_"
}

# Start Menu shortcut
$StartMenuDir = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
$StartShortcut = Join-Path $StartMenuDir "Kleitos.lnk"
try {
    $Shortcut2 = $WScriptShell.CreateShortcut($StartShortcut)
    $Shortcut2.TargetPath = $LaunchTarget
    $Shortcut2.WorkingDirectory = $ProjectDir
    $Shortcut2.Description = "Kleitos Portfolio Intelligence System"
    if (-not (Test-Path $ExePath) -and (Test-Path $IconFile)) {
        $Shortcut2.IconLocation = $IconFile
    }
    $Shortcut2.Save()
    Write-OK "Start Menu shortcut created"
} catch {
    Write-Warn "Could not create Start Menu shortcut: $_"
}

# --------------------------------------------------------------------------
Write-Step 9 9 "Setting up auto-start on login"
# --------------------------------------------------------------------------

# Remove old task if present
schtasks /Delete /TN "$TaskName" /F 2>&1 | Out-Null

if (Test-Path $ExePath) {
    # Direct .exe — no VBS wrapper needed, no CMD flash
    try {
        schtasks /Create `
            /TN "$TaskName" `
            /TR "`"$ExePath`"" `
            /SC ONLOGON `
            /RL LIMITED `
            /F | Out-Null
        Write-OK "Auto-start configured (Kleitos.exe — no terminal window)"
    } catch {
        Write-Warn "Could not create scheduled task: $_"
    }
} else {
    # Fallback: VBS wrapper for .bat
    $StartupScript = Join-Path $ProjectDir "scripts\kleitos-service.vbs"
    $BatPath = Join-Path $ProjectDir "Kleitos.bat"
    @"
' Kleitos Windows Service Wrapper — runs Kleitos.bat hidden
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$($ProjectDir -replace '\\','\\')"
WshShell.Run """$($BatPath -replace '\\','\\')"" /autostart", 0, False
"@ | Set-Content -Path $StartupScript -Encoding ASCII

    try {
        schtasks /Create `
            /TN "$TaskName" `
            /TR "wscript.exe `"$StartupScript`"" `
            /SC ONLOGON `
            /RL LIMITED `
            /F | Out-Null
        Write-OK "Auto-start configured (via VBS wrapper)"
    } catch {
        Write-Warn "Could not create scheduled task: $_"
    }
}

# --------------------------------------------------------------------------
# Register in Add/Remove Programs
# --------------------------------------------------------------------------
try {
    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kleitos"
    New-Item -Path $regPath -Force | Out-Null
    Set-ItemProperty -Path $regPath -Name "DisplayName" -Value "$AppName"
    Set-ItemProperty -Path $regPath -Name "DisplayVersion" -Value "$AppVersion"
    Set-ItemProperty -Path $regPath -Name "Publisher" -Value "Kleitos"
    Set-ItemProperty -Path $regPath -Name "InstallLocation" -Value "$ProjectDir"
    Set-ItemProperty -Path $regPath -Name "DisplayIcon" -Value "$IconFile"
    Set-ItemProperty -Path $regPath -Name "UninstallString" -Value "powershell -ExecutionPolicy Bypass -File `"$ProjectDir\scripts\uninstall-windows.ps1`""
    Set-ItemProperty -Path $regPath -Name "NoModify" -Value 1 -Type DWord
    Set-ItemProperty -Path $regPath -Name "NoRepair" -Value 1 -Type DWord
    Write-OK "Registered in Add/Remove Programs"
} catch {
    Write-Warn "Could not register in Add/Remove Programs: $_"
}

# --------------------------------------------------------------------------
# Start Kleitos now
# --------------------------------------------------------------------------
Write-Host ""
Write-OK "Starting Kleitos..."

if (Test-Path $ExePath) {
    Start-Process -FilePath $ExePath -WorkingDirectory $ProjectDir
    Write-Host "  Kleitos.exe launched — look for the K icon in your system tray" -ForegroundColor Cyan
} else {
    # Set env vars
    $env:KLEITOS_DATA_DIR = $DataDir
    $env:KLEITOS_DB_PATH = Join-Path $DataDir "db\kleitos.db"
    $env:PATH = "$VenvDir\Scripts;$env:PATH"

    # Start server in background
    $logOut = Join-Path $DataDir "logs\kleitos-stdout.log"
    $logErr = Join-Path $DataDir "logs\kleitos-stderr.log"

    Start-Process -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "$Port" `
        -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput $logOut `
        -RedirectStandardError $logErr `
        -WindowStyle Hidden `
        -PassThru | Out-Null
}

# Wait for health
$waited = 0
$maxWait = 45
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 1
    $waited++
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$Port/api/v1/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) { break }
    } catch { }
    if ($waited % 5 -eq 0) {
        Write-Host "  Waiting for server... ($waited`s)" -ForegroundColor Gray
    }
}

if ($waited -ge $maxWait) {
    Write-Warn "Kleitos did not start within $maxWait seconds."
    Write-Host "  It may still be starting up. Check: $DataDir\logs\" -ForegroundColor Yellow
} else {
    # Open dashboard
    Start-Process "http://localhost:$Port"
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Kleitos installed successfully!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""

if (Test-Path $ExePath) {
    Write-Host "  App        :  Kleitos.exe (system tray)" -ForegroundColor Cyan
}
Write-Host "  Dashboard  :  http://localhost:$Port" -ForegroundColor Cyan
Write-Host "  Shortcut   :  Desktop + Start Menu" -ForegroundColor White
Write-Host "  Data       :  $DataDir" -ForegroundColor White
Write-Host "  Logs       :  $DataDir\logs" -ForegroundColor White
Write-Host "  Config     :  $ProjectDir\.env" -ForegroundColor White
Write-Host ""
Write-Host "  Auto-start :  Starts silently on login" -ForegroundColor White
Write-Host "  Tray icon  :  Right-click the K icon for controls" -ForegroundColor White
Write-Host "  Uninstall  :  Settings > Apps > Kleitos" -ForegroundColor White
Write-Host ""
Write-Host "  To add an Anthropic API key for AI-powered analysis:" -ForegroundColor Yellow
Write-Host "    Edit $ProjectDir\.env" -ForegroundColor Yellow
Write-Host "    Add: ANTHROPIC_API_KEY=sk-ant-..." -ForegroundColor Yellow
Write-Host "    Then restart Kleitos from the tray icon" -ForegroundColor Yellow
Write-Host ""
Write-Host "  No Docker required. No terminal required after this." -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to close"
