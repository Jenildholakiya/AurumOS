# -*- coding: utf-8 -*-
"""
AurumOS - All-in-One Cryptographic Build Script
Run: python build.py

Handles environment verification, secure key-driven source bytecode encryption,
PyInstaller binary packaging, and validation ledger baseline hashing automatically.
"""

import os
import sys
import glob
import platform
import re
import shutil
import subprocess
import fnmatch
import time
import secrets
import base64
import zlib
import hashlib

# ── Force UTF-8 stdout/stderr ──────────────────────────────────────────────────
# The build logs use Unicode glyphs (✓ / ❌) that fail to encode on a cp1252
# Windows console, which aborts the build mid-obfuscation (after source files
# are already stubbed but before they are restored). Reconfigure the streams so
# the build is reliable regardless of the active console codepage.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.abspath('.')
DIST = os.path.join(ROOT, 'dist')
BAK  = '.orig'

# Everything build.py generates for PyInstaller (spec, hooks, version files)
# goes here rather than into the project root. build/ is git-ignored and is
# already where PyInstaller keeps its work files.
GEN = os.path.join(ROOT, 'build', 'generated')


def gen_path(name):
    """Path of a generated build file inside GEN, creating GEN if needed."""
    os.makedirs(GEN, exist_ok=True)
    return os.path.join(GEN, name)

# ── Runtime hook: initialize the .NET CLR (pythonnet) EARLY ────────────────────
# The frozen crash "Failed to resolve Python.Runtime.Loader.Initialize" happens
# because pywebview's winforms backend lazily does `import clr` deep inside
# webview.start(), where the OS DLL-search path / working dir no longer point at
# the bundled Python.Runtime.dll + its System.*.dll deps, so the CLR host can't
# resolve the assembly. This hook fixes it PERMANENTLY by, at process start:
#   1. preloading the embedded python DLL (so pythonnet's PYDLL resolves),
#   2. putting the flattened bundle root AND pythonnet/runtime on the DLL search
#      path + PATH (so Python.Runtime.dll and every System.*.dll resolve),
#   3. discovering the .NET runtime (hostfxr + hostpolicy) and adding its dirs
#      to the DLL search path - without this hostfxr can't load in frozen builds,
#   4. actually calling pythonnet.load() now - coreclr first (resolves deps.json
#      transitively, works reliably in frozen builds), netfx as fallback - so
#      pywebview later REUSES this live runtime instead of trying (and failing)
#      to initialize it itself.
_PYTHONNET_RTHOOK = '''\
import os, sys, glob as _glob

def _boot_clr():
    try:
        import ctypes
    except Exception:
        return
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    base = getattr(sys, "_MEIPASS", exe_dir)

    roots = []
    for d in (exe_dir, base, os.path.join(base, "_internal")):
        if d and d not in roots:
            roots.append(d)

    # 1) preload embedded python DLL + pin PYDLL
    for d in roots:
        for name in ("python3.dll", "python312.dll"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                try: ctypes.WinDLL(p)
                except Exception: pass
                os.environ.setdefault("PYTHONNET_PYDLL", p)

    # 2) locate pythonnet/runtime (Python.Runtime.dll lives here)
    rt = None
    for d in roots:
        cand = os.path.join(d, "pythonnet", "runtime")
        if os.path.isfile(os.path.join(cand, "Python.Runtime.dll")):
            rt = cand
            break
    if rt is None:
        try:
            import pythonnet as _pn
            cand = os.path.join(os.path.dirname(_pn.__file__), "runtime")
            if os.path.isfile(os.path.join(cand, "Python.Runtime.dll")):
                rt = cand
        except Exception:
            pass

    # 3) put bundle root + runtime dir on DLL search path
    for d in [r for r in (rt, base, exe_dir) if r and os.path.isdir(r)]:
        try: os.add_dll_directory(d)
        except Exception: pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    # 4a) .NET Framework 4.x dir (netfx backend needs System.Windows.Forms)
    net_fw_dirs = []
    for arch in ("Framework64", "Framework"):
        candidate = os.path.join(
            os.environ.get("WINDIR", r"C:\\\\Windows"),
            "Microsoft.NET", arch, "v4.0.30319",
        )
        if os.path.isdir(candidate) and candidate not in net_fw_dirs:
            net_fw_dirs.append(candidate)
    for d in net_fw_dirs:
        try: os.add_dll_directory(d)
        except Exception: pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    # 4b) .NET Core / .NET 5+ (coreclr backend needs hostfxr)
    dotnet_root = os.environ.get("DOTNET_ROOT")
    if not dotnet_root:
        for candidate in (
            os.path.join(os.environ.get("ProgramFiles", ""), "dotnet"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "dotnet"),
            r"C:\\Program Files\\dotnet",
            r"C:\\Program Files (x86)\\dotnet",
        ):
            if os.path.isdir(candidate):
                dotnet_root = candidate
                break
    if dotnet_root:
        os.environ.setdefault("DOTNET_ROOT", dotnet_root)
        fxr_root = os.path.join(dotnet_root, "host", "fxr")
        if os.path.isdir(fxr_root):
            versions = sorted(
                [v for v in os.listdir(fxr_root) if os.path.isdir(os.path.join(fxr_root, v))],
                reverse=True,
            )
            if versions:
                fxr_dir = os.path.join(fxr_root, versions[0])
                try: os.add_dll_directory(fxr_dir)
                except Exception: pass
                os.environ["PATH"] = fxr_dir + os.pathsep + os.environ.get("PATH", "")
        shared_root = os.path.join(dotnet_root, "shared")
        if os.path.isdir(shared_root):
            for fw_name in ("Microsoft.NETCore.App",):
                fw_dir = os.path.join(shared_root, fw_name)
                if os.path.isdir(fw_dir):
                    versions = sorted(
                        [v for v in os.listdir(fw_dir) if os.path.isdir(os.path.join(fw_dir, v))],
                        reverse=True,
                    )
                    if versions:
                        rt_dir = os.path.join(fw_dir, versions[0])
                        try: os.add_dll_directory(rt_dir)
                        except Exception: pass
                        os.environ["PATH"] = rt_dir + os.pathsep + os.environ.get("PATH", "")

    # 5) initialize the CLR - try netfx FIRST (WinForms needs .NET Framework),
    #    only fall back to coreclr. NEVER set PYTHONNET_RUNTIME=coreclr up front
    #    because that forces coreclr which can't load System.Windows.Forms.
    clr_ok = False
    why = []
    try:
        import pythonnet
        os.environ.pop("PYTHONNET_RUNTIME", None)
        # clr_loader 0.2.10's ffi/__init__.py uses os.path without importing
        # os, so load_netfx() silently falls back to its own
        # clr_loader/ffi/dlls ClrLoader.dll whose AppDomain cannot see
        # pythonnet/runtime/*.dll. Frozen builds then die with "Failed to
        # resolve Python.Runtime.Loader.Initialize" even though every file
        # is present. Point it at the right ClrLoader explicitly.
        try:
            import clr_loader.ffi as _clriffi
            _orig_load_netfx = _clriffi.load_netfx
            def _fixed_load_netfx():
                try:
                    import pythonnet as _pn2
                    rt2 = os.path.join(
                        os.path.dirname(_pn2.__file__),
                        "runtime", "ClrLoader.dll")
                    if os.path.isfile(rt2):
                        return _clriffi.ffi.dlopen(rt2)
                except Exception:
                    pass
                return _orig_load_netfx()
            _clriffi.load_netfx = _fixed_load_netfx
        except Exception as _patch_err:
            why.append("clr patch: " + repr(_patch_err)[:100])
        for runtime in ("netfx", "coreclr"):
            try:
                pythonnet.load(runtime)
                os.environ["PYTHONNET_RUNTIME"] = runtime
                clr_ok = True
                return
            except Exception as e:
                # Keep the reason. Without it main.py could only say the CLR
                # did not start, which is how every failure ended up being
                # reported to the customer as a missing download.
                why.append(runtime + ": " + repr(e)[:200])
                continue
    except Exception as e:
        why.append("pythonnet import: " + repr(e)[:200])

    # 6) could not start a runtime - record the flag AND the reason, so main.py
    #    can tell a missing install apart from a broken one.
    if not clr_ok:
        os.environ["AURUM_CLR_FAILED"] = "1"
        os.environ["AURUM_CLR_ERROR"] = " | ".join(why)[:500]

_boot_clr()
'''

# Files targeted for symmetric core encryption pass
TARGETS = [
    'main.py',
    'updater.py',
    os.path.join('database', 'db_manager.py'),
    os.path.join('core', 'tag_engine.py'),
]

# ── WHO PUBLISHES THIS ────────────────────────────────────────────────────────
# Windows embeds these in the EXE and shows them in file Properties. An
# executable carrying none of them, unsigned, that then opens a listening
# socket is the shape of a thing antivirus is built to stop, and heuristic
# engines score it accordingly. Filling them in costs nothing and removes a
# large part of the reason AurumOS gets flagged.
#
# SET COMPANY to the registered business name BEFORE buying a code-signing
# certificate: a certificate is issued to a legal entity, and CompanyName here
# should be the same name, or the two disagree in front of the customer.
COMPANY     = "Pentarch"
PRODUCT     = "AurumOS"
DESCRIPTION = "AurumOS by Pentarch — Jewellery Retail and Wholesale Management"
COPYRIGHT   = "Copyright (c) 2026 Pentarch. All rights reserved."
SUPPORT_URL = "aurumos.software@gmail.com"


# ── STEP 0: Verify we're in venv ─────────────────────────────────────────────
def check_env():
    print("\n[1/7] Checking environment...")
    exe = sys.executable.lower()
    if '.venv' not in exe and 'venv' not in exe:
        print(f"  WARNING: Not running inside venv ({sys.executable})")
        print(f"  Run:  .venv\\Scripts\\activate  then  python build.py")
        if input("  Continue anyway? (y/n): ").strip().lower() != 'y':
            sys.exit(1)

    missing = []
    for pkg in ['requests', 'PyInstaller', 'webview', 'PIL', 'qrcode', 'win32print']:
        try:
            __import__(pkg if pkg != 'PIL' else 'PIL.Image')
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"  Missing packages: {missing}")
        print(f"  Run: pip install requests pyinstaller pywebview pillow qrcode[pil] pywin32")
        sys.exit(1)
    print("  OK")

# ── STEP 1: Find paths ────────────────────────────────────────────────────────
def get_paths():
    candidates = [
        os.path.join(ROOT, '.venv', 'Lib', 'site-packages'),
        os.path.join(ROOT, 'venv',  'Lib', 'site-packages'),
    ]
    venv_site = next((p for p in candidates if os.path.isdir(p)), '')
    if not venv_site:
        # Falling back to getsitepackages()[0] is wrong on Windows: the first
        # entry is the environment prefix, not its site-packages folder, so
        # every binary path in the generated spec came out one level too high
        # and the build silently lost them. Pick the entry that is actually
        # site-packages, and ask sysconfig if none of them is.
        import site as _s
        import sysconfig as _sc
        venv_site = next(
            (p for p in _s.getsitepackages()
             if p.lower().replace('\\', '/').rstrip('/').endswith('site-packages')),
            '')
        if not venv_site or not os.path.isdir(venv_site):
            venv_site = _sc.get_paths().get('purelib', '')

    # Prefer venv's webview to avoid cross-drive relpath issues
    _wv_venv = os.path.join(venv_site, 'webview')
    if os.path.isdir(_wv_venv):
        webview_dir = _wv_venv
    else:
        import webview as _wv
        webview_dir = os.path.dirname(_wv.__file__)

    machine = platform.machine().lower()
    if 'arm' in machine:      arch = 'win-arm64'
    elif sys.maxsize > 2**32: arch = 'win-x64'
    else:                     arch = 'win-x86'

    print(f"  venv_site  : {venv_site}")
    print(f"  webview    : {webview_dir}")
    print(f"  arch       : {arch}")
    return venv_site, webview_dir, arch

# ── STEP 2: Write hook files ──────────────────────────────────────────────────
def write_hooks():
    print("\n[2/7] Writing hook files...")

    # Runtime hook - fixes http circular import (most critical)
    open(gen_path('rthook_fix_http.py'), 'w').write('''\
# Runtime hook: pre-load stdlib http before webview imports anything
# Fixes: circular import crash in frozen EXE
import sys, importlib
for _m in [
    'http','http.server','http.client','http.cookies','http.cookiejar',
    'wsgiref','wsgiref.simple_server','wsgiref.util',
    'wsgiref.handlers','wsgiref.headers','wsgiref.validate',
    'urllib','urllib.parse','urllib.request','urllib.error',
    'email','email.parser','email.message','email.feedparser',
    'email.policy','email.header','email.charset','email.encoders',
    'email.utils','html','html.parser','socket','ssl','socketserver',
]:
    if _m not in sys.modules:
        try: importlib.import_module(_m)
        except: pass
''')

    # Collection hook for webview
    open(gen_path('hook_webview.py'), 'w').write('''\
from PyInstaller.utils.hooks import collect_all, collect_submodules
datas, binaries, hiddenimports = collect_all("webview")
hiddenimports += collect_submodules("webview")
''')

    # Collection hook for win32
    open(gen_path('hook_win32.py'), 'w').write('''\
from PyInstaller.utils.hooks import collect_submodules
hiddenimports = collect_submodules("win32") + ["pywintypes","win32con","win32api","win32print","win32gui"]
''')

    # Collection hook for LOCAL application packages (database / core / network)
    # CRITICAL: main.py is obfuscated to marshal BEFORE PyInstaller runs, so
    # PyInstaller cannot statically see main.py's lazy imports. This hook
    # force-bundles every local submodule so nothing import-time is missing
    # (this is what caused the "stuck at banner / missing module" crash).
    open(gen_path('hook_local.py'), 'w').write('''\
from PyInstaller.utils.hooks import collect_submodules
hiddenimports = (
    collect_submodules("database") +
    collect_submodules("core") +
    collect_submodules("network")
)
''')

    print("  OK - rthook_fix_http.py, hook_webview.py, hook_win32.py, hook_local.py")

    # Runtime hook - fix stdout encoding + pre-load serial
    open(gen_path('rthook_serial.py'), 'w').write('''\
import sys, os

# Force UTF-8 stdout so print() never crashes with charmap error
os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
try:
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

# Pre-load serial for COM port detection
try:
    import serial
    import serial.tools.list_ports
    import serial.serialutil
    import serial.win32
except Exception:
    pass
''')
    print("  OK - rthook_serial.py (UTF-8 stdout + serial preload)")

    # Runtime hook - preload the embedded Python DLL before pythonnet loads.
    # Fixes: "Failed to resolve Python.Runtime.Loader.Initialize" crash on
    # webview.start(). pywebview's winforms backend (mandatory on Windows -
    # it hosts every renderer) initializes pythonnet, whose managed
    # Python.Runtime.dll must LoadLibrary the embedded Python DLL. If that DLL
    # is not on the OS loader search path the assembly fails to load and
    # pythonnet raises "Failed to resolve ...Loader.Initialize". Preloading it
    # here pins it into the process so any later LoadLibrary finds it.
    open(gen_path('rthook_pythonnet.py'), 'w', encoding='utf-8').write(_PYTHONNET_RTHOOK)


# ── STEP 3: Write AurumOS.spec ────────────────────────────────────────────────
def stamp_version():
    """Write build_version.txt: the number this EXE will report as its own.

    Releases are numbered by git (every push to main tags the next vX.Y.Z, see
    .github/workflows/release.yml), so the version is taken from the highest
    tag in the repository rather than typed into a source file. The file is
    bundled into _internal/ and read by updater.CURRENT_VERSION.

    Build from a checkout with the tags fetched. Without them this falls back
    to updater.FALLBACK_VERSION, and an EXE that under-reports its version
    re-downloads an update it already has.
    """
    print("\n[2b/7] Stamping build version...")
    version, source = None, ''
    try:
        out = subprocess.run(['git', 'tag', '-l', 'v*'], capture_output=True,
                             text=True, cwd=ROOT, timeout=30)
        found = []
        for t in out.stdout.split():
            m = re.match(r'^v(\d+)\.(\d+)\.(\d+)$', t.strip())
            if m:
                found.append(tuple(int(x) for x in m.groups()))
        if found:
            version = '.'.join(str(x) for x in max(found))
            source = 'highest git tag'
    except Exception as e:
        print(f"  (warning) could not read git tags: {e}")

    if not version:
        try:
            with open(os.path.join(ROOT, 'updater.py'), 'r', encoding='utf-8') as f:
                for line in f:
                    m = re.match(r'FALLBACK_VERSION\s*=\s*["\'](.+?)["\']', line)
                    if m:
                        version, source = m.group(1), 'updater.FALLBACK_VERSION'
                        break
        except Exception:
            pass
    if not version:
        version, source = '0.0.0', 'nothing found'
        print("  WARNING: no git tags and no fallback -- this EXE will report 0.0.0")

    with open(gen_path('build_version.txt'), 'w', encoding='utf-8') as f:
        f.write(version)
    print(f"  OK -- v{version} (from {source}) -> build_version.txt")
    # When the number was built: every build today reports the same vX.Y.Z,
    # so the number alone can never say which build is running. The stamp
    # rides in the bundle next to it and is shown in Settings → System.
    import datetime as _dt
    stamp = _dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    with open(gen_path('build_stamp.txt'), 'w', encoding='utf-8') as f:
        f.write(stamp)
    print(f"  OK -- {stamp} -> build_stamp.txt")
    return version


def write_version_resource(version):
    """Write the Windows version resource PyInstaller stamps into the EXE.

    Without this the file has no CompanyName, ProductName, FileDescription or
    FileVersion at all -- Properties shows an empty tab. That absence is not
    neutral. Reputation and heuristic engines weigh it, and an unsigned binary
    with no publisher details that then listens on a socket scores like
    something they are meant to stop. Costs nothing to fill in.

    Not a substitute for signing. It removes a reason to be suspicious; only a
    certificate removes the SmartScreen warning.
    """
    print("")
    print("[2c/7] Writing version resource...")
    parts = tuple(int(x) for x in version.split('.')[:3]) + (0,)
    out = gen_path('version_info.txt')
    body = f"""# Generated by build.py -- edit COMPANY/PRODUCT at the top of build.py, not here.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={parts},
    prodvers={parts},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName',      {COMPANY!r}),
         StringStruct('FileDescription',  {DESCRIPTION!r}),
         StringStruct('FileVersion',      {version!r}),
         StringStruct('InternalName',     'AurumOS'),
         StringStruct('LegalCopyright',   {COPYRIGHT!r}),
         StringStruct('OriginalFilename', 'AurumOS.exe'),
         StringStruct('ProductName',      {PRODUCT!r}),
         StringStruct('ProductVersion',   {version!r}),
         StringStruct('Comments',         {SUPPORT_URL!r})])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(out, 'w', encoding='utf-8') as f:
        f.write(body)
    print(f"  OK -- {COMPANY} / {PRODUCT} v{version} -> version_info.txt")
    return out


def write_spec(venv_site, webview_dir, arch):
    print("\n[3/7] Writing AurumOS.spec...")

    win32_dir       = os.path.join(venv_site, 'win32')
    win32_lib_dir   = os.path.join(venv_site, 'win32', 'lib')
    pywin32_sys_dir = os.path.join(venv_site, 'pywin32_system32')

    # Collect WebView2 runtimes
    webview_datas = []
    runtimes_root = os.path.join(webview_dir, 'lib', 'runtimes')
    if os.path.isdir(runtimes_root):
        for dirpath, _, filenames in os.walk(runtimes_root):
            for fname in filenames:
                src = os.path.join(dirpath, fname)
                rel = os.path.relpath(dirpath, venv_site)
                webview_datas.append(f'        ({repr(src)}, {repr(rel)}),')
    wv_datas_str = '\n'.join(webview_datas)

    # Copy ClrLoader.dll (the clr_loader native host) into pythonnet's
    # runtime/ folder. clr_loader's netfx loader derives the new AppDomain's
    # ApplicationBase from the directory of the loaded ClrLoader.dll; when it
    # sits in pythonnet/runtime/ (next to Python.Runtime.dll and all of its
    # System.*.dll dependencies) the .NET loader finds everything by default
    # probing - which is what makes pythonnet initialise in a frozen build
    # instead of raising "Failed to resolve Python.Runtime.Loader.Initialize".
    # NOTE: clr_loader ships its native DLL under ffi/dlls/amd64 (not win-x64).
    clrloader_runtime_str = ''
    clr_src = os.path.join(venv_site, 'clr_loader', 'ffi', 'dlls', 'amd64', 'ClrLoader.dll')
    if os.path.isfile(clr_src):
        clrloader_runtime_str = f'        ({repr(clr_src)}, "pythonnet/runtime"),'

    # serial binaries - collect all .pyd files from pyserial
    serial_dir  = os.path.join(venv_site, 'serial')
    serial_pyds = glob.glob(os.path.join(serial_dir, '*.pyd')) if os.path.isdir(serial_dir) else []
    serial_bin_str = '\n'.join(f'        ({repr(f)}, "serial"),' for f in serial_pyds if os.path.isfile(f))

    # ── Guarantee python312.dll is bundled (fixes "Failed to load Python DLL" on client PCs) ──
    python_dll = os.path.join(sys.prefix, 'python312.dll')
    if os.path.isfile(python_dll):
        python_bin_str = f'        ({repr(python_dll)}, "."),\n'
    else:
        python_bin_str = ''

    # win32 binaries
    win32_pdyds  = glob.glob(os.path.join(win32_dir, '*.pyd'))
    system_dlls  = glob.glob(os.path.join(pywin32_sys_dir, '*.dll'))
    win32_lib_py = glob.glob(os.path.join(win32_lib_dir, '*.py'))

    win32_bin_str = '\n'.join(f'        ({repr(f)}, "win32"),' for f in win32_pdyds if os.path.isfile(f))
    sysdll_str    = '\n'.join(f'        ({repr(f)}, "."),'     for f in system_dlls  if os.path.isfile(f))
    win32lib_str  = '\n'.join(f'        ({repr(f)}, {repr(os.path.join("win32","lib"))}),' for f in win32_lib_py if os.path.isfile(f))

    winspool = r'C:\Windows\System32\winspool.drv'
    winspool_str = f'        ({repr(winspool)}, "."),\n' if os.path.exists(winspool) else ''

    serial_dir_str = serial_dir.replace('\\', '/')
    spec = f'''# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build.py - do not edit manually
import os, fnmatch

block_cipher = None

STRIP_DLLS = [
    "msvcp140.dll","msvcp140_1.dll","msvcp140_2.dll",
    "vcruntime140.dll","vcruntime140_1.dll",
    "api-ms-win-*.dll","ext-ms-win-*.dll",
    "kernel32.dll","kernelbase.dll","user32.dll","gdi32.dll",
    "advapi32.dll","ole32.dll","oleaut32.dll","shell32.dll",
    "ntdll.dll","ws2_32.dll","rpcrt4.dll","secur32.dll",
    "crypt32.dll","bcrypt.dll","ucrtbase.dll","combase.dll",
]
def _strip(name):
    n = os.path.basename(name).lower()
    return any(fnmatch.fnmatch(n, p.lower()) for p in STRIP_DLLS)

a = Analysis(
    [{repr(os.path.join(ROOT, "main.py"))}],
    pathex=[{repr(ROOT)}, {repr(venv_site)}, {repr(win32_dir)}, {repr(win32_lib_dir)}],
    binaries=[
{python_bin_str}
{win32_bin_str}
{sysdll_str}
{winspool_str}{serial_bin_str}
    ],
    datas=[
        ({repr(gen_path("build_version.txt"))}, "."),
        ({repr(gen_path("build_stamp.txt"))}, "."),
        ({repr(os.path.join(ROOT, "ui"))}, "ui"),
        ({repr(os.path.join(ROOT, "fonts"))}, "fonts"),
        ({repr(os.path.join(ROOT, "assets"))}, "assets"),
        ({repr(os.path.join(ROOT, "core"))}, "core"),
        ({repr(os.path.join(ROOT, "network"))}, "network"),
        ("{serial_dir_str}", "serial"),
{win32lib_str}
{wv_datas_str}
{clrloader_runtime_str}
    ],
    hiddenimports=[
        "pythonnet","clr_loader","clr_loader.netfx","clr_loader.hostfxr","clr_loader.ffi", # ── FIXED: CLR host for pywebview winforms
        "requests", "urllib3", "charset_normalizer", "idna", # ── FIXED: FORCE INJECTED REQUESTS BINARIES
        # Dashboard temp passwords are AES-GCM. main.py imports these inside a
        # function and is obfuscated before analysis, so PyInstaller cannot
        # see them; without them the EXE falls back to a decrypt that fails.
        "cryptography","cryptography.hazmat.primitives.ciphers.aead",
        "cryptography.hazmat.primitives.kdf.pbkdf2","cryptography.hazmat.primitives.hashes",
        "cryptography.hazmat.backends","cryptography.hazmat.bindings._rust",
        "updater","subscription_manager","database","database.db_manager","core","core.tag_engine",
        "serial","serial.tools","serial.tools.list_ports","serial.serialutil",
        "serial.serialwin32","serial.win32","serial.win32con","serial.win32file",
        "serial.win32pipe","serial.urlhandler","serial.urlhandler.protocol_hwgrep",
        "serial.urlhandler.protocol_com","serial.urlhandler.protocol_rfc2217",
        "webview","webview.http","webview.util","webview.guilib",
        "webview.platforms","webview.platforms.edgechromium","webview.platforms.winforms",
        "http","http.server","http.client","http.cookies","http.cookiejar",
        "wsgiref","wsgiref.simple_server","wsgiref.util","wsgiref.handlers",
        "wsgiref.headers","wsgiref.validate",
        "urllib","urllib.parse","urllib.request","urllib.error",
        "urllib.response","urllib.robotparser",
        "email","email.parser","email.message","email.feedparser",
        "email.policy","email.header","email.charset","email.encoders","email.utils",
        "html","html.parser","socket","ssl","socketserver",
        "threading","queue","io","copy","inspect","functools","contextlib",
        "typing","enum","weakref","struct","string","codecs","platform",
        "pathlib","uuid","hashlib","base64","zlib","marshal",
        "sqlite3","json","logging",
        "encodings","encodings.utf_8","encodings.ascii","encodings.latin_1","encodings.idna",
        "win32api","win32con","win32print","win32gui","win32security","win32file",
        "win32process","win32event","win32clipboard","win32cred","win32crypt",
        "win32inet","win32job","win32pipe","win32ts","pywintypes",
        "qrcode","qrcode.image.base","qrcode.image.pil",
        "PIL","PIL._imaging","PIL.ImageDraw","PIL.ImageFont","PIL.ImageWin",
        "PIL.Image","PIL.ImageColor",
        # ── FIXED: ALL local project modules (hidden from PyInstaller
        #    because main.py is obfuscated to marshal before the build step) ──
        "sse_listener",
        "network","network.cluster_server","network.cluster_client","network.lan_scan","network.hm_share",
        "database.cluster_store","core.permissions","core.cluster_api",
        "database.bastion_ai","database.bastion_report","database.aurum_health",
        "core.bastion_sync","core.ai_support","core.io_safety","core.security_lock","core.print_bridge",
        "core.auto_backup","cryptography.hazmat.primitives.kdf.scrypt",
    ],
    hookspath=[{repr(GEN)}],
    hooksconfig={{}},
    runtime_hooks=[
    {repr(gen_path("rthook_fix_http.py"))},
    {repr(gen_path("rthook_serial.py"))},
    {repr(gen_path("rthook_pythonnet.py"))},
],
    excludes=["tkinter","matplotlib","numpy","pandas","scipy","test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

before = len(a.binaries)
a.binaries = [(n,p,k) for (n,p,k) in a.binaries if not _strip(n)]
print(f"[SPEC] Stripped {{before - len(a.binaries)}} system DLLs")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    name="AurumOS",
    icon={repr(os.path.join(ROOT, "AurumOS.ico"))} if os.path.exists({repr(os.path.join(ROOT, "AurumOS.ico"))}) else None,
    version={repr(gen_path("version_info.txt"))},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX off, deliberately. A UPX-packed binary is treated as packed by every
    # scanner, because that is what malware uses to hide, and it raises the
    # detection rate on its own. UPX is not installed on this machine, so
    # upx=True had been doing nothing -- which means the day someone built on a
    # machine that had it, the EXE would have started getting flagged for no
    # visible reason. Saying False keeps that from being luck.
    upx=False,
    upx_exclude=["win32api.pyd","win32print.pyd","win32gui.pyd",
                 "pywintypes*.dll","pythoncom*.dll","winspool.drv","WebView2Loader.dll",
                 "Python.Runtime.dll","ClrLoader.dll","Python.Runtime.deps.json",
                 "python3.dll","python312.dll"],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Keep all deps inside an _internal/ subfolder for a CLEAN client folder:
    # the top level shows only AurumOS.exe (+ the app's runtime data dirs). The
    # pythonnet "Failed to resolve ...Loader.Initialize" crash is handled by
    # rthook_pythonnet.py, which puts _internal/ and _internal/pythonnet/runtime
    # on the DLL search path, discovers the .NET runtime dirs, and initializes
    # the CLR at startup (coreclr first, netfx fallback) - so we no longer need
    # to flatten the bundle to make pythonnet resolve.
    contents_directory="_internal",
)

# ── ONEDIR COLLECT ──────────────────────────────────────────────────────
# Using ONEDIR (instead of a single bundled onefile) is the permanent fix
# for "the EXE takes too long to open". A onefile build extracts ALL of its
# binaries + the WebView2 runtimes + bundled data to a temp _MEIxxxx folder
# on EVERY launch - for an app this size that extraction alone costs several
# seconds before the window even appears. ONEDIR keeps the EXE and its
# dependencies side-by-side in dist/AurumOS/, so there is no per-launch
# extraction: the app starts as fast as the OS can load the binary.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=["win32api.pyd","win32print.pyd","win32gui.pyd",
                 "pywintypes*.dll","pythoncom*.dll","winspool.drv","WebView2Loader.dll",
                 "Python.Runtime.dll","ClrLoader.dll","Python.Runtime.deps.json",
                 "python3.dll","python312.dll"],
    name="AurumOS",
)
'''
    open(gen_path('AurumOS.spec'), 'w', encoding='utf-8').write(spec)
    print("  OK - AurumOS.spec written")

# ── STEP 4: Symmetrically Encrypted Obfuscation Pass ───────────────────────────
def _generate_key_stream(key: bytes, length: int) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        state = hashlib.sha256(key + str(counter).encode()).digest()
        stream.extend(state)
        counter += 1
    return bytes(stream[:length])

def _encrypt_payload(data: bytes, key: bytes) -> bytes:
    ks = _generate_key_stream(key, len(data))
    return bytes(b ^ k for b, k in zip(data, ks))

def obfuscate():
    print("\n[4/7] Obfuscating source files (Cryptographic Key Pass)...")
    import marshal, zlib, base64
    backed = []

    for rel in TARGETS:
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            print(f"  SKIP (not found): {rel}")
            continue
        try:
            src  = open(full, 'r', encoding='utf-8').read()
            # A stub here means a previous build died between obfuscating
            # and restoring. Shipping that stub into an EXE — or backing
            # the stub up as the "original" — would destroy the real
            # source. Refuse loudly instead; recover with:
            #   git checkout -- <file>
            if src.lstrip().startswith('import marshal') and 'exec(marshal.loads(zlib.decompress(' in src:
                raise RuntimeError(
                    f"{rel} is still an obfuscated stub from an interrupted "
                    f"build. Restore it first (git checkout -- {rel}), then "
                    f"rebuild. Refusing to bake a stub into the EXE.")
            code = compile(src, full, 'exec')
            serialized = marshal.dumps(code)
            compressed = zlib.compress(serialized, level=9)

            # Generate temporary random 32-byte key per file build
            one_time_key = secrets.token_bytes(32)
            encrypted = _encrypt_payload(compressed, one_time_key)

            b85_payload = base64.b85encode(encrypted).decode('ascii')
            b85_key = base64.b85encode(one_time_key).decode('ascii')

            # Form dynamic symmetric decryption inline runtime wrapper stub
            stub = (
                "import marshal,zlib,base64,hashlib\n"
                f"K=base64.b85decode({repr(b85_key)})\n"
                f"D=base64.b85decode({repr(b85_payload)})\n"
                "S=bytearray()\nC=0\n"
                "while len(S)<len(D):\n"
                "    S.extend(hashlib.sha256(K+str(C).encode()).digest())\n"
                "    C+=1\n"
                "P=bytes(b^k for b,k in zip(D,S[:len(D)]))\n"
                "exec(marshal.loads(zlib.decompress(P)))\n"
            )

            bak = full + BAK
            shutil.copy2(full, bak)
            backed.append(bak)
            open(full, 'w', encoding='utf-8').write(stub)
            print(f"  ✓ ENCRYPTED: {rel}")
        except Exception as e:
            print(f"  ❌ FAILED ({e}): {rel}")
    return backed

def restore(backed):
    print("\n[6/7] Restoring original source files...")
    for bak in backed:
        orig = bak[:-len(BAK)]
        if os.path.exists(bak):
            shutil.copy2(bak, orig)
            os.remove(bak)
            print(f"  Restored: {os.path.relpath(orig, ROOT)}")


def verify_sources(backed):
    """After restore, prove no target is still a stub.

    A crashed or killed build used to leave obfuscated stubs in the tree,
    silently — the next build then baked stubs into the EXE. If anything
    is still a stub here, stop before signing/finalizing so the damage is
    loud and local instead of shipped.
    """
    print("\n[6b/7] Verifying restored sources...")
    bad = []
    for bak in backed:
        orig = bak[:-len(BAK)]
        try:
            with open(orig, 'r', encoding='utf-8') as f:
                head = f.read(400)
        except OSError:
            bad.append(orig + ' (missing)')
            continue
        if head.lstrip().startswith('import marshal') \
                and 'exec(marshal.loads(zlib.decompress(' in head:
            bad.append(orig)
    if bad:
        print("  BUILD REFUSED — still stubbed:")
        for b in bad:
            print(f"    - {os.path.relpath(b, ROOT) if os.path.exists(b) else b}")
        print("  Restore them (git checkout -- <file>) and rebuild.")
        sys.exit(1)
    print("  OK -- all sources are real code")

# ── STEP 5: Run PyInstaller ───────────────────────────────────────────────────
def run_pyinstaller():
    print("\n[5/7] Running PyInstaller...")

    # A stale running instance can hold dist\\AurumOS.exe open, which makes
    # PyInstaller's final os.remove() fail with "PermissionError: Access is
    # denied". kill_running_exe() already tried to terminate it; as a belt-and-
    # braces fallback, RENAME the locked file out of the way. Rename succeeds
    # even while a process holds the file, so PyInstaller can write the new EXE.
    import glob as _glob
    exe_dir = os.path.join(ROOT, 'dist')
    for old in _glob.glob(os.path.join(exe_dir, 'AurumOS.exe')):
        for attempt in range(3):
            try:
                os.remove(old)
                print(f"  Removed stale EXE: {old}")
                break
            except PermissionError:
                try:
                    bak = old + '.old-' + str(int(time.time())) + str(attempt)
                    os.rename(old, bak)
                    print(f"  Stale EXE in use - renamed aside: {os.path.basename(bak)}")
                    break
                except Exception as _re:
                    if attempt == 2:
                        print(f"  (warning) could not free {old}: {_re}")
                    time.sleep(1)

    # Clear any flattened runtime folder from a previous build so PyInstaller
    # rebuilds a clean nested bundle before finalize_dist() flattens it again.
    stale_int = os.path.join(DIST, '_internal')
    if os.path.isdir(stale_int):
        try:
            shutil.rmtree(stale_int)
            print(f"  Removed stale runtime: dist\\_internal")
        except Exception as e:
            print(f"  (warning) could not free dist\\_internal: {e}")

    r = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', gen_path('AurumOS.spec'), '--clean', '--noconfirm',
         '--distpath', DIST, '--workpath', os.path.join(ROOT, 'build')],
        cwd=ROOT
    )
    if r.returncode != 0:
        print("\n  ERROR: PyInstaller failed")
        return False
    return True

# ── STEP 5a: Code-sign the EXE (Authenticode) ───────────────────────────────
# SmartScreen flags an UNSIGNED exe as "unknown publisher / suspicious".
# Signing with a certificate from a trusted CA (Sectigo/GlobalSign/DigiCert/...)
# proves the publisher identity and removes that warning. This step is a NO-OP
# until you provide a cert - see config/sign_config.json.example.
def _locate_signtool():
    """Find signtool.exe (part of the Windows SDK)."""
    from glob import glob as _glob
    candidate_dirs = [
        r"C:\Program Files (x86)\Windows Kits\10\bin\x64",
        r"C:\Program Files\Windows Kits\10\bin\x64",
        r"C:\Program Files (x86)\Windows Kits\10\bin\arm64",
    ]
    for d in candidate_dirs:
        hit = os.path.join(d, "signtool.exe")
        if os.path.isfile(hit):
            return hit
    for pat in _glob(r"C:\Program Files*\Windows Kits\10\bin\**\signtool.exe", recursive=True):
        return pat
    # fall back to PATH
    import shutil as _sh
    return _sh.which("signtool.exe") or _sh.which("signtool")

def _load_sign_config():
    """Read signing config from sign_config.json OR environment variables.

    Env vars win, so a password never has to sit in a file:
      AURUMOS_PFX, AURUMOS_PFX_PASSWORD, AURUMOS_TIMESTAMP_URL,
      AURUMOS_CERT_THUMBPRINT
    """
    cfg = {}
    path = os.path.join(ROOT, "sign_config.json")
    if os.path.isfile(path):
        try:
            import json as _json
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(_json.load(f))
        except Exception as e:
            print(f"  WARNING: could not read sign_config.json ({e})")
    cfg.setdefault("pfx_path", os.environ.get("AURUMOS_PFX", ""))
    cfg.setdefault("password", os.environ.get("AURUMOS_PFX_PASSWORD", ""))
    # An OV/EV certificate issued after June 2023 lives on a hardware token or
    # in an HSM and is used from the Windows certificate store by thumbprint --
    # there is no .pfx to point at. Support both.
    cfg.setdefault("thumbprint", os.environ.get("AURUMOS_CERT_THUMBPRINT", ""))
    cfg.setdefault("timestamp_url", os.environ.get(
        "AURUMOS_TIMESTAMP_URL", "http://timestamp.digicert.com"))
    return cfg


def _sign_with_powershell(exe_path, cfg):
    """Sign using Windows' own Set-AuthenticodeSignature.

    signtool comes with the Windows SDK, which is a large download and was not
    installed here, so the build kept producing unsigned EXEs for a reason
    that had nothing to do with the certificate. PowerShell can do the same
    job with what is already on every Windows machine.

    The PFX password is handed over through the environment, not on the
    command line, because a command line is readable by any other process on
    the machine while it runs.
    """
    pfx = cfg.get("pfx_path", "")
    thumb = str(cfg.get("thumbprint", "") or "").replace(" ", "")
    ts = cfg.get("timestamp_url", "http://timestamp.digicert.com")
    store = "LocalMachine" if cfg.get("machine_store") else "CurrentUser"

    if thumb:
        get_cert = (
            "$c = Get-ChildItem -Path Cert:\\%s\\My -CodeSigningCert | "
            "Where-Object { $_.Thumbprint -eq $env:AURUM_THUMB }; "
            "if (-not $c) { Write-Output 'AURUMFAIL:No code-signing certificate "
            "with that thumbprint in the %s store.'; exit 2 }" % (store, store)
        )
    else:
        get_cert = (
            "$pw = $env:AURUM_PFX_PW; "
            "if ($pw) { $sec = ConvertTo-SecureString $pw -AsPlainText -Force; "
            "$c = Get-PfxCertificate -FilePath $env:AURUM_PFX -Password $sec } "
            "else { $c = Get-PfxCertificate -FilePath $env:AURUM_PFX }; "
            "if (-not $c) { Write-Output 'AURUMFAIL:Could not read that .pfx.'; exit 2 }"
        )

    script = (
        get_cert + "; "
        "$r = Set-AuthenticodeSignature -FilePath $env:AURUM_EXE -Certificate $c "
        "-HashAlgorithm SHA256 -TimestampServer $env:AURUM_TS -Force; "
        "Write-Output $r.Status; "
        "if ($r.Status -ne 'Valid') { Write-Output ('AURUMFAIL:' + $r.StatusMessage); exit 3 }"
    )

    env = dict(os.environ)
    env["AURUM_EXE"] = exe_path
    env["AURUM_TS"] = ts
    if thumb:
        env["AURUM_THUMB"] = thumb
    else:
        env["AURUM_PFX"] = pfx
        if cfg.get("password"):
            env["AURUM_PFX_PW"] = str(cfg["password"])

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    except Exception as e:
        print(f"  X PowerShell signing could not run: {e}")
        return False
    out = (r.stdout or "").strip()
    if r.returncode != 0 or "Valid" not in out:
        print("  X Signing FAILED.")
        # Only the line the script marked as the reason. PowerShell otherwise
        # echoes the whole command back and buries it.
        said = [ln.split("AURUMFAIL:", 1)[1].strip()
                for ln in out.split(chr(10)) if "AURUMFAIL:" in ln]
        if not said:
            said = [ln.strip() for ln in (r.stderr or "").split(chr(10))
                    if ln.strip() and not ln.strip().startswith("+")][:2]
        for line in said[:3] or ["PowerShell reported no reason."]:
            print(f"    {line}")
        return False
    print("  OK SIGNED via Windows (no SDK needed).")
    return True


def _verify_signature(exe_path):
    """Confirm the signature actually validates, using Windows itself.

    signtool would be used if it were here. It is not, and a signature that
    does not verify fails only on the customer's PC, which is the worst place
    to find out.
    """
    script = ("$s = Get-AuthenticodeSignature -FilePath $env:AURUM_EXE; "
              "Write-Output $s.Status; "
              "if ($s.TimeStamperCertificate) { Write-Output 'timestamped' } "
              "else { Write-Output 'no-timestamp' }")
    env = dict(os.environ)
    env["AURUM_EXE"] = exe_path
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    except Exception:
        return True  # cannot check; do not claim a failure
    lines = [x.strip() for x in (r.stdout or "").strip().split(chr(10)) if x.strip()]
    status = lines[0] if lines else "Unknown"
    if status != "Valid":
        print(f"  X SIGNED BUT DOES NOT VERIFY ({status}).")
        print("    A customer PC will reject this. Check the certificate chain.")
        return False
    if len(lines) > 1 and lines[1] == "no-timestamp":
        print("  (warning) not timestamped - the signature dies with the cert.")
    return True


def sign_exe():
    """Authenticode-sign the EXE, and say plainly what is lost when it is not.

    Signing is the only thing that removes the SmartScreen "unknown publisher"
    warning outright, and it is what lets a file build reputation instead of
    being judged fresh on every machine. Everything else in this build --
    version resource, no packer -- reduces suspicion; only this ends it.
    """
    print("")
    print("[5a/7] Code-signing EXE (Authenticode)...")
    # Nested path first: signing runs before finalize_dist() lifts the EXE
    # to dist/. The top-level path is the fallback when this step is
    # re-run standalone after a build.
    exe_path = os.path.join(DIST, "AurumOS", "AurumOS.exe")
    if not os.path.isfile(exe_path):
        exe_path = os.path.join(DIST, "AurumOS.exe")
    if not os.path.isfile(exe_path):
        print("  SKIP -- EXE not found at dist\\AurumOS\\AurumOS.exe nor dist\\AurumOS.exe")
        return False

    cfg = _load_sign_config()
    pfx = cfg.get("pfx_path", "")
    thumb = str(cfg.get("thumbprint", "") or "").replace(" ", "")

    if not pfx and not thumb:
        print("  SKIP -- no certificate configured. The EXE will be UNSIGNED.")
        print("    Every customer who DOWNLOADS this gets 'Windows protected")
        print("    your PC' and has to click More info, then Run anyway.")
        print("    A code-signing certificate is the only thing that removes it.")
        print("")
        print("    Cheapest route: Azure Trusted Signing, a Microsoft service")
        print("    billed monthly rather than a few hundred up front. It needs")
        print("    an organisation with verifiable trading history.")
        print("    Otherwise Sectigo, DigiCert or GlobalSign sell the usual")
        print("    one- to three-year certificates. Extended Validation clears")
        print("    SmartScreen immediately; a standard one has to accumulate")
        print("    reputation over some weeks of installs first.")
        print("")
        print("    Then point sign_config.json at it and this step signs by")
        print("    itself. The Windows SDK is NOT required -- signing falls")
        print("    back to Windows' own Set-AuthenticodeSignature.")
        return False
    if pfx and not os.path.isfile(pfx):
        print(f"  ERROR -- PFX not found: {pfx}")
        return False

    signtool = _locate_signtool()
    if not signtool:
        # No SDK. Windows can still sign, so use it rather than shipping an
        # unsigned EXE over a missing download.
        print("  signtool not found - signing with Windows instead.")
        if not _sign_with_powershell(exe_path, cfg):
            print("    If you would rather use signtool, install the Windows SDK:")
            print("    https://developer.microsoft.com/windows/downloads/windows-sdk/")
            return False
        return _verify_signature(exe_path)

    # SHA256 digest plus an RFC3161 timestamp, so the signature stays valid
    # after the certificate expires. /d and /du are what a user is shown in the
    # UAC and SmartScreen dialogs, so they should read like the product.
    cmd = [
        signtool, "sign",
        "/fd", "SHA256",
        "/tr", cfg.get("timestamp_url", "http://timestamp.digicert.com"),
        "/td", "SHA256",
        "/d", PRODUCT,
    ]
    if thumb:
        cmd += ["/sha1", thumb]
        if not pfx:
            cmd += ["/sm"] if cfg.get("machine_store") else []
    else:
        cmd += ["/f", pfx]
        pw = cfg.get("password", "")
        if pw:
            cmd += ["/p", pw]
    cmd.append(exe_path)

    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("  X Signing FAILED -- see output above. EXE left UNSIGNED.")
        return False

    # Signtool reports success even where the chain does not build on a clean
    # machine, which is the only place it matters. Verify rather than assume.
    v = subprocess.run([signtool, "verify", "/pa", "/v", exe_path],
                       cwd=ROOT, capture_output=True, text=True)
    if v.returncode != 0:
        print("  X SIGNED BUT DOES NOT VERIFY -- a customer PC will reject it.")
        for line in (v.stdout or v.stderr or "").strip().split('\n')[-6:]:
            print(f"    {line}")
        return False
    print("  OK SIGNED and verified -- SmartScreen 'unknown publisher' removed.")
    return True


# ── STEP 5b: Write trusted EXE hash ───────────────────────────────────────────
def write_trusted_hash():
    print("\n[5b/7] Writing trusted EXE hash...")
    exe_path = os.path.join(DIST, 'AurumOS', 'AurumOS.exe')
    if not os.path.exists(exe_path):
        exe_path = os.path.join(DIST, 'AurumOS.exe')
    if not os.path.exists(exe_path):
        print(f"  SKIP -- EXE not found at {exe_path}")
        return
    h = hashlib.sha256()
    with open(exe_path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    digest = h.hexdigest()
    trust_path = os.path.join(DIST, 'exe_trusted_hash.txt')
    with open(trust_path, 'w') as f:
        f.write(digest)
    print(f"  OK -- {digest[:16]}... written to dist\\exe_trusted_hash.txt")

# ── STEP 6: Report ────────────────────────────────────────────────────────────
def _report_smartscreen(exe):
    """Say plainly what a customer's PC will do with this file.

    A build that finishes with a tick and no mention of signing gets shipped,
    and the first anyone hears about SmartScreen is a phone call from a shop
    that will not click past a red box. Say it here, while there is still
    something to do about it.
    """
    print("")
    print("  What a customer will see")
    try:
        with open(exe, 'rb') as f:
            head = f.read(4096)
        packed = b'UPX0' in head or b'UPX1' in head or b'UPX!' in head
        data = open(exe, 'rb').read()
        # 'CompanyName' as UTF-16, which is how the version resource
        # stores it. Built with chr() so no literal nulls sit in this file.
        needle = ''.join(c + chr(0) for c in 'CompanyName').encode('latin1')
        named = needle in data
        import struct
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        opt = struct.unpack_from('<H', data, pe + 20)[0]
        sec = struct.unpack_from('<II', data, pe + 24 + (144 if opt >= 240 else 128))
        signed = bool(sec[1])
    except Exception as e:
        print(f"  (could not inspect the EXE: {e})")
        return

    print(f"  Signed          : {'yes' if signed else 'NO'}")
    print(f"  Publisher shown : {'yes' if named else 'NO'}")
    print(f"  Packed          : {'YES' if packed else 'no'}")
    if not signed:
        print("")
        print("  This build is UNSIGNED. Anyone who DOWNLOADS it gets")
        print("  \"Windows protected your PC\" and has to click More info,")
        print("  then Run anyway. Copied from a USB stick or a network")
        print("  share it runs without the prompt, because Windows only")
        print("  tags files that came from the internet.")
        print("")
        print("  Only a code-signing certificate removes it. Set one up in")
        print("  sign_config.json and this step signs automatically.")
        print(r"  See docs\ANTIVIRUS.md.")
    print("")
    print("  Check any built folder before shipping it:")
    print(r"    powershell -ExecutionPolicy Bypass -File tools\check_release.ps1")


def report(built_version='?'):
    print("\n[7/7] Build report...")
    exe = os.path.join(DIST, 'AurumOS.exe')
    if os.path.exists(exe):
        # Match version.lock to this build. A lock left behind by an older
        # install claims a version this EXE does not have, and the app trusts
        # the higher of the two, so a stale lock hides real updates.
        try:
            with open(os.path.join(DIST, 'version.lock'), 'w', encoding='utf-8') as f:
                f.write(built_version)
        except Exception as e:
            print(f"  (warning) could not write dist\\version.lock: {e}")
        size_mb = os.path.getsize(exe) / 1024 / 1024
        print(f"\n  ✓ SUCCESS")
        print(f"  EXE   : {exe}")
        print(f"  Version: v{built_version}  (shown in the app; updates count up from here)")
        print(f"  Runtime: {os.path.join(ROOT, 'dist', '_internal')}")
        print(f"  Size: {size_mb:.1f} MB")
        print(f"\n  Ship the WHOLE dist\\ folder to each client PC. Clean layout:")
        print(f"  dist\\AurumOS.exe            (double-click to run)")
        print(f"  dist\\_internal\\             (all deps - keep NEXT TO the EXE)")
        print(f"  dist\\database\\  dist\\logs\\  (runtime data)")
        print(f"  dist\\config.json  dist\\exe_trusted_hash.txt")
        print(f"  dist\\AurumOS.exe.config       (lets .NET load DLLs after a copy)")
        print(f"  (the hash file tells Bastion AI this exact build is legitimate --")
        print(f"   without it next to the EXE, tamper-detection can't run)")
        print(f"  If a client PC still reports 0x80131515, run once on that PC:")
        print(f"    Get-ChildItem dist -Recurse | Unblock-File")
        _report_smartscreen(exe)
    else:
        print(f"\n  �- EXE not found at {exe}")
        print(f"  Check build output above for errors")

# ── Kill any running instance so PyInstaller can replace dist/AurumOS.exe ──
def kill_running_exe():
    """Terminate any running AurumOS.exe so the build can overwrite the EXE.

    A lingering process holds the frozen EXE open, which makes PyInstaller's
    final os.remove() fail with 'PermissionError: Access is denied'. Killing it
    here makes the build self-sufficient (no manual process cleanup needed).
    Uses ctypes directly so it works without an external taskkill binary.
    """
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        PROCESS_QUERY_INFORMATION = 0x0400
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(pe)
        killed = []
        if kernel32.Process32First(snapshot, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode('ascii', 'ignore')
                if name.lower() == 'aurumos.exe':
                    pid = pe.th32ProcessID
                    h = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION, False, pid)
                    if h:
                        if kernel32.TerminateProcess(h, 0):
                            killed.append(pid)
                        kernel32.CloseHandle(h)
                if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(snapshot)
        if killed:
            print(f"  Killed running AurumOS.exe instance(s): {killed}")
            import time as _t
            _t.sleep(2)  # let the OS release the file handle
    except Exception as e:
        print(f"  (note: could not auto-kill running instance: {e})")


# ── STEP 8: Remove stale top-level EXE + fix launch script ───────────────────
def _merge_move(src, dst):
    """Move ``src`` to ``dst``, merging directories instead of overwriting.

    Used when flattening the bundle: bundled code/asset folders (ui/, fonts/,
    assets/, core/, network/) are merged into the runtime copies already present in
    dist/, while runtime state folders (Reports/, database/) are never touched
    because they are not part of the bundle.
    """
    if os.path.isdir(src) and os.path.isdir(dst):
        for name in os.listdir(src):
            _merge_move(os.path.join(src, name), os.path.join(dst, name))
        try:
            os.rmdir(src)
        except OSError:
            pass
    else:
        if os.path.lexists(dst):
            if os.path.isdir(dst) and not os.path.islink(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)


def finalize_dist():
    """Produce a CLEAN client folder with a single hidden deps folder:

        dist\\AurumOS.exe        (double-click to run)
        dist\\_internal\\         (ALL bundled deps live in here)
        dist\\database\\  dist\\logs\\  dist\\config.json  ...  (runtime data)

    PyInstaller writes the bundle as dist\\AurumOS\\AurumOS.exe +
    dist\\AurumOS\\_internal\\ (contents_directory="_internal"). We lift the EXE
    and its _internal\\ up into dist\\ so the top level shows only the EXE next
    to the app's runtime-data folders, and delete dev-only launch scripts. The
    "Failed to resolve ...Loader.Initialize" crash is handled at runtime by
    rthook_pythonnet.py (it puts _internal\\ + _internal\\pythonnet\\runtime on
    the DLL search path and initializes the CLR at startup).
    """
    dist    = DIST
    nested  = os.path.join(dist, 'AurumOS')
    top_exe = os.path.join(dist, 'AurumOS.exe')
    top_int = os.path.join(dist, '_internal')

    # Never leave stale _internal_old / _internal_tmp behind: they were only
    # ever a locked-folder fallback, not something to ship or keep.
    for _stale in (top_int + '_old', top_int + '_tmp'):
        if os.path.isdir(_stale):
            shutil.rmtree(_stale, ignore_errors=True)
            if not os.path.isdir(_stale):
                print(f"  Removed stale: dist\\{os.path.basename(_stale)}\\")

    # Clear any _internal / stale EXE from a previous build so the move is clean.
    if os.path.isdir(top_int):
        shutil.rmtree(top_int, ignore_errors=True)
    if os.path.isdir(top_int):
        # rmtree failed (locked) — try removing files one by one
        for _dp, _dns, _fns in os.walk(top_int, topdown=False):
            for _f in _fns:
                try: os.remove(os.path.join(_dp, _f))
                except OSError: pass
            for _d in _dns:
                try: os.rmdir(os.path.join(_dp, _d))
                except OSError: pass
        try: os.rmdir(top_int)
        except OSError:
            # Still locked — rename so move can succeed
            _bak = top_int + '_old'
            if os.path.isdir(_bak):
                shutil.rmtree(_bak, ignore_errors=True)
            try: os.rename(top_int, _bak)
            except OSError:
                print(f"  WARNING: dist\\_internal\\ is locked — close AurumOS and rebuild")
    if os.path.isfile(top_exe):
        try:
            os.remove(top_exe)
        except OSError:
            pass

    if not os.path.isdir(nested):
        print("  (note) nested build folder not found - nothing to finalize")
        return

    # Lift _internal/ up to dist\_internal\ ...
    src_int = os.path.join(nested, '_internal')
    if os.path.isdir(src_int):
        if os.path.isdir(top_int):
            # Destination still exists — force-remove it
            shutil.rmtree(top_int, ignore_errors=True)
        if os.path.isdir(top_int):
            # Can't remove (locked files) — shove it aside as _internal_old
            _old = top_int + '_old'
            if os.path.isdir(_old):
                shutil.rmtree(_old, ignore_errors=True)
            try:
                os.rename(top_int, _old)
            except OSError:
                shutil.move(top_int, _old)
            # Now clean any leftover _tmp from a previous failed attempt
            _tmp = top_int + '_tmp'
            if os.path.isdir(_tmp):
                shutil.rmtree(_tmp, ignore_errors=True)
            shutil.move(src_int, top_int)
        else:
            shutil.move(src_int, top_int)
        print("  Moved deps    -> dist\\_internal\\")
        # The rename-aside fallback above must not leave dist\_internal_old
        # behind: retry the delete now that the new tree is in place.
        _leftover = top_int + '_old'
        if os.path.isdir(_leftover):
            shutil.rmtree(_leftover, ignore_errors=True)
            if os.path.isdir(_leftover):
                print(f"  NOTE: dist\\_internal_old\\ is still locked by a running app"
                      f" and will be removed by the next build — close AurumOS first")
            else:
                print("  Removed fallback: dist\\_internal_old\\")

    # ... then the EXE (last, so its _internal is already beside it).
    exe_src = os.path.join(nested, 'AurumOS.exe')
    if os.path.isfile(exe_src):
        shutil.move(exe_src, top_exe)
        print("  Moved EXE     -> dist\\AurumOS.exe")
    if not os.path.isfile(top_exe):
        raise RuntimeError("Build completed without dist\\AurumOS.exe; refusing to leave the EXE elsewhere")

    # Remove the now-empty nested folder.
    shutil.rmtree(nested, ignore_errors=True)

    # Ship the firewall helper beside the EXE. The main PC needs an inbound
    # rule before staff PCs can reach it, and the alternative is whoever is at
    # the counter guessing at a Windows prompt -- where Cancel writes a BLOCK
    # rule that no later allow rule overrides.
    tools_src = os.path.join(ROOT, 'tools')
    if os.path.isdir(tools_src):
        tools_dst = os.path.join(dist, 'tools')
        try:
            if os.path.isdir(tools_dst):
                shutil.rmtree(tools_dst, ignore_errors=True)
            shutil.copytree(tools_src, tools_dst)
            print("  Tools         -> dist\\tools\\ (firewall helper)")
        except Exception as e:
            print(f"  (warning) could not copy tools: {e}")

    # Prune dev-only launch scripts so the client folder stays clean.
    for junk in ('run_with_log.bat', 'show_log.bat'):
        p = os.path.join(dist, junk)
        if os.path.isfile(p):
            try:
                os.remove(p)
                print(f"  Removed dev script: dist\\{junk}")
            except OSError:
                pass
    print("  Clean layout  -> dist\\AurumOS.exe + dist\\_internal\\ (+ database, logs)")

    # ── AurumOS.exe.config: survive being copied between PCs ──────────────
    # Copying dist/ from another PC, drive, or download stamps every DLL with
    # a Mark-of-the-Web (Zone.Identifier). The .NET Framework CLR then refuses
    # to load managed assemblies such as Microsoft.Web.WebView2.Core.dll and
    # the app dies with "Failed to execute script 'main'" + HRESULT
    # 0x80131515. <loadFromRemoteSources> declares the load intended, so a
    # copied install keeps working. Written on every build, beside the EXE.
    try:
        _cfg = os.path.join(dist, 'AurumOS.exe.config')
        open(_cfg, 'w', encoding='utf-8').write(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<configuration>\n'
            '  <runtime>\n'
            '    <loadFromRemoteSources enabled="true"/>\n'
            '  </runtime>\n'
            '  <startup>\n'
            '    <supportedRuntime version="v4.0" '
            'sku=".NETFramework,Version=v4.8"/>\n'
            '  </startup>\n'
            '</configuration>\n')
        print("  EXE config    -> dist\\AurumOS.exe.config (loadFromRemoteSources)")
    except Exception as _cfge:
        print(f"  (warning) could not write EXE config: {_cfge}")

    # ── WebView2 runtimes: guarantee ALL arch folders exist ──────────────
    # pywebview's edgechromium loader unconditionally loops over
    #   ('win-arm64', 'win-x64', 'win-x86')
    # and raises FileNotFoundError on the FIRST folder that is missing.
    # PyInstaller deduplicates WebView2Loader.dll by basename during binary
    # reclassification, so typically only win-x64 survives the bundle -> the
    # app crashes at startup with "Cannot find win-arm64". Copy every arch
    # folder straight from the venv so all three always ship.
    try:
        import webview as _wv
        rt_src = os.path.join(os.path.dirname(_wv.__file__), 'lib', 'runtimes')
        rt_dst = os.path.join(top_int, 'webview', 'lib', 'runtimes')
        copied = []
        if os.path.isdir(rt_src):
            for arch_name in os.listdir(rt_src):
                s = os.path.join(rt_src, arch_name, 'native', 'WebView2Loader.dll')
                d = os.path.join(rt_dst, arch_name, 'native')
                if os.path.isfile(s):
                    os.makedirs(d, exist_ok=True)
                    dst_dll = os.path.join(d, 'WebView2Loader.dll')
                    if not os.path.isfile(dst_dll):
                        shutil.copy2(s, dst_dll)
                        copied.append(arch_name)
        if copied:
            print(f"  WebView2 rt   -> added {', '.join(copied)}")
        else:
            print("  WebView2 rt   -> all arch folders already present")
    except Exception as _wve:
        print(f"  (warning) WebView2 runtime copy skipped: {_wve}")

    # ── pythonnet runtime DLLs → _internal\ root ────────────────────────
    # The .NET Framework CLR (hosted via ClrLoader.dll) resolves assembly
    # dependencies using the AppDomain probing path, which includes the
    # _internal/ root but NOT _internal/pythonnet/runtime/.  Copy the
    # System.*.dll + netstandard.dll facades to _internal/ so the CLR can
    # find them when loading Python.Runtime.dll.
    try:
        pn_rt = os.path.join(top_int, 'pythonnet', 'runtime')
        if os.path.isdir(pn_rt):
            copied_pn = 0
            for fname in os.listdir(pn_rt):
                if fname.lower().endswith('.dll') and fname.lower() != 'python.runtime.dll':
                    src_dll = os.path.join(pn_rt, fname)
                    dst_dll = os.path.join(top_int, fname)
                    if not os.path.isfile(dst_dll):
                        shutil.copy2(src_dll, dst_dll)
                        copied_pn += 1
            print(f"  pythonnet rt  -> copied {copied_pn} .NET facade DLLs to _internal\\")
    except Exception as _pne:
        print(f"  (warning) pythonnet runtime copy skipped: {_pne}")


# ── STEP 8: Client-clean folder ──────────────────────────────────────────
# dist/ is the developer's own install: live database, license marker,
# backups, logs, photos, machine paths. Shipping it as-is would hand a
# client your business data. stage_client() builds dist-client/ instead:
# the program plus empty shelves, nothing private. Safe to zip and send.
CLIENT_EXCLUDE_DIRS = {
    'database', 'exports', 'logs', 'photos', 'Reports',
    '_restore_test_safety',
    # Stray legacy copy: the exe only ever reads _internal/ui.
    'ui',
}
CLIENT_EXCLUDE_FILES = {
    'config.json', 'db_path.txt', '.update_applied', 'version.lock',
    'AurumOS_debug.exe',
}
CLIENT_EMPTY_DIRS = (
    'database', 'exports', 'logs', 'photos', 'Reports',
)


def stage_client(src_dist=None, dst=None):
    """Copy a shippable, private-data-free tree. Returns dst.

    src_dist defaults to dist/, dst to dist-client/. Pure paths-in,
    paths-out so the test suite can stage a fake tree in temp.
    """
    import shutil as _sh
    src = src_dist or DIST
    out = dst or os.path.join(ROOT, 'dist-client')
    print("\n[8/7] Staging client-clean folder...")
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    for name in sorted(os.listdir(src)):
        if name in CLIENT_EXCLUDE_DIRS or name in CLIENT_EXCLUDE_FILES:
            continue
        s = os.path.join(src, name)
        d = os.path.join(out, name)
        try:
            if os.path.isdir(s):
                _sh.copytree(s, d, ignore=shutil.ignore_patterns(
                    '.git', '__pycache__'))
            else:
                _sh.copy2(s, d)
        except Exception as e:
            print(f"  (warning) could not stage {name}: {e}")
    for name in CLIENT_EMPTY_DIRS:
        try:
            os.makedirs(os.path.join(out, name), exist_ok=True)
        except Exception:
            pass
    # Clean default config from the example: no cluster role, no machine
    # paths, no secrets. The app fills in its own on first setup.
    try:
        ex = os.path.join(ROOT, 'config', 'config.example.json')
        if os.path.isfile(ex):
            _sh.copy2(ex, os.path.join(out, 'config.json'))
            print("  config.json   -> clean default from example")
    except Exception as e:
        print(f"  (warning) clean config not staged: {e}")
    print(f"  Client folder -> {out}")
    return out


def zip_client(src_dir=None, out_dir=None, version='', stamp=''):
    """Zip a staged client folder into CLIENTS BUILD, ready to send.

    Only ever zips the staged dist-client/ tree — never the repo root —
    so Admin Credential/, CLIENTS BUILD/ itself and every private file
    are structurally unable to enter the zip. Returns the zip path.
    """
    import shutil as _sh
    import datetime as _dt
    src = src_dir or os.path.join(ROOT, 'dist-client')
    dest_dir = out_dir or os.path.join(ROOT, 'CLIENTS BUILD')
    os.makedirs(dest_dir, exist_ok=True)
    safe_stamp = (_dt.datetime.now().strftime('%Y%m%d-%H%M%S')
                  if not stamp else str(stamp).replace(' ', '-').replace(':', ''))
    name = 'AurumOS-client-%s-%s' % (
        ('v' + str(version)) if version else 'vX', safe_stamp)
    # Never zip a zip loop: skip any CLIENTS BUILD content or stray zips.
    dest_base = os.path.join(dest_dir, name)
    _sh.make_archive(dest_base, 'zip', root_dir=src)
    out_zip = dest_base + '.zip'
    print(f"  Client zip    -> {out_zip}")
    return out_zip


# Full main-folder zip: the whole project for handoff, minus everything
# private or regenerable. Same guarantee as stage_client, wider net.
FULL_EXCLUDE_DIRS = {
    '.git', '.venv', '__pycache__', '.idea', '.claude',
    'build', 'dist', 'dist-client', 'CLIENTS BUILD', 'Admin Credential',
    '_internal', '_restore_test_safety', 'pythonnet_runtime_backup_20260919',
}
FULL_EXCLUDE_FILES = {
    'config.json', 'db_path.txt', 'sign_config.json',
    'build_version.txt', 'build_stamp.txt', 'version_info.txt',
    'AurumOS.spec', '.update_applied', 'version.lock',
    'exe_trusted_hash.txt',
}
FULL_EXCLUDE_EXTS = {
    '.db', '.db-shm', '.db-wal', '.db-journal', '.log', '.pyc', '.pyo',
}
FULL_KEEP_EMPTY_DIRS = (
    'database', 'exports', 'logs', 'photos', 'Reports',
)


def zip_full(version='', stamp='', out_dir=None):
    """Zip the whole main folder (source + assets + docs) into
    CLIENTS BUILD, minus private data, secrets, environments, caches,
    databases, logs and prior builds. Returns the zip path."""
    import datetime as _dt
    import zipfile as _zf
    dest_dir = out_dir or os.path.join(ROOT, 'CLIENTS BUILD')
    os.makedirs(dest_dir, exist_ok=True)
    safe_stamp = (_dt.datetime.now().strftime('%Y%m%d-%H%M%S')
                  if not stamp else str(stamp).replace(' ', '-').replace(':', ''))
    name = 'AurumOS-full-%s-%s.zip' % (
        ('v' + str(version)) if version else 'vX', safe_stamp)
    out_zip = os.path.join(dest_dir, name)

    def _excluded_dir(rel):
        parts = rel.split(os.sep)
        return any(p in FULL_EXCLUDE_DIRS for p in parts)

    def _excluded_file(fname):
        if fname in FULL_EXCLUDE_FILES:
            return True
        _root, ext = os.path.splitext(fname)
        return ext.lower() in FULL_EXCLUDE_EXTS

    count = 0
    with _zf.ZipFile(out_zip, 'w', _zf.ZIP_DEFLATED) as zf:
        for dp, dns, fns in os.walk(ROOT):
            rel = os.path.relpath(dp, ROOT)
            if rel == '.':
                rel = ''
            # Prune excluded dirs so we never descend into them.
            dns[:] = sorted(
                d for d in dns
                if not _excluded_dir(os.path.join(rel, d) if rel else d))
            if rel and _excluded_dir(rel):
                continue
            for f in sorted(fns):
                if _excluded_file(f):
                    continue
                full = os.path.join(dp, f)
                arc = os.path.relpath(full, ROOT)
                try:
                    zf.write(full, arc)
                    count += 1
                except Exception as e:
                    print(f"  (warning) could not zip {arc}: {e}")
        for d in FULL_KEEP_EMPTY_DIRS:
            try:
                zf.writestr(d + '/', '')
            except Exception:
                pass
    print(f"  Full zip      -> {out_zip} ({count} files)")
    return out_zip


def prune_client_builds(keep=2):
    """Keep only the newest few zips per kind in CLIENTS BUILD.

    Old zips pile up and the wrong one gets sent. Anything beyond the
    newest `keep` of each prefix is deleted.
    """
    import glob as _glob
    dest_dir = os.path.join(ROOT, 'CLIENTS BUILD')
    if not os.path.isdir(dest_dir):
        return
    for prefix in ('AurumOS-client-', 'AurumOS-full-'):
        zips = sorted(_glob.glob(os.path.join(dest_dir, prefix + '*.zip')),
                      key=os.path.getmtime)
        for old in zips[:-keep] if len(zips) > keep else []:
            try:
                os.remove(old)
                print(f"  Pruned old: {os.path.basename(old)}")
            except Exception as e:
                print(f"  (warning) could not prune {old}: {e}")


# Generated files that older builds wrote into the project root. They now live
# in GEN; copies left in the root are stale and only clutter it.
ROOT_LEFTOVERS = (
    'AurumOS.spec', 'version_info.txt', 'build_version.txt', 'build_stamp.txt',
    'hook_local.py', 'hook_webview.py', 'hook_win32.py',
    'rthook_fix_http.py', 'rthook_serial.py', 'rthook_pythonnet.py',
)


def remove_root_leftovers():
    for name in ROOT_LEFTOVERS:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
                print(f"  Removed old root copy: {name}")
            except Exception as e:
                print(f"  (warning) could not remove {name}: {e}")


# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    t0 = time.time()
    print("=" * 50)
    print(" AurumOS Build Script")
    print("=" * 50)

    check_env()
    kill_running_exe()
    venv_site, webview_dir, arch = get_paths()
    remove_root_leftovers()
    built_version = stamp_version()
    write_version_resource(built_version)
    write_hooks()
    write_spec(venv_site, webview_dir, arch)

    backed = obfuscate()
    try:
        ok = run_pyinstaller()
    finally:
        # Restore runs even when PyInstaller raises or the step fails:
        # skipping it is how stubs survive in the tree.
        restore(backed)
    verify_sources(backed)

    if not ok:
        print("\nBuild FAILED - originals restored")
        sys.exit(1)

    sign_exe()
    write_trusted_hash()
    finalize_dist()
    stage_client()
    # No zips during build: CLIENTS BUILD zips are made on demand only
    # (zip_client / zip_full in this file). Normal build ends here.
    report(built_version)
    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("=" * 50)