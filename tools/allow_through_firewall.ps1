<#
.SYNOPSIS
    Let staff PCs reach the AurumOS shop server through Windows Firewall.

.DESCRIPTION
    Run this ONCE on the main PC (the one that holds the shop's data), as
    Administrator. Staff PCs do not need it: they only make outgoing
    connections, which Windows already allows.

    Without a rule, the first launch pops "Allow AurumOS to communicate on
    these networks?". Whoever is standing at the counter clicks whatever
    dismisses it fastest, and if that is Cancel, Windows writes a BLOCK rule
    that persists. Staff PCs then fail to connect with no visible reason and
    nothing in the log, because the packets never arrive.

    The rule created here is deliberately narrow:
      - inbound only, TCP only, on the single port AurumOS listens on
      - tied to this AurumOS.exe, so nothing else can use the opening
      - Private and Domain networks only. Never Public: a till on a hotel or
        airport network must not be reachable, and Windows treats an unknown
        network as Public.

.PARAMETER Remove
    Delete the rule instead of creating it.

.PARAMETER Port
    Override the port. Only needed if AURUM_CLUSTER_PORT was changed from the
    default 58903.

.PARAMETER ExePath
    Path to AurumOS.exe. Defaults to the copy beside this script's parent
    folder, which is where it sits in a normal install.

.EXAMPLE
    Right-click > Run with PowerShell, as Administrator. Or:
    powershell -ExecutionPolicy Bypass -File .\allow_through_firewall.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\allow_through_firewall.ps1 -Remove
#>

[CmdletBinding()]
param(
    [switch]$Remove,
    [int]$Port = 58903,
    [string]$ExePath
)

$ErrorActionPreference = 'Stop'
$RuleName = 'AurumOS Shop Server'

function Assert-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host ''
        Write-Host '  This needs Administrator rights to change the firewall.' -ForegroundColor Yellow
        Write-Host '  Close this, right-click PowerShell, choose "Run as administrator",' -ForegroundColor Yellow
        Write-Host '  and run it again.' -ForegroundColor Yellow
        Write-Host ''
        exit 1
    }
}

function Resolve-ExePath {
    param([string]$Given)
    if ($Given) {
        if (-not (Test-Path -LiteralPath $Given)) { throw "AurumOS.exe not found at: $Given" }
        return (Resolve-Path -LiteralPath $Given).Path
    }
    # tools\ sits beside the EXE in an install, and inside dist\ in a build.
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    foreach ($candidate in @(
        (Join-Path (Split-Path -Parent $here) 'AurumOS.exe'),
        (Join-Path $here 'AurumOS.exe'),
        (Join-Path (Split-Path -Parent $here) 'dist\AurumOS.exe')
    )) {
        if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    throw "Could not find AurumOS.exe. Pass -ExePath 'C:\path\to\AurumOS.exe'."
}

function Remove-ExistingRule {
    # Removes allow AND block rules of this name. A block rule left by someone
    # clicking Cancel on the Windows prompt outranks any allow rule added
    # afterwards, so creating one without clearing that first fixes nothing.
    $existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | Remove-NetFirewallRule
        Write-Host "  Removed $($existing.Count) existing '$RuleName' rule(s)." -ForegroundColor DarkGray
    }
    # Windows names its own prompt-generated rules after the program, not ours.
    $auto = Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
            Where-Object { $_.Program -and $_.Program -ieq $script:Exe } |
            ForEach-Object { $_ | Get-NetFirewallRule -ErrorAction SilentlyContinue } |
            Where-Object { $_.DisplayName -ne $RuleName }
    if ($auto) {
        $blocking = $auto | Where-Object { $_.Action -eq 'Block' }
        if ($blocking) {
            $blocking | Remove-NetFirewallRule
            Write-Host "  Removed $($blocking.Count) BLOCK rule(s) Windows had recorded for this EXE." -ForegroundColor Yellow
        }
    }
}

Assert-Administrator
$script:Exe = Resolve-ExePath -Given $ExePath

Write-Host ''
Write-Host '  AurumOS - Windows Firewall' -ForegroundColor Cyan
Write-Host "  EXE  : $script:Exe"
Write-Host "  Port : $Port (TCP, inbound)"
Write-Host ''

if ($Remove) {
    Remove-ExistingRule
    Write-Host '  Done. Staff PCs can no longer reach this one.' -ForegroundColor Green
    Write-Host ''
    exit 0
}

Remove-ExistingRule

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Description 'Lets staff PCs in this shop reach the AurumOS server on the main PC. Private and Domain networks only.' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Program $script:Exe `
    -Profile Private,Domain `
    -Enabled True | Out-Null

Write-Host '  Rule created.' -ForegroundColor Green
Write-Host ''
Get-NetFirewallRule -DisplayName $RuleName |
    Format-Table DisplayName, Enabled, Direction, Action, Profile -AutoSize

Write-Host '  If staff PCs still cannot connect:' -ForegroundColor DarkGray
Write-Host '   - check this PC and the staff PC are on the SAME network' -ForegroundColor DarkGray
Write-Host '   - check Windows calls that network Private, not Public:' -ForegroundColor DarkGray
Write-Host '       Get-NetConnectionProfile' -ForegroundColor DarkGray
Write-Host '     A network set to Public will block this rule by design.' -ForegroundColor DarkGray
Write-Host '   - some third-party security suites keep their own firewall,' -ForegroundColor DarkGray
Write-Host '     separate from Windows. That one needs the same rule.' -ForegroundColor DarkGray
Write-Host ''
