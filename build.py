"""
AurumOS — All-in-One Build Script
Run: python build.py

Does everything:
  1. Checks environment
  2. Writes hook files
  3. Writes AurumOS.spec
  4. Obfuscates source files
  5. Runs PyInstaller
  6. Restores original source files
  7. Reports result
"""

import os, sys, glob, platform, shutil, subprocess, fnmatch, time

ROOT = os.path.abspath('.')
BAK  = '.orig'

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
    for pkg in ['PyInstaller', 'webview', 'PIL', 'qrcode', 'win32print']:
        try: __import__(pkg if pkg != 'PIL' else 'PIL.Image')
        except ImportError: missing.append(pkg)
    if missing:
        print(f"  Missing packages: {missing}")
        print(f"  Run: pip install pyinstaller pywebview pillow qrcode[pil] pywin32")
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

    # Runtime hook — fixes http circular import (most critical)
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

    print("  OK — rthook_fix_http.py, hook_webview.py, hook_win32.py")

    # Runtime hook — fix stdout encoding + pre-load serial
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
    print("  OK — rthook_serial.py (UTF-8 stdout + serial preload)")


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

    # serial binaries — collect all .pyd files from pyserial
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
# Auto-generated by build.py — do not edit manually
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
        ("{serial_dir_str}", "serial"),
{win32lib_str}
{wv_datas_str}
    ],
    hiddenimports=[
        "updater","database","database.db_manager","core","core.tag_engine",
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
    ],
    hookspath=["."],
    hooksconfig={{}},
    runtime_hooks=["rthook_fix_http.py"],
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
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="AurumOS",
    icon="AurumOS.ico" if os.path.exists("AurumOS.ico") else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["win32api.pyd","win32print.pyd","win32gui.pyd",
                 "pywintypes*.dll","pythoncom*.dll","winspool.drv","WebView2Loader.dll"],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''
    open(os.path.join(ROOT, 'AurumOS.spec'), 'w', encoding='utf-8').write(spec)
    print("  OK — AurumOS.spec written")

# ── STEP 4: Obfuscate ─────────────────────────────────────────────────────────
TARGETS = [
    'main.py',
    'updater.py',
    os.path.join('database', 'db_manager.py'),
    os.path.join('core', 'tag_engine.py'),
]

def obfuscate():
    print("\n[4/7] Obfuscating source files...")
    import marshal, zlib, base64, compileall
    backed = []
    for rel in TARGETS:
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            print(f"  SKIP (not found): {rel}")
            continue
        try:
            src  = open(full, 'r', encoding='utf-8').read()
            code = compile(src, full, 'exec')
            enc  = base64.b85encode(zlib.compress(marshal.dumps(code), 9)).decode()
            stub = f"import marshal,zlib,base64\nexec(marshal.loads(zlib.decompress(base64.b85decode(\n    {repr(enc)}\n))))\n"
            bak  = full + BAK
            shutil.copy2(full, bak)
            backed.append(bak)
            open(full, 'w', encoding='utf-8').write(stub)
            print(f"  Obfuscated: {rel}")
        except Exception as e:
            print(f"  SKIP ({e}): {rel}")
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
    r = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', 'AurumOS.spec', '--clean', '--noconfirm'],
        cwd=ROOT
    )
    if r.returncode != 0:
        print("\n  ERROR: PyInstaller failed")
        return False
    return True

# ── STEP 6: Report ────────────────────────────────────────────────────────────
def report():
    print("\n[7/7] Build report...")
    exe = os.path.join(ROOT, 'dist', 'AurumOS.exe')
    if os.path.exists(exe):
        size_mb = os.path.getsize(exe) / 1024 / 1024
        print(f"\n  ✓ SUCCESS")
        print(f"  EXE : {exe}")
        print(f"  Size: {size_mb:.1f} MB")
        print(f"\n  Copy to client:")
        print(f"  dist\\AurumOS.exe")
    else:
        print(f"\n  ✗ EXE not found at {exe}")
        print(f"  Check build output above for errors")

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    t0 = time.time()
    print("=" * 50)
    print(" AurumOS Build Script")
    print("=" * 50)

    check_env()
    venv_site, webview_dir, arch = get_paths()
    write_hooks()
    write_spec(venv_site, webview_dir, arch)

    backed = obfuscate()
    ok     = run_pyinstaller()
    restore(backed)

    if not ok:
        print("\n✗ Build FAILED — originals restored")
        sys.exit(1)

    report()
    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print("=" * 50)