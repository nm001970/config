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

if not exist "%PROJECT_DIR%\irancell_export_publish_cycle.py" (
    echo ERROR: Export/publish orchestrator not found:
    echo %PROJECT_DIR%\irancell_export_publish_cycle.py
    exit /b 1
)

if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
)

rem ============================================================
rem Irancell runtime profile
rem ============================================================

set "CONFIG_DATABASE_URL=sqlite:///configs.db"
set "PUBLIC_CONFIGS_FILE=public_configs.json"

set "SMART_BATCH_OPERATOR=irancell"
set "PUBLIC_POOL_OPERATOR=irancell"
set "AUTO_CYCLE_POOL_SUPERVISOR_ENABLED=false"

set "PUBLIC_EXPORT_SCRIPT=export_public_configs_pool_metrics.py"

set "PUBLIC_HOT_LIMIT=5"
set "PUBLIC_RESERVE_LIMIT=40"
set "SUBSCRIPTION_CONFIG_LIMIT=5"

set "ENABLE_DELIVERY_VALIDATION=false"
set "SAFE_EXPORT_ALLOW_LAST_GOOD_SUCCESS=false"

set "GIT_REMOTE=origin"
set "GIT_BRANCH=main"

set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"

rem ============================================================
rem Worker schedule
rem ============================================================

rem Run after each Hot Manager interval.
set "LOOP_SLEEP_SECONDS=600"

rem Retry sooner after export or Git failure.
set "ERROR_SLEEP_SECONDS=120"

set "RUN_ONCE=false"

if /I "%~1"=="once" (
    set "RUN_ONCE=true"
)

echo ============================================================
echo MN PROXY - IRANCELL EXPORT AND PUBLISH
echo PROJECT: %PROJECT_DIR%
echo DATABASE: %CONFIG_DATABASE_URL%
echo PUBLIC FILE: %PUBLIC_CONFIGS_FILE%
echo EXPORTER: %PUBLIC_EXPORT_SCRIPT%
echo OPERATOR: %PUBLIC_POOL_OPERATOR%
echo LOOP SLEEP: %LOOP_SLEEP_SECONDS% seconds
echo RUN ONCE: %RUN_ONCE%
echo ============================================================

rem ============================================================
rem Profile guard
rem ============================================================

"%PYTHON%" -c "import os; assert os.environ.get('CONFIG_DATABASE_URL') == 'sqlite:///configs.db', 'invalid database profile'; assert os.environ.get('PUBLIC_CONFIGS_FILE') == 'public_configs.json', 'invalid public file'; assert os.environ.get('SMART_BATCH_OPERATOR') == 'irancell', 'invalid smart operator'; assert os.environ.get('PUBLIC_POOL_OPERATOR') == 'irancell', 'invalid pool operator'; assert os.environ.get('PUBLIC_EXPORT_SCRIPT') == 'export_public_configs_pool_metrics.py', 'invalid exporter'; assert os.environ.get('AUTO_CYCLE_POOL_SUPERVISOR_ENABLED', '').lower() == 'false', 'legacy supervisor must be disabled'; print('PROFILE GUARD: OK')"

if errorlevel 1 (
    echo ERROR: Irancell export/publish profile guard failed.
    exit /b 1
)

:run_export_publish

for /f %%I in (
    'powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"'
) do (
    set "LOG_DATE=%%I"
)

set "LOG_FILE=%LOG_DIR%\export_publish_irancell_!LOG_DATE!.log"

echo.
echo ============================================================
echo EXPORT AND PUBLISH CYCLE START
echo TIME: %date% %time%
echo LOG: !LOG_FILE!
echo ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Continue';" ^
    "& '%PYTHON%' -u '%PROJECT_DIR%\irancell_export_publish_cycle.py' 2>&1 | Tee-Object -FilePath '!LOG_FILE!' -Append;" ^
    "$exitCode=$LASTEXITCODE;" ^
    "exit $exitCode"

set "CYCLE_EXIT_CODE=!ERRORLEVEL!"

echo.
echo ============================================================
echo EXPORT AND PUBLISH CYCLE FINISHED
echo TIME: %date% %time%
echo EXIT CODE: !CYCLE_EXIT_CODE!
echo ============================================================

if /I "%RUN_ONCE%"=="true" (
    exit /b !CYCLE_EXIT_CODE!
)

if not "!CYCLE_EXIT_CODE!"=="0" (
    echo Export or publish cycle failed.
    echo Retrying in %ERROR_SLEEP_SECONDS% seconds...
    timeout /t %ERROR_SLEEP_SECONDS% /nobreak >nul
    goto run_export_publish
)

echo Next export and publish cycle in %LOOP_SLEEP_SECONDS% seconds...
echo Press Ctrl+C to stop this terminal.

timeout /t %LOOP_SLEEP_SECONDS% /nobreak >nul
goto run_export_publish
