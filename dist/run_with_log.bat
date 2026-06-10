@echo off
REM AurumOS — Run EXE and show live log in this terminal
REM Place this .bat next to AurumOS.exe and double-click it

cd /d "%~dp0"

echo ============================================
echo  AurumOS Live Log Monitor
echo ============================================
echo  EXE  : %~dp0AurumOS.exe
echo  Log  : %~dp0logs\aurumos.log
echo ============================================
echo.

REM Start the EXE in background
start "" "%~dp0AurumOS.exe"

REM Wait for log file to appear
echo Waiting for AurumOS to start...
:wait_log
if exist "%~dp0logs\aurumos.log" goto show_log
timeout /t 1 /nobreak >nul
goto wait_log

:show_log
echo Log file found. Streaming live logs...
echo ============================================
echo.

REM Stream log file live (like tail -f)
powershell -Command "Get-Content '%~dp0logs\aurumos.log' -Wait -Tail 50"