@echo off
setlocal EnableExtensions

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
rem Raw to Reserve settings
rem ============================================================

set "BATCH_SIZE=10"
set "STUCK_MINUTES=30"
set "RECOVER_LIMIT=100"
set "RESTART_DELAY_SECONDS=15"
set "RUN_ONCE=false"

if /I "%~1"=="once" (
    set "RUN_ONCE=true"
    set "BATCH_SIZE=2"
)

echo ============================================================
echo MN PROXY - IRANCELL RAW TO RESERVE
echo PROJECT: %PROJECT_DIR%
echo DATABASE: %CONFIG_DATABASE_URL%
echo OPERATOR: %SMART_BATCH_OPERATOR%
echo BATCH SIZE: %BATCH_SIZE%
echo RUN ONCE: %RUN_ONCE%
echo ============================================================

rem Profile guard
"%PYTHON%" -c "import os; assert os.environ.get('CONFIG_DATABASE_URL') == 'sqlite:///configs.db', 'invalid database profile'; assert os.environ.get('SMART_BATCH_OPERATOR') == 'irancell', 'invalid operator profile'; assert os.environ.get('PUBLIC_POOL_OPERATOR') == 'irancell', 'invalid pool operator profile'; assert os.environ.get('AUTO_CYCLE_POOL_SUPERVISOR_ENABLED', '').lower() == 'false', 'legacy supervisor must be disabled'; print('PROFILE GUARD: OK')"

if errorlevel 1 (
    echo ERROR: Irancell profile guard failed.
    exit /b 1
)

:run_worker

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do (
    set "LOG_STAMP=%%I"
)

set "LOG_FILE=%LOG_DIR%\raw_to_reserve_irancell_%LOG_STAMP%.log"

echo.
echo ============================================================
echo RAW TO RESERVE PROCESS START
echo TIME: %date% %time%
echo LOG: %LOG_FILE%
echo ============================================================

if /I "%RUN_ONCE%"=="true" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ErrorActionPreference='Continue';" ^
        "& '%PYTHON%' -u '%PROJECT_DIR%\v2_raw_to_reserve_cycle.py' --operator irancell --batch-size %BATCH_SIZE% --recover-stuck --stuck-minutes %STUCK_MINUTES% --recover-limit %RECOVER_LIMIT% --once 2>&1 | Tee-Object -FilePath '%LOG_FILE%' -Append;" ^
        "exit $LASTEXITCODE"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ErrorActionPreference='Continue';" ^
        "& '%PYTHON%' -u '%PROJECT_DIR%\v2_raw_to_reserve_cycle.py' --operator irancell --batch-size %BATCH_SIZE% --recover-stuck --stuck-minutes %STUCK_MINUTES% --recover-limit %RECOVER_LIMIT% 2>&1 | Tee-Object -FilePath '%LOG_FILE%' -Append;" ^
        "exit $LASTEXITCODE"
)

set "WORKER_EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
echo RAW TO RESERVE PROCESS EXITED
echo TIME: %date% %time%
echo EXIT CODE: %WORKER_EXIT_CODE%
echo ============================================================

if /I "%RUN_ONCE%"=="true" (
    exit /b %WORKER_EXIT_CODE%
)

echo Restarting in %RESTART_DELAY_SECONDS% seconds...
echo Press Ctrl+C to stop this terminal.

timeout /t %RESTART_DELAY_SECONDS% /nobreak >nul
goto run_worker