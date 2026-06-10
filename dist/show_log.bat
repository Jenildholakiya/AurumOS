@echo off
REM AurumOS — Show live log (run AFTER EXE is already open)
REM Double-click this any time to tail the log

cd /d "%~dp0"

if not exist "logs\aurumos.log" (
    echo No log file found yet. Start AurumOS first.
    pause
    exit /b
)

echo ============================================
echo  AurumOS Live Log — Press Ctrl+C to stop
echo ============================================
echo.

powershell -Command "Get-Content 'logs\aurumos.log' -Wait -Tail 80"