@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0.") do set "PROJECT_DIR=%%~fI"
set "ONESHOT_RUNNER=%PROJECT_DIR%\run_publish_received_mci.bat"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "SLEEP_SECONDS=10"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not enter project directory.
    exit /b 1
)

if not exist "%ONESHOT_RUNNER%" (
    echo ERROR: Publisher runner not found:
    echo %ONESHOT_RUNNER%
    exit /b 1
)

if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
    if errorlevel 1 (
        echo ERROR: Could not create log directory.
        exit /b 1
    )
)

rem Prevent a second permanent publisher loop from starting.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "try {" ^
    "$parent=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)).ParentProcessId;" ^
    "$other=@(Get-CimInstance Win32_Process | Where-Object {" ^
    "$_.ProcessId -ne $PID -and" ^
    "$_.ProcessId -ne $parent -and" ^
    "$_.CommandLine -match 'run_publish_received_mci_loop\.bat'" ^
    "});" ^
    "if ($other.Count -gt 0) { exit 2 } else { exit 0 }" ^
    "} catch {" ^
    "Write-Error $_;" ^
    "exit 1" ^
    "}"

if errorlevel 2 (
    echo MCI CENTRAL PUBLISHER LOOP IS ALREADY RUNNING.
    exit /b 2
)

if errorlevel 1 (
    echo ERROR: Failed to inspect existing MCI publisher processes.
    exit /b 1
)

echo ======================================================
echo MN Proxy - Permanent MCI Central Publisher
echo PROJECT: %PROJECT_DIR%
echo INTERVAL: %SLEEP_SECONDS% seconds
echo ======================================================

:loop

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do (
    set "LOG_DATE=%%I"
)

set "LOG_FILE=%LOG_DIR%\publisher_mci_loop_!LOG_DATE!.log"

for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format o"') do (
    set "CYCLE_STARTED=%%I"
)

>>"!LOG_FILE!" echo.
>>"!LOG_FILE!" echo ======================================================
>>"!LOG_FILE!" echo PUBLISHER LOOP CYCLE START: !CYCLE_STARTED!
>>"!LOG_FILE!" echo ======================================================

call "%ONESHOT_RUNNER%" >>"!LOG_FILE!" 2>&1
set "PUBLISH_EXIT_CODE=!ERRORLEVEL!"

for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format o"') do (
    set "CYCLE_FINISHED=%%I"
)

>>"!LOG_FILE!" echo PUBLISHER LOOP CYCLE FINISHED: !CYCLE_FINISHED!
>>"!LOG_FILE!" echo PUBLISHER EXIT CODE: !PUBLISH_EXIT_CODE!

if "!PUBLISH_EXIT_CODE!"=="0" (
    echo [!CYCLE_FINISHED!] Publisher cycle completed successfully.
) else if "!PUBLISH_EXIT_CODE!"=="2" (
    echo [!CYCLE_FINISHED!] Publisher busy; retrying later.
) else (
    echo [!CYCLE_FINISHED!] Publisher cycle failed with code !PUBLISH_EXIT_CODE!; retrying later.
)

timeout /t %SLEEP_SECONDS% /nobreak >nul
goto loop
