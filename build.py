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
    try:
        import pythonnet
        os.environ.pop("PYTHONNET_RUNTIME", None)
        for runtime in ("netfx", "coreclr"):
            try:
                pythonnet.load(runtime)
                os.environ["PYTHONNET_RUNTIME"] = runtime
                clr_ok = True
                return
            except Exception:
                continue
    except Exception:
        pass

    # 6) no .NET runtime found - set flag for friendly error in main.py
    if not clr_ok:
        os.environ["AURUM_CLR_FAILED"] = "1"

_boot_clr()
'''

# Files targeted for symmetric core encryption pass
TARGETS = [
    'main.py',
    'updater.py',
    os.path.join('database', 'db_manager.py'),
    os.path.join('core', 'tag_engine.py'),
]

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
        import site as _s
        venv_site = _s.getsitepackages()[0]

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
    open(os.path.join(ROOT, 'rthook_fix_http.py'), 'w').write('''\
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
    open(os.path.join(ROOT, 'hook_webview.py'), 'w').write('''\
from PyInstaller.utils.hooks import collect_all, collect_submodules
datas, binaries, hiddenimports = collect_all("webview")
hiddenimports += collect_submodules("webview")
''')

    # Collection hook for win32
    open(os.path.join(ROOT, 'hook_win32.py'), 'w').write('''\
from PyInstaller.utils.hooks import collect_submodules
hiddenimports = collect_submodules("win32") + ["pywintypes","win32con","win32api","win32print","win32gui"]
''')

    # Collection hook for LOCAL application packages (database / core / network)
    # CRITICAL: main.py is obfuscated to marshal BEFORE PyInstaller runs, so
    # PyInstaller cannot statically see main.py's lazy imports. This hook
    # force-bundles every local submodule so nothing import-time is missing
    # (this is what caused the "stuck at banner / missing module" crash).
    open(os.path.join(ROOT, 'hook_local.py'), 'w').write('''\
from PyInstaller.utils.hooks import collect_submodules
hiddenimports = (
    collect_submodules("database") +
    collect_submodules("core") +
    collect_submodules("network")
)
''')

    print("  OK - rthook_fix_http.py, hook_webview.py, hook_win32.py, hook_local.py")

    # Runtime hook - fix stdout encoding + pre-load serial
    open(os.path.join(ROOT, 'rthook_serial.py'), 'w').write('''\
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
    open(os.path.join(ROOT, 'rthook_pythonnet.py'), 'w', encoding='utf-8').write(_PYTHONNET_RTHOOK)


# ── STEP 3: Write AurumOS.spec ────────────────────────────────────────────────
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
    ["main.py"],
    pathex=[{repr(ROOT)}, {repr(venv_site)}, {repr(win32_dir)}, {repr(win32_lib_dir)}],
    binaries=[
{win32_bin_str}
{sysdll_str}
{winspool_str}{serial_bin_str}
    ],
    datas=[
        ("ui",    "ui"),
        ("fonts", "fonts"),
        ("core",  "core"),
        ("network", "network"),
        ("{serial_dir_str}", "serial"),
{win32lib_str}
{wv_datas_str}
{clrloader_runtime_str}
    ],
    hiddenimports=[
        "pythonnet","clr_loader","clr_loader.netfx","clr_loader.hostfxr","clr_loader.ffi", # ── FIXED: CLR host for pywebview winforms
        "requests", "urllib3", "charset_normalizer", "idna", # ── FIXED: FORCE INJECTED REQUESTS BINARIES
        "updater","subscription_manager","database","database.db_manager","core","core.tag_engine","sync_engine","network_client",
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
        "network","network.discovery","network.brain_server","network.brain_client",
        "network.brain_guard","network.connection_store","network.handshake","network.hardware_map",
        "database.bastion_ai","database.bastion_report","database.aurum_health",
        "core.bastion_sync","core.ai_support","core.io_safety","core.security_lock","core.print_bridge",
    ],
    hookspath=["."],
    hooksconfig={{}},
    runtime_hooks=[
    "rthook_fix_http.py",
    "rthook_serial.py",
    "rthook_pythonnet.py",
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
    icon="AurumOS.ico" if os.path.exists("AurumOS.ico") else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=["win32api.pyd","win32print.pyd","win32gui.pyd",
                 "pywintypes*.dll","pythoncom*.dll","winspool.drv","WebView2Loader.dll",
                 "Python.Runtime.dll","ClrLoader.dll","Python.Runtime.deps.json",
                 "python3.dll","python312.dll"],
    name="AurumOS",
)
'''
    open(os.path.join(ROOT, 'AurumOS.spec'), 'w', encoding='utf-8').write(spec)
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
            # Skip files that are ALREADY obfuscated stubs (e.g. left stubbed by
            # a previously interrupted build). Re-wrapping a stub only nests
            # another decrypt layer; the existing single-layer stub already runs
            # the real code at import, so leave it untouched and don't back it up.
            if src.lstrip().startswith('import marshal') and 'exec(marshal.loads(zlib.decompress(' in src:
                print(f"  SKIP (already obfuscated - left as-is): {rel}")
                continue
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
        [sys.executable, '-m', 'PyInstaller', 'AurumOS.spec', '--clean', '--noconfirm'],
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
# until you provide a cert - see sign_config.json.example.
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
    Env vars take precedence (so you never have to put the password in a file):
      AURUMOS_PFX, AURUMOS_PFX_PASSWORD, AURUMOS_TIMESTAMP_URL
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
    cfg.setdefault("timestamp_url", os.environ.get(
        "AURUMOS_TIMESTAMP_URL", "http://timestamp.digicert.com"))
    return cfg

def sign_exe():
    print("\n[5a/7] Code-signing EXE (Authenticode)...")
    # PyInstaller's temporary onedir output is finalized to this fixed path.
    exe_path = os.path.join(DIST, "AurumOS", "AurumOS.exe")
    if not os.path.isfile(exe_path):
        print("  SKIP -- EXE not found at dist\\AurumOS.exe")
        return False

    cfg = _load_sign_config()
    pfx = cfg.get("pfx_path", "")
    if not pfx:
        print("  SKIP -- no certificate configured.")
        print("  → To remove the SmartScreen 'unknown publisher' warning, get a")
        print("    code-signing cert (.pfx) and put its path in sign_config.json")
        print("    (see sign_config.json.example), or set env: AURUMOS_PFX.")
        return False
    if not os.path.isfile(pfx):
        print(f"  ERROR -- PFX not found: {pfx}")
        return False

    signtool = _locate_signtool()
    if not signtool:
        print("  ERROR -- signtool.exe not found (install the Windows SDK).")
        print("    https://developer.microsoft.com/windows/downloads/windows-sdk/")
        return False

    # Sign with SHA256 file digest + RFC3161 timestamp (keeps signature valid
    # after the cert expires). Password via env or config; empty password allowed.
    cmd = [
        signtool, "sign",
        "/fd", "SHA256",
        "/tr", cfg.get("timestamp_url", "http://timestamp.digicert.com"),
        "/td", "SHA256",
        "/f", pfx,
    ]
    pw = cfg.get("password", "")
    if pw:
        cmd += ["/p", pw]
    cmd.append(exe_path)

    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("  X Signing FAILED - see output above. EXE left UNSIGNED"
              " (SmartScreen will still warn).")
        return False
    print("  OK SIGNED - SmartScreen 'unknown publisher' warning removed.")
    return True

# ── STEP 5b: Write trusted EXE hash ───────────────────────────────────────────
def write_trusted_hash():
    print("\n[5b/7] Writing trusted EXE hash...")
    exe_path = os.path.join(DIST, 'AurumOS', 'AurumOS.exe')
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
def report():
    print("\n[7/7] Build report...")
    exe = os.path.join(DIST, 'AurumOS.exe')
    if os.path.exists(exe):
        size_mb = os.path.getsize(exe) / 1024 / 1024
        print(f"\n  ✓ SUCCESS")
        print(f"  EXE   : {exe}")
        print(f"  Runtime: {os.path.join(ROOT, 'dist', '_internal')}")
        print(f"  Size: {size_mb:.1f} MB")
        print(f"\n  Ship the WHOLE dist\\ folder to each client PC. Clean layout:")
        print(f"  dist\\AurumOS.exe            (double-click to run)")
        print(f"  dist\\_internal\\             (all deps - keep NEXT TO the EXE)")
        print(f"  dist\\database\\  dist\\logs\\  (runtime data)")
        print(f"  dist\\config.json  dist\\exe_trusted_hash.txt")
        print(f"  (the hash file tells Bastion AI this exact build is legitimate --")
        print(f"   without it next to the EXE, tamper-detection can't run)")
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
    core/, network/) are merged into the runtime copies already present in
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
                print(f"  WARNING: Could not remove {_bak} — previous build may be running")
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

    # ... then the EXE (last, so its _internal is already beside it).
    exe_src = os.path.join(nested, 'AurumOS.exe')
    if os.path.isfile(exe_src):
        shutil.move(exe_src, top_exe)
        print("  Moved EXE     -> dist\\AurumOS.exe")
    if not os.path.isfile(top_exe):
        raise RuntimeError("Build completed without dist\\AurumOS.exe; refusing to leave the EXE elsewhere")

    # Remove the now-empty nested folder.
    shutil.rmtree(nested, ignore_errors=True)

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


# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    t0 = time.time()
    print("=" * 50)
    print(" AurumOS Build Script")
    print("=" * 50)

    check_env()
    kill_running_exe()
    venv_site, webview_dir, arch = get_paths()
    write_hooks()
    write_spec(venv_site, webview_dir, arch)

    backed = obfuscate()
    ok     = run_pyinstaller()
    restore(backed)

    if not ok:
        print("\n�- Build FAILED - originals restored")
        sys.exit(1)

    sign_exe()
    write_trusted_hash()
    finalize_dist()
    report()
    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("=" * 50)