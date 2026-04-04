# ============================================================================
# Axion by 4Labs — Windows Installer
#
# NOTE: This is OPTIONAL. Just double-click Axion.bat and everything
# sets itself up automatically. This script provides the full install:
#   - Builds Axion.exe (native system tray app)
#   - Desktop and Start Menu shortcuts
#   - Silent auto-start on login
#   - Add/Remove Programs entry
#
# Run on the target Windows machine:
#   cd C:\path\to\Axion
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

$AppName = "Axion"
$AppVersion = "1.0.0"
$Port = if ($env:AXION_PORT) { [int]$env:AXION_PORT } elseif ($env:KLEITOS_PORT) { [int]$env:KLEITOS_PORT } else { 7777 }
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DataDir = if ($env:AXION_DATA_DIR) { $env:AXION_DATA_DIR } elseif (Test-Path (Join-Path $env:USERPROFILE "axion-data")) { Join-Path $env:USERPROFILE "axion-data" } elseif (Test-Path (Join-Path $env:USERPROFILE "kleitos-data")) { Join-Path $env:USERPROFILE "kleitos-data" } else { Join-Path $env:USERPROFILE "axion-data" }
$VenvDir = Join-Path $ProjectDir ".venv"
$AssetsDir = Join-Path $ProjectDir "assets"
$TaskName = "Axion Auto-Start"

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "--- $num/$total  $msg ---" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "[!] $msg" -ForegroundColor Yellow
}

function Write-Fail($msg) {
    Write-Host "[X] $msg" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Banner
Write-Host ""
Write-Host "  Axion — Portfolio Intelligence" -ForegroundColor Cyan
Write-Host "  by 4Labs" -ForegroundColor White
Write-Host ""

# --------------------------------------------------------------------------
Write-Step 1 9 "Checking prerequisites"
# --------------------------------------------------------------------------

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

    $install = Read-Host "  Open the Python download page? (Y/n)"
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

if (Test-Path $VenvDir) {
    Write-OK "Virtual environment already exists"
} else {
    Write-Host "  Creating venv at $VenvDir ..."
    if ($PythonExe -eq "py -3.12") {
        & py -3.12 -m venv $VenvDir
    } else {
        & $PythonExe -m venv $VenvDir
    }
    Write-OK "Created virtual environment"
}

& $VenvPython -m pip install --upgrade pip -q 2>&1 | Out-Null

# --------------------------------------------------------------------------
Write-Step 3 9 "Installing dependencies"
# --------------------------------------------------------------------------

& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt") -q
Write-OK "All Python packages installed"

& $VenvPython -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, apscheduler; print('All imports OK')"

# --------------------------------------------------------------------------
Write-Step 4 9 "Installing desktop app dependencies"
# --------------------------------------------------------------------------

& $VenvPython -m pip install pystray Pillow requests pyinstaller -q
Write-OK "Desktop app dependencies installed"

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
Write-Step 6 9 "Building Axion.exe"
# --------------------------------------------------------------------------

$ExePath = Join-Path $ProjectDir "dist\Axion.exe"
$BuildExe = $true

if (Test-Path $ExePath) {
    Write-OK "Axion.exe already exists"
    $rebuild = Read-Host "  Rebuild? (y/N)"
    if ($rebuild -ne "y") { $BuildExe = $false }
}

if ($BuildExe) {
    Write-Host "  Building Axion.exe (this takes 1-2 minutes)..."
    & $VenvPython (Join-Path $ProjectDir "scripts\build-exe.py")
    if (Test-Path $ExePath) {
        $size = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
        Write-OK "Axion.exe built ($size MB)"
    } else {
        Write-Warn "Build failed — will use script mode instead"
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

# --------------------------------------------------------------------------
Write-Step 8 9 "Creating shortcuts"
# --------------------------------------------------------------------------

$LaunchTarget = if (Test-Path $ExePath) { $ExePath } else { Join-Path $ProjectDir "Axion.bat" }

# Desktop shortcut
$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Axion.lnk"
try {
    $WScriptShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $LaunchTarget
    $Shortcut.WorkingDirectory = $ProjectDir
    $Shortcut.Description = "Axion Portfolio Intelligence"
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
$StartShortcut = Join-Path $StartMenuDir "Axion.lnk"
try {
    $Shortcut2 = $WScriptShell.CreateShortcut($StartShortcut)
    $Shortcut2.TargetPath = $LaunchTarget
    $Shortcut2.WorkingDirectory = $ProjectDir
    $Shortcut2.Description = "Axion Portfolio Intelligence"
    if (-not (Test-Path $ExePath) -and (Test-Path $IconFile)) {
        $Shortcut2.IconLocation = $IconFile
    }
    $Shortcut2.Save()
    Write-OK "Start Menu shortcut created"
} catch {
    Write-Warn "Could not create Start Menu shortcut: $_"
}

# --------------------------------------------------------------------------
Write-Step 9 9 "Setting up auto-start"
# --------------------------------------------------------------------------

schtasks /Delete /TN "$TaskName" /F 2>&1 | Out-Null
schtasks /Delete /TN "Kleitos Auto-Start" /F 2>&1 | Out-Null  # Clean up old name

if (Test-Path $ExePath) {
    try {
        schtasks /Create /TN "$TaskName" /TR "`"$ExePath`"" /SC ONLOGON /RL LIMITED /F | Out-Null
        Write-OK "Auto-start configured (Axion.exe)"
    } catch {
        Write-Warn "Could not create scheduled task: $_"
    }
} else {
    $BatPath = Join-Path $ProjectDir "Axion.bat"
    $StartupScript = Join-Path $ProjectDir "scripts\axion-service.vbs"
    @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$($ProjectDir -replace '\\','\\')"
WshShell.Run """$($BatPath -replace '\\','\\')"" /autostart", 0, False
"@ | Set-Content -Path $StartupScript -Encoding ASCII

    try {
        schtasks /Create /TN "$TaskName" /TR "wscript.exe `"$StartupScript`"" /SC ONLOGON /RL LIMITED /F | Out-Null
        Write-OK "Auto-start configured"
    } catch {
        Write-Warn "Could not create scheduled task: $_"
    }
}

# Register in Add/Remove Programs
try {
    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Axion"
    # Clean up old Kleitos entry if present
    Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kleitos" -Force -ErrorAction SilentlyContinue
    New-Item -Path $regPath -Force | Out-Null
    Set-ItemProperty -Path $regPath -Name "DisplayName" -Value "$AppName"
    Set-ItemProperty -Path $regPath -Name "DisplayVersion" -Value "$AppVersion"
    Set-ItemProperty -Path $regPath -Name "Publisher" -Value "4Labs"
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
# Start Axion now
# --------------------------------------------------------------------------
Write-Host ""
Write-OK "Starting Axion..."

if (Test-Path $ExePath) {
    Start-Process -FilePath $ExePath -WorkingDirectory $ProjectDir
} else {
    $env:AXION_DATA_DIR = $DataDir
    $env:AXION_DB_PATH = Join-Path $DataDir "db\kleitos.db"
    $env:PATH = "$VenvDir\Scripts;$env:PATH"

    $logOut = Join-Path $DataDir "logs\kleitos-stdout.log"
    $logErr = Join-Path $DataDir "logs\kleitos-stderr.log"

    Start-Process -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput $logOut `
        -RedirectStandardError $logErr `
        -WindowStyle Hidden `
        -PassThru | Out-Null
}

$waited = 0
while ($waited -lt 45) {
    Start-Sleep -Seconds 1
    $waited++
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$Port/api/v1/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) { break }
    } catch { }
}

if ($waited -ge 45) {
    Write-Warn "Axion did not start within 45 seconds. Check: $DataDir\logs\"
} else {
    Start-Process "http://localhost:$Port"
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Axion installed successfully!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard  :  http://localhost:$Port" -ForegroundColor Cyan
Write-Host "  Data       :  $DataDir" -ForegroundColor White
Write-Host "  Logs       :  $DataDir\logs" -ForegroundColor White
Write-Host ""
Write-Host "  Auto-start :  Starts silently on login" -ForegroundColor White
Write-Host "  Uninstall  :  Settings > Apps > Axion" -ForegroundColor White
Write-Host ""
Write-Host "  Configure AI in the dashboard Settings tab." -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to close"
