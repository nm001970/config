@echo off
setlocal EnableExtensions

set "PROJECT_DIR=C:\Users\White Ram\Desktop\v2ray_platform"

set "RAW_RUNNER=%PROJECT_DIR%\run_v2_raw_to_reserve_irancell.bat"
set "RESERVE_RUNNER=%PROJECT_DIR%\run_v2_reserve_health_irancell.bat"
set "HOT_RUNNER=%PROJECT_DIR%\run_v2_hot_manager_irancell.bat"
set "UPDATE_RUNNER=%PROJECT_DIR%\run_config_update_irancell.bat"
set "PUBLISH_RUNNER=%PROJECT_DIR%\run_export_publish_irancell.bat"
set "MCI_PUBLISHER_LOOP_RUNNER=%PROJECT_DIR%\run_publish_received_mci_loop.bat"

cd /d "%PROJECT_DIR%"

echo ============================================================
echo MN PROXY - START ALL IRANCELL WORKERS + MCI PUBLISHER
echo PROJECT: %PROJECT_DIR%
echo ============================================================

if not exist "%RAW_RUNNER%" (
    echo ERROR: Missing runner:
    echo %RAW_RUNNER%
    exit /b 1
)

if not exist "%RESERVE_RUNNER%" (
    echo ERROR: Missing runner:
    echo %RESERVE_RUNNER%
    exit /b 1
)

if not exist "%HOT_RUNNER%" (
    echo ERROR: Missing runner:
    echo %HOT_RUNNER%
    exit /b 1
)

if not exist "%UPDATE_RUNNER%" (
    echo ERROR: Missing runner:
    echo %UPDATE_RUNNER%
    exit /b 1
)

if not exist "%PUBLISH_RUNNER%" (
    echo ERROR: Missing runner:
    echo %PUBLISH_RUNNER%
    exit /b 1
)

if not exist "%MCI_PUBLISHER_LOOP_RUNNER%" (
    echo ERROR: Missing runner:
    echo %MCI_PUBLISHER_LOOP_RUNNER%
    exit /b 1
)

rem ============================================================
rem Legacy worker guard
rem ============================================================

powershell -NoProfile -Command "$legacy = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'auto_cycle\.py|auto_publish\.py|v2_pool_supervisor\.py|run_irancell_worker_console\.bat' }); if ($legacy.Count -gt 0) { Write-Host 'ERROR: Legacy worker or supervisor is running.'; $legacy | Select-Object ProcessId,Name,CommandLine | Format-List; exit 1 }; Write-Host 'LEGACY WORKER GUARD: OK'; exit 0"

if errorlevel 1 (
    echo Startup cancelled.
    exit /b 1
)

rem ============================================================
rem Terminal A - Raw to Reserve
rem ============================================================

powershell -NoProfile -Command "$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_v2_raw_to_reserve_irancell\.bat' }); if ($running.Count -gt 0) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo Starting Terminal A: Raw to Reserve...
    start "MN PROXY - A - RAW TO RESERVE" cmd /k call "%RAW_RUNNER%"
) else (
    echo Terminal A is already running. Skipped.
)

rem ============================================================
rem Terminal B - Reserve Health
rem ============================================================

powershell -NoProfile -Command "$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_v2_reserve_health_irancell\.bat' }); if ($running.Count -gt 0) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo Starting Terminal B: Reserve Health...
    start "MN PROXY - B - RESERVE HEALTH" cmd /k call "%RESERVE_RUNNER%"
) else (
    echo Terminal B is already running. Skipped.
)

rem ============================================================
rem Terminal C - Hot Manager
rem ============================================================

powershell -NoProfile -Command "$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_v2_hot_manager_irancell\.bat' }); if ($running.Count -gt 0) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo Starting Terminal C: Hot Manager...
    start "MN PROXY - C - HOT MANAGER" cmd /k call "%HOT_RUNNER%"
) else (
    echo Terminal C is already running. Skipped.
)

rem ============================================================
rem Terminal D - Config Update
rem ============================================================

powershell -NoProfile -Command "$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_config_update_irancell\.bat' }); if ($running.Count -gt 0) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo Starting Terminal D: Config Update...
    start "MN PROXY - D - CONFIG UPDATE" cmd /k call "%UPDATE_RUNNER%"
) else (
    echo Terminal D is already running. Skipped.
)


rem ============================================================
rem Terminal E - Export and Publish
rem ============================================================

powershell -NoProfile -Command "$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_export_publish_irancell\.bat' }); if ($running.Count -gt 0) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo Starting Terminal E: Export and Publish...
    start "MN PROXY - E - EXPORT PUBLISH" cmd /k call "%PUBLISH_RUNNER%"
) else (
    echo Terminal E is already running. Skipped.
)

rem ============================================================
rem Terminal F - MCI Central Publisher
rem ============================================================

powershell -NoProfile -Command "$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_publish_received_mci_loop\.bat' }); if ($running.Count -gt 0) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo Starting Terminal F: MCI Central Publisher...
    start "MN PROXY - F - MCI CENTRAL PUBLISHER" cmd /k call "%MCI_PUBLISHER_LOOP_RUNNER%"
) else (
    echo Terminal F is already running. Skipped.
)

echo.
echo Waiting for worker terminals to initialize...
timeout /t 5 /nobreak >nul

echo.
echo ============================================================
echo CURRENT IRANCELL RUNNER WINDOWS AND MCI PUBLISHER
echo ============================================================

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run_v2_raw_to_reserve_irancell\.bat|run_v2_reserve_health_irancell\.bat|run_v2_hot_manager_irancell\.bat|run_config_update_irancell\.bat|run_export_publish_irancell\.bat|run_publish_received_mci_loop\.bat' } | Select-Object ProcessId,Name,CommandLine | Format-Table -Wrap"

echo.
echo ============================================================
echo ALL IRANCELL WORKERS AND MCI PUBLISHER STARTED
echo ============================================================

exit /b 0