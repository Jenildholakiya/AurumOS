@echo off
REM AurumOS keyboard diagnostic — run while the shop copy stays open.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python diag_keys.py
) else (
  py -3 diag_keys.py
)
pause
