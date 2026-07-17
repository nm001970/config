@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_DIR=C:\Users\White Ram\Desktop\v2ray_platform"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"

cd /d "%PROJECT_DIR%"

if not exist "%PYTHON%" (
    echo ERROR: Python virtual environment not found:
    echo %PYTHON%
    exit /b 1
)

if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
)

rem ============================================================
rem Irancell database profile
rem ============================================================

set "CONFIG_DATABASE_URL=sqlite:///configs.db"
set "PUBLIC_CONFIGS_FILE=public_configs.json"
set "SMART_BATCH_OPERATOR=irancell"
set "PUBLIC_POOL_OPERATOR=irancell"
set "AUTO_CYCLE_POOL_SUPERVISOR_ENABLED=false"
set "PYTHONUNBUFFERED=1"

rem Update once every 60 minutes.
set "UPDATE_INTERVAL_SECONDS=3600"

rem Retry sooner after a failed update.
set "ERROR_SLEEP_SECONDS=300"

set "RUN_ONCE=false"

if /I "%~1"=="once" (
    set "RUN_ONCE=true"
)

echo ============================================================
echo MN PROXY - IRANCELL CONFIG UPDATE
echo PROJECT: %PROJECT_DIR%
echo DATABASE: %CONFIG_DATABASE_URL%
echo UPDATE INTERVAL: %UPDATE_INTERVAL_SECONDS% seconds
echo RUN ONCE: %RUN_ONCE%
echo ============================================================

"%PYTHON%" -c "import os; assert os.environ.get('CONFIG_DATABASE_URL') == 'sqlite:///configs.db', 'invalid database profile'; assert os.environ.get('SMART_BATCH_OPERATOR') == 'irancell', 'invalid smart operator'; assert os.environ.get('PUBLIC_POOL_OPERATOR') == 'irancell', 'invalid pool operator'; assert os.environ.get('AUTO_CYCLE_POOL_SUPERVISOR_ENABLED', '').lower() == 'false', 'legacy supervisor must be disabled'; print('PROFILE GUARD: OK')"

if errorlevel 1 (
    echo ERROR: Irancell profile guard failed.
    exit /b 1
)

:run_update

for /f %%I in (
    'powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"'
) do (
    set "LOG_DATE=%%I"
)

set "LOG_FILE=%LOG_DIR%\config_update_irancell_!LOG_DATE!.log"

echo.
echo ============================================================
echo CONFIG UPDATE START
echo TIME: %date% %time%
echo LOG: !LOG_FILE!
echo ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Continue';" ^
    "& '%PYTHON%' -u '%PROJECT_DIR%\main.py' update 2>&1 | Tee-Object -FilePath '!LOG_FILE!' -Append;" ^
    "$exitCode=$LASTEXITCODE;" ^
    "exit $exitCode"

set "UPDATE_EXIT_CODE=!ERRORLEVEL!"

echo.
echo ============================================================
echo CONFIG UPDATE FINISHED
echo TIME: %date% %time%
echo EXIT CODE: !UPDATE_EXIT_CODE!
echo ============================================================

if /I "%RUN_ONCE%"=="true" (
    exit /b !UPDATE_EXIT_CODE!
)

if not "!UPDATE_EXIT_CODE!"=="0" (
    echo Config update failed.
    echo Retrying in %ERROR_SLEEP_SECONDS% seconds...
    timeout /t %ERROR_SLEEP_SECONDS% /nobreak >nul
    goto run_update
)

echo Next config update in %UPDATE_INTERVAL_SECONDS% seconds...
echo Press Ctrl+C to stop this terminal.

timeout /t %UPDATE_INTERVAL_SECONDS% /nobreak >nul
goto run_update