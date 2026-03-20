try {
    [System.IO.Directory]::Delete("C:\Users\Tassos\AppData\Local\docker-secrets-engine", $true)
    Write-Host "Deleted via .NET"
} catch {
    Write-Host "Failed .NET: $_"
    # Try cmd del
    cmd /c "rmdir /s /q C:\Users\Tassos\AppData\Local\docker-secrets-engine" 2>&1
    Write-Host "Tried cmd rmdir"
}

# Check result
if (Test-Path "C:\Users\Tassos\AppData\Local\docker-secrets-engine") {
    Write-Host "STILL EXISTS"
    Get-ChildItem "C:\Users\Tassos\AppData\Local\docker-secrets-engine" -Force | Format-List
} else {
    Write-Host "SUCCESSFULLY REMOVED"
}
