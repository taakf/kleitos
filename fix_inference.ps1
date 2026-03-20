$runDir = "C:\Users\Tassos\AppData\Local\Docker\run"
$backupDir = "C:\Users\Tassos\AppData\Local\Docker\run.broken"

# Remove old backup if exists
if (Test-Path $backupDir) {
    Remove-Item $backupDir -Recurse -Force -ErrorAction SilentlyContinue
}

# Rename the run directory
Rename-Item $runDir $backupDir -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $runDir)) {
    Write-Host "SUCCESS: run directory renamed to run.broken"
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    Write-Host "Created fresh empty run directory"
} else {
    Write-Host "FAILED: Could not rename run directory"
}
