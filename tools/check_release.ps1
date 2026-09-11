<#
.SYNOPSIS
    Say what a customer's PC will do with this build, before you ship it.

.DESCRIPTION
    Run this on dist\ after building. It answers one question: when someone
    downloads and double-clicks AurumOS.exe, what happens?

    Three things decide that, and only one of them is a real fix:

      Signature   An Authenticode signature from a trusted CA is the only
                  thing that stops SmartScreen outright. Nothing else
                  substitutes for it.
      Publisher   Company, product and version, embedded in the file. An
                  executable carrying none of them scores worse with
                  reputation and heuristic engines, and its Properties tab
                  is blank when a suspicious customer looks.
      Mark of the Web
                  Windows tags anything downloaded from the internet with a
                  hidden Zone.Identifier stream. SmartScreen's "Windows
                  protected your PC" box is driven by that tag. A file
                  copied from a USB stick does not carry it and does not get
                  the box; the same file downloaded from a website does.

    This reports all three and tells you which customers will see what.

.PARAMETER Path
    The folder to check. Defaults to dist\ beside this script's parent.

.PARAMETER Unblock
    Remove the Mark of the Web from every file in the folder. Use this on a
    copy you have just downloaded and are about to install, which is the same
    thing as ticking Unblock on the file's Properties tab. It does not disable
    any scanning: antivirus still inspects the file. It only clears the "this
    came from the internet" tag that drives the SmartScreen prompt.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\check_release.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\check_release.ps1 -Unblock
#>

[CmdletBinding()]
param(
    [string]$Path,
    [switch]$Unblock
)

$ErrorActionPreference = 'Stop'

function Resolve-Target {
    param([string]$Given)
    if ($Given) {
        if (-not (Test-Path -LiteralPath $Given)) { throw "Folder not found: $Given" }
        return (Resolve-Path -LiteralPath $Given).Path
    }
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    foreach ($c in @(
        (Split-Path -Parent $here),
        (Join-Path (Split-Path -Parent $here) 'dist'),
        (Join-Path $here 'dist')
    )) {
        if ($c -and (Test-Path (Join-Path $c 'AurumOS.exe'))) { return (Resolve-Path $c).Path }
    }
    throw "Could not find AurumOS.exe. Pass -Path 'C:\path\to\dist'."
}

$target = Resolve-Target -Given $Path
$exe = Join-Path $target 'AurumOS.exe'

Write-Host ''
Write-Host '  AurumOS - release check' -ForegroundColor Cyan
Write-Host "  $exe"
Write-Host ''

$problems = @()

# ── 1. Signature ────────────────────────────────────────────────────────
$sig = Get-AuthenticodeSignature $exe
if ($sig.Status -eq 'Valid') {
    Write-Host '  [ OK ] Signed' -ForegroundColor Green
    Write-Host ("         by    : " + $sig.SignerCertificate.Subject)
    Write-Host ("         until : " + $sig.SignerCertificate.NotAfter)
    if ($sig.SignerCertificate.NotAfter -lt (Get-Date).AddDays(60)) {
        Write-Host '  [WARN] That certificate expires within 60 days.' -ForegroundColor Yellow
    }
    # A timestamp is what keeps the signature valid after the cert expires.
    if (-not $sig.TimeStamperCertificate) {
        Write-Host '  [WARN] No timestamp. This signature dies with the certificate.' -ForegroundColor Yellow
        $problems += 'signature is not timestamped'
    }
} else {
    Write-Host ("  [FAIL] Not signed (" + $sig.Status + ")") -ForegroundColor Red
    Write-Host '         Every customer sees "Windows protected your PC" on first run.'
    Write-Host '         A code-signing certificate is the only thing that removes it.'
    $problems += 'unsigned'
}

# ── 2. Publisher details ────────────────────────────────────────────────
$vi = (Get-Item $exe).VersionInfo
$missing = @()
foreach ($f in @('CompanyName', 'ProductName', 'FileVersion', 'FileDescription')) {
    if (-not $vi.$f) { $missing += $f }
}
if ($missing.Count -eq 0) {
    Write-Host '  [ OK ] Publisher details present' -ForegroundColor Green
    Write-Host ("         " + $vi.CompanyName + " / " + $vi.ProductName + " v" + $vi.FileVersion)
} else {
    Write-Host ("  [FAIL] Publisher details missing: " + ($missing -join ', ')) -ForegroundColor Red
    Write-Host '         Properties shows a blank Details tab, and reputation engines'
    Write-Host '         weigh that. Rebuild with build.py, which embeds them.'
    $problems += 'no publisher details'
}

# ── 3. Packer ───────────────────────────────────────────────────────────
$bytes = [System.IO.File]::ReadAllBytes($exe)
$text = [System.Text.Encoding]::ASCII.GetString($bytes[0..([Math]::Min(4096, $bytes.Length - 1))])
if ($text -match 'UPX[0-9!]') {
    Write-Host '  [FAIL] The EXE is UPX-packed.' -ForegroundColor Red
    Write-Host '         Scanners treat packed files as packed. Set upx=False.'
    $problems += 'UPX-packed'
} else {
    Write-Host '  [ OK ] Not packed' -ForegroundColor Green
}

# ── 4. Mark of the Web ──────────────────────────────────────────────────
$marked = @(Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                try { Get-Item $_.FullName -Stream Zone.Identifier -ErrorAction Stop | Out-Null; $true }
                catch { $false }
            })
if ($marked.Count -gt 0) {
    Write-Host ("  [WARN] " + $marked.Count + " file(s) are tagged as downloaded from the internet.") -ForegroundColor Yellow
    Write-Host '         That tag is what raises the SmartScreen box on this PC.'
    if ($Unblock) {
        $marked | Unblock-File
        Write-Host ("  [ OK ] Cleared the tag from " + $marked.Count + " file(s).") -ForegroundColor Green
        Write-Host '         Same as ticking Unblock on the file Properties tab.'
        Write-Host '         Antivirus still scans these files; only the prompt is gone.'
    } else {
        Write-Host '         Re-run with -Unblock to clear it on this copy.'
    }
} else {
    Write-Host '  [ OK ] No internet-download tag on this copy' -ForegroundColor Green
}

# ── 5. The folder itself ────────────────────────────────────────────────
$internal = Join-Path $target '_internal'
if (Test-Path $internal) {
    Write-Host '  [ OK ] _internal is beside the EXE' -ForegroundColor Green
} else {
    Write-Host '  [FAIL] _internal is missing.' -ForegroundColor Red
    Write-Host '         The app will not start. This is the single most common'
    Write-Host '         cause of "AurumOS could not start .NET" on a customer PC.'
    $problems += '_internal missing'
}

# ── verdict ─────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '  What a customer will see' -ForegroundColor Cyan
if ($problems -contains 'unsigned') {
    Write-Host '   - Downloading it: "Windows protected your PC". They must click' -ForegroundColor Yellow
    Write-Host '     More info, then Run anyway. Many will not, and will ring you.' -ForegroundColor Yellow
    Write-Host '   - Copying it from a USB stick or a local network share: no prompt,' -ForegroundColor Yellow
    Write-Host '     because the download tag is never applied.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '   The fix is a code-signing certificate issued to your registered'
    Write-Host '   business. An EV certificate clears SmartScreen from the first'
    Write-Host '   download; a standard one has to build reputation over some weeks'
    Write-Host '   of installs first. build.py signs automatically once you set one'
    Write-Host '   up in sign_config.json - see docs/ANTIVIRUS.md.'
} elseif ($problems.Count -eq 0) {
    Write-Host '   Nothing. Signed, named, unpacked and complete.' -ForegroundColor Green
} else {
    Write-Host ('   Signed, but still worth fixing: ' + ($problems -join ', ')) -ForegroundColor Yellow
}
Write-Host ''

if ($problems.Count -gt 0) { exit 1 }
exit 0
