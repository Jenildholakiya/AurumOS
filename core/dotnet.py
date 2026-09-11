# -*- coding: utf-8 -*-
"""
Work out what .NET is actually on this PC, and say something true about it.

AurumOS used to show one message whenever the CLR failed to start:

    AurumOS requires Microsoft .NET Desktop Runtime 8.0 or later.

That message is wrong far more often than it is right. The app asks pythonnet
for the .NET **Framework** backend first, and .NET Framework 4.x ships inside
Windows 10 and 11, so it is already there on essentially every machine a shop
owns. When the load fails anyway the cause is almost always something else:
a missing Python.Runtime.dll, a half-copied install folder, or an antivirus
quarantining a DLL out of it. Telling that customer to install .NET 8 sends
them away for twenty minutes to fix something that was never broken, and they
come back to the identical dialog.

So: gather what is genuinely installed, then decide. The gathering touches the
registry and the disk; the deciding is a pure function over plain facts, which
is the part worth testing.
"""

import os
import re
import sys

# The build number in the registry under NDP\v4\Full maps to a Framework
# version. Published by Microsoft; only the ones still found in the wild.
FRAMEWORK_RELEASES = (
    (533320, '4.8.1'),
    (528040, '4.8'),
    (461808, '4.7.2'),
    (461308, '4.7.1'),
    (460798, '4.7'),
    (394802, '4.6.2'),
    (393295, '4.6.1'),
    (379893, '4.5.2'),
)

# pywebview's Windows backend is WinForms, so a desktop runtime is what counts.
# The plain "Microsoft.NETCore.App" runtime has no WinForms in it.
DESKTOP_PACK = 'Microsoft.WindowsDesktop.App'
CORE_PACK = 'Microsoft.NETCore.App'

MIN_DESKTOP_MAJOR = 8


# ── pure helpers, which is the part worth testing ───────────────────────────

def parse_version(text):
    """'8.0.11' -> (8, 0, 11). Anything unparsable -> None."""
    m = re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?', str(text or '').strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def framework_version(release):
    """Registry release number -> a version string a person recognises."""
    try:
        release = int(release)
    except (TypeError, ValueError):
        return ''
    for threshold, label in FRAMEWORK_RELEASES:
        if release >= threshold:
            return label
    return ''


def best_version(versions):
    """Highest of a list of version strings, or None."""
    parsed = [v for v in (parse_version(x) for x in versions or []) if v]
    return max(parsed) if parsed else None


def decide(facts):
    """Given what is installed and what went wrong, say what to tell the user.

    `facts` carries:
        framework       '4.8' or '' for .NET Framework
        desktop         list of Microsoft.WindowsDesktop.App versions
        core            list of Microsoft.NETCore.App versions
        arch            'AMD64' | 'x86' | 'ARM64'
        clr_error       whatever the runtime load actually raised, if anything

    Returns {'ok', 'reason', 'title', 'message', 'detail'}. `ok` True means
    do not block the user: something usable is present, so whatever went wrong
    is not a missing runtime and should not be reported as one.
    """
    facts = facts or {}
    framework = str(facts.get('framework') or '')
    desktop = list(facts.get('desktop') or [])
    core = list(facts.get('core') or [])
    arch = str(facts.get('arch') or '').upper() or 'AMD64'
    err = str(facts.get('clr_error') or '').strip()

    best_desktop = best_version(desktop)
    has_desktop = bool(best_desktop and best_desktop[0] >= MIN_DESKTOP_MAJOR)
    has_framework = bool(framework)

    # Nothing at all. This is the only case where the old message was right.
    if not has_framework and not has_desktop:
        only_core = bool(core) and not desktop
        extra = ('\n\nYou have the plain .NET runtime installed, but not the '
                 'Desktop Runtime. They are separate downloads and AurumOS '
                 'needs the Desktop one.' if only_core else '')
        return {
            'ok': False,
            'reason': 'missing',
            'title': 'AurumOS \u2014 Missing Component',
            'message': ('AurumOS needs the Microsoft .NET Desktop Runtime '
                        '%d.0 or later (%s).\n\n'
                        'Download it from:\n'
                        'https://dotnet.microsoft.com/download/dotnet/%d.0\n\n'
                        'Pick "Desktop Runtime" and the %s installer, then '
                        'restart AurumOS.%s'
                        % (MIN_DESKTOP_MAJOR, _arch_label(arch),
                           MIN_DESKTOP_MAJOR, _arch_label(arch), extra)),
            'detail': 'framework=none desktop=none core=%s' % (core or 'none'),
        }

    # Something usable is installed, yet the runtime would not start. Sending
    # this customer to the download page is what made them come back with the
    # same dialog, so say what is actually wrong instead.
    if err:
        present = []
        if has_framework:
            present.append('.NET Framework ' + framework)
        if best_desktop:
            present.append('.NET Desktop Runtime '
                           + '.'.join(str(n) for n in best_desktop))
        return {
            'ok': False,
            'reason': 'broken',
            'title': 'AurumOS \u2014 Cannot Start',
            'message': ('%s %s installed on this PC, so this is not a missing '
                        'download.\n\nAurumOS could not start it, which almost '
                        'always means the program folder is incomplete \u2014 '
                        'often because it was copied without the _internal '
                        'folder beside AurumOS.exe, or because antivirus '
                        'removed a file from it.\n\n'
                        'Reinstall AurumOS from the original folder, keeping '
                        '_internal next to the EXE.\n\n'
                        'Details are in logs\\aurumos.log.'
                        % (' and '.join(present),
                           'are' if len(present) > 1 else 'is')),
            'detail': err[:400],
        }

    # Installed and nothing reported a failure: let the app get on with it.
    return {'ok': True, 'reason': 'ok', 'title': '', 'message': '',
            'detail': 'framework=%s desktop=%s' % (framework or 'none',
                                                   desktop or 'none')}


def _arch_label(arch):
    a = str(arch or '').upper()
    if a in ('AMD64', 'X64'):
        return 'x64'
    if a in ('ARM64',):
        return 'Arm64'
    return 'x86'


# ── probing the machine ─────────────────────────────────────────────────────

def framework_installed():
    """The .NET Framework 4.x version from the registry, or '' if absent."""
    try:
        import winreg
    except Exception:
        return ''
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full')
        try:
            release, _ = winreg.QueryValueEx(key, 'Release')
            return framework_version(release)
        finally:
            winreg.CloseKey(key)
    except Exception:
        return ''


def dotnet_root():
    """Where .NET Core lives, if it does."""
    root = os.environ.get('DOTNET_ROOT') or ''
    if root and os.path.isdir(root):
        return root
    for candidate in (os.path.join(os.environ.get('ProgramFiles', ''), 'dotnet'),
                      os.path.join(os.environ.get('ProgramW6432', ''), 'dotnet'),
                      r'C:\Program Files\dotnet'):
        if candidate and os.path.isdir(candidate):
            return candidate
    return ''


def shared_runtimes(root=None):
    """{pack name: [versions]} read off disk, which needs no dotnet.exe."""
    out = {}
    root = root if root is not None else dotnet_root()
    if not root:
        return out
    shared = os.path.join(root, 'shared')
    if not os.path.isdir(shared):
        return out
    try:
        for pack in os.listdir(shared):
            pack_dir = os.path.join(shared, pack)
            if not os.path.isdir(pack_dir):
                continue
            out[pack] = sorted(v for v in os.listdir(pack_dir)
                               if os.path.isdir(os.path.join(pack_dir, v)))
    except Exception:
        pass
    return out


def gather(clr_error=''):
    """Everything decide() needs, read from this machine."""
    packs = shared_runtimes()
    return {
        'framework': framework_installed(),
        'desktop': packs.get(DESKTOP_PACK, []),
        'core': packs.get(CORE_PACK, []),
        'arch': os.environ.get('PROCESSOR_ARCHITECTURE', 'AMD64'),
        'dotnet_root': dotnet_root(),
        'clr_error': clr_error or '',
    }


def diagnose(clr_error=''):
    """Gather, then decide. What main.py calls."""
    facts = gather(clr_error)
    verdict = decide(facts)
    verdict['facts'] = facts
    return verdict


def summary_line(facts=None):
    """One line for the log, so a support call starts with a fact."""
    f = facts or gather()
    return ('[.NET] framework=%s desktop=%s core=%s arch=%s root=%s'
            % (f.get('framework') or 'none',
               ','.join(f.get('desktop') or []) or 'none',
               ','.join(f.get('core') or []) or 'none',
               f.get('arch'), f.get('dotnet_root') or 'none'))
