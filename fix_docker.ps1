# Stop Docker
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "docker-ai" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$sockPath = "C:\Users\Tassos\AppData\Local\docker-secrets-engine\engine.sock"
$sockDir = "C:\Users\Tassos\AppData\Local\docker-secrets-engine"

# Attempt 1: takeown + icacls + del
Write-Host "Attempt 1: takeown + icacls"
takeown /f $sockPath /a 2>&1
icacls $sockPath /grant Tassos:F 2>&1
Remove-Item $sockPath -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $sockPath)) {
    Write-Host "SUCCESS: Socket deleted"
    exit 0
}

# Attempt 2: fsutil
Write-Host "Attempt 2: fsutil delete"
fsutil file delete $sockPath 2>&1

if (-not (Test-Path $sockPath)) {
    Write-Host "SUCCESS: Socket deleted via fsutil"
    exit 0
}

# Attempt 3: Rename the directory so Docker creates a fresh one
Write-Host "Attempt 3: Rename directory"
$backupDir = "C:\Users\Tassos\AppData\Local\docker-secrets-engine.broken"
if (Test-Path $backupDir) {
    Remove-Item $backupDir -Recurse -Force -ErrorAction SilentlyContinue
}
Rename-Item $sockDir $backupDir -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $sockDir)) {
    Write-Host "SUCCESS: Directory renamed to .broken"
    # Create a fresh empty directory
    New-Item -ItemType Directory -Path $sockDir -Force | Out-Null
    Write-Host "Created fresh empty directory"
    exit 0
}

Write-Host "All attempts failed. Socket is truly locked by the OS."
Write-Host "File attributes:"
Get-Item $sockPath | Select-Object Mode, Attributes, Length | Format-List
