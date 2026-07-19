@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0.") do set "PROJECT_DIR=%%~fI"
set "LOCK_FILE=%PROJECT_DIR%\publisher_mci.lock"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not enter project directory.
    exit /b 1
)

echo ======================================================
echo MN Proxy - MCI CENTRAL PUBLISHER
echo TIME: %date% %time%
echo PROJECT: %PROJECT_DIR%
echo ======================================================

if not exist "%PYTHON%" (
    echo ERROR: Python venv not found:
    echo %PYTHON%
    exit /b 1
)

if exist "%LOCK_FILE%" (
    echo Existing publisher lock found:
    echo %LOCK_FILE%

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ErrorActionPreference='Stop';" ^
        "$lock='%LOCK_FILE%';" ^
        "$age=(New-TimeSpan -Start (Get-Item $lock).LastWriteTime -End (Get-Date)).TotalMinutes;" ^
        "Write-Host ('LOCK AGE MINUTES: {0:N1}' -f $age);" ^
        "if ($age -ge 150) {" ^
        "Remove-Item $lock -Force;" ^
        "Write-Host 'STALE LOCK REMOVED';" ^
        "exit 0" ^
        "} else {" ^
        "Write-Host 'LOCK IS RECENT. ANOTHER PUBLISHER MAY BE RUNNING.';" ^
        "exit 2" ^
        "}"

    if errorlevel 2 (
        echo Publisher lock is recent. Exiting without publishing.
        exit /b 2
    )

    if errorlevel 1 (
        echo ERROR: Failed to inspect publisher lock.
        exit /b 1
    )
)

echo %date% %time% > "%LOCK_FILE%"
if errorlevel 1 (
    echo ERROR: Could not create publisher lock.
    exit /b 1
)

echo.
echo [1/2] Validate and publish received MCI artifact...
"%PYTHON%" -u publish_received_mci_artifact.py
set "PUBLISH_EXIT_CODE=!ERRORLEVEL!"

echo.
echo [2/2] Receive MCI dashboard snapshot...
"%PYTHON%" -u receive_mci_dashboard_snapshot.py
set "METRICS_EXIT_CODE=!ERRORLEVEL!"

if not "!METRICS_EXIT_CODE!"=="0" (
    echo.
    echo WARNING: MCI dashboard snapshot receiver failed.
    echo METRICS EXIT CODE: !METRICS_EXIT_CODE!
    echo Check:
    echo %PROJECT_DIR%\.runtime\dashboard\mci_metrics_receiver_status.json
) else (
    echo MCI dashboard snapshot receiver completed successfully.
)

if not "!PUBLISH_EXIT_CODE!"=="0" goto publish_error

echo.
echo ======================================================
echo MCI CENTRAL PUBLISHER FINISHED SUCCESSFULLY
echo PUBLISH EXIT CODE: !PUBLISH_EXIT_CODE!
echo METRICS EXIT CODE: !METRICS_EXIT_CODE!
echo ======================================================

del "%LOCK_FILE%" >nul 2>&1
exit /b 0

:publish_error
echo.
echo ======================================================
echo ERROR: MCI PUBLIC ARTIFACT PUBLISH FAILED
echo PUBLISH EXIT CODE: !PUBLISH_EXIT_CODE!
echo METRICS EXIT CODE: !METRICS_EXIT_CODE!
echo Dashboard metrics receiver was executed independently.
echo Check publisher_status_mci.json and console output.
echo ======================================================

del "%LOCK_FILE%" >nul 2>&1
exit /b !PUBLISH_EXIT_CODE!
