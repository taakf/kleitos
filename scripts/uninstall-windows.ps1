# ============================================================================
# Kleitos — Windows Uninstaller
#
# Stops the service, removes shortcuts, disables auto-start.
# Does NOT delete data — remove %USERPROFILE%\kleitos-data manually if desired.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
# ============================================================================

$ErrorActionPreference = "SilentlyContinue"

$AppName = "Kleitos"
$TaskName = "Kleitos Auto-Start"
$DataDir = Join-Path $env:USERPROFILE "kleitos-data"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Port = if ($env:KLEITOS_PORT) { [int]$env:KLEITOS_PORT } else { 7777 }

function Write-OK($msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

Write-Host ""
$confirm = Read-Host "Uninstall Kleitos? (y/N)"
if ($confirm -ne "y") {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}
Write-Host ""
Write-Host "Uninstalling Kleitos..." -ForegroundColor White
Write-Host ""

# 1. Stop running processes
Write-Host "Stopping Kleitos..." -ForegroundColor Gray
# Kill by command line match
Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*uvicorn src.main:app*" } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
# Kill by port
$connections = netstat -ano | Select-String ":${Port}\s+.*LISTENING"
foreach ($conn in $connections) {
    $pid = ($conn -split '\s+')[-1]
    if ($pid -match '^\d+$') {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
}
Write-OK "Stopped running processes"

# 2. Remove scheduled task
schtasks /Delete /TN "$TaskName" /F 2>&1 | Out-Null
Write-OK "Removed auto-start task"

# 3. Remove desktop shortcut
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Kleitos.lnk"
if (Test-Path $desktopShortcut) {
    Remove-Item $desktopShortcut -Force
    Write-OK "Removed desktop shortcut"
}

# 4. Remove Start Menu shortcut
$startShortcut = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Kleitos.lnk"
if (Test-Path $startShortcut) {
    Remove-Item $startShortcut -Force
    Write-OK "Removed Start Menu shortcut"
}

# 5. Remove VBS service wrapper
$vbsFile = Join-Path $ProjectDir "scripts\kleitos-service.vbs"
if (Test-Path $vbsFile) {
    Remove-Item $vbsFile -Force
    Write-OK "Removed service wrapper"
}

# 6. Remove Add/Remove Programs entry
$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kleitos"
if (Test-Path $regPath) {
    Remove-Item $regPath -Force
    Write-OK "Removed Add/Remove Programs entry"
}

# 7. Kill tray app if running
Get-Process -Name "Kleitos" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Kleitos uninstalled." -ForegroundColor Green
Write-Host ""
Write-Host "Your data is still at $DataDir" -ForegroundColor White
Write-Host "  Database : $DataDir\db\kleitos.db" -ForegroundColor Gray
Write-Host "  Logs     : $DataDir\logs\" -ForegroundColor Gray
Write-Host "  Backups  : $DataDir\backups\" -ForegroundColor Gray
Write-Host ""
Write-Host "To remove all data:  Remove-Item -Recurse -Force $DataDir" -ForegroundColor Yellow
Write-Host "To remove the venv:  Remove-Item -Recurse -Force $ProjectDir\.venv" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to close"
