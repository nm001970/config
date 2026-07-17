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
rem Hot Manager policy
rem ============================================================

set "HOT_TARGET=5"
set "MAX_CANDIDATES=10"

set "MIN_SCORE=30"
set "MAX_PING_MS=2000"

set "MIN_QUALITY_DOWNLOAD=1.5"
set "MIN_GATE_DOWNLOAD=1.5"
set "MIN_HOT_KEEP_DOWNLOAD=1.5"

set "TEST_ROUNDS=1"
set "ROUND_DELAY_SECONDS=0"
set "MIN_SUCCESSFUL_ROUNDS=1"

set "FAILURE_LIMIT=2"
set "HOT_MIN_AGE_MINUTES=60"
set "PROMOTION_RETRY_MIN_AGE_MINUTES=30"

set "MIN_HOT_CAPACITY=3"
set "CORRELATED_FAILURE_MIN_COUNT=2"
set "CORRELATED_FAILURE_RATIO=0.60"

set "STUCK_MINUTES=30"
set "RECOVER_LIMIT=100"

rem Run Hot Manager every 10 minutes.
set "LOOP_SLEEP_SECONDS=600"
set "ERROR_SLEEP_SECONDS=60"

set "RUN_ONCE=false"

if /I "%~1"=="once" (
    set "RUN_ONCE=true"
    set "MAX_CANDIDATES=5"
)

echo ============================================================
echo MN PROXY - IRANCELL HOT MANAGER
echo PROJECT: %PROJECT_DIR%
echo DATABASE: %CONFIG_DATABASE_URL%
echo OPERATOR: %PUBLIC_POOL_OPERATOR%
echo HOT TARGET: %HOT_TARGET%
echo MAX CANDIDATES: %MAX_CANDIDATES%
echo LOOP SLEEP: %LOOP_SLEEP_SECONDS% seconds
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

:run_hot_manager

for /f %%I in (
    'powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"'
) do (
    set "LOG_DATE=%%I"
)

set "LOG_FILE=%LOG_DIR%\hot_manager_irancell_!LOG_DATE!.log"

echo.
echo ============================================================
echo HOT MANAGER BATCH START
echo TIME: %date% %time%
echo LOG: !LOG_FILE!
echo ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Continue';" ^
    "& '%PYTHON%' -u '%PROJECT_DIR%\v2_hot_manager.py' --operator irancell --target %HOT_TARGET% --max-candidates %MAX_CANDIDATES% --min-score %MIN_SCORE% --max-ping %MAX_PING_MS% --min-quality-download %MIN_QUALITY_DOWNLOAD% --min-gate-download %MIN_GATE_DOWNLOAD% --min-hot-keep-download %MIN_HOT_KEEP_DOWNLOAD% --rounds %TEST_ROUNDS% --round-delay %ROUND_DELAY_SECONDS% --min-successful-rounds %MIN_SUCCESSFUL_ROUNDS% --failure-limit %FAILURE_LIMIT% --hot-min-age-minutes %HOT_MIN_AGE_MINUTES% --promotion-retry-min-age-minutes %PROMOTION_RETRY_MIN_AGE_MINUTES% --min-hot-capacity %MIN_HOT_CAPACITY% --correlated-failure-min-count %CORRELATED_FAILURE_MIN_COUNT% --correlated-failure-ratio %CORRELATED_FAILURE_RATIO% --recover-stuck --stuck-minutes %STUCK_MINUTES% --recover-limit %RECOVER_LIMIT% 2>&1 | Tee-Object -FilePath '!LOG_FILE!' -Append;" ^
    "$exitCode=$LASTEXITCODE;" ^
    "exit $exitCode"

set "HOT_EXIT_CODE=!ERRORLEVEL!"

echo.
echo ============================================================
echo HOT MANAGER BATCH FINISHED
echo TIME: %date% %time%
echo EXIT CODE: !HOT_EXIT_CODE!
echo ============================================================

if /I "%RUN_ONCE%"=="true" (
    exit /b !HOT_EXIT_CODE!
)

if not "!HOT_EXIT_CODE!"=="0" (
    echo Hot Manager failed.
    echo Restarting in %ERROR_SLEEP_SECONDS% seconds...
    timeout /t %ERROR_SLEEP_SECONDS% /nobreak >nul
    goto run_hot_manager
)

echo Next Hot Manager batch in %LOOP_SLEEP_SECONDS% seconds...
echo Press Ctrl+C to stop this terminal.

timeout /t %LOOP_SLEEP_SECONDS% /nobreak >nul
goto run_hot_manager