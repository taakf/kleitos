$ErrorActionPreference = "Continue"

# Stop Docker processes
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "docker-ai" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# Use WSL to remove Unix domain sockets that Windows can't delete
wsl -d Ubuntu -e rm -rf /mnt/c/Users/Tassos/AppData/Local/docker-secrets-engine
wsl -d Ubuntu -e rm -f /mnt/c/Users/Tassos/AppData/Local/Docker/run/dockerInference

# Verify
$secretsDir = "C:\Users\Tassos\AppData\Local\docker-secrets-engine"
$inferenceFile = "C:\Users\Tassos\AppData\Local\Docker\run\dockerInference"

if (Test-Path $secretsDir) {
    Write-Host "WARNING: secrets-engine dir still exists"
} else {
    Write-Host "OK: secrets-engine dir removed"
}

if (Test-Path $inferenceFile) {
    Write-Host "WARNING: dockerInference still exists"
} else {
    Write-Host "OK: dockerInference removed"
}

# Start Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Docker Desktop starting..."
