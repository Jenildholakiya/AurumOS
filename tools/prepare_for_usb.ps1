<#
.SYNOPSIS
    Prepare AurumOS for USB distribution to client PCs.
.DESCRIPTION
    This is the PERMANENT fix for "Failed to load Python DLL" on client PCs.

    It does three things:

      1. Stages a clean client folder (dist-client/) with the EXE + _internal\
         beside it. Never ship just AurumOS.exe alone - it needs _internal\
         right next to it.
      2. Unblocks every file (clears Mark of the Web / Zone.Identifier that
         Windows stamps on files copied from USB). Without this, .NET and the
         Python DLL loader can refuse to load bundled DLLs.
      3. Verifies python312.dll is present so the error can never happen.

    Run this AFTER building (python build.py), then copy dist-client/ to USB.

.PARAMETER Dist
    Path to the built dist\ folder. Defaults to dist\ beside this script.

.PARAMETER Out
    Output folder for the client-ready tree. Defaults to dist-client\.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\prepare_for_usb.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\prepare_for_usb.ps1 -Dist C:\build\dist -Out C:\usb\AurumOS
#>

[CmdletBinding()]
param(
    [string]$Dist,
    [string]$Out
)

$ErrorActionPreference = 'Stop'

# ── Resolve paths ─────────────────────────────────────────────────────────
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
if (-not $Dist) { $Dist = Join-Path $root 'dist' }
if (-not $Out)  { $Out  = Join-Path $root 'dist-client' }

$exe = Join-Path $Dist 'AurumOS.exe'
$internal = Join-Path $Dist '_internal'
$pythonDll = Join-Path $internal 'python312.dll'

Write-Host ''
Write-Host '  AurumOS - USB distribution preparation' -ForegroundColor Cyan
Write-Host "  Source : $Dist"
Write-Host "  Output : $Out"
Write-Host ''

# ── 1. Validate source ────────────────────────────────────────────────────
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Host "  [FAIL] AurumOS.exe not found at $exe" -ForegroundColor Red
    Write-Host '         Build first:  python build.py' -ForegroundColor Yellow
    exit 1
}
Write-Host '  [ OK ] AurumOS.exe found' -ForegroundColor Green

if (-not (Test-Path -LiteralPath $internal)) {
    Write-Host "  [FAIL] _internal folder missing at $internal" -ForegroundColor Red
    Write-Host '         The app cannot start without it. Rebuild with build.py.' -ForegroundColor Yellow
    exit 1
}
Write-Host '  [ OK ] _internal folder present' -ForegroundColor Green

if (-not (Test-Path -LiteralPath $pythonDll)) {
    Write-Host "  [FAIL] python312.dll missing at $pythonDll" -ForegroundColor Red
    Write-Host '         This IS the DLL that fails to load. Rebuild with build.py.' -ForegroundColor Yellow
    exit 1
}
Write-Host '  [ OK ] python312.dll present' -ForegroundColor Green

# Check VC++ redist dependency DLLs too
foreach ($dll in @('vcruntime140.dll', 'msvcp140.dll', 'ucrtbase.dll')) {
    $p = Join-Path $internal $dll
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Host "  [WARN] $dll not in _internal\ - client PC needs VC++ Redistributable" -ForegroundColor Yellow
    }
}

# ── 2. Stage clean output folder ──────────────────────────────────────────
if (Test-Path -LiteralPath $Out) {
    Write-Host "  Removing old $Out ..."
    Remove-Item -LiteralPath $Out -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $Out -Force | Out-Null

# Copy everything EXCEPT runtime data dirs (database, logs, etc.)
$exclude = @('database', 'exports', 'logs', 'photos', 'Reports', '_restore_test_safety', 'ui', 'tools')
Get-ChildItem -LiteralPath $Dist | ForEach-Object {
    if ($exclude -contains $_.Name) { return }
    $dst = Join-Path $Out $_.Name
    if ($_.PSIsContainer) {
        Copy-Item -LiteralPath $_.FullName -Destination $dst -Recurse -Force
    } else {
        Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
    }
    Write-Host "  Copied $($_.Name)"
}

# Create empty runtime data dirs so the app doesn't crash on first run
foreach ($d in @('database', 'exports', 'logs', 'photos', 'Reports')) {
    $p = Join-Path $Out $d
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

Write-Host ''
Write-Host '  [ OK ] Client folder staged' -ForegroundColor Green

# ── 3. Unblock every file (clears Mark of the Web from USB copy) ──────────
Write-Host ''
Write-Host '  Clearing Mark of the Web on all files...'
$count = 0
Get-ChildItem -LiteralPath $Out -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $stream = Get-Item $_.FullName -Stream Zone.Identifier -ErrorAction Stop
        Unblock-File -LiteralPath $_.FullName -ErrorAction Stop
        $count++
    } catch {
        # No Zone.Identifier - already clean
    }
}
if ($count -gt 0) {
    Write-Host "  [ OK ] Unblocked $count file(s)" -ForegroundColor Green
} else {
    Write-Host '  [ OK ] No files were blocked' -ForegroundColor Green
}

# ── 4. Write client README ────────────────────────────────────────────────
$readme = Join-Path $Out 'READ_FIRST.txt'
$readmeContent = @"
================================================================================
  AURUMOS - CLIENT PC SETUP
================================================================================

WHAT TO DO
----------
1. Copy this entire folder to the client PC (anywhere - C:\AurumOS, D:\AurumOS, etc.)
2. Double-click AurumOS.exe to run.
3. Do NOT delete _internal\ - the EXE needs it right beside it.

IF YOU SEE "Failed to load Python DLL"
---------------------------------------
This means python312.dll could not load. On the client PC, run this in
PowerShell as Administrator:

    powershell -ExecutionPolicy Bypass -File "tools\prepare_for_usb.ps1"

Or manually:
  - Right-click AurumOS.exe -> Properties -> Unblock (if present)
  - Right-click dist\_internal\python312.dll -> Properties -> Unblock
  - Install Microsoft Visual C++ Redistributable for Visual Studio 2015-2022
    (x64): https://aka.ms/vs/17/release/vc_redist.x64.exe

IF YOU SEE "Windows protected your PC"
---------------------------------------
The app is unsigned. Click More info -> Run anyway.
To remove this permanently, get a code-signing certificate and set it up in
sign_config.json, then rebuild with python build.py.

================================================================================
"@
$readmeContent | Out-File -FilePath $readme -Encoding utf8
Write-Host "  [ OK ] Wrote $readme" -ForegroundColor Green

# ── 5. Done ───────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '  USB distribution ready:' -ForegroundColor Green
Write-Host "    $Out" -ForegroundColor Cyan
Write-Host ''
Write-Host '  Copy this whole folder to USB, then to each client PC.' -ForegroundColor Yellow
Write-Host '  The client PC must have the VC++ Redistributable installed:' -ForegroundColor Yellow
Write-Host '    https://aka.ms/vs/17/release/vc_redist.x64.exe' -ForegroundColor Yellow
Write-Host ''
