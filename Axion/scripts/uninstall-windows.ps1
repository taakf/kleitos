# ============================================================================
# Axion by 4Labs — Windows Uninstaller
#
# Stops the service, removes shortcuts, disables auto-start.
# Does NOT delete data — remove your data directory manually if desired.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
# ============================================================================

$ErrorActionPreference = "SilentlyContinue"

$AppName = "Axion"
$TaskName = "Axion Auto-Start"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Port = if ($env:AXION_PORT) { [int]$env:AXION_PORT } elseif ($env:KLEITOS_PORT) { [int]$env:KLEITOS_PORT } else { 7777 }

function Write-OK($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

Write-Host ""
$confirm = Read-Host "Uninstall Axion? (y/N)"
if ($confirm -ne "y") {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}
Write-Host ""

# 1. Stop running processes
Write-Host "Stopping Axion..." -ForegroundColor Gray
Get-Process -Name "Axion" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*uvicorn src.main:app*" } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
$connections = netstat -ano | Select-String ":${Port}\s+.*LISTENING"
foreach ($conn in $connections) {
    $pid = ($conn -split '\s+')[-1]
    if ($pid -match '^\d+$') {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
}
Write-OK "Stopped processes"

# 2. Remove scheduled tasks (Axion + legacy Kleitos)
schtasks /Delete /TN "$TaskName" /F 2>&1 | Out-Null
schtasks /Delete /TN "Kleitos Auto-Start" /F 2>&1 | Out-Null
Write-OK "Removed auto-start"

# 3. Remove shortcuts (Axion + legacy Kleitos)
$shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Axion.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Kleitos.lnk"),
    (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Axion.lnk"),
    (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Kleitos.lnk")
)
foreach ($s in $shortcuts) {
    if (Test-Path $s) { Remove-Item $s -Force }
}
Write-OK "Removed shortcuts"

# 4. Remove service wrappers
foreach ($vbs in @("axion-service.vbs", "kleitos-service.vbs")) {
    $p = Join-Path $ProjectDir "scripts\$vbs"
    if (Test-Path $p) { Remove-Item $p -Force }
}

# 5. Remove Add/Remove Programs entries
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Axion" -Force -ErrorAction SilentlyContinue
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kleitos" -Force -ErrorAction SilentlyContinue
Write-OK "Removed from Add/Remove Programs"

Write-Host ""
Write-OK "Axion uninstalled."
Write-Host ""
Write-Host "  Your data is preserved. To delete it, remove:" -ForegroundColor Yellow
Write-Host "    %USERPROFILE%\axion-data  (or kleitos-data)" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to close"
