@echo off
:: ============================================================================
:: Axion by 4Labs — Stop Server (Windows)
:: ============================================================================

if not defined KLEITOS_PORT set "KLEITOS_PORT=7777"
echo Stopping Axion (port %KLEITOS_PORT%)...

:: Kill tray app (.exe)
taskkill /F /IM "Axion.exe" >NUL 2>&1

:: Kill uvicorn processes running our app
taskkill /F /FI "WINDOWTITLE eq Axion*" >NUL 2>&1
wmic process where "commandline like '%%uvicorn src.main:app%%'" call terminate >NUL 2>&1
taskkill /F /IM "uvicorn.exe" >NUL 2>&1

:: Also kill by port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%KLEITOS_PORT% " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >NUL 2>&1
)

:: Clean PID file
if exist "%USERPROFILE%\kleitos-data\kleitos.pid" del "%USERPROFILE%\kleitos-data\kleitos.pid"

echo Axion stopped.
timeout /t 2 /nobreak >NUL
