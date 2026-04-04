@echo off
:: ============================================================================
:: Axion by 4Labs — Plug & Play Launcher for Windows
::
:: Double-click this file. Everything sets itself up automatically.
:: No terminal, no commands, no setup needed.
::
:: Launch priority:
::   1. Axion.exe (PyInstaller build — no CMD window at all)
::   2. System tray app via pythonw (no CMD window)
::   3. Direct uvicorn fallback (CMD stays open)
:: ============================================================================

title Axion - Portfolio Intelligence
setlocal enabledelayedexpansion

set APP_NAME=Axion
if not defined KLEITOS_PORT set "KLEITOS_PORT=7777"
set PORT=%KLEITOS_PORT%
set HEALTH_URL=http://localhost:%PORT%/api/v1/health
set MAX_WAIT=45

:: Resolve project directory (where this .bat lives)
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "VENV_DIR=%PROJECT_DIR%\.venv"
set "DATA_DIR=%USERPROFILE%\kleitos-data"
set "LOG_DIR=%DATA_DIR%\logs"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
set "PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
set "LOG_FILE=%DATA_DIR%\logs\launcher.log"
set "TASK_NAME=Axion Auto-Start"

:: Create data directories
if not exist "%DATA_DIR%\db" mkdir "%DATA_DIR%\db"
if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
if not exist "%DATA_DIR%\backups" mkdir "%DATA_DIR%\backups"

:: ============================================================================
:: CHECK IF ALREADY RUNNING — just open dashboard
:: ============================================================================
curl -s -o NUL -w "%%{http_code}" "%HEALTH_URL%" 2>NUL | findstr "200" >NUL 2>&1
if %errorlevel%==0 (
    echo [%date% %time%] Already running - opening dashboard >> "%LOG_FILE%"
    start "" "http://localhost:%PORT%"
    exit /b 0
)

:: ============================================================================
:: OPTION 1: Launch Axion.exe (built with PyInstaller — best experience)
:: ============================================================================
if exist "%PROJECT_DIR%\dist\Axion.exe" (
    echo [%date% %time%] Launching Axion.exe >> "%LOG_FILE%"
    start "" "%PROJECT_DIR%\dist\Axion.exe"
    exit /b 0
)

:: ============================================================================
:: OPTION 2: Launch tray app via pythonw (no CMD window)
:: ============================================================================
:: Need pystray and Pillow — check if available
if exist "%PYTHONW%" (
    "%PYTHON%" -c "import pystray, PIL" >NUL 2>&1
    if !errorlevel!==0 (
        echo [%date% %time%] Launching tray app via pythonw >> "%LOG_FILE%"
        start "" "%PYTHONW%" "%PROJECT_DIR%\scripts\axion-tray.pyw"
        exit /b 0
    )
)

:: ============================================================================
:: OPTION 3: Direct uvicorn fallback (original behavior)
:: ============================================================================
echo.
echo     _          _
echo    / \  __  __(_) ___  _ __
echo   / _ \ \ \/ /^| ^|/ _ \^| '_ \
echo  / ___ \ ^>  ^< ^| ^| (_) ^| ^| ^| ^|
echo /_/   \_\/_/\_\^|_^|\___/^|_^| ^|_^|
echo.
echo   Portfolio Intelligence by 4Labs
echo.

:: ============================================================================
:: AUTO-SETUP: Find Python
:: ============================================================================
if exist "%PYTHON%" goto :skip_setup

echo   First launch detected — setting up automatically...
echo.

:: Find system Python
set "SYS_PYTHON="
where python 2>NUL >NUL && (
    for /f "delims=" %%i in ('python --version 2^>^&1') do set "PY_VER=%%i"
    echo !PY_VER! | findstr /R "3\.1[1-9] 3\.[2-9][0-9]" >NUL 2>&1
    if !errorlevel!==0 set "SYS_PYTHON=python"
)

if not defined SYS_PYTHON (
    where python3 2>NUL >NUL && (
        for /f "delims=" %%i in ('python3 --version 2^>^&1') do set "PY_VER=%%i"
        echo !PY_VER! | findstr /R "3\.1[1-9] 3\.[2-9][0-9]" >NUL 2>&1
        if !errorlevel!==0 set "SYS_PYTHON=python3"
    )
)

if not defined SYS_PYTHON (
    where py 2>NUL >NUL && (
        for /f "delims=" %%i in ('py -3 --version 2^>^&1') do set "PY_VER=%%i"
        echo !PY_VER! | findstr /R "3\.1[1-9] 3\.[2-9][0-9]" >NUL 2>&1
        if !errorlevel!==0 set "SYS_PYTHON=py -3"
    )
)

if not defined SYS_PYTHON (
    echo.
    echo   Python 3.11+ is required but was not found.
    echo.
    echo   Opening the Python download page for you...
    echo   IMPORTANT: Check "Add Python to PATH" during install!
    echo   Then double-click this launcher again.
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

echo   [1/5] Found %PY_VER%
echo   [2/5] Creating virtual environment...
%SYS_PYTHON% -m venv "%VENV_DIR%"
if %errorlevel% neq 0 (
    echo   ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)

echo   [3/5] Installing dependencies (this takes 1-2 minutes)...
"%PYTHON%" -m pip install --upgrade pip -q 2>NUL
"%PYTHON%" -m pip install -r "%PROJECT_DIR%\requirements.txt" -q
if %errorlevel% neq 0 (
    echo   ERROR: Failed to install dependencies.
    echo   Check your internet connection and try again.
    pause
    exit /b 1
)

echo   [4/5] Verifying installation...
"%PYTHON%" -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, apscheduler; print('         All packages OK')"
if %errorlevel% neq 0 (
    echo   ERROR: Package verification failed.
    pause
    exit /b 1
)

echo   [5/5] Installing tray app dependencies...
"%PYTHON%" -m pip install pystray Pillow requests -q 2>NUL

:: Create .env if it doesn't exist
if not exist "%PROJECT_DIR%\.env" (
    (
        echo # Axion Environment Configuration
        echo # -----------------------------------
        echo # Anthropic API key ^(optional - works without it using rule-based fallbacks^)
        echo # ANTHROPIC_API_KEY=sk-ant-...
        echo.
        echo # NewsAPI key ^(optional - for news collection^)
        echo # NEWSAPI_KEY=...
    ) > "%PROJECT_DIR%\.env"
)

echo.
echo   Setup complete!
echo.

:skip_setup

:: ============================================================================
:: AUTO-SETUP: Try launching tray app now that deps are installed
:: ============================================================================
if exist "%PYTHONW%" (
    "%PYTHON%" -c "import pystray, PIL" >NUL 2>&1
    if !errorlevel!==0 (
        echo   Launching Axion tray app...
        echo [%date% %time%] Launching tray app after setup >> "%LOG_FILE%"
        start "" "%PYTHONW%" "%PROJECT_DIR%\scripts\axion-tray.pyw"

        :: Set up shortcuts and auto-start pointing to the tray app
        goto :setup_shortcuts
    )
)

:: ============================================================================
:: FALLBACK: Start uvicorn directly (if tray deps failed)
:: ============================================================================
echo   Starting Axion on http://localhost:%PORT% ...
echo.

set "KLEITOS_DATA_DIR=%DATA_DIR%"
set "KLEITOS_DB_PATH=%DATA_DIR%\db\kleitos.db"
set "PATH=%VENV_DIR%\Scripts;%PATH%"

:: Check if port is already in use
netstat -ano 2>NUL | findstr ":%PORT% " | findstr "LISTENING" >NUL 2>&1
if %errorlevel%==0 (
    echo.
    echo   Port %PORT% is already in use by another application.
    echo   Either close the other application or set KLEITOS_PORT
    echo   to a different port number and try again.
    echo.
    echo [%date% %time%] Port %PORT% in use >> "%LOG_FILE%"
    pause
    exit /b 1
)

echo [%date% %time%] Starting Axion (direct) >> "%LOG_FILE%"

start /B "" "%PYTHON%" -m uvicorn src.main:app --host 0.0.0.0 --port %PORT% >> "%LOG_DIR%\kleitos-stdout.log" 2>> "%LOG_DIR%\kleitos-stderr.log"

timeout /t 2 /nobreak >NUL

:: ============================================================================
:: WAIT FOR HEALTH
:: ============================================================================
set waited=0
:healthloop
curl -s -o NUL -w "%%{http_code}" "%HEALTH_URL%" 2>NUL | findstr "200" >NUL 2>&1
if %errorlevel%==0 goto healthy

timeout /t 1 /nobreak >NUL
set /a waited+=1
if %waited% geq %MAX_WAIT% (
    echo.
    echo   Startup timed out after %MAX_WAIT% seconds.
    echo   This usually means a dependency issue or port conflict.
    echo   Logs are at: %LOG_DIR%
    echo.
    echo [%date% %time%] Startup timed out >> "%LOG_FILE%"
    pause
    exit /b 1
)

:: Show dots for progress
set /a mod=%waited% %% 5
if %mod%==0 echo   Waiting... (%waited%s)
goto healthloop

:healthy
echo.
echo   ============================================
echo     Axion is running!
echo   ============================================
echo.
echo   Dashboard  :  http://localhost:%PORT%
echo   Data       :  %DATA_DIR%
echo   Logs       :  %LOG_DIR%
echo.
echo   This window can be closed — Axion keeps
echo   running in the background and auto-starts
echo   when you log in.
echo.

echo [%date% %time%] Axion healthy, opening dashboard >> "%LOG_FILE%"

:: Open dashboard
start "" "http://localhost:%PORT%"

:setup_shortcuts
:: ============================================================================
:: AUTO-SETUP: Desktop Shortcut (one-time)
:: ============================================================================
if not exist "%USERPROFILE%\Desktop\Axion.lnk" (
    powershell -NoProfile -Command ^
        "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%USERPROFILE%\Desktop\Axion.lnk'); $s.TargetPath = '%PROJECT_DIR%\Axion.bat'; $s.WorkingDirectory = '%PROJECT_DIR%'; $s.Description = 'Axion Portfolio Intelligence by 4Labs'; $ico = '%PROJECT_DIR%\assets\axion.ico'; if (Test-Path $ico) { $s.IconLocation = $ico } else { $s.IconLocation = '%%SystemRoot%%\System32\shell32.dll,21' }; $s.Save()" 2>NUL
)

:: ============================================================================
:: AUTO-SETUP: Start Menu Shortcut (one-time)
:: ============================================================================
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
if not exist "%START_MENU%\Axion.lnk" (
    powershell -NoProfile -Command ^
        "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%START_MENU%\Axion.lnk'); $s.TargetPath = '%PROJECT_DIR%\Axion.bat'; $s.WorkingDirectory = '%PROJECT_DIR%'; $s.Description = 'Axion Portfolio Intelligence by 4Labs'; $ico = '%PROJECT_DIR%\assets\axion.ico'; if (Test-Path $ico) { $s.IconLocation = $ico } else { $s.IconLocation = '%%SystemRoot%%\System32\shell32.dll,21' }; $s.Save()" 2>NUL
)

:: ============================================================================
:: AUTO-SETUP: Auto-start on login (one-time, silent)
:: ============================================================================
schtasks /Query /TN "%TASK_NAME%" >NUL 2>&1
if %errorlevel% neq 0 (
    :: Create a hidden VBS wrapper so it runs without a terminal window
    set "VBS_FILE=%PROJECT_DIR%\scripts\axion-service.vbs"
    (
        echo Set WshShell = CreateObject^("WScript.Shell"^)
        echo WshShell.CurrentDirectory = "%PROJECT_DIR%"
        echo WshShell.Run """%PROJECT_DIR%\Axion.bat"" /autostart", 0, False
    ) > "!VBS_FILE!"

    schtasks /Create /TN "%TASK_NAME%" /TR "wscript.exe \"!VBS_FILE!\"" /SC ONLOGON /RL LIMITED /F >NUL 2>&1
)

:: If launched by autostart (/autostart flag), exit silently
if "%~1"=="/autostart" exit /b 0

:: Only show "press any key" if we're in direct-uvicorn mode
if not exist "%PYTHONW%" (
    echo   Press any key to close this window.
    pause >NUL
)
