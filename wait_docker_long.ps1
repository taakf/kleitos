for ($i = 1; $i -le 30; $i++) {
    Start-Sleep 10
    if (Test-Path '\\.\pipe\docker_engine') {
        Write-Host "DOCKER_READY (via docker_engine pipe)"
        & "C:\Program Files\Docker\Docker\resources\bin\docker.exe" ps
        exit 0
    }
    if (Test-Path '\\.\pipe\dockerDesktopLinuxEngine') {
        Write-Host "DOCKER_READY (via dockerDesktopLinuxEngine pipe)"
        & "C:\Program Files\Docker\Docker\resources\bin\docker.exe" ps
        exit 0
    }
    Write-Host "Waiting... attempt $i"
}
Write-Host "DOCKER_TIMEOUT"
exit 1
