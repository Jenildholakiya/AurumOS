<#
    ============================================================================
    AURUMOS - UNBLOCK FILES
    ============================================================================
    Run this on any client PC when you get:
      "Failed to load Python DLL"
      "python312.dll ... The specified module could not be found."

    WHAT IT DOES
    ------------
    Clears Windows' "Mark of the Web" security tag (Zone.Identifier) from every
    file in this folder and all subfolders. Windows stamps this tag on files
    copied from USB / downloaded from the internet, which can block bundled
    DLLs such as python312.dll from loading.

    WHEN TO USE
    -----------
    - After copying AurumOS from USB to a client PC
    - When the EXE won't start and shows a Python DLL error
    - Run once per client PC, in the AurumOS folder

    HOW TO RUN
    -----------
    1. Open PowerShell in the AurumOS folder (right-click folder -> "Open in Terminal")
    2. Run:  powershell -ExecutionPolicy Bypass -File tools\unblock_files.ps1
    3. Then double-click AurumOS.exe again

    ============================================================================
#>

# Clear Mark of the Web from all files so bundled DLLs can load
# This script ships in <AurumOS>\tools\, so unblock its parent folder, wherever it is run from.
$AppRoot = Split-Path -Parent $PSScriptRoot
Get-ChildItem -Path $AppRoot -Recurse -File | Unblock-File

Write-Host ""
Write-Host "  Done. All files unblocked." -ForegroundColor Green
Write-Host "  Now double-click AurumOS.exe to run." -ForegroundColor Cyan
Write-Host ""
Write-Host "  If it still fails, install the VC++ Redistributable:" -ForegroundColor Yellow
Write-Host "  https://aka.ms/vs/17/release/vc_redist.x64.exe" -ForegroundColor Yellow
Write-Host ""
