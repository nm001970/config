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
rem Irancell runtime profile
rem ============================================================

set "CONFIG_DATABASE_URL=sqlite:///configs.db"
set "PUBLIC_CONFIGS_FILE=public_configs.json"
set "SMART_BATCH_OPERATOR=irancell"
set "PUBLIC_POOL_OPERATOR=irancell"
set "AUTO_CYCLE_POOL_SUPERVISOR_ENABLED=false"
set "PYTHONUNBUFFERED=1"

rem ============================================================
rem Reserve Health policy
rem ============================================================

set "BATCH_LIMIT=5"
set "MIN_DOWNLOAD_MBPS=1.5"
set "FAILURE_LIMIT=2"

set "HEALTHY_MIN_AGE_MINUTES=60"
set "SUSPECT_MIN_AGE_MINUTES=30"

set "STUCK_MINUTES=15"
set "RECOVER_LIMIT=100"

set "LOOP_SLEEP_SECONDS=60"
set "ERROR_SLEEP_SECONDS=30"

set "RUN_ONCE=false"

if /I "%~1"=="once" (
    set "RUN_ONCE=true"
    set "BATCH_LIMIT=1"
)

echo ============================================================
echo MN PROXY - IRANCELL RESERVE HEALTH
echo PROJECT: %PROJECT_DIR%
echo DATABASE: %CONFIG_DATABASE_URL%
echo OPERATOR: %PUBLIC_POOL_OPERATOR%
echo BATCH LIMIT: %BATCH_LIMIT%
echo FAILURE LIMIT: %FAILURE_LIMIT%
echo HEALTHY AGE: %HEALTHY_MIN_AGE_MINUTES% minutes
echo SUSPECT AGE: %SUSPECT_MIN_AGE_MINUTES% minutes
echo RUN ONCE: %RUN_ONCE%
echo ============================================================

rem ============================================================
rem Profile guard
rem ============================================================

"%PYTHON%" -c "import os; assert os.environ.get('CONFIG_DATABASE_URL') == 'sqlite:///configs.db', 'invalid database profile'; assert os.environ.get('PUBLIC_CONFIGS_FILE') == 'public_configs.json', 'invalid export profile'; assert os.environ.get('SMART_BATCH_OPERATOR') == 'irancell', 'invalid smart operator'; assert os.environ.get('PUBLIC_POOL_OPERATOR') == 'irancell', 'invalid pool operator'; assert os.environ.get('AUTO_CYCLE_POOL_SUPERVISOR_ENABLED', '').lower() == 'false', 'legacy supervisor must be disabled'; print('PROFILE GUARD: OK')"

if errorlevel 1 (
    echo ERROR: Irancell profile guard failed.
    exit /b 1
)

:run_health

for /f %%I in (
    'powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"'
) do (
    set "LOG_DATE=%%I"
)

set "LOG_FILE=%LOG_DIR%\reserve_health_irancell_!LOG_DATE!.log"

echo.
echo ============================================================
echo RESERVE HEALTH BATCH START
echo TIME: %date% %time%
echo LOG: !LOG_FILE!
echo ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Continue';" ^
    "& '%PYTHON%' -u '%PROJECT_DIR%\v2_reserve_health.py' --operator irancell --limit %BATCH_LIMIT% --min-download %MIN_DOWNLOAD_MBPS% --failure-limit %FAILURE_LIMIT% --healthy-min-age-minutes %HEALTHY_MIN_AGE_MINUTES% --suspect-min-age-minutes %SUSPECT_MIN_AGE_MINUTES% --recover-stuck --stuck-minutes %STUCK_MINUTES% --recover-limit %RECOVER_LIMIT% 2>&1 | Tee-Object -FilePath '!LOG_FILE!' -Append;" ^
    "$exitCode=$LASTEXITCODE;" ^
    "exit $exitCode"

set "HEALTH_EXIT_CODE=!ERRORLEVEL!"

echo.
echo ============================================================
echo RESERVE HEALTH BATCH FINISHED
echo TIME: %date% %time%
echo EXIT CODE: !HEALTH_EXIT_CODE!
echo ============================================================

if /I "%RUN_ONCE%"=="true" (
    exit /b !HEALTH_EXIT_CODE!
)

if not "!HEALTH_EXIT_CODE!"=="0" (
    echo Reserve Health failed.
    echo Restarting in %ERROR_SLEEP_SECONDS% seconds...
    timeout /t %ERROR_SLEEP_SECONDS% /nobreak >nul
    goto run_health
)

echo Next Reserve Health batch in %LOOP_SLEEP_SECONDS% seconds...
echo Press Ctrl+C to stop this terminal.

timeout /t %LOOP_SLEEP_SECONDS% /nobreak >nul
goto run_health