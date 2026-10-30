@echo off
REM AurumOS instant-refresh dev run — no build needed.
REM Edits in ui\ show on Refresh (F5); Python edits apply on next start.
REM --dev lets this run NEXT TO the installed EXE (separate instance lock).
REM This file lives in scripts\dev\; main.py is two folders up.
cd /d "%~dp0..\.."
where python >nul 2>nul
if %errorlevel%==0 (
  python main.py --dev
) else (
  py -3 main.py --dev
)
pause
