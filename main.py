# -*- coding: utf-8 -*-
import os as _os
_os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
_os.environ.setdefault('PYTHONUTF8', '1')
import io as _io, sys as _sys
try:
    if hasattr(_sys.stdout, 'buffer'):
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(_sys.stderr, 'buffer'):
        _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass


# ── APPLY STAGED BACKEND UPDATES (before any app imports) ─────────────────
def _apply_staged_updates():
    """Copy _update_staging/ files to their final locations, then delete staging.
    Must run BEFORE any app imports so updated modules are loaded fresh.
    Checks BOTH the EXE directory and the app root, because the downloader
    stages relative to get_app_root() while the EXE may live in dist/ —
    checking only one side silently dropped backend updates forever."""
    import shutil
    try:
        if getattr(_sys, 'frozen', False):
            exe_base = _os.path.dirname(_os.path.abspath(_sys.executable))
        else:
            exe_base = _os.getcwd()
        try:
            from updater import get_app_root as _gar
            app_base = str(_gar())
        except Exception:
            app_base = exe_base
        bases = []
        for b in (exe_base, app_base, _os.getcwd()):
            if b and b not in bases:
                bases.append(b)
        for base in bases:
            staging = _os.path.join(base, '_update_staging')
            if not _os.path.isdir(staging):
                continue
            applied = 0
            for dirpath, _, filenames in _os.walk(staging):
                for fname in filenames:
                    src = _os.path.join(dirpath, fname)
                    rel = _os.path.relpath(src, staging).replace('\\', '/')
                    dst = _os.path.join(base, rel.replace('/', _os.sep))
                    try:
                        _os.makedirs(_os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        applied += 1
                    except Exception:
                        pass
            # Clean up staging
            shutil.rmtree(staging, ignore_errors=True)
            if applied:
                _ANY_APPLIED = applied
        # Mark update as applied (prevents duplicate banner)
        if '_ANY_APPLIED' in dir():
            try:
                marker = _os.path.join(exe_base, '.update_applied')
                vlock = _os.path.join(exe_base, 'version.lock')
                ver = ''
                if _os.path.isfile(vlock):
                    with open(vlock, 'r', encoding='utf-8') as f:
                        ver = f.read().strip()
                with open(marker, 'w', encoding='utf-8') as f:
                    f.write(ver)
            except Exception:
                pass
    except Exception:
        pass

_apply_staged_updates()
# ── END STAGED UPDATE APPLY ───────────────────────────────────────────────

import threading
import time
import hashlib
import re
import urllib.request
import urllib.error
import urllib.parse
import base64
import uuid
import hmac
import struct
import os
import sys

# ── Defer webview import — pywebview internally imports clr which ──
# loads System.Windows.Forms. If .NET is missing this crashes immediately.
# We import it lazily AFTER checking .NET availability in run_aur_os().
webview = None

# ── Monkey-patch pywebview to suppress stale callback / .NET async crashes ──
# Applied lazily when webview is first imported.
_webview_patched = False
def _patch_webview():
    global webview, _webview_patched
    if _webview_patched:
        return
    import importlib
    try:
        webview = importlib.import_module('webview')
    except ImportError:
        webview = None
        return
    try:
        webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
        webview.settings['ALLOW_DOWNLOADS'] = True
    except Exception:
        pass
    try:
        from webview.window import Window as _WVWindow
        _orig_eval_js = _WVWindow.evaluate_js
        def _safe_eval_js(self, script, **kwargs):
            try:
                return _orig_eval_js(self, script, **kwargs)
            except Exception:
                return None
        _WVWindow.evaluate_js = _safe_eval_js
    except Exception:
        pass
    _webview_patched = True


def _ensure_webview():
    """Guarantee webview is importable or exit with a clear message."""
    global webview
    if webview is not None and hasattr(webview, 'create_window'):
        return
    _patch_webview()
    if webview is not None and hasattr(webview, 'create_window'):
        return
    # Last-chance fallback: direct import
    try:
        import importlib
        webview = importlib.import_module('webview')
        if hasattr(webview, 'create_window'):
            return
    except Exception:
        pass
    # Nothing worked — tell the user exactly what to do
    _msg = (
        "AurumOS cannot start because 'pywebview' is not installed or is broken.\n\n"
        "Fix — run this command:\n\n"
        "    pip install --force-reinstall pywebview\n\n"
        "If you already installed it, make sure you are using the same\n"
        "Python interpreter that is running this script."
    )
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, _msg, "AurumOS — Missing Dependency", 0x10)
    except Exception:
        print(_msg, file=sys.stderr)
    sys.exit(1)

# Set DB path env var BEFORE DBManager import so it always uses EXE directory
def _get_db_base():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath('.')
_db_base_dir = _get_db_base()
os.environ['AURUM_DB_DIR'] = _db_base_dir
os.makedirs(os.path.join(_db_base_dir, 'database'), exist_ok=True)
import json
import random
from datetime import datetime
from pathlib import Path

# ── License key format validation ────────────────────────────────────────────
# Accepted prefixes: AU- (standard), AR- (retail).  Format: XX-XXXX-XXXX-XXXX-XXXX (22 chars)
_VALID_KEY_PREFIXES = ("AU-", "AR-")
def _is_valid_key_format(key):
    """Return True if *key* matches AU-XXXX-XXXX-XXXX-XXXX or AR-XXXX-XXXX-XXXX-XXXX."""
    return isinstance(key, str) and len(key) == 22 and key.upper().startswith(_VALID_KEY_PREFIXES)
# noinspection PyUnresolvedReferences
from database.db_manager import DBManager  # obfuscated module — resolved at runtime
from subscription_manager import SubscriptionManager
from sse_listener import LicenseEventListener
try:
    from database.bastion_ai import BastionAI
    _BASTION_AVAILABLE = True
except ImportError:
    _BASTION_AVAILABLE = False
    class BastionAI:
        def __init__(self, *a): pass
        def start(self): pass
        def stop(self): pass
        def notify_write(self): pass
        def notify_session_active(self, *a): pass
        def get_weekly_report(self): return {}

# The version is not typed in here: build.py stamps the highest git tag into
# build_version.txt inside the bundle and updater reads it, so every build
# reports the number its release was published under.
# This import used to also ask for download_and_install, which was deleted from
# updater.py in June. The missing name made the whole import fail, so the app
# silently fell back to the stub below: it called itself 1.0.6 and every update
# check returned None. Import only what exists.
try:
    from updater import check_for_update, CURRENT_VERSION
except ImportError as _upd_err:
    CURRENT_VERSION = '0.0.0'
    def check_for_update(**kw): return None
    print(f'[UPDATE] updater unavailable, update checks disabled: {_upd_err}')
# noinspection PyUnresolvedReferences
from core.tag_engine import TagFactory  # obfuscated module — resolved at runtime

# ── TAG ENGINE TAMPER-EVIDENCE ─────────────────────────────────────────
# core/tag_engine.py ships obfuscated (source was lost to a build that never
# restored it; only the encrypted stub is committed). Its interface was
# re-mapped from the bytecode: TagFactory with generate_tag_image,
# generate_preview, print_to_thermal_printer and set_printer. Until the
# source is rewritten, the stub's hash is pinned in core/tag_engine.sha256
# and checked here. Log-only by design: a mismatch must never brick a shop,
# it only names the file so the next update can replace it deliberately.
def _verify_tag_engine():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        stub = os.path.join(base, 'core', 'tag_engine.py')
        pin = os.path.join(base, 'core', 'tag_engine.sha256')
        if not (os.path.isfile(stub) and os.path.isfile(pin)):
            return
        want = open(pin, 'r', encoding='utf-8').read().strip().split()[0]
        got = hashlib.sha256(open(stub, 'rb').read()).hexdigest()
        if got != want:
            LOG(f"[TAGENGINE] hash mismatch (want {want[:12]}…, got {got[:12]}…) — module replaced outside release")
        else:
            LOG("[TAGENGINE] pinned stub verified")
    except Exception as _te:
        LOG(f"[TAGENGINE] verify skipped: {_te}")

try:
    from core.ai_support import AISupport
    _AI_AVAILABLE = True
except Exception:
    _AI_AVAILABLE = False
    class AISupport:
        def __init__(self, *a, **k): pass
        def ask(self, *a, **k): return {"status": "error", "message": "AI assistant unavailable."}
        def get_status(self): return {"provider": "openai", "model": "", "configured": False, "key_masked": ""}
        def save_config(self, *a, **k): pass

HOT_RELOAD = False

# ── SUPPRESS pythonnet .NET interop crashes ──────────────────────────
# pythonnet sometimes fails to convert obscure .NET exceptions
# (InvalidAsynchronousStateException etc.) into Python objects, causing
# a hard crash. This hook swallows those so the app stays alive.
import sys as _sys
_original_excepthook = _sys.excepthook
def _safe_excepthook(exc_type, exc_value, exc_tb):
    try:
        _estr = str(exc_value) if exc_value else ''
        if 'InvalidAsynchronousStateException' in _estr or 'TypeManager' in _estr:
            try: print(f'[PYNET-SAFE] Suppressed .NET interop crash: {_estr[:120]}', flush=True)
            except: pass
            return
    except Exception:
        pass
    _original_excepthook(exc_type, exc_value, exc_tb)
_sys.excepthook = _safe_excepthook

# Also catch it in background threads
import threading as _threading
_orig_thread_exc_hook = getattr(_threading, 'excepthook', None)
def _safe_thread_exc_hook(args):
    try:
        _estr = str(args.exc_value) if args and args.exc_value else ''
        if 'InvalidAsynchronousStateException' in _estr or 'TypeManager' in _estr:
            try: print(f'[PYNET-SAFE] Suppressed .NET crash in thread: {_estr[:120]}', flush=True)
            except: pass
            return
    except Exception:
        pass
    if _orig_thread_exc_hook:
        _orig_thread_exc_hook(args)
try:
    _threading.excepthook = _safe_thread_exc_hook
except AttributeError:
    pass


# -- PERSISTENT LOG FILE -------------------------------------------------------
import logging as _logging
_log_path = None

def _safe(msg):
    """Strip non-ASCII chars so Windows charmap codec never crashes."""
    try:
        return str(msg).encode('ascii', errors='replace').decode('ascii')
    except Exception:
        return repr(msg)
def LOG(msg): _logging.info(_safe(msg))
def ERR(msg): _logging.error(_safe(msg))

def _init_log():
    global _log_path
    base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath('.')
    log_dir = os.path.join(base,'logs')
    os.makedirs(log_dir, exist_ok=True)
    _log_path = os.path.join(log_dir,'aurumos.log')
    if os.path.exists(_log_path) and os.path.getsize(_log_path) > 500_000:
        open(_log_path,'w').close()
    _logging.basicConfig(
        filename=_log_path, level=_logging.DEBUG,
        format='%(asctime)s %(levelname)s %(message)s',
        encoding='utf-8', errors='replace'
    )
    # Console handler — force UTF-8 so box-drawing chars never crash on Windows
    try:
        import io as _io2
        utf8_stdout = _io2.TextIOWrapper(
            sys.stdout.buffer if hasattr(sys.stdout,'buffer') else open(os.devnull,'wb'),
            encoding='utf-8', errors='replace', line_buffering=True
        )
        console_handler = _logging.StreamHandler(utf8_stdout)
    except Exception:
        console_handler = _logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    _logging.getLogger().addHandler(console_handler)
    # Ensure db_manager logger propagates to root (captures _dblog calls)
    _logging.getLogger('aurumos.db').setLevel(_logging.DEBUG)
    _logging.getLogger('aurumos.db').propagate = True
    LOG('=== AurumOS Started ===')
    LOG(f'=== Log file: {_log_path} ===')
    LOG(f'=== Version : {CURRENT_VERSION} ===')
    LOG(f'=== EXE     : {sys.executable} ===')
    LOG(f'=== Frozen  : {getattr(sys,"frozen",False)} ===')

_init_log()
_verify_tag_engine()


def get_asset_path(relative_path):
    """
    Locate UI/asset files.

    Frozen: _internal/ beside the EXE, which is both where the build puts
    these files and where the updater writes new ones, so a downloaded screen
    is the screen that opens next.

    This used to climb out of any folder called dist/ or AurumOS/ and prefer a
    ui/ folder found up there. The updater climbed the same way and the two did
    not land together. On a PC installed to C:/AurumOS the app read
    C:/AurumOS/_internal/ui while updates were written to C:/_internal/ui; on
    the developer's own machine the app read the source ui/ while updates went
    to dist/_internal/ui. Either way an update downloaded perfectly, recorded
    itself in version.lock, and changed nothing on screen. One rule now, shared
    with updater.get_app_root().
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller always sets _MEIPASS in a frozen process, and for this
        # onedir build it is _internal/ beside the EXE. Trust it rather than
        # testing whether the folder is there: a missing folder is a broken
        # install, and quietly reading somewhere else would hide that while
        # making updates land out of reach again.
        base = getattr(sys, '_MEIPASS', '')
        if not base:
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            internal = os.path.join(exe_dir, '_internal')
            base = internal if os.path.isdir(internal) else exe_dir
        return os.path.join(base, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def _log_ui_paths():
    """Say where screens are read from and where updates are written.

    These two silently disagreed for months, so an update downloaded, recorded
    itself in version.lock and changed nothing on screen. MATCH is what a
    working install looks like; MISMATCH names the fault outright instead of
    leaving it to be rediscovered.
    """
    try:
        ui_dir = get_asset_path('ui')
        LOG(f'=== UI from : {ui_dir} ===')
        if not getattr(sys, 'frozen', False):
            LOG('=== UI to   : n/a, updates only apply to a built copy ===')
            return
        from updater import get_app_root as _gar0
        upd_dir = os.path.join(str(_gar0()), '_internal', 'ui')
        same = (os.path.normcase(os.path.normpath(ui_dir))
                == os.path.normcase(os.path.normpath(upd_dir)))
        LOG(f'=== UI to   : {upd_dir} '
            f'({"MATCH" if same else "MISMATCH - updates will not show"}) ===')
    except Exception as e:
        LOG(f'=== UI paths: could not resolve ({e}) ===')


_log_ui_paths()


def _powershell_hidden(args, **kw):
    """Run PowerShell without flashing a console window.

    Every file/folder picker and clipboard call used to spawn a visible
    powershell.exe console (the blue window that pops up behind the dialog).
    Hiding it here, once, fixes all of them permanently — new callers must
    use this instead of subprocess.run for powershell.
    """
    import subprocess
    kw.setdefault('capture_output', True)
    kw.setdefault('text', True)
    if os.name == 'nt':
        try:
            kw.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
        try:
            _si = subprocess.STARTUPINFO()
            _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kw.setdefault('startupinfo', _si)
        except Exception:
            pass
    return subprocess.run(args, **kw)


def _sta_call(fn, timeout=120):
    """Run fn on a dedicated STA thread (WinForms dialogs require STA).

    Returns (value, err) where err is None on success, 'timeout' if the
    user left the dialog open past the budget (treated as cancelled — no
    second dialog is opened), or the exception on technical failure.
    """
    import threading
    box = {}

    def _run():
        try:
            import ctypes
            try:
                ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
            except Exception:
                pass
            try:
                box['value'] = fn()
            finally:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass
        except Exception as e:
            box['error'] = e

    t = threading.Thread(target=_run, daemon=True, name='AurumSTA')
    t.start()
    t.join(timeout)
    if t.is_alive():
        return ('', 'timeout')
    if 'error' in box:
        return (None, box['error'])
    return (box.get('value') or '', None)


def _native_pick_file(file_filter, title, timeout=115):
    """In-process Windows file dialog via WinForms (pythonnet).

    No child process is ever started, so no console window can appear —
    this is the permanent fix for the powershell.exe flash. Returns the
    chosen path, '' if cancelled/timed-out, or None when native dialogs
    are unavailable (caller falls back to hidden PowerShell).
    """
    def _show():
        import clr
        clr.AddReference('System.Windows.Forms')
        from System.Windows.Forms import OpenFileDialog, DialogResult
        dlg = OpenFileDialog()
        dlg.Filter = file_filter
        dlg.Title = title
        dlg.CheckFileExists = True
        return dlg.FileName if dlg.ShowDialog() == DialogResult.OK else ''
    try:
        value, err = _sta_call(_show, timeout)
    except Exception:
        return None
    if err is None:
        return value
    if err == 'timeout':
        return ''
    return None


def _native_pick_folder(description, timeout=55):
    """In-process Windows folder dialog via WinForms. Same contract as
    _native_pick_file: path, '' on cancel/timeout, None when unavailable."""
    def _show():
        import clr
        clr.AddReference('System.Windows.Forms')
        from System.Windows.Forms import FolderBrowserDialog, DialogResult
        dlg = FolderBrowserDialog()
        dlg.Description = description
        dlg.ShowNewFolderButton = True
        return dlg.SelectedPath if dlg.ShowDialog() == DialogResult.OK else ''
    try:
        value, err = _sta_call(_show, timeout)
    except Exception:
        return None
    if err is None:
        return value
    if err == 'timeout':
        return ''
    return None


def get_data_path(relative_path=""):
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.abspath(".")
    return os.path.join(base, relative_path) if relative_path else base


# -- LICENSE KEY ENCRYPTION ----------------------------------------------------
def _derive_client_key(machine_id: str) -> bytes:
    """PBKDF2 key from machine_id — used for E2E temp password encryption."""
    return hashlib.pbkdf2_hmac('sha256', machine_id.encode('utf-8'),
                                b'AurumOS-Salt-v1', 100_000, dklen=32)

def _decrypt_temp_password(payload: str, machine_id: str):
    """
    AES-GCM decrypt — matches Web Crypto API used in Next.js dashboard.
    Format: base64(iv[12] + ciphertext+tag[variable])
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes as _h
        from cryptography.hazmat.backends import default_backend
        import base64 as _b64

        # Derive same key as dashboard
        kdf = PBKDF2HMAC(algorithm=_h.SHA256(), length=32,
                          salt=b'AurumOS-Salt-v1', iterations=100000,
                          backend=default_backend())
        key = kdf.derive(machine_id.encode('utf-8'))

        raw = _b64.b64decode(payload.encode('ascii'))
        iv  = raw[:12]
        ct  = raw[12:]
        return AESGCM(key).decrypt(iv, ct, None).decode('utf-8')
    except ImportError:
        # cryptography not installed — fallback XOR (dev only)
        LOG("[DECRYPT] cryptography lib not found — using fallback")
        import hmac as _hmac, base64 as _b64
        try:
            key = _derive_client_key(machine_id)
            raw = _b64.b64decode(payload.encode('ascii'))
            iv, mac, ct2 = raw[:16], raw[-8:], raw[16:-8]
            expected = _hmac.new(key, iv+ct2, hashlib.sha256).digest()[:8]
            if not _hmac.compare_digest(mac, expected): return None
            def ks(k,iv,n):
                s,b=b'',iv
                while len(s)<n: b=hashlib.sha256(k+b).digest(); s+=b
                return s[:n]
            return bytes(a^b for a,b in zip(ct2,ks(key,iv,len(ct2)))).decode('utf-8')
        except Exception: return None
    except Exception as e:
        LOG(f"[DECRYPT] Failed: {e}")
        return None

def _get_machine_key():
    import uuid, hashlib
    machine_id = str(uuid.getnode()).encode()
    return hashlib.sha256(machine_id).digest()

def _xor_cipher(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt_license_key(key_str: str) -> bytes:
    raw = key_str.encode('utf-8')
    enc = _xor_cipher(raw, _get_machine_key())
    return b'\xAA\x01' + enc

def decrypt_license_key(data: bytes) -> str:
    try:
        if not data.startswith(b'\xAA\x01'):
            return ''
        enc = data[2:]  # header is exactly the 2 bytes checked above — a
        # previous build stripped 3 here, silently eating the first cipher
        # byte. Every stored key then decoded to garbage, the except below
        # swallowed it to '', and the whole subscription system sat on Lite
        # forever despite a valid enterprise key on file.
        raw = _xor_cipher(enc, _get_machine_key())
        return raw.decode('utf-8')
    except Exception:
        return ''


# -- SCALE READER --------------------------------------------------------------
import re as _re

class ScaleReader:
    """
    Robust serial scale reader.
    - Auto-detects port if none specified
    - Retries on read error
    - Persists last used port in app_config
    - Pushes weight to ALL open windows via __onScale
    """
    WEIGHT_PATTERNS = [
        _re.compile(r'[+-]?\s*(\d+\.\d+)\s+G\s+[SU]'),
        _re.compile(r'ST[,\s]+GS[,\s]+[+-]?\s*(\d+\.\d+)'),
        _re.compile(r'GS[,\s]+[+-]?\s*(\d+\.\d+)'),
        _re.compile(r'[+-]?\s*(\d+\.\d+)\s*g', _re.I),
        _re.compile(r'[+-]?\s*(\d+\.\d{2,3})\s*$'),
        _re.compile(r'(\d+\.\d+)'),
    ]
    STABLE_MARKERS = ['ST,', 'ST ', 'STABLE', 'S,+', 'S,-', 'S +', 'S -', ',S,', ' S ']
    COMMON_BAUDS   = [9600, 1200, 2400, 4800, 19200, 38400]

    def __init__(self):
        self._port    = None
        self._baud    = 9600
        self._last_wt_count = 0
        self._last_wt_val   = None
        self._running = False
        self._thread  = None
        self._serial  = None
        self._window  = None
        self._last_wt = None
        self._cb      = None   # optional extra callback

    def set_window(self, w):
        self._window = w
        LOG(f"[SCALE] set_window: {type(w).__name__ if w else 'None'}")

    def get_last(self):
        return self._last_wt

    def parse(self, raw):
        """Parse weight from any scale format. Returns (weight, stable)."""
        try:    text = raw.decode('ascii', errors='ignore').strip()
        except: text = ''
        if not text: return None, False
        t = text.upper()

        # Hard unstable signals — if scale explicitly says unstable, honour it
        is_unstable = bool(_re.search(r'UNSTABLE|UNST|\bMOT\b|MOTION|\bE\s*R\b', t))

        for pat in self.WEIGHT_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    val = float(m.group(1))
                    if val <= 0: continue
                    # Any weight successfully parsed = stable
                    # UNLESS scale explicitly says unstable
                    stable = not is_unstable
                    return round(val, 3), stable
                except: pass
        return None, False

    def list_ports(self):
        """Return all available COM ports."""
        try:
            import serial.tools.list_ports as _lp
            ports = [{'port': p.device, 'desc': p.description} for p in _lp.comports()]
            LOG(f"[SCALE] Available ports: {[p['port'] for p in ports]}")
            return ports
        except Exception as e:
            ERR(f"[SCALE] list_ports error: {e}")
            return []

    def start(self, port=None, baud=None):
        """
        Connect to scale.
        - If port given: use it
        - If no port: try last saved port, then auto-scan all ports
        - Tests port before starting thread
        """
        import serial as _ser

        if baud: self._baud = int(baud)
        if port: self._port = str(port).strip()

        # Auto-detect if no port specified
        if not self._port:
            return {"status":"error","message":"No port selected. Choose a COM port."}

        if self._running: self.stop(); time.sleep(0.3)

        # ── Auto-detect baud rate ─────────────────────────────────
        #  garbage = wrong baud. Try all common rates.
        LOG(f"[SCALE] Auto-detecting baud for {self._port}...")
        BAUDS = [1200, 2400, 4800, 9600, 19200]
        detected_baud = None
        for try_baud in BAUDS:
            try:
                t = _ser.Serial(self._port, try_baud,
                    bytesize=8, parity='N', stopbits=1,
                    timeout=1.5, xonxoff=False, rtscts=False)
                t.flushInput()
                raw = t.read(32)
                t.close()
                if raw:
                    txt = raw.decode('ascii', errors='replace')
                    has_digit = any(ch.isdigit() for ch in txt)
                    has_garbage = txt.count('�') > len(txt) * 0.3
                    LOG(f"[SCALE] Baud {try_baud}: {raw[:16]} ascii={has_digit} garbage={has_garbage}")
                    if has_digit and not has_garbage:
                        detected_baud = try_baud
                        break
                else:
                    LOG(f"[SCALE] Baud {try_baud}: no data received")
            except _ser.SerialException as e:
                ERR(f"[SCALE] Baud {try_baud}: port error {e}")
                return {"status":"error","message":str(e)}
            except Exception as e:
                LOG(f"[SCALE] Baud {try_baud}: {e}")

        if detected_baud:
            self._baud = detected_baud
            LOG(f"[SCALE] ✓ Auto-detected baud: {self._baud}")
        else:
            LOG(f"[SCALE] Could not auto-detect baud, using {self._baud}")

        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="ScaleReader")
        self._thread.start()
        LOG(f"[SCALE] Started: {self._port} @ {self._baud}")
        return {"status":"success","port":self._port,"baud":self._baud}

    def stop(self):
        LOG("[SCALE] stop() called")
        self._running = False
        # Give thread 1s to exit cleanly
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        # Force close serial port
        if self._serial:
            try:
                self._serial.cancel_read()
            except: pass
            try:
                self._serial.close()
                LOG("[SCALE] Serial port closed")
            except Exception as e:
                ERR(f"[SCALE] Serial close error: {e}")
        self._serial  = None
        self._thread  = None
        self._running = False
        time.sleep(0.5)  # Give OS time to release port
        LOG("[SCALE] Stopped OK")

    def _push(self, payload: str):
        """Broadcast weight to ALL open webview windows."""
        import webview as _wv
        js = f'window.__onScale && window.__onScale({payload})'
        sent = 0
        try:
            wins = _wv.windows
            if not wins and self._window:
                wins = [self._window]
            for w in wins:
                try:
                    w.evaluate_js(js)
                    sent += 1
                except Exception as we:
                    ERR(f"[SCALE] _push window error: {we}")
            if sent:
                LOG(f"[SCALE] _push → {sent} window(s): {payload[:50]}")
            else:
                ERR("[SCALE] _push: no windows available")
        except Exception as e:
            ERR(f"[SCALE] _push failed: {e}")
            if self._window:
                try: self._window.evaluate_js(js)
                except: pass

    def _loop(self):
        import serial as _serial
        retry_delay = 1.0
        while self._running:
            try:
                ser = _serial.Serial(
                    self._port, self._baud,
                    bytesize=8, parity='N', stopbits=1,
                    timeout=1, xonxoff=False, rtscts=False
                )
                self._serial = ser
                ser.flushInput()
                LOG(f"[SCALE] Connected: {self._port} @ {self._baud}")
                retry_delay = 1.0  # reset on success
                while self._running:
                    try:
                        raw = b''
                        if ser.in_waiting:
                            raw = ser.readline()
                        else:
                            raw = ser.read(32)
                        if not raw:
                            time.sleep(0.05); continue
                        wt, stable = self.parse(raw)
                        if wt is not None:
                            self._last_wt = wt
                            # Consecutive identical reading = definitely stable
                            if self._last_wt_val == wt:
                                self._last_wt_count += 1
                            else:
                                self._last_wt_count = 1
                                self._last_wt_val   = wt
                            if self._last_wt_count >= 2:
                                stable = True
                            stable = True  # Weight parsed = stable (hard unstable signals handled in parse())
                            payload = '{' + f'"weight":{wt},"stable":true' + '}'
                            LOG(f"[SCALE] Weight parsed: {wt}g stable={stable} raw={raw[:20]}")
                            self._push(payload)
                        else:
                            if raw.strip():
                                LOG(f"[SCALE] Parse failed for raw: {raw[:30]}")
                    except _serial.SerialException as e:
                        ERR(f"[SCALE] Read error: {e}")
                        break
                    except Exception as e:
                        ERR(f"[SCALE] Loop error: {e}")
                        time.sleep(0.2)
                ser.close()
            except Exception as e:
                ERR(f"[SCALE] Connection failed: {e}")
                # Push error to UI
                safe = str(e).replace('"', "'")[:80]
                self._push('{' + f'"error":"{safe}","weight":null,"stable":false' + '}')
                if self._running:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 10.0)  # exponential backoff

_scale = ScaleReader()



# -- TAG ITEM NORMALIZER -------------------------------------------------------
_api_db_ref = None

def normalize_tag_item(item_data: dict) -> dict:
    item = dict(item_data)
    item_id = item.get('id') or item.get('item_id')
    db_wastage = None
    if item_id and _api_db_ref is not None:
        try:
            with _api_db_ref._get_connection() as conn:
                row = conn.execute(
                    "SELECT wastage, touch, gr_wt FROM stock_inventory WHERE id = ?",
                    (int(item_id),)
                ).fetchone()
                if row:
                    db_wastage = row['wastage']
                    if not item.get('touch') or str(item.get('touch')) in ('0', '', 'None'):
                        item['touch'] = str(row['touch'] or '0')
                    if not item.get('gross_wt') or str(item.get('gross_wt')) in ('0', '0.000', ''):
                        item['gross_wt'] = f"{float(row['gr_wt'] or 0):.3f}"
            LOG(f"[NORMALIZE] DB id={item_id} wastage={db_wastage!r}")
        except Exception as e:
            ERR(f"[NORMALIZE] DB fetch failed: {e}")

    _MISSING = object()
    def _sentinel_get(d, *keys):
        for k in keys:
            v = d.get(k, _MISSING)
            if v is not _MISSING: return v
        return _MISSING

    raw_w = db_wastage if db_wastage is not None else _sentinel_get(item, 'wastage','wastage_pct','wst','wstg','waste')
    if raw_w is _MISSING: raw_w = 0
    try:
        w_val = float(str(raw_w).strip()) if str(raw_w).strip() not in ('','-','None','null','none') else 0.0
    except: w_val = 0.0
    item['wastage'] = str(int(w_val)) if w_val == int(w_val) else str(w_val)
    item['touch']   = str(item.get('touch') or '0').strip()
    try:
        item['gross_wt'] = f"{float(item.get('gross_wt') or item.get('gr_wt') or 0):.3f}"
    except: item['gross_wt'] = '0.000'
    _var = str(item.get('variation') or 'RING').strip().upper()
    if _var not in ('RING','PARA','KATTI','STANDARD'): _var = 'RING'
    item['variation'] = _var
    item['tag_id']    = str(item.get('tag_id') or 'N/A').strip()
    LOG(f"[NORMALIZE] variation={item['variation']} gross={item['gross_wt']} "
        f"touch={item['touch']} wastage={item['wastage']} tag={item['tag_id']}")
    return item


def _print_template_for(bill):
    """Which print template renders this bill.

    Wholesale normal bills keep the legacy (original) design. Everything
    else — retail, uchak, and GST bills that need the tax machinery —
    uses the current template. Retail is never routed to legacy.
    Mirrors the buildSlip() dispatcher in ui/bill_print.html.
    """
    try:
        b = bill or {}
        if not b:
            return 'bill_print.html'  # unknown payload: status quo
        if b.get('is_uchak'):
            return 'bill_print.html'
        kind = str(b.get('kind') or '').strip().lower()
        if kind in ('sale', 'estimate'):
            return 'bill_print.html'
        # Presence (not value) mirrors the template's `!== undefined` test.
        if 'making_total' in b or 'makingTotal' in b:
            return 'bill_print.html'
        mode = str(b.get('_billMode') or b.get('bill_mode') or '').strip().lower()
        if mode == 'gst':
            return 'bill_print.html'
        try:
            for _k in ('gstAmount', 'gst_amount', 'cgst', 'sgst'):
                if float(b.get(_k) or 0) > 0:
                    return 'bill_print.html'
        except Exception:
            pass
        return 'bill_print_legacy.html'
    except Exception:
        return 'bill_print.html'


class AurumAPI:
    def __init__(self):
        # ── DB Path Resolution ────────────────────────────────────────
        # Priority:
        #   1. Saved preference (db_path.txt next to EXE)
        #   2. Default: <EXE folder>/database/aurum_local.db
        #   3. If neither exists: show folder picker

        if getattr(sys, 'frozen', False):
            _exe_dir = os.path.dirname(sys.executable)
        else:
            _exe_dir = os.path.abspath('.')

        _pref_file = os.path.join(_exe_dir, 'db_path.txt')

        def _pick_db_folder():
            """Show folder picker dialog — no tkinter needed."""
            try:
                import ctypes
                co  = ctypes.windll.ole32
                co.CoInitialize(None)
                buf = ctypes.create_unicode_buffer(260)
                # Use SHBrowseForFolder via shell32
                from ctypes import wintypes
                shell32 = ctypes.windll.shell32
                # In-process dialog first (no console flash), hidden
                # powershell only if CLR dialogs are unavailable.
                native = _native_pick_folder("Select folder to store AurumOS database")
                if native is not None:
                    return native if native else None
                r = _powershell_hidden(
                    ['powershell', '-Command',
                     '[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")|Out-Null;'
                     '$f=New-Object System.Windows.Forms.FolderBrowserDialog;'
                     '$f.Description="Select folder to store AurumOS database";'
                     '$f.ShowNewFolderButton=$true;'
                     'if($f.ShowDialog() -eq "OK"){$f.SelectedPath}else{""}'],
                    timeout=60
                )
                chosen = r.stdout.strip()
                return chosen if chosen else None
            except Exception as _pe:
                LOG(f"[DB] Picker error: {_pe}")
                return None

        # 1. Check saved preference
        _db_path = None
        if os.path.exists(_pref_file):
            try:
                _saved = open(_pref_file, 'r', encoding='utf-8').read().strip()
                if _saved and os.path.isdir(os.path.dirname(_saved)):
                    _db_path = _saved
                    LOG(f"[DB] Using saved path: {_db_path}")
            except Exception:
                pass

        # 2. Default path
        if not _db_path:
            _default_dir = os.path.join(_exe_dir, 'database')
            _default_db  = os.path.join(_default_dir, 'aurum_local.db')
            # If default DB already exists — use it silently
            if os.path.exists(_default_db):
                _db_path = _default_db
                LOG(f"[DB] Using default path: {_db_path}")

        # 3. First run — show picker
        if not _db_path:
            LOG("[DB] First run — showing folder picker")
            _chosen_dir = _pick_db_folder()
            if _chosen_dir and os.path.isdir(_chosen_dir):
                _db_path = os.path.join(_chosen_dir, 'aurum_local.db')
            else:
                # User cancelled — use default
                _default_dir = os.path.join(_exe_dir, 'database')
                _db_path     = os.path.join(_default_dir, 'aurum_local.db')
                LOG(f"[DB] Picker cancelled — using default: {_db_path}")

        # Ensure directory exists and save preference
        os.makedirs(os.path.dirname(_db_path), exist_ok=True)
        try:
            open(_pref_file, 'w', encoding='utf-8').write(_db_path)
        except Exception:
            pass

        LOG(f"[DB] Database path: {_db_path}")
        try:
            self.db = DBManager(_db_path)
        except TypeError:
            # DBManager doesn't accept a path arg — use env var approach
            os.environ['AURUM_DB_PATH'] = _db_path
            self.db = DBManager()
        LOG(f"[DB] DBManager initialized, setup_done={self.db.is_setup_done()}")

        # ── SUBSCRIPTION MANAGER ────────────────────────────────────────
        self.sub = SubscriptionManager()
        try:
            api_base = self._get_license_check_url()
            self.sub.set_api_base(api_base)
        except Exception:
            pass
        LOG("[SUB] SubscriptionManager initialized")

        # ── RENEWAL GRACE TIMESTAMP ────────────────────────────────────
        self._renewed_at = 0

        # Ensure backup dir exists
        self.ensure_backup_structure()

        # ── BASTION: EXE integrity check (Layer 9) ─────────────────────
        # Re-enabled: bastion_verify_exe() no longer self-captures the first
        # run as trusted (that is what falsely suspended rebuilt shops —
        # every PyInstaller build hashes differently). Trust now comes from
        # exe_trusted_hash.txt written by build.py, and the check fails OPEN
        # on missing metadata, dev mode, or any error — it only suspends on
        # a confirmed mismatch. Runs off-thread so a slow disk never
        # delays startup.
        def _exe_check():
            try:
                ok = self.db.bastion_verify_exe()
                LOG(f"[BASTION] EXE integrity: {'OK' if ok else 'MISMATCH'}")
            except Exception as _ee:
                LOG(f"[BASTION] EXE check skipped: {_ee}")
        threading.Thread(target=_exe_check, daemon=True).start()

        # ── SHOP NETWORK: this PC serves the shop, or joins one ───────────
        # The main PC listens so staff PCs can verify its address, enrol, and
        # have their logins checked here rather than on their own machine.
        # A staff PC starts nothing; it only ever calls out.
        self._start_cluster()

        # ── PERMISSION GUARD ──────────────────────────────────────────────
        # Up to now a staff member's limits were the sidebar choosing not to
        # draw things. The role itself came from the address bar, so editing
        # the URL restored the full menu, and every method here stayed
        # callable from the developer console regardless. This makes the
        # bridge refuse work the signed-in role has no business doing.
        # The policy sits in core/permissions.py, which auto-updates, so a
        # rule can be tightened without rebuilding the EXE.
        try:
            from core.permissions import install_guard
            _guarded = install_guard(
                self,
                on_denied=lambda method, role: LOG(
                    f"[PERM] refused {method} for role={role}"))
            LOG(f"[PERM] Guard active on {_guarded} bridge methods")
        except Exception as _pe:
            ERR(f"[PERM] Guard NOT installed: {_pe}")

        # ── HEALTH DASHBOARD: auto-provision, zero manual setup ───────────
        # Runs once per PC, in the background. If already provisioned,
        # this is a no-op. Never blocks startup -- happens silently.
        def _auto_provision():
            try:
                result = self.db.auto_provision_health_key('https://aurum-os-admin.vercel.app/')
                LOG(f"[HEALTH] auto-provision: {result}")
            except Exception as _ape:
                LOG(f"[HEALTH] auto-provision error: {_ape}")
        threading.Thread(target=_auto_provision, daemon=True).start()

        # ── BASTION REAL-TIME ENFORCEMENT ───────────────────────────────
        # Polls suspension status every 3s. The instant it flips True,
        # forces the lock screen into the CURRENTLY OPEN window -- no
        # waiting for the next login or next save attempt.
        self._app_closing = False

        def _bastion_watch():
            while True:
                if self._app_closing:
                    break
                try:
                    st = self.db.bastion_get_status()
                    if st.get('suspended') and self._window and not self._app_closing:
                        payload = __import__('json').dumps(st)
                        try:
                            self._window.evaluate_js(
                                "if(typeof bastionShow==='function'){bastionShow(" + payload + ");}"
                            )
                        except Exception:
                            pass  # window may have just closed mid-call -- safe to ignore
                except Exception as _bwe:
                    LOG(f"[BASTION] watch error: {_bwe}")
                time.sleep(1)
        threading.Thread(target=_bastion_watch, daemon=True).start()


        # Layer 10: Generate session token
        try:
            self.db._generate_session_token()
            LOG("[SESSION] Session token generated at startup")
        except Exception as _se:
            LOG(f"[SESSION] Token generation skipped: {_se}")

        # ── BASTION SETTINGS SYNC: pull admin dashboard toggles ──
        try:
            from core import bastion_sync
            bastion_sync.start_sync(db_manager=self.db, interval=30)
            LOG("[BASTION_SYNC] Settings sync started (30s interval)")
        except Exception as _bs:
            LOG(f"[BASTION_SYNC] Start skipped: {_bs}")

        # ── BASTION AI: Start background monitor ─────────────────
        try:
            self.bastion = BastionAI(self.db)
            self.bastion.start()
            LOG("[BASTION_AI] Background monitor started")
        except Exception as _be:
            LOG(f"[BASTION_AI] Start skipped: {_be}")
            self.bastion = BastionAI(self.db)

        self._window = None
        self.TEMP_KEY = "aurum-dev-2026"
        self.tag_factory = TagFactory()
        try:
            self.ai = AISupport()
        except Exception as _ae:
            LOG(f"[AI] init skipped: {_ae}")
            self.ai = AISupport()
        global _api_db_ref
        _api_db_ref = self.db
        self._session_role     = None
        self._session_username = None
        self._session_bundles  = []
        self._login_attempts   = 0
        self._lockout_until    = None
        self._lockout_tier     = 0
        self._attempt_history  = []
        # Enterprise progressive rate-limit tiers
        # (max_attempts_before_tier, cooldown_seconds, severity)
        self._RATE_TIERS = [
            # Tier 0: first 3 attempts — no delay
            {"threshold": 3,  "cooldown": 0,    "severity": "none"},
            # Tier 1: attempt 4 — 30s cooldown
            {"threshold": 4,  "cooldown": 30,   "severity": "warning"},
            # Tier 2: attempt 5 — 2min cooldown
            {"threshold": 5,  "cooldown": 120,  "severity": "caution"},
            # Tier 3: attempt 6 — 5min cooldown
            {"threshold": 6,  "cooldown": 300,  "severity": "elevated"},
            # Tier 4: attempt 7 — 15min cooldown
            {"threshold": 7,  "cooldown": 900,  "severity": "high"},
            # Tier 5: attempt 8-9 — 30min cooldown + lock code
            {"threshold": 9,  "cooldown": 1800, "severity": "critical"},
            # Tier 6: attempt 10+ — permanent lock (admin reset)
            {"threshold": 10, "cooldown": -1,   "severity": "permanent"},
        ]
        self._last_update_files = []
        self._update_state = {"pct": 0, "msg": "", "done": False, "ok": False}
        self._load_lockout_state()
        LOG("[API] AurumAPI initialized")

    def ensure_backup_structure(self):
        secret_dir = r"C:\ProgramData\AurumOS"
        try:
            if not os.path.exists(secret_dir):
                os.makedirs(secret_dir, exist_ok=True)
                LOG(f"[BACKUP] Created directory: {secret_dir}")
            import subprocess
            result = subprocess.run(['attrib', '+H', secret_dir], capture_output=True, text=True)
            if result.returncode == 0:
                LOG(f"[BACKUP] Backup dir hidden: {secret_dir}")
            else:
                ERR(f"[BACKUP] Failed to hide folder: {result.stderr}")
        except Exception as e:
            ERR(f"[BACKUP] Dir setup failed: {e}")

    def _detect_my_lan_ip(self):
        """
        Returns this PC's local LAN IP (e.g. '192.168.1.1') by opening a
        throwaway UDP socket toward the shop router -- doesn't actually
        send any data, just asks the OS which local interface/IP it
        WOULD use. Works without internet access. Returns '' on failure.
        """
        import socket as _sock
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            try:
                s.connect(('192.168.1.1', 1))
                ip = s.getsockname()[0]
            finally:
                s.close()
            return ip
        except Exception as e:
            LOG(f"[SYNC] Could not detect LAN IP: {e}")
            return ''

    def set_window(self, window):
        self._window = window
        global _api_db_ref
        _api_db_ref = self.db
        self._start_remote_reset_poller()

    def _audit(self, action: str, detail: str = '', category: str = 'general'):
        try:
            user = getattr(self, '_session_username', 'system') or 'system'
            self.db.add_audit_log(action, detail, user, category)
        except Exception as e:
            LOG(f"[AUDIT ERR] {e}")

    # -- NAVIGATION ------------------------------------------------------------
    def navigate(self, html_file):
        try:
            raw = str(html_file or '')
            # Pages keep query strings (?role=&type=) — strip them for the check.
            name = os.path.basename(raw.split('?')[0].split('#')[0])
            if not name or name.startswith('.') or '/' in raw.replace('\\', '/').strip('/').rsplit(name, 1)[0]:
                return {"status": "error", "message": f"bad page: {raw[:60]}"}
            target_path = os.path.join(get_asset_path("ui"), name)
            if not os.path.isfile(target_path):
                ERR(f"[NAV] missing page: {name}")
                return {"status": "error", "message": f"page not found: {name}"}
            ui_dir = get_asset_path("ui")
            target_path = os.path.join(ui_dir, name)
            url = Path(target_path).as_uri()
            if self._window:
                safe_url = url.replace("'", "\\'")
                self._window.evaluate_js(f"window.location.href='{safe_url}';")
            return {"status": "success", "page": name}
        except Exception as e:
            ERR(f"[NAV] Error: {e}")
            return {"status": "error", "message": str(e)}

    def open_url(self, url):
        """Open an external link in the system browser.

        Pages (expiry, revoked, upgrade prompts) prefer this over
        window.open, which has no effect inside pywebview. Only http/https
        ever leave the app — anything else is refused.
        """
        try:
            import webbrowser
            u = str(url or '').strip()
            if not u.lower().startswith(('http://', 'https://', 'mailto:')):
                return {"status": "error", "message": "Only web links can be opened."}
            webbrowser.open(u)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def ui_stamp(self, page):
        """Fingerprint of the screen file this app is actually serving.

        Screens ship through the updater and take effect on the next screen
        opened — but a page that stays open (like a login stuck on sign-in
        failures) keeps running its old copy forever, so fixes are tested
        against yesterday's code without anyone realising. The page polls
        this cheap stat call and reloads itself when a newer copy lands.
        Only the file name is accepted; directories never leave ui/.
        """
        try:
            raw = str(page or '')
            name = os.path.basename(raw)
            if not name or name.startswith('.') or raw != name:
                return {"status": "error", "message": "bad page"}
            target = get_asset_path(os.path.join('ui', name))
            st = os.stat(target)
            return {"status": "success", "page": name,
                    "mtime": st.st_mtime, "size": st.st_size}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # -- SESSION ---------------------------------------------------------------
    def get_session(self):
        if self._session_role:
            return {"status": "ok", "role": self._session_role, "username": self._session_username or ""}
        return {"status": "no_session", "role": None}

    # -- DASHBOARD -------------------------------------------------------------
    def get_dynamic_greeting(self):
        try:
            h = datetime.now().hour
            if 5 <= h < 12:    greeting = "Good Morning"
            elif 12 <= h < 17: greeting = "Good Afternoon"
            elif 17 <= h < 22: greeting = "Good Evening"
            else:               greeting = "Welcome Back"
            owner_name = self.db.get_config("owner_name", "")
            if not owner_name:
                try: owner_name = self.db.get_inventory_stats().get("owner_name") or ""
                except: pass
            if not owner_name:
                owner_name = self._session_username or "Director"
            biz_name = self.db.get_config("business_name", "") or owner_name
            return {"status": "success", "greeting_prefix": greeting, "owner_title": biz_name, "business_name": biz_name}
        except Exception as e:
            ERR(f"[GREETING] {e}")
            return {"status": "error", "greeting_prefix": "Welcome", "owner_title": "Director"}

    def get_metal_rates(self):
        """Editable per-gram rates shown on the retail dashboard."""
        try:
            def _f(key, default):
                try:
                    return float(self.db.get_config(key, default) or default)
                except (TypeError, ValueError):
                    return float(default)
            return {"status": "success", "gold_22k": _f('gold_rate_22k', 0),
                    "gold_24k": _f('gold_rate_24k', 0), "silver": _f('silver_rate', 0)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def set_metal_rates(self, data):
        """Save editable per-gram rates from the retail dashboard."""
        try:
            d = data or {}
            for key, field in (('gold_rate_22k', 'gold_22k'), ('gold_rate_24k', 'gold_24k'),
                               ('silver_rate', 'silver')):
                if field in d:
                    try:
                        self.db.set_config(key, float(d[field] or 0))
                    except (TypeError, ValueError):
                        pass
            return self.get_metal_rates()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # -- NOTIFICATION CENTER -------------------------------------------------
    # Owner inbox: staff-bill alerts (local) + AurumOS admin messages (remote).

    # -- URGENT MESSAGES (admin → this terminal) -------------------------------
    # Python daemon is the always-on receiver: persist + forward to any open
    # page. Display rules live in ui/urgent.js. Informational only — NEVER
    # locks billing/stock (only status_change revoked/expired does that).

    def urgent_list(self, limit=100):
        try:
            return {"status": "success",
                    "messages": self.db.get_urgent_messages(limit),
                    "unread": self.db.urgent_unread_count()}
        except Exception as e:
            return {"status": "error", "message": str(e), "messages": []}

    def urgent_mark_read(self, mid):
        try:
            return {"status": "success" if self.db.mark_urgent_read(mid) else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def urgent_mark_all_read(self):
        try:
            return {"status": "success" if self.db.mark_all_urgent_read() else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def urgent_delete(self, mid):
        try:
            return {"status": "success" if self.db.delete_urgent_message(mid) else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def urgent_ingest(self, msg):
        """JS-side ingest: target-filter + dedupe store. Returns stored flag."""
        try:
            if not isinstance(msg, dict) or msg.get('id') is None:
                return {"status": "error", "stored": False}
            key = str(getattr(self, '_sse_key', '') or '').strip().upper()
            if not key:
                try:
                    key = str(self.db.get_config('license_key', '') or '').strip().upper()
                except Exception:
                    key = ''
            applies = False
            if str(msg.get('broadcast') or '').lower() == 'all':
                applies = True
            elif msg.get('key') is not None:
                applies = (str(msg.get('key') or '').strip().upper() == key)
            else:
                applies = DBManager.urgent_applies(msg, key)
            if not applies:
                return {"status": "ignored", "stored": False}
            stored = self.db.store_urgent_message(msg)
            return {"status": "success", "stored": bool(stored)}
        except Exception as e:
            return {"status": "error", "message": str(e), "stored": False}

    def _sse_on_urgent_message(self, data):
        """Python-side receiver: dedupe + persist, then poke the open page."""
        try:
            if not isinstance(data, dict):
                return
            is_new = self.db.store_urgent_message(data)
            self._forward_urgent_to_ui(data)
            LOG(f"[URGENT] id={data.get('id')} priority={data.get('priority')} new={is_new}")
        except Exception as e:
            ERR(f"[URGENT] receive: {e}")

    def _forward_urgent_to_ui(self, data):
        """Best-effort push to the open webview page (dedupe happens in JS)."""
        try:
            import json as _js
            if getattr(self, '_window', None):
                payload = _js.dumps(data or {})
                self._window.evaluate_js(
                    "window.dispatchEvent(new CustomEvent('aurum-urgent',"
                    " {detail:" + payload + "}));")
        except Exception:
            pass

    def urgent_fetch_missed(self):
        """Catch-up: GET /api/messages, keep what targets THIS terminal.
        Silent on network error. Returns count of newly stored messages."""
        try:
            import urllib.request as _url, urllib.parse as _qp, json as _js
            key = str(getattr(self, '_sse_key', '') or '').strip().upper()
            if not key:
                try:
                    key = str(self.db.get_config('license_key', '') or '').strip().upper()
                except Exception:
                    key = ''
            base = self._get_server_url().rstrip('/')
            url = base + '/api/messages?key=' + _qp.quote(key) + '&limit=30'
            req = _url.Request(url, headers={'User-Agent': 'AurumOS-Client'})
            with _url.urlopen(req, timeout=10) as resp:
                data = _js.loads(resp.read().decode('utf-8', 'replace'))
            items = data.get('messages', []) if isinstance(data, dict) else []
            fresh = 0
            for m in (items or [])[:30]:
                if not isinstance(m, dict):
                    continue
                if not DBManager.urgent_applies(m, key):
                    continue
                if self.db.store_urgent_message(m):
                    fresh += 1
                    self._forward_urgent_to_ui(m)
            if fresh:
                LOG(f"[URGENT] missed-fetch: {fresh} new")
            return {"status": "success", "new": fresh}
        except Exception:
            # Offline counters and shop PCs without internet hit this on every
            # poll; logging each timeout buries real signal in the log file.
            # The contract is "silent on network error" — keep it silent.
            return {"status": "offline", "new": 0}

    def _urgent_watchdog(self):
        """Polling fallback: SSE silent >60s → fetch missed every 5 min."""
        import time as _t
        last_poll = 0.0
        while getattr(self, '_urgent_watch', False):
            try:
                _t.sleep(30)
                if not getattr(self, '_urgent_watch', False):
                    break
                lst = getattr(self, '_sse_listener', None)
                if lst is None:
                    continue
                idle = lst.seconds_since_activity()
                now = _t.time()
                if idle > 60 and (now - last_poll) > 300:
                    last_poll = now
                    try:
                        self.urgent_fetch_missed()
                    except Exception:
                        pass
            except Exception:
                pass

    def get_notifications(self, limit=20):
        """Newest-first inbox. Best-effort admin sync first (silent offline)."""
        try:
            self._sync_admin_notices()
        except Exception:
            pass
        try:
            return {"status": "success",
                    "notifications": self.db.get_notifications(limit)}
        except Exception as e:
            return {"status": "error", "message": str(e), "notifications": []}

    def mark_notification_read(self, nid):
        try:
            return {"status": "success" if self.db.mark_notification_read(nid) else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def mark_all_notifications_read(self):
        try:
            return {"status": "success" if self.db.mark_all_notifications_read() else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _notify_staff_bill(self, vch_id, customer, amount):
        """Owner gets an inbox alert whenever STAFF raises a bill."""
        try:
            if getattr(self, '_session_role', '') != 'staff':
                return
            user = getattr(self, '_session_username', 'staff') or 'staff'
            try:
                amt = float(amount or 0)
            except (TypeError, ValueError):
                amt = 0.0
            self.db.push_notification(
                'bill', f"Bill {vch_id} by {user}",
                f"{user} billed Rs. {amt:,.2f} to {customer or 'Walk-in'}.",
                'voucher_history.html')
        except Exception as e:
            ERR(f"[NOTIF] staff bill: {e}")

    def _sync_admin_notices(self):
        """Pull AurumOS owner messages. Silent no-op when offline."""
        try:
            import urllib.request as _url, urllib.parse as _qp, json as _js
            base = (self.db.get_config('api_base_url', '') or
                    'https://aurum-os-admin.vercel.app').rstrip('/')
            shop = self.db.get_config('shop_id', '') or ''
            url = base + '/api/notices?shop_id=' + _qp.quote(str(shop))
            req = _url.Request(url, headers={'User-Agent': 'AurumOS-Client'})
            with _url.urlopen(req, timeout=8) as resp:
                data = _js.loads(resp.read().decode('utf-8', 'replace'))
            items = data if isinstance(data, list) else data.get('notices', [])
            for n in (items or []):
                if not isinstance(n, dict):
                    continue
                nid = str(n.get('id') or n.get('title') or '')
                if not nid:
                    continue
                self.db.push_notification(
                    str(n.get('type') or 'admin'),
                    str(n.get('title') or 'AurumOS'),
                    str(n.get('body') or n.get('message') or ''),
                    str(n.get('page') or ''), 'admin', nid)
        except Exception:
            pass

    def _retail_dashboard_data(self, conn, today_str):
        """Retail dashboard block: sales, rates, pay split, dues, stock value,
        old gold, low stock, top customers, recent bills, category split."""
        out = {"today_amount": 0.0, "today_bills": 0, "pay_split": {"cash": 0.0, "upi": 0.0, "card": 0.0},
               "rates": {"gold_10g": 0.0, "gold_g": 0.0},
               "stock_value": 0.0, "old_gold": {"wt": 0.0, "value": 0.0},
               "low_stock": [], "top_customers": [], "recent_bills": [],
               "category_split": {"labels": [], "values": []}}
        # Gold rate comes from billing itself: latest non-zero bill rate.
        # Whenever billing uses a new rate, the dashboard follows automatically.
        try:
            rrow = conn.execute(
                "SELECT gold_rate FROM sales_history WHERE COALESCE(gold_rate,0)>0 "
                "ORDER BY id DESC LIMIT 1").fetchone()
            try:
                g10 = float(rrow['gold_rate'] or 0) if rrow else 0.0
            except (TypeError, ValueError):
                g10 = 0.0
            out["rates"] = {"gold_10g": round(g10, 2), "gold_g": round(g10 / 10.0, 2)}
        except Exception:
            pass
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(sales_history)").fetchall()]
            has_pay = 'payment_mode' in cols
            pay_expr = "LOWER(COALESCE(payment_mode,'cash'))" if has_pay else "'cash'"
            ogv_expr = "COALESCE(old_gold_value,0)" if 'old_gold_value' in cols else "0"
            ogw_expr = "COALESCE(old_gold_wt,0)" if 'old_gold_wt' in cols else "0"
            trows = conn.execute(
                f"SELECT total_amount, {pay_expr} AS pay, COALESCE(fine_995,0) AS f9, "
                f"COALESCE(fine_dhal,0) AS fd, COALESCE(gold_rate,0) AS gr, {ogv_expr} AS ogv, "
                f"{ogw_expr} AS ogw FROM sales_history WHERE date=? "
                "AND UPPER(TRIM(status)) NOT IN ('ESTIMATE','CREDIT')", (today_str,)).fetchall()
            for r in trows:
                try:
                    amt = float(r['total_amount'] or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                out["today_amount"] += amt
                out["today_bills"] += 1
                pay = str(r['pay'] or 'cash').lower()
                if pay not in ('cash', 'upi', 'card'):
                    pay = 'cash'
                out["pay_split"][pay] += amt
                try:
                    f99 = float(r['f9'] or 0) + float(r['fd'] or 0)
                    gr = float(r['gr'] or 0)
                    ogv = float(r['ogv'] or 0) + f99 * gr / 10.0
                    ogw = float(r['ogw'] or 0) + f99
                except (TypeError, ValueError):
                    ogv, ogw = 0.0, 0.0
                out["old_gold"]["wt"] += ogw
                out["old_gold"]["value"] += ogv
            out["today_amount"] = round(out["today_amount"], 2)
            for k in out["pay_split"]:
                out["pay_split"][k] = round(out["pay_split"][k], 2)
            out["old_gold"]["wt"] = round(out["old_gold"]["wt"], 3)
            out["old_gold"]["value"] = round(out["old_gold"]["value"], 2)
        except Exception:
            pass
        try:
            rate10 = out["rates"]["gold_10g"]
            srow = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(nt_wt,gr_wt,0)*COALESCE(touch,0)/100.0),0) AS fw "
                "FROM stock_inventory WHERE COALESCE(gr_wt,0)>0 AND COALESCE(touch,0)>0").fetchone()
            try:
                fw = float(srow['fw'] or 0) if srow else 0.0
            except (TypeError, ValueError):
                fw = 0.0
            out["stock_value"] = round(fw * rate10 / 10.0, 2)
            out["stock_fine_wt"] = round(fw, 3)
        except Exception:
            pass
        try:
            out["low_stock"] = [dict(r) for r in conn.execute(
                "SELECT COALESCE(it_code,'—') AS code, COALESCE(it_name,'') AS name, "
                "COALESCE(SUM(pcs),0) AS pcs, COALESCE(SUM(gr_wt),0) AS wt "
                "FROM stock_inventory GROUP BY COALESCE(it_code,'—') "
                "HAVING SUM(COALESCE(pcs,0))<=2 ORDER BY SUM(COALESCE(pcs,0)) ASC LIMIT 8").fetchall()]
        except Exception:
            pass
        try:
            out["top_customers"] = [dict(r) for r in conn.execute(
                "SELECT customer AS name, COUNT(*) AS bills, ROUND(SUM(COALESCE(total_amount,0)),2) AS total "
                "FROM sales_history WHERE UPPER(TRIM(status)) NOT IN ('ESTIMATE') "
                "GROUP BY customer ORDER BY SUM(COALESCE(total_amount,0)) DESC LIMIT 5").fetchall()]
            out["recent_bills"] = [dict(r) for r in conn.execute(
                "SELECT vch_id, customer, status, date, ROUND(COALESCE(total_amount,0),2) AS total "
                "FROM sales_history WHERE UPPER(TRIM(status)) NOT IN ('ESTIMATE') "
                "ORDER BY id DESC LIMIT 8").fetchall()]
        except Exception:
            pass
        try:
            band_rows = conn.execute(
                "SELECT items, COALESCE(total_amount,0) AS total_amount FROM sales_history "
                "WHERE date>=date('now','-30 days') "
                "AND UPPER(TRIM(status)) NOT IN ('ESTIMATE','CREDIT')").fetchall()
            bands = {}
            for br in band_rows:
                try:
                    items = json.loads(br['items'] or '[]')
                except (TypeError, ValueError):
                    continue
                if not isinstance(items, list):
                    continue
                try:
                    bill_total = float(br['total_amount'] or 0)
                except (TypeError, ValueError):
                    bill_total = 0.0
                # Scale item amounts to the bill total so categories always
                # sum to the actual billed Rs (discount-adjusted).
                sum_amt = 0.0
                for it in items:
                    if isinstance(it, dict):
                        try:
                            sum_amt += float(it.get('amount') or 0)
                        except (TypeError, ValueError):
                            pass
                factor = (bill_total / sum_amt) if sum_amt > 0 else 0.0
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    try:
                        t = float(it.get('touch') or 0)
                    except (TypeError, ValueError):
                        t = 0.0
                    try:
                        a = float(it.get('amount') or 0) * factor
                    except (TypeError, ValueError):
                        a = 0.0
                    if t >= 99:
                        b = '24K'
                    elif t >= 91:
                        b = '22K'
                    elif t >= 74:
                        b = '18K'
                    elif t > 0:
                        b = 'Other gold'
                    else:
                        b = 'Silver/Others'
                    bands[b] = bands.get(b, 0.0) + a
            order = ['24K', '22K', '18K', 'Other gold', 'Silver/Others']
            out["category_split"] = {"labels": [b for b in order if bands.get(b, 0) > 0],
                                     "values": [round(bands[b], 2) for b in order if bands.get(b, 0) > 0]}
        except Exception:
            pass
        return out

    def get_live_command_metrics(self):
        try:
            from datetime import timedelta
            today_str = datetime.now().strftime('%Y-%m-%d')
            def safe(fn, default):
                try: return fn()
                except Exception as e:
                    ERR(f"[DASHBOARD] {e}"); return default
            db_stats = safe(self.db.get_inventory_stats,
                            {"net":0,"pcs":0,"uchak_pcs":0,"packets":0,"owner_name":None})
            spine = [(datetime.now()-timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6,-1,-1)]
            def item_fine(items):
                total = 0.0
                for item in items if isinstance(items, list) else []:
                    if not isinstance(item, dict):
                        continue
                    try:
                        if item.get('fine') not in (None, ''):
                            total += float(item.get('fine') or 0)
                        else:
                            total += float(item.get('weight') or item.get('gr_wt') or 0) * float(item.get('touch') or 0) / 100
                    except (TypeError, ValueError):
                        continue
                return total

            def resolve_fine(vch_id, db_fine, total_amt, parsed_items):
                """Grams only. R- always from items. Any Rs-contaminated
                value (equals Rs total, or absurd grams) falls back to items."""
                try:
                    db_f = float(db_fine or 0)
                except (TypeError, ValueError):
                    db_f = 0.0
                try:
                    amt = float(total_amt or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if str(vch_id or '').upper().startswith('R-'):
                    return item_fine(parsed_items)
                if amt > 500 and abs(db_f - amt) < 0.01:
                    return item_fine(parsed_items)
                if db_f > 500:
                    return item_fine(parsed_items)
                return db_f

            rows = safe(lambda: list(self.db._get_connection().__enter__().execute(
                "SELECT date, vch_id, status, total_amount, collected_fine, items "
                "FROM sales_history WHERE date>=? AND UPPER(TRIM(status)) NOT IN ('ESTIMATE','CREDIT')", (spine[0],)).fetchall()), [])
            rev_map = {}
            fine_map = {}
            for row in rows:
                date_key = row['date']
                rev_map[date_key] = rev_map.get(date_key, 0.0) + float(row['total_amount'] or 0)
                try:
                    parsed_items = json.loads(row['items'] or '[]')
                except (TypeError, ValueError):
                    parsed_items = []
                fine_value = resolve_fine(row['vch_id'], row['collected_fine'], row['total_amount'], parsed_items)
                fine_map[date_key] = fine_map.get(date_key, 0.0) + fine_value
            chart_labels=[]; chart_revenue=[]; chart_fine=[]
            for d in spine:
                try: label = datetime.strptime(d,'%Y-%m-%d').strftime('%a %d')
                except: label = d[-5:]
                chart_labels.append(label)
                chart_revenue.append(round(rev_map.get(d,0.0),2))
                chart_fine.append(round(fine_map.get(d,0.0),3))
            credit_risk_list=[]
            dues_total = 0.0
            for cl in self.db.get_all_clients():
                bal = self.get_client_balances(cl['name'])
                cash_out = float(bal.get('cash',0.0))
                if cash_out > 0:
                    dues_total += cash_out
                    ratio = (cash_out/cash_lim*100) if cash_lim>0 else 0
                    credit_risk_list.append({
                        "account_name": cl['name'], "outstanding": cash_out,
                        "percentage": round(ratio,0),
                        "status_class": "status-danger" if ratio>=90 else "status-warn",
                        "badge_label": f"{round(ratio,0)}% {'Limit' if ratio>=90 else 'Bound'}"
                    })
            credit_risk_list = sorted(credit_risk_list,key=lambda x:x['outstanding'],reverse=True)[:3]
            raw_logs = safe(lambda: self.db.get_audit_logs(limit=8), [])
            cat_icons={'billing':'&#128203;','stock':'&#128230;','print':'&#128424;','auth':'&#128274;','general':'&#9679;'}
            live_audit_logs=[]
            for r in raw_logs:
                ts_str=r.get('ts','')
                try: time_str=ts_str.split(' ')[1][:8] if ' ' in ts_str else ts_str[:8]
                except: time_str='--:--:--'
                icon=cat_icons.get(r.get('category','general'),'&#9679;')
                msg=f"{icon} <strong>{r.get('action','')}</strong>"
                if r.get('detail'): msg+=f" &mdash; {r['detail']}"
                live_audit_logs.append({"time":time_str,"msg":msg})
            if not live_audit_logs:
                live_audit_logs=[{"time":"--:--:--","msg":"&#9679; No activity recorded yet."}]
            with self.db._get_connection() as conn:
                fine_rows=conn.execute(
                    "SELECT vch_id, status, collected_fine, total_amount, items FROM sales_history "
                    "WHERE UPPER(TRIM(status)) NOT IN ('ESTIMATE','CREDIT')"
                ).fetchall()
                total_fine = 0.0
                for row in fine_rows:
                    try:
                        parsed_items = json.loads(row['items'] or '[]')
                    except (TypeError, ValueError):
                        parsed_items = []
                    total_fine += resolve_fine(row['vch_id'], row['collected_fine'], row['total_amount'], parsed_items)
                inv_rows=conn.execute(
                    "SELECT CAST(touch AS TEXT) || '%' as it_code, "
                    "COALESCE(SUM(gr_wt),0) as total_wt FROM stock_inventory "
                    "WHERE gr_wt>0 AND touch IS NOT NULL AND touch>0 "
                    "AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%' OR tag_id LIKE 'OPENING-%') "
                    "GROUP BY CAST(touch AS TEXT) ORDER BY total_wt DESC LIMIT 10"
                ).fetchall()
                try:
                    retail = self._retail_dashboard_data(conn, today_str)
                except Exception as _re:
                    ERR(f"[DASHBOARD-RETAIL] {_re}")
                    retail = {}
            dues_total = round(dues_total, 2)
            if not isinstance(retail, dict):
                retail = {}
            # Stock without a recorded purity cannot be valued by fine weight
            # and is silently dropped from stock_value above. Report it
            # separately (weight and pieces) instead of valuing it at zero.
            try:
                with self.db._get_connection() as _uc:
                    urow = _uc.execute(
                        "SELECT COALESCE(SUM(COALESCE(gr_wt,0)),0) AS g, "
                        "COUNT(*) AS n FROM stock_inventory "
                        "WHERE COALESCE(gr_wt,0)>0 AND COALESCE(touch,0)<=0"
                        ).fetchone()
                unvalued = {"gross": round(float(urow['g'] or 0), 3),
                            "pcs": int(urow['n'] or 0)} if urow else \
                           {"gross": 0.0, "pcs": 0}
            except Exception:
                unvalued = {"gross": 0.0, "pcs": 0}
            # Hallmark summary for the dashboard widget: counts only, never
            # the pending lists, so this stays cheap on every dashboard load.
            try:
                from core import hallmark as _hm
                self.db.ensure_hallmark_tables()
                _hm_out = _hm.dashboard(self.db.hm_pending_stock(2000),
                                        self.db.hm_lots(True, 200))
                hallmark = {
                    "waiting": int(_hm_out['waiting']['pieces']),
                    "out": int(_hm_out['out']['pieces']),
                    "late": int(_hm_out['late']['pieces']),
                    "failed": int(_hm_out['failed']['pieces']),
                    "marked": int(_hm_out['marked']),
                    "saleable_gap": int(_hm_out['saleable_gap']),
                }
            except Exception as _he:
                ERR(f"[DASHBOARD-HM] {_he}")
                hallmark = {}
            return {
                "status":"success",
                "accumulated_sales": rev_map.get(today_str,0.0),
                "total_fine_collected": total_fine,
                "tracked_units": int(db_stats.get("pcs",0)+db_stats.get("uchak_pcs",0)),
                "metallic_weight": float(db_stats.get("net",0.0)),
                "huid_status":"100% Verified","sync_node":"Operational",
                "chart":{"labels":chart_labels,"revenue":chart_revenue,"fine":chart_fine},
                "inventory_chart":{"labels":[r['it_code'] for r in inv_rows],"weights":[round(float(r['total_wt']),3) for r in inv_rows]},
                "risk_monitor":credit_risk_list,"audit_logs":live_audit_logs,
                "retail": retail, "dues_total": round(dues_total, 2),
                "hallmark": hallmark, "stock_unvalued": unvalued
            }
        except Exception as e:
            ERR(f"[DASHBOARD] {e}")
            return {"status":"error","accumulated_sales":0.0,"tracked_units":0,"metallic_weight":0.000}

    def _extract_tag_id(self, data):
        return data.get('tag_id') or data.get('tag') or "N/A"

    # -- PRINTING --------------------------------------------------------------
    def open_print_window(self, html_content):
        """Open print report in new window — mirrors trigger_print_window exactly."""
        try:
            import threading

            # Inject auto-print on load
            inject = (
                "<script>"
                "window.addEventListener('load',function(){"
                "setTimeout(function(){window.print();},600);"
                "});"
                "</script>"
            )
            if '</body>' in html_content:
                html_content = html_content.replace('</body>', inject + '</body>', 1)
            elif '</html>' in html_content:
                html_content = html_content.replace('</html>', inject + '</html>', 1)
            else:
                html_content = html_content + inject

            def open_window():
                try:
                    _wv = webview if webview is not None else __import__('webview')
                    _wv.create_window(
                        "AurumOS — Stock Report",
                        html      = html_content,
                        js_api    = self,
                        width     = 900,
                        height    = 780,
                        resizable = True
                    )
                    LOG("[PRINT_WIN] window created")
                except Exception as e:
                    ERR(f"[PRINT_WIN] {e}")

            threading.Thread(target=open_window, daemon=False).start()
            return {"status": "success"}
        except Exception as e:
            ERR(f"[PRINT_WIN] outer: {e}")
            return {"status": "error", "message": str(e)}

    def _resolve_printer_name(self, item_data=None):
        """Printer the user selected (per-item) falls back to TagFactory default."""
        name = ''
        if isinstance(item_data, dict):
            name = str(item_data.get('printer') or '').strip()
        if not name:
            try:
                name = str(getattr(self.tag_factory, 'PRINTER_NAME', '') or '').strip()
            except Exception:
                name = ''
        return name

    def _apply_printer(self, item_data=None):
        """Point TagFactory at the selected printer (no-op if unavailable)."""
        name = self._resolve_printer_name(item_data)
        if name:
            try:
                self.tag_factory.set_printer(name)
            except Exception as e:
                ERR(f"[PRINT] set_printer({name}) failed: {e}")
        return name

    def _check_printer_status(self, printer_name=''):
        """Replacement for the missing TagFactory.check_printer_status().
        Returns (ok, msg). Only blocks when there is genuinely no usable
        printer — benign driver statuses never stop a print."""
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            installed = {p[2] for p in win32print.EnumPrinters(flags)}
            if not installed:
                return False, "No printer installed on this PC."
            target = (printer_name or '').strip()
            if target and target not in installed:
                try:
                    default = win32print.GetDefaultPrinter()
                except Exception:
                    default = ''
                if default:
                    return True, f"'{target}' not found; using default '{default}'."
                return False, f"Printer '{target}' not found."
            probe = target or ''
            if not probe:
                try:
                    probe = win32print.GetDefaultPrinter()
                except Exception:
                    probe = ''
            if probe:
                try:
                    h = win32print.OpenPrinter(probe)
                    try:
                        info = win32print.GetPrinter(h, 2)
                    finally:
                        win32print.ClosePrinter(h)
                    status = info.get('Status', 0) if isinstance(info, dict) else 0
                    fatal = (win32print.PRINTER_STATUS_OFFLINE
                             | win32print.PRINTER_STATUS_PAPER_OUT
                             | win32print.PRINTER_STATUS_PAPER_JAM
                             | win32print.PRINTER_STATUS_ERROR)
                    if status & fatal:
                        probs = []
                        if status & win32print.PRINTER_STATUS_OFFLINE:   probs.append("offline")
                        if status & win32print.PRINTER_STATUS_PAPER_OUT:  probs.append("out of paper")
                        if status & win32print.PRINTER_STATUS_PAPER_JAM:  probs.append("paper jam")
                        if status & win32print.PRINTER_STATUS_ERROR:      probs.append("error")
                        return False, "Printer " + ", ".join(probs) + "."
                except Exception as pe:
                    LOG(f"[PRINT] status probe skipped: {pe}")
            return True, "Printer ready."
        except Exception as e:
            # win32print missing/failed — never block printing on the check itself
            LOG(f"[PRINT] printer check skipped: {e}")
            return True, f"Printer check skipped ({e})."

    def check_printer_status(self):
        """Exposed to UI: quick readiness check for the default/selected printer."""
        ok, msg = self._check_printer_status(self._resolve_printer_name())
        return {"ok": ok, "ready": ok, "message": msg}

    def print_multiple_tags(self, items_list):
        LOG(f"[PRINT] print_multiple_tags called with {len(items_list)} item(s)")
        try:
            target = self._resolve_printer_name(items_list[0] if items_list else None)
            is_ok, msg = self._check_printer_status(target)
            LOG(f"[PRINT] Printer status: {msg}")
            if not is_ok:
                return {"status":"error","message":f"Printer not ready: {msg}"}
            success_count=0; errors=[]
            for item_data in items_list:
                try:
                    item_data['tag_id'] = self._extract_tag_id(item_data)
                    item_data = normalize_tag_item(item_data)
                    self._apply_printer(item_data)
                    LOG(f"[PRINT] variation={item_data.get('variation')} touch={item_data.get('touch')} "
                        f"wastage={item_data.get('wastage')} gross={item_data.get('gross_wt')} tag={item_data.get('tag_id')}")
                    try:
                        tag_img = self.tag_factory.generate_tag_image(item_data)
                    except Exception as eng_err:
                        ERR(f"[PRINT] Engine failed ({eng_err}), retrying as RING")
                        item_data['variation']='RING'; item_data['size']=item_data.get('size','')
                        tag_img = self.tag_factory.generate_tag_image(item_data)
                    self.tag_factory.print_to_thermal_printer(tag_img)
                    item_id = item_data.get('id')
                    if item_id: self.db.mark_as_tagged(item_id)
                    success_count += 1
                    LOG(f"[PRINT] OK: {item_data.get('tag_id')}")
                except Exception as item_err:
                    ERR(f"[PRINT ERR] {item_data.get('tag_id')}: {item_err}")
                    errors.append(str(item_err))
            if errors and success_count==0:
                return {"status":"error","message":"; ".join(errors[:2])}
            self._audit(f"Tags printed: {success_count}","","print")
            return {"status":"success","count":success_count,"errors":errors,"message":f"Printed {success_count} tag(s)."}
        except Exception as e:
            ERR(f"[PRINT ERR] print_multiple_tags: {e}")
            return {"status":"error","message":f"Print failed: {str(e)}"}

    def print_tag(self, item_data):
        try:
            target = self._resolve_printer_name(item_data)
            is_ok, msg = self._check_printer_status(target)
            LOG(f"[PRINTER] {msg}")
            if not is_ok:
                return {"status":"error","message":msg}
            item_data['tag_id'] = self._extract_tag_id(item_data)
            item_data = normalize_tag_item(item_data)
            self._apply_printer(item_data)
            tag_img = self.tag_factory.generate_tag_image(item_data)
            self.tag_factory.print_to_thermal_printer(tag_img)
            item_id = item_data.get('id')
            if item_id: self.db.mark_as_tagged(item_id)
            return {"status":"success","message":"Tag sent to printer."}
        except Exception as e:
            ERR(f"[PRINT ERR] {e}")
            return {"status":"error","message":str(e)}

    def get_tag_preview(self, item_data):
        try:
            item_data['tag_id'] = self._extract_tag_id(item_data)
            item_data = normalize_tag_item(item_data)
            LOG(f"[PREVIEW] variation={item_data.get('variation')} gross={item_data.get('gross_wt')} "
                f"touch={item_data.get('touch')} wastage={item_data.get('wastage')}")
            try:
                url = self.tag_factory.generate_preview(item_data)
                return {"status":"success","url":url}
            except Exception as eng_err:
                ERR(f"[PREVIEW] Engine failed ({eng_err}), retrying as RING")
                item_data['variation']='RING'; item_data['size']=item_data.get('size','')
                url = self.tag_factory.generate_preview(item_data)
                return {"status":"success","url":url}
        except Exception as e:
            ERR(f"[PREVIEW ERR] {e} | item={item_data}")
            return {"status":"error","message":str(e)}

    def get_available_printers(self):
        """Return installed printers as [{name, is_default}]. Used by tag print UI."""
        try:
            import win32print
            try:
                default = win32print.GetDefaultPrinter()
            except Exception:
                default = ''
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers = []
            for p in win32print.EnumPrinters(flags):
                name = p[2]
                if name:
                    printers.append({"name": name, "is_default": (name == default)})
            LOG(f"[PRINTERS] {len(printers)} found, default={default}")
            return printers
        except Exception as e:
            ERR(f"[PRINTERS] enum failed: {e}")
            return []

    def get_printers(self):
        """Alias for get_available_printers (UI calls either name)."""
        return self.get_available_printers()

    def set_default_printer(self, printer_name):
        """Set the system default printer. Used before bill print."""
        try:
            import win32print
            if printer_name and str(printer_name).strip():
                win32print.SetDefaultPrinter(str(printer_name).strip())
                LOG(f"[PRINT] Default printer set to: {printer_name}")
                return {"status":"success"}
            return {"status":"error","message":"No printer name provided"}
        except Exception as e:
            ERR(f"[PRINT] set_default_printer failed: {e}")
            return {"status":"error","message":str(e)}

    # -- BASTION AI (ChatGPT support assistant) --------------------------------
    def ai_ask(self, question, history=None):
        """Ask the ChatGPT-backed support assistant a question."""
        try:
            return self.ai.ask(question, history)
        except Exception as e:
            ERR(f"[AI] ask failed: {e}")
            return {"status": "error", "message": f"AI error: {e}"}

    def ai_get_config(self):
        """Return AI config state (never exposes the raw key)."""
        try:
            st = self.ai.get_status()
            st["status"] = "success"
            return st
        except Exception as e:
            ERR(f"[AI] get_config failed: {e}")
            return {"status": "error", "message": str(e)}

    def ai_save_config(self, provider, api_key, model):
        """Persist an owner-supplied OpenAI key/model to config.json."""
        try:
            self.ai.save_config(provider, api_key, model)
            return {"status": "success"}
        except Exception as e:
            ERR(f"[AI] save_config failed: {e}")
            return {"status": "error", "message": str(e)}

    # -- LICENSE ---------------------------------------------------------------
    @staticmethod
    def _ssl_context():
        import ssl as _ssl
        try:
            return _ssl.create_default_context()
        except Exception:
            return _ssl._create_unverified_context()

    def _safe_urlopen(self, req, timeout=10):
        """Verified-TLS urlopen. Cert failures raise so the caller treats the
        server as unreachable (keeping cached state) instead of trusting an
        unverified answer."""
        import urllib.request
        import ssl as _ssl
        return urllib.request.urlopen(req, timeout=timeout,
                                      context=self._ssl_context())

    def check_license_revoked(self, network=True):
        # network=False -> only the instant LOCAL checks (.revoked flag + key
        # recovery), skipping the remote POST. Used at startup so the window
        # opens immediately; the live remote revocation check still runs a few
        # seconds later on the background thread (see _bg_check in run_aur_os).
        import urllib.request, json as _j, uuid as _uuid
        base = os.path.dirname(sys.executable) if getattr(sys,"frozen",False) else os.path.abspath(".")
        flag_path = os.path.join(base,"database",".revoked")
        key_path  = os.path.join(base,"database",".license_key")
        _TRANSIENT = ("server_error", "timeout", "error")

        # Resolve key from file or DB (needed for both flagged and non-flagged paths)
        key = ""
        if os.path.exists(key_path):
            try:
                enc = open(key_path,"rb").read()
                key = decrypt_license_key(enc).strip().upper()
            except Exception as e:
                ERR(f"[LICENSE] Key read error: {e}")
        if not key or not _is_valid_key_format(key):
            try:
                db_key = self.db.get_config("license_key","").strip().upper()
                if db_key and _is_valid_key_format(db_key):
                    key = db_key
                    # Re-create key file from DB so future checks work
                    try:
                        enc2 = encrypt_license_key(db_key)
                        open(key_path,"wb").write(enc2)
                    except: pass
            except: pass

        has_flag = os.path.exists(flag_path)
        flag_reason = ""
        if has_flag:
            try:
                flag_reason = open(flag_path,"r").read().strip() or "revoked"
            except:
                flag_reason = "revoked"
            LOG(f"[LICENSE] .revoked flag present: {flag_reason}")

        # If flagged but we have a key and network is available, re-check server
        # so reactivation on the server is picked up immediately
        if has_flag and key and _is_valid_key_format(key) and network:
            LOG("[LICENSE] Re-checking server despite .revoked flag (key exists)")
            try:
                machine_id = str(_uuid.getnode())
                CHECK_URL  = self._get_license_check_url() + "/api/check"
                payload    = _j.dumps({"key":key,"machine_id":machine_id}).encode()
                req = urllib.request.Request(CHECK_URL,data=payload,
                    headers={"Content-Type":"application/json","User-Agent":f"AurumOS/{CURRENT_VERSION}"},
                    method="POST")
                with self._safe_urlopen(req,timeout=10) as resp:
                    data = _j.loads(resp.read().decode())
                if data.get("valid"):
                    LOG(f"[LICENSE] REACTIVATED on server ({key[:10]}...) — clearing .revoked flag")
                    try: os.remove(flag_path)
                    except: pass
                    # Auto-revoke rule enforcement on reactivation
                    try:
                        from core.auto_revoke import enforce as _ar_enforce, get_rules as _ar_rules
                        if _ar_rules():
                            _ar_ctx = {"machine_id": machine_id, "license_key": key}
                            _ar_result = _ar_enforce(_ar_ctx)
                            if _ar_result.get("action") == "revoke":
                                LOG(f"[LICENSE] Auto-revoke triggered on reactivation: {_ar_result.get('violations')}")
                                return "revoked"
                    except Exception as _ar_e:
                        ERR(f"[LICENSE] Auto-revoke reactivation check error: {_ar_e}")
                    return "ok"
                else:
                    reason = data.get("status", flag_reason)
                    LOG(f"[LICENSE] Still revoked on server: {reason}")
                    if reason == "expired" and self._renewed_at and (time.time() - self._renewed_at < 300):
                        LOG(f"[LICENSE] Server says expired but recently renewed — clearing flag")
                        try: os.remove(flag_path)
                        except: pass
                        return "ok"
                    if reason in ("revoked","invalid","not_found"):
                        try: open(flag_path,"w").write(reason)
                        except: pass
                        return reason
                    if reason in ("expired", "subscription_expired"):
                        # Don't write 'expired' to .revoked flag — let subscription system handle it
                        try:
                            if os.path.exists(flag_path): os.remove(flag_path)
                        except: pass
                        return "expired"
                    if reason in _TRANSIENT:
                        LOG(f"[LICENSE] Server transient error ({reason}) — removing stale flag")
                        try: os.remove(flag_path)
                        except: pass
                        return "error"
                    return "revoked"
            except urllib.error.URLError:
                LOG("[LICENSE] Network unavailable during flag re-check — using cached flag")
                return flag_reason if flag_reason in ("revoked","invalid","not_found","expired") else "revoked"
            except Exception as e:
                ERR(f"[LICENSE] Flag re-check error: {e}")
                return flag_reason if flag_reason in ("revoked","invalid","not_found","expired") else "revoked"

        # Flagged + key exists but network=False: treat as ok for startup fast-path;
        # the background thread will do the authoritative remote check.
        if has_flag and key and _is_valid_key_format(key) and not network:
            LOG("[LICENSE] .revoked flag present but network=False — treating as ok for startup")
            return "ok"

        # Flagged with no key: can't re-check server — remove stale flag
        if has_flag:
            if not key or not _is_valid_key_format(key):
                LOG("[LICENSE] .revoked flag exists but no key — removing stale flag")
                try: os.remove(flag_path)
                except: pass
                return "ok"
            if flag_reason in _TRANSIENT:
                LOG(f"[LICENSE] .revoked flag is transient ({flag_reason}) — removing")
                try: os.remove(flag_path)
                except: pass
                return "error"
            return flag_reason if flag_reason in ("revoked","invalid","not_found","expired") else "revoked"

        # No flag, no key: nothing to check
        if not key or not _is_valid_key_format(key):
            LOG("[LICENSE] No key available -- skip check")
            return "ok"

        if not network:
            # Startup fast-path: no local .revoked flag -> treat as ok for now;
            # the background thread will do the authoritative remote check.
            return "ok"

        try:
            machine_id = str(_uuid.getnode())
            CHECK_URL  = self._get_license_check_url() + "/api/check"
            payload    = _j.dumps({"key":key,"machine_id":machine_id}).encode()
            req = urllib.request.Request(CHECK_URL,data=payload,
                headers={"Content-Type":"application/json","User-Agent":f"AurumOS/{CURRENT_VERSION}"},
                method="POST")
            with self._safe_urlopen(req,timeout=10) as resp:
                data = _j.loads(resp.read().decode())
            if data.get("valid"):
                LOG(f"[LICENSE] VALID ({key[:10]}...)")
                try:
                    if os.path.exists(flag_path): os.remove(flag_path)
                except: pass
                # Auto-revoke rule enforcement on heartbeat
                try:
                    from core.auto_revoke import enforce as _ar_enforce, get_rules as _ar_rules
                    if _ar_rules():
                        _ar_ctx = {"machine_id": machine_id, "license_key": key}
                        _ar_result = _ar_enforce(_ar_ctx)
                        if _ar_result.get("action") == "revoke":
                            LOG(f"[LICENSE] Auto-revoke triggered: {_ar_result.get('violations')}")
                            return "revoked"
                except Exception as _ar_e:
                    ERR(f"[LICENSE] Auto-revoke check error: {_ar_e}")
                return "ok"
            else:
                reason = data.get("status","revoked")
                LOG(f"[LICENSE] Server status: {reason}")
                # If we just renewed, server may still say expired — treat as transient
                if reason == "expired" and self._renewed_at and (time.time() - self._renewed_at < 300):
                    LOG(f"[LICENSE] Server says expired but recently renewed — treating as transient")
                    try: os.remove(flag_path)
                    except: pass
                    return "ok"
                if reason in ("revoked","invalid","not_found"):
                    try: open(flag_path,"w").write(reason)
                    except: pass
                    return reason
                if reason in ("expired", "subscription_expired"):
                    try:
                        if os.path.exists(flag_path): os.remove(flag_path)
                    except: pass
                    return "expired"
                if reason in _TRANSIENT:
                    LOG(f"[LICENSE] Server transient error ({reason}) — offline grace")
                    return "offline"
                return "revoked"
        except urllib.error.URLError as e:
            LOG(f"[LICENSE] Network unavailable -- offline grace")
            return "offline"
        except Exception as e:
            ERR(f"[LICENSE] Check error: {e}")
            return "error"

    def check_license(self):
        """Public API: check if the current license is active. Called from revoked.html."""
        try:
            result = self.check_license_revoked(network=True)
            if result == 'ok':
                return {"status": "success", "license_valid": True, "message": "License is active."}
            elif result == 'expired':
                return {"status": "success", "license_valid": False, "message": "License has expired. Please renew."}
            elif result in ('revoked', 'invalid', 'not_found'):
                return {"status": "success", "license_valid": False, "message": f"License {result}. Contact AurumOS support."}
            elif result == 'offline':
                return {"status": "success", "license_valid": True, "message": "License check offline — using local status."}
            else:
                return {"status": "success", "license_valid": False, "message": f"License status: {result}"}
        except Exception as e:
            ERR(f"[LICENSE] check_license error: {e}")
            return {"status": "error", "license_valid": False, "message": str(e)}

    def reload(self):
        """Public API: reload the main window to billing page."""
        try:
            if self._window:
                self._window.evaluate_js("window.location.href='billing.html'")
        except Exception as e:
            ERR(f"[LICENSE] reload error: {e}")

    def fire_revoked_screen(self, reason='revoked'):
        try:
            # Route: expired/subscription_expired → expiry.html, revoked → revoked.html
            page = 'expiry.html' if reason in ('expired', 'subscription_expired') else 'revoked.html'
            if self._window:
                try:
                    cur = self._window.get_current_url() or ''
                    cur_page = cur.split('/')[-1].split('?')[0].lower()
                    # Already on the CORRECT page — skip
                    if cur_page == page:
                        LOG(f'[LICENSE] Already on {page} — skipping navigation')
                        return
                    # On wrong page — navigate to correct one
                    if cur_page in ('revoked.html', 'expiry.html'):
                        LOG(f'[LICENSE] On {cur_page} but should be {page} — navigating')
                except Exception:
                    pass
            msg_map={'revoked':'Your license has been revoked. Please contact AurumOS support.',
                     'not_found':'License key not found on server. Please contact AurumOS support.',
                     'invalid':'Your license is no longer valid. Please contact AurumOS support.',
                     'expired':'Your subscription has expired. Please renew to continue.'}
            msg = msg_map.get(reason,'License issue detected. Please contact AurumOS support.')
            js  = f"window.location.href='{page}?reason={reason}&msg={msg.replace(chr(39),'')}'",
            if self._window: self._window.evaluate_js(js[0])
        except Exception as e:
            ERR(f'[LICENSE] Revoked screen error: {e}')

    def reactivate_check(self):
        import urllib.request, json as _j, uuid as _uuid
        base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath('.')
        flag_path = os.path.join(base,'database','.revoked')
        key_path  = os.path.join(base,'database','.license_key')
        key = ''
        try: key = self.db.get_config('license_key','').strip().upper()
        except: pass
        if not key or not _is_valid_key_format(key):
            try:
                enc = open(key_path,'rb').read()
                key = decrypt_license_key(enc).strip().upper()
            except: key = ''
        if not key or not _is_valid_key_format(key):
            return 'error'
        try:
            machine_id = str(_uuid.getnode())
            CHECK_URL  = self._get_license_check_url() + '/api/check'
            payload    = _j.dumps({'key':key,'machine_id':machine_id}).encode()
            req = urllib.request.Request(CHECK_URL,data=payload,
                headers={'Content-Type':'application/json','User-Agent':f'AurumOS/{CURRENT_VERSION}'},
                method='POST')
            with self._safe_urlopen(req,timeout=10) as resp:
                data = _j.loads(resp.read().decode())
            if data.get('valid'):
                LOG(f'[REACTIVATE] License ACTIVE again ({key[:10]}...)')
                try:
                    if os.path.exists(flag_path): os.remove(flag_path)
                except: pass
                try:
                    enc = encrypt_license_key(key)
                    open(key_path,'wb').write(enc)
                except: pass
                # Also check subscription status — don't allow login if expired
                try:
                    self.sub.set_license_key(key)
                    self.sub.set_window(self._window)
                    self.sub.sync_subscription()
                    sub_state = self.sub.get_state_json()
                    st = sub_state.get('status', 'active')
                    if st == 'grace':
                        LOG(f'[REACTIVATE] Subscription in grace — allowing login')
                    elif st in ('expired', 'revoked') or not sub_state.get('valid', True):
                        LOG(f'[REACTIVATE] Subscription {st} — blocking login')
                        return 'expired'
                except Exception as _sub_err:
                    LOG(f'[REACTIVATE] Subscription check error: {_sub_err}')
                try:
                    if self._window:
                        self._window.evaluate_js("try{localStorage.removeItem('aurum_revoke_reason');}catch(e){}")
                except: pass
                return 'ok'
            else:
                reason = data.get('status','revoked')
                if reason in ('revoked','invalid','not_found'):
                    try: open(flag_path,'w').write(reason)
                    except: pass
                elif reason in ('expired', 'subscription_expired'):
                    try:
                        if os.path.exists(flag_path): os.remove(flag_path)
                    except: pass
                return reason if reason in ('revoked','invalid','not_found','expired','subscription_expired') else 'revoked'
        except urllib.error.URLError:
            return 'offline'
        except Exception as e:
            ERR(f'[REACTIVATE] {e}'); return 'error'

    # -- SSE REAL-TIME STREAM ---------------------------------------------------
    def _read_license_key(self):
        """Read the current license key from file or DB."""
        base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
        key_path = os.path.join(base, 'database', '.license_key')
        key = ''
        if os.path.exists(key_path):
            try:
                enc = open(key_path, 'rb').read()
                key = decrypt_license_key(enc).strip().upper()
            except Exception:
                pass
        if not key or not _is_valid_key_format(key):
            try:
                key = self.db.get_config('license_key', '').strip().upper()
            except Exception:
                pass
        return key if key and _is_valid_key_format(key) else ''

    def _get_server_url(self):
        """Resolve the SSE server base URL from config.json.
        Priority: server_ip:port (local dev) > api_base_url > production default.
        Used ONLY for the SSE stream — license checks always use api_base_url."""
        base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
        server_url = 'https://aurum-os-admin.vercel.app'
        try:
            raw_cfg = open(os.path.join(base, 'config.json'), 'r', encoding='utf-8').read()
            cfg = json.loads(raw_cfg)
            local_ip = (cfg.get('server_ip') or '').strip()
            if local_ip:
                local_port = cfg.get('server_port') or 3000
                server_url = f'http://{local_ip}:{local_port}'
            elif cfg.get('api_base_url'):
                server_url = cfg['api_base_url']
        except Exception:
            pass
        return server_url

    def _get_license_check_url(self):
        """License check ALWAYS goes to the production server."""
        return 'https://aurum-os-admin.vercel.app'
    def start_sse_stream(self):
        """Start Python SSE listener for real-time license events.
        Called from _bg_check after the first successful license verification."""
        try:
            base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
            key_path = os.path.join(base, 'database', '.license_key')
            key = ''
            # Try encrypted file first
            if os.path.exists(key_path):
                try:
                    enc = open(key_path, 'rb').read()
                    key = decrypt_license_key(enc).strip().upper()
                except Exception:
                    pass
            # Fallback to DB
            if not key or not _is_valid_key_format(key):
                try:
                    key = self.db.get_config('license_key', '').strip().upper()
                except Exception:
                    pass
            if not key or not _is_valid_key_format(key):
                LOG('[SSE-PY] No valid license key found — skipping stream')
                return

            server_url = self._get_server_url()
            LOG(f'[SSE-PY] Starting Python listener for key={key[:10]}... server={server_url}')

            # Stop existing listener if any
            if hasattr(self, '_sse_listener') and self._sse_listener:
                self._sse_listener.stop()

            # Create and start Python SSE listener
            self._sse_key = key
            _mid = ''
            try:
                _mid = self.db._machine_fingerprint()
            except Exception:
                pass
            self._sse_listener = LicenseEventListener(
                license_key=key,
                server_url=server_url,
                on_plan_change=self._sse_on_plan_change,
                on_status_change=self._sse_on_status_change,
                on_connected=self._sse_on_connected,
                on_urgent_message=self._sse_on_urgent_message,
                machine_id=_mid,
                app_version=CURRENT_VERSION,
            )
            self._sse_listener.start()
            LOG('[SSE-PY] Python listener started')

            # Urgent-message safety net: catch-up now + watchdog polling
            try:
                threading.Thread(target=self.urgent_fetch_missed, daemon=True).start()
            except Exception:
                pass
            try:
                if not getattr(self, '_urgent_watch', False):
                    self._urgent_watch = True
                    threading.Thread(target=self._urgent_watchdog, daemon=True).start()
            except Exception:
                pass

            # Also inject JS SSE for browser-side UI updates
            js_path = os.path.join(get_asset_path('ui'), 'sse_stream.js')
            if os.path.exists(js_path) and self._window:
                try:
                    with open(js_path, 'r', encoding='utf-8') as f:
                        sse_js = f.read()
                    self._window.evaluate_js(sse_js)
                    self._window.evaluate_js(
                        f"window.__aurumSSEConnect('{server_url}', '{key}')"
                    )
                    LOG('[SSE-PY] JS SSE script also injected for UI')
                except Exception as js_err:
                    LOG(f'[SSE-PY] JS inject skipped: {js_err}')

            # Urgent-message display module (toasts/modal/inbox wiring)
            try:
                urg_path = os.path.join(get_asset_path('ui'), 'urgent.js')
                if os.path.exists(urg_path) and self._window:
                    with open(urg_path, 'r', encoding='utf-8') as f:
                        self._window.evaluate_js(f.read())
                    LOG('[SSE-PY] urgent.js injected for UI')
            except Exception as urg_err:
                LOG(f'[SSE-PY] urgent.js inject skipped: {urg_err}')

            # Inject subscription.js
            sub_js_path = os.path.join(get_asset_path('ui'), 'subscription.js')
            if os.path.exists(sub_js_path) and self._window:
                try:
                    with open(sub_js_path, 'r', encoding='utf-8') as f:
                        sub_js = f.read()
                    self._window.evaluate_js(sub_js)
                    self._window.evaluate_js(
                        f"window.__aurumSubInit && window.__aurumSubInit('{key}')"
                    )
                    LOG('[SSE-PY] Subscription script injected')
                except Exception as sub_err:
                    LOG(f'[SSE-PY] Subscription inject skipped: {sub_err}')

        except Exception as e:
            ERR(f'[SSE-PY] start_sse_stream error: {e}')

    def _sse_on_connected(self, data=None):
        """Called when SSE connection is established."""
        LOG('[SSE-PY] Connected to AurumOS server')
        # Sync subscription on connect
        try:
            self.sub.sync_subscription()
        except Exception:
            pass
        # Reconnect catch-up: SSE events expire after ~5 min
        try:
            threading.Thread(target=self.urgent_fetch_missed, daemon=True).start()
        except Exception:
            pass

    def _sse_on_plan_change(self, data):
        """Called when admin changes plan — apply features immediately."""
        from subscription_manager import PLAN_FEATURES, LITE_FEATURES, _clamp_expiry, SUBSCRIPTION_DAYS
        new_plan = data.get('plan', 'lite')
        LOG(f'[SSE-PY] Plan change received: {new_plan}')

        # Try sync with server first
        try:
            self.sub.sync_subscription()
        except Exception as e:
            LOG(f'[SSE-PY] Sync after plan_change error: {e}')

        # Check if sync gave us a valid active state — if not, apply SSE data directly
        current = self.sub.get_state_json()
        if not current.get('valid') or current.get('status') not in ('active', 'grace'):
            LOG(f'[SSE-PY] Sync result not active (status={current.get("status")}) — applying SSE data directly')
            try:
                cur = self.sub._state
                plan = (data.get('plan') or data.get('effective_plan')
                        or cur.get('effective_plan') or cur.get('plan') or 'lite')
                plan = str(plan).strip().lower() or 'lite'
                features = data.get('features') or PLAN_FEATURES.get(plan, list(LITE_FEATURES))
                expires = _clamp_expiry(data.get('subscription_expires_at')
                                        or (cur.get('subscription') or {}).get('expires_at'))
                if 'remaining_days' in data:
                    remaining = _clamp_remaining(data.get('remaining_days') or 0)
                else:
                    remaining = ((cur.get('subscription') or {}).get('remaining_days')) or 0
                self.sub._set_state({
                    'valid': True,
                    'status': 'active',
                    'effective_plan': plan,
                    'features': features,
                    'business': data.get('business') or cur.get('business', ''),
                    'owner': data.get('owner') or cur.get('owner', ''),
                    'plan': plan,
                    'is_trial': data.get('is_trial', False),
                    'trial_end_ms': data.get('trial_end_ms'),
                    'subscription': {
                        'status': 'active',
                        'expires_at': expires,
                        'remaining_days': remaining,
                        'grace_remaining_days': data.get('grace_remaining_days', 0) or 0,
                        'renewal_amount': data.get('renewal_amount', 0)
                    }
                })
                self.sub._save_cache()
                self._renewed_at = time.time()
                try: self.sub.set_force_active(300)
                except Exception: pass
                LOG(f'[SSE-PY] Applied SSE data: plan={new_plan} expires={expires}')
            except Exception as e:
                ERR(f'[SSE-PY] Apply SSE data error: {e}')
        else:
            # Sync returned active — just clamp the expiry if needed
            try:
                sub = current.get('subscription', {})
                clamped = _clamp_expiry(sub.get('expires_at'))
                if clamped != sub.get('expires_at'):
                    self.sub._state['subscription']['expires_at'] = clamped
                    self.sub._save_cache()
                    LOG(f'[SSE-PY] Clamped existing expiry to {clamped}')
            except Exception:
                pass

        # Send the FULL subscription state to JS (not raw SSE data)
        # This ensures features array is always present and correct
        if self._window:
            try:
                full_state = self.sub.get_state_json()
                js_data = json.dumps(full_state)
                # Push to current page AND save to localStorage for future pages
                safe_js_data = js_data.replace('\\', '\\\\').replace("'", "\\'")
                self._window.evaluate_js(
                    f"window.__onSubscriptionEvent && window.__onSubscriptionEvent('plan_change', {js_data});"
                    f"try{{localStorage.setItem('aurum_sub_state', '{safe_js_data}');}}catch(e){{}}"
                )
                LOG(f'[SSE-PY] JS notified: plan={full_state.get("effective_plan")} features={len(full_state.get("features",[]))}')
            except Exception as e:
                LOG(f'[SSE-PY] JS notify error: {e}')

        # Trial→paid conversion: dismiss countdown banner
        if not data.get('is_trial', False):
            try:
                from core.trial_manager import dismiss_banner as _tm_dismiss
                _tm_dismiss()
                LOG('[SSE-PY] Trial banner dismissed (trial→paid conversion)')
            except Exception:
                pass

        # Save to local cache for offline use
        try:
            cache_path = os.path.join(
                os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.'),
                'database', '.subscription_cache'
            )
            full_state = self.sub.get_state_json()
            cache = {
                'state': full_state,
                'plan': new_plan,
                'features': full_state.get('features', []),
                'expires_at': full_state.get('subscription', {}).get('expires_at'),
                'cached_at': time.time()
            }
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w') as f:
                json.dump(cache, f)
            LOG(f'[SSE-PY] Cached plan={new_plan}')
        except Exception as e:
            ERR(f'[SSE-PY] Cache error: {e}')

    def _sse_on_status_change(self, data):
        """Called when license status changes (revoked/activated)."""
        status = data.get('status', 'unknown')
        message = data.get('message', '')
        key = data.get('key', '')

        LOG(f'[SSE-PY] Status change: {status} — {message}')

        if status in ('revoked', 'expired', 'disabled', 'suspended'):
            # If recently renewed, ignore server revoke
            if time.time() < self.sub._force_active_until:
                LOG(f'[SSE-PY] Ignoring server status={status} — in force-active grace period')
                return
            # Handle revoke — same as existing sse_handle_revoke
            self.sse_handle_revoke(status, message)
        elif status == 'active':
            # License reactivated — apply what SSE said, keeping what it did
            # not. Never demote the plan or blank the names here: an 'active'
            # ping carries no plan, and an enterprise shop reactivated as
            # 'pro' (or nameless) was the old bug.
            was_active = self.sub._state.get('valid', False) and self.sub._state.get('status', '') in ('active', 'grace')
            LOG(f'[SSE-PY] License reactivated via SSE — setting state to active (was_active={was_active})')
            try:
                from subscription_manager import PLAN_FEATURES, LITE_FEATURES
                cur = self.sub._state
                plan = (data.get('plan') or data.get('effective_plan')
                        or cur.get('effective_plan') or cur.get('plan') or 'pro')
                plan = str(plan).strip().lower() or 'pro'
                features = data.get('features') or PLAN_FEATURES.get(plan, LITE_FEATURES)
                if 'remaining_days' in data:
                    remaining = data.get('remaining_days') or 0
                else:
                    remaining = (cur.get('subscription') or {}).get('remaining_days', 0) or 0
                self.sub._set_state({
                    'valid': True,
                    'status': 'active',
                    'effective_plan': plan,
                    'features': features,
                    'business': data.get('business') or cur.get('business', ''),
                    'owner': data.get('owner') or cur.get('owner', ''),
                    'plan': plan,
                    'is_trial': False,
                    'trial_end_ms': None,
                    'subscription': {
                        'status': 'active',
                        'expires_at': data.get('expires_at') or (cur.get('subscription') or {}).get('expires_at'),
                        'remaining_days': remaining,
                        'grace_remaining_days': data.get('grace_remaining_days', 0) or 0,
                        'renewal_amount': data.get('renewal_amount', 0)
                    }
                })
                self.sub._save_cache()
                self.sub.set_force_active(300)
            except Exception as e:
                ERR(f'[SSE-PY] Set active state error: {e}')
            # Clear revoked flag
            try:
                base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
                flag_path = os.path.join(base, 'database', '.revoked')
                if os.path.exists(flag_path):
                    os.remove(flag_path)
            except Exception:
                pass
            # Push reactivation event to JS only on actual transition
            if self._window and not was_active:
                try:
                    state_json = json.dumps(self.sub.get_state_json())
                    self._window.evaluate_js(
                        f"window.__onSubscriptionEvent && window.__onSubscriptionEvent('reactivated', {state_json});"
                    )
                except Exception:
                    pass
                try:
                    self._window.evaluate_js("window.location.href='login.html'")
                except Exception:
                    pass

    def sse_handle_revoke(self, reason='revoked', message=''):
        """Called from JS when SSE receives a revoke/expire event.
        Clears cached license state and forces the revoked screen."""
        LOG(f'[SSE] Revoke received: reason={reason} msg={message}')
        # If recently renewed, ignore revoke
        if time.time() < self.sub._force_active_until:
            LOG(f'[SSE] Ignoring revoke={reason} — in force-active grace period')
            return
        # Notify subscription manager
        try:
            self.sub.handle_sse_revoke(reason, message)
        except Exception:
            pass
        try:
            base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
            # Write .revoked flag so it persists across restarts
            # BUT: don't write for 'expired' — let subscription system handle expiry
            # Writing 'expired' to .revoked flag causes false expiry screens on startup
            flag_path = os.path.join(base, 'database', '.revoked')
            if reason not in ('expired', 'subscription_expired'):
                try:
                    open(flag_path, 'w').write(reason if reason in ('revoked', 'disabled', 'suspended') else 'revoked')
                except Exception:
                    pass
            else:
                # For expired: remove any stale .revoked flag so startup doesn't block
                try:
                    if os.path.exists(flag_path):
                        os.remove(flag_path)
                except Exception:
                    pass
            # Only delete license key for actual revocation (not expiry — user needs key to renew)
            if reason not in ('expired', 'subscription_expired'):
                key_path = os.path.join(base, 'database', '.license_key')
                try:
                    if os.path.exists(key_path):
                        os.remove(key_path)
                        LOG('[SSE] Removed cached .license_key file')
                except Exception:
                    pass
                # Clear license key from DB so it doesn't fall back to old key
                try:
                    with self.db._get_connection() as conn:
                        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('license_key','')")
                    LOG('[SSE] Cleared license key from DB')
                except Exception as db_err:
                    ERR(f'[SSE] DB key clear failed: {db_err}')
            # Clear any session state
            self._session_role = None
            self._session_username = None
        except Exception as e:
            ERR(f'[SSE] sse_handle_revoke cleanup error: {e}')
        # Navigate to revoked page
        self.fire_revoked_screen(reason)

    # ── SUBSCRIPTION API (for frontend) ─────────────────────────────────
    def subscription_startup_check(self):
        """Called from frontend on EVERY page load.
        ALWAYS does a synchronous server check — never trusts stale cache.
        Only falls back to state file if server is unreachable.
        This ensures expired/revoked status is enforced immediately."""
        try:
            # Read key
            base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
            key_path = os.path.join(base, 'database', '.license_key')
            key = ''
            if os.path.exists(key_path):
                try:
                    enc = open(key_path, 'rb').read()
                    key = decrypt_license_key(enc).strip().upper()
                except Exception:
                    pass
            if not key or not _is_valid_key_format(key):
                try:
                    key = self.db.get_config('license_key', '').strip().upper()
                except Exception:
                    pass
            if not key or not _is_valid_key_format(key):
                # No key here: Lite, or for a staff PC the main PC's plan.
                self.sub.startup_check('')
                return self.sub.get_state_json()

            self.sub.set_license_key(key)
            self.sub.set_window(self._window)

            # If we just renewed (within 5 min), skip server check and use cached active state
            if self._renewed_at and (time.time() - self._renewed_at < 300):
                LOG('[SUB] Recently renewed — skipping server check, using cached active state')
                self.sub._load_cache()
                return self.sub.get_state_json()

            # ALWAYS do a synchronous server check — this is the source of truth
            try:
                self.sub.startup_check(key)
                result = self.sub.get_state_json()
                LOG(f'[SUB] Startup server check: valid={result.get("valid")} '
                    f'status={result.get("status")} plan={result.get("effective_plan")} '
                    f'features={len(result.get("features", []))}')
                return result
            except Exception as server_err:
                ERR(f'[SUB] Startup server check failed: {server_err}')
                # Server unreachable — fall back to state file
                file_state = self.sub.load_state_file()
                if file_state:
                    self.sub._apply_cache_data({'state': file_state, '_hours_old': 0})
                    LOG(f'[SUB] Startup: server failed, loaded from file — plan={file_state.get("effective_plan")}')
                    return self.sub.get_state_json()
                # No file either — return default (expired-safe)
                from subscription_manager import LITE_FEATURES
                return {'valid': False, 'status': 'unknown', 'effective_plan': 'lite',
                        'features': list(LITE_FEATURES), 'subscription': {'status': 'unknown',
                        'expires_at': None, 'remaining_days': 0, 'grace_remaining_days': 0,
                        'renewal_amount': 0}, 'business': '', 'owner': '', 'plan': 'lite',
                        'is_trial': False, 'trial_end_ms': None}
        except Exception as e:
            ERR(f'[SUB] startup_check error: {e}')
            return self.sub.get_state_json()

    def subscription_sync(self):
        """Manual sync trigger from frontend."""
        try:
            result = self.sub.sync_subscription()
            return self.sub.get_state_json()
        except Exception as e:
            ERR(f'[SUB] sync error: {e}')
            return self.sub.get_state_json()

    def subscription_check_update(self):
        """Lightweight poll: returns state file timestamp. JS calls this every 5s."""
        try:
            ts = self.sub.get_state_file_timestamp()
            return {'ts': ts}
        except Exception:
            return {'ts': 0}

    def subscription_poll_check(self):
        """Called by JS every 8s. Polls /api/poll for reactivation changes."""
        try:
            self.sub._do_poll()
            return self.sub.get_state_json()
        except Exception as e:
            ERR(f'[SUB] poll error: {e}')
            return self.sub.get_state_json()

    def renew_subscription(self):
        """Call POST /api/subscription/renew after user pays.
        Returns {ok, plan, subscription_expires_at} or {ok:false, error}."""
        import urllib.request, urllib.error, json as _j, uuid as _uuid
        LOG('[RENEW] renew_subscription called')
        base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
        flag_path = os.path.join(base, 'database', '.revoked')
        key_path = os.path.join(base, 'database', '.license_key')
        key = ''
        try:
            key = self.db.get_config('license_key', '').strip().upper()
        except Exception:
            pass
        if not key or not _is_valid_key_format(key):
            if os.path.exists(key_path):
                try:
                    enc = open(key_path, 'rb').read()
                    key = decrypt_license_key(enc).strip().upper()
                except Exception:
                    pass
        if not key or not _is_valid_key_format(key):
            LOG('[RENEW] No valid license key found')
            return {'ok': False, 'error': 'No valid license key'}
        LOG(f'[RENEW] Key: {key[:10]}...')

        machine_id = str(_uuid.getnode())
        url = self._get_license_check_url() + '/api/subscription/renew'
        payload = _j.dumps({'key': key, 'machine_id': machine_id}).encode()
        LOG(f'[RENEW] POST {url}')

        try:
            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'User-Agent': f'AurumOS/{CURRENT_VERSION}'
            }, method='POST')
            with self._safe_urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
            LOG(f'[RENEW] Response: {raw[:200]}')
            data = _j.loads(raw)

            if data.get('ok'):
                LOG(f'[RENEW] Renewed: plan={data.get("plan")} expires={data.get("subscription_expires_at")}')
                try:
                    self.sub.set_license_key(key)
                    self.sub.set_window(self._window)
                except Exception as _se:
                    ERR(f'[RENEW] Sub setup after renew: {_se}')
                # Force subscription state to active from renewal response.
                # Plan, names and day counts come from the server first and
                # the current state second — never from a hardcoded default
                # that would demote an enterprise shop or invent a year.
                try:
                    from subscription_manager import LITE_FEATURES, PRO_FEATURES, ENTERPRISE_FEATURES, PLAN_FEATURES, _clamp_expiry, _clamp_remaining, SUBSCRIPTION_DAYS
                    cur = self.sub._state
                    plan = (data.get('plan') or data.get('effective_plan')
                            or cur.get('effective_plan') or cur.get('plan') or 'pro')
                    plan = str(plan).strip().lower() or 'pro'
                    features = data.get('features') or PLAN_FEATURES.get(plan, PRO_FEATURES)
                    expires = _clamp_expiry(data.get('subscription_expires_at')
                                            or (cur.get('subscription') or {}).get('expires_at'))
                    if 'remaining_days' in data:
                        remaining = _clamp_remaining(data.get('remaining_days') or 0)
                    else:
                        remaining = ((cur.get('subscription') or {}).get('remaining_days')) or 0
                    self.sub._set_state({
                        'valid': True,
                        'status': 'active',
                        'effective_plan': plan,
                        'features': features,
                        'business': data.get('business') or cur.get('business', ''),
                        'owner': data.get('owner') or cur.get('owner', ''),
                        'plan': plan,
                        'is_trial': False,
                        'trial_end_ms': None,
                        'subscription': {
                            'status': 'active',
                            'expires_at': expires,
                            'remaining_days': remaining,
                            'grace_remaining_days': data.get('grace_remaining_days', 0) or 0,
                            'renewal_amount': data.get('renewal_amount', 0)
                        }
                    })
                    self.sub._save_cache()
                    LOG(f'[RENEW] Subscription state forced active: plan={plan}')
                except Exception as _fe:
                    ERR(f'[RENEW] Force state after renew: {_fe}')
                self._renewed_at = time.time()
                try: self.sub.set_force_active(300)
                except Exception: pass
                # Push active state to JS so localStorage is updated before navigation
                try:
                    if self._window:
                        import json as _j2
                        sub_json = _j2.dumps(self.sub.get_state_json())
                        self._window.evaluate_js(
                            f"try{{localStorage.setItem('aurum_sub_state','{sub_json.replace(chr(39),chr(92)+chr(39))}');}}catch(e){{}}"
                        )
                except Exception as _jse:
                    ERR(f'[RENEW] Push state to JS: {_jse}')
                try:
                    if os.path.exists(flag_path):
                        os.remove(flag_path)
                except Exception:
                    pass
                try:
                    if self._window:
                        self._window.evaluate_js("try{localStorage.removeItem('aurum_revoke_reason');}catch(e){}")
                except Exception:
                    pass
                return data
            else:
                error = data.get('error', 'unknown')
                LOG(f'[RENEW] Failed: {error}')
                return {'ok': False, 'error': error}
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            ERR(f'[RENEW] HTTP {e.code}: {body[:200]}')
            if e.code == 404:
                return {'ok': False, 'error': 'Renewal endpoint not configured on server. Contact support.'}
            return {'ok': False, 'error': f'Server error ({e.code}). Try again later.'}
        except urllib.error.URLError as e:
            ERR(f'[RENEW] Network error: {e}')
            return {'ok': False, 'error': 'offline'}
        except ValueError as e:
            ERR(f'[RENEW] JSON parse error: {e}')
            return {'ok': False, 'error': 'Invalid response from server. Contact support.'}
        except Exception as e:
            ERR(f'[RENEW] Error: {e}')
            return {'ok': False, 'error': str(e)}

    def subscription_check_feature(self, feature_id):
        """Check if a specific feature is allowed."""
        try:
            return self.sub.check_feature(feature_id)
        except Exception as e:
            return {'allowed': False, 'plan': 'lite', 'required_plan': 'pro', 'features': []}

    def subscription_get_state(self):
        """Return full subscription state for frontend."""
        try:
            return self.sub.get_state_json()
        except Exception as e:
            return {'valid': False, 'status': 'unknown', 'effective_plan': 'lite', 'features': []}

    def open_admin_renewal(self):
        """Open the shop's renewal page (upgrade prompts, subscription badges)."""
        try:
            base = 'https://aurum-os-admin.vercel.app'
            try:
                base = self._get_license_check_url() or base
            except Exception:
                pass
            return self.open_url(base.rstrip('/') + '/pricing')
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def subscription_start_sync(self, interval_minutes=30):
        """Start periodic background sync."""
        try:
            self.sub.start_periodic_sync(interval_minutes)
            return {'status': 'ok'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def get_trial_status(self):
        """Return trial status for dashboard."""
        try:
            state = self.sub.get_state_json()
            result = {
                'is_trial': state.get('is_trial', False),
                'status': state.get('status', 'unknown'),
                'expiry_ms': state.get('trial_end_ms'),
                'effective_plan': state.get('effective_plan', 'lite'),
                'remaining_days': state.get('subscription', {}).get('remaining_days', 0)
            }
            try:
                from core.trial_manager import get_status as _tm_status
                tm = _tm_status()
                if tm.get('is_trial'):
                    result['days_remaining'] = tm.get('days_remaining', 0)
                    result['machine_bound'] = tm.get('machine_bound', False)
                    result['degraded'] = tm.get('degraded', False)
                    result['cache_stale'] = tm.get('cache_stale', False)
            except Exception:
                pass
            return result
        except Exception as e:
            return {'is_trial': False, 'status': 'unknown', 'expiry_ms': None}

    def recover_from_trial(self):
        """Trial portal's Recover button: a paid licence returns to login.

        Returns {success} exactly as trial_portal.html reads it. On a live
        licence the window is moved to the login screen as well, so Recover
        actually recovers instead of just reporting.
        """
        try:
            state = self.sub.get_state_json()
            valid = bool(state.get('valid'))
            plan = str(state.get('effective_plan') or 'lite').lower()
            if valid and plan in ('pro', 'enterprise'):
                try:
                    self.navigate('login.html')
                except Exception:
                    pass
                return {'success': True,
                        'message': 'Premium initialised. Opening sign-in.'}
            return {'success': False,
                    'message': 'No active premium licence on this terminal yet. '
                               'Complete verification with your administrator.'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def quit_app(self):
        try:
            if self._window: self._window.destroy()
            sys.exit(0)
        except: pass

    def get_plans(self):
        """Fetch plan details from server for revoked.html display."""
        import urllib.request, json as _json
        api_base = self._get_license_check_url()
        url = api_base + '/api/subscription/plans'
        try:
            req = urllib.request.Request(url, headers={
                'Content-Type': 'application/json',
                'User-Agent': 'AurumOS/Client'
            })
            with self._safe_urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            return data
        except Exception as e:
            ERR(f'[PLANS] fetch error: {e}')
            return {
                'plans': [
                    {'id': 'lite', 'name': 'Lite', 'emoji': '\U0001f949', 'price': 3000, 'onboarding': 15000, 'period': 'year', 'desc': '10 Features', 'features': ['local_mode', 'billing_retail', 'stock_entry', 'product_master', 'client_ledger', 'staff_login_lockout', 'tag_printing_local', 'scale_weighing', 'sales_report_basic', 'bastion_core'], 'accent': '#b45309'},
                    {'id': 'pro', 'name': 'Pro', 'emoji': '\U0001f948', 'price': 7000, 'onboarding': 35000, 'period': 'year', 'desc': '21 Features', 'features': ['local_mode', 'billing_retail', 'stock_entry', 'product_master', 'client_ledger', 'staff_login_lockout', 'tag_printing_local', 'scale_weighing', 'sales_report_basic', 'bastion_core', 'lan_multi_pc', 'karigar_vouchers', 'touch_groups', 'full_accounts', 'tag_audit', 'stock_med_reports', 'tsc_network_printing', 'multi_staff', 'analytics_dashboard', 'year_close', 'bastion_enhanced'], 'accent': '#6b7280'},
                    {'id': 'enterprise', 'name': 'Enterprise', 'emoji': '\U0001f947', 'price': 15000, 'onboarding': 75000, 'period': 'year', 'desc': '30 Features', 'features': ['local_mode', 'billing_retail', 'stock_entry', 'product_master', 'client_ledger', 'staff_login_lockout', 'tag_printing_local', 'scale_weighing', 'sales_report_basic', 'bastion_core', 'lan_multi_pc', 'karigar_vouchers', 'touch_groups', 'full_accounts', 'tag_audit', 'stock_med_reports', 'tsc_network_printing', 'multi_staff', 'analytics_dashboard', 'year_close', 'bastion_enhanced', 'cloud_sync', 'fleet_bastion', 'customer_loyalty', 'bastion_ai', 'nexus_management', 'bridge_server', 'custom_db_location', 'priority_support', 'api_integration'], 'accent': '#a87d1e'}
                ]
            }

    def verify_key(self, key):
        import urllib.request, json as _json, uuid as _uuid
        key = str(key).strip().upper()
        if key.lower() == self.TEMP_KEY:
            return {"status":"success","business":"Dev Mode","owner":"Developer"}
        if not _is_valid_key_format(key):
            return {"status":"error","message":"Invalid key format. Expected AU/AR-XXXX-XXXX-XXXX-XXXX"}
        try: machine_id = str(_uuid.getnode())
        except: machine_id = "unknown"
        CHECK_URL = self._get_license_check_url() + "/api/check"
        LOG(f"[LICENSE] Checking key={key[:10]}... machine={machine_id[:8]}")
        try:
            payload = _json.dumps({"key":key,"machine_id":machine_id}).encode()
            req = urllib.request.Request(CHECK_URL,data=payload,
                headers={"Content-Type":"application/json","User-Agent":f"AurumOS/{CURRENT_VERSION}"},
                method="POST")
            with self._safe_urlopen(req,timeout=10) as resp:
                raw = resp.read().decode()
                LOG(f"[LICENSE] Server: {raw}")
                data = _json.loads(raw)
            if data.get("valid"):
                try:
                    base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath(".")
                    key_path = os.path.join(base,"database",".license_key")
                    open(key_path,"wb").write(encrypt_license_key(key))
                    LOG("[LICENSE] Key cached")
                except Exception as e: ERR(f"[LICENSE] Cache error: {e}")
                return {"status":"success","business":data.get("business",""),"owner":data.get("owner","")}
            else:
                reason = data.get("status","invalid")
                msgs={"not_found":"License key not found.","revoked":"This license has been revoked.",
                      "bad_request":"Invalid key format.","server_error":"Server error."}
                return {"status":"error","message":msgs.get(reason,f"License check failed ({reason}).")}
        except urllib.error.HTTPError as e:
            return {"status":"error","message":f"Server error ({e.code})."}
        except urllib.error.URLError as e:
            LOG(f"[LICENSE] Network error -- checking cache")
            try:
                base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath(".")
                key_path = os.path.join(base,"database",".license_key")
                if os.path.exists(key_path):
                    saved = decrypt_license_key(open(key_path,"rb").read()).strip().upper()
                    if saved == key:
                        return {"status":"success","business":"","owner":"","offline":True}
            except: pass
            return {"status":"error","message":"No internet connection."}
        except Exception as e:
            ERR(f"[LICENSE] {e}")
            return {"status":"error","message":f"Verification error: {str(e)}"}

    def get_license_details(self, key=None):
        """Business profile behind a licence key (setup auto-fill).

        Returns {status:'success', business_name, owner_name, city, phone,
        email, gstin} as setup.html:745 reads it. Server supplies the names;
        the shop's own saved settings supply the rest.
        """
        try:
            key = str(key or '').strip().upper()
            if not key:
                try:
                    key = str(self.db.get_config('license_key', '') or '').strip().upper()
                except Exception:
                    key = ''
            business, owner = '', ''
            if key:
                res = self.verify_key(key)
                if isinstance(res, dict) and res.get('status') == 'success':
                    d = res.get('details') if isinstance(res.get('details'), dict) else {}
                    business = res.get('business') or d.get('business_name') or ''
                    owner = res.get('owner') or d.get('owner_name') or ''
            def _cfg(k):
                try:
                    return str(self.db.get_config(k, '') or '')
                except Exception:
                    return ''
            return {"status": "success",
                    "business_name": business or _cfg('business_name') or _cfg('shop_name'),
                    "owner_name": owner or _cfg('owner_name'),
                    "city": _cfg('city'),
                    "phone": _cfg('owner_phone') or _cfg('phone'),
                    "email": _cfg('email'),
                    "gstin": _cfg('gstin')}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # -- LOCKOUT ---------------------------------------------------------------
    def _get_lockout_file(self):
        base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath('.')
        return os.path.join(base,'database','.lockout_state')

    def _save_lockout_state(self):
        try:
            import json as _j
            data = {
                'attempts': self._login_attempts,
                'until': self._lockout_until.isoformat() if self._lockout_until else None,
                'tier': self._lockout_tier,
                'history': self._attempt_history[-50:],
            }
            open(self._get_lockout_file(), 'w').write(_j.dumps(data))
        except: pass

    def _load_lockout_state(self):
        try:
            import json as _j
            from datetime import datetime as _dt
            path = self._get_lockout_file()
            if not os.path.exists(path): return
            data = _j.loads(open(path).read())
            self._login_attempts = data.get('attempts', 0)
            self._lockout_tier = data.get('tier', 0)
            self._attempt_history = data.get('history', [])
            until_str = data.get('until')
            if until_str:
                until = _dt.fromisoformat(until_str)
                if until > _dt.now():
                    self._lockout_until = until
                    LOG(f"[LOGIN] Lockout restored -- tier {self._lockout_tier}, {int((until-_dt.now()).total_seconds())}s remaining")
                else:
                    self._lockout_until = None
                    # Keep attempts and tier — don't reset on expiry (progressive)
        except: pass

    def _get_rate_tier(self):
        """Return the current rate-limit tier dict based on attempt count."""
        for tier in reversed(self._RATE_TIERS):
            if self._login_attempts >= tier["threshold"]:
                return tier
        return self._RATE_TIERS[0]

    def _record_attempt_audit(self, success):
        """Record login attempt in the audit history."""
        from datetime import datetime as _dt
        entry = {
            "time": _dt.now().isoformat(),
            "attempt": self._login_attempts,
            "success": success,
            "tier": self._lockout_tier,
        }
        self._attempt_history.append(entry)
        if len(self._attempt_history) > 50:
            self._attempt_history = self._attempt_history[-50:]

    # -- LOGIN -----------------------------------------------------------------
    # ══════════════════════════════════════════════════════════════════
    # TWO-FACTOR AUTHENTICATION (TOTP · RFC 6238) — owner account
    # The shared secret + hashed backup codes live in app_config. TOTP codes
    # are computed with the Python standard library (hmac/hashlib), so no extra
    # third-party package (pyotp) needs to be bundled. UI contract:
    #   settings.html : totp_status / totp_begin_enroll / totp_confirm_enroll / totp_disable
    #   login.html    : verify_login -> {status:'2fa_required'} -> verify_2fa
    # ══════════════════════════════════════════════════════════════════
    def _tfa_get(self, key, default=None):
        try:
            with self.db._get_connection() as _c:
                r = _c.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default
        except Exception:
            return default

    def _tfa_set(self, key, value):
        with self.db._get_connection() as _c:
            _c.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)", (key, value))
            _c.commit()

    def _tfa_del(self, *keys):
        try:
            with self.db._get_connection() as _c:
                _c.execute(
                    "DELETE FROM app_config WHERE key IN (%s)" % ",".join("?" * len(keys)),
                    keys)
                _c.commit()
        except Exception:
            pass

    @staticmethod
    def _totp_code(secret_b32, when=None, step=30, digits=6):
        import hmac as _hm, hashlib as _hl, base64 as _b64, struct as _st, time as _tm
        if when is None:
            when = _tm.time()
        pad = '=' * ((8 - len(secret_b32) % 8) % 8)
        key = _b64.b32decode(secret_b32 + pad, casefold=True)
        msg = _st.pack('>Q', int(when // step))
        h = _hm.new(key, msg, _hl.sha1).digest()
        o = h[-1] & 0x0F
        val = (_st.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
        return str(val).zfill(digits)

    def _totp_verify(self, secret_b32, code, window=1):
        import time as _tm
        code = ''.join(ch for ch in str(code) if ch.isdigit())
        if len(code) != 6:
            return False
        now = _tm.time()
        for w in range(-window, window + 1):
            if self._totp_code(secret_b32, now + w * 30) == code:
                return True
        return False

    def _totp_is_enabled(self):
        try:
            return self._tfa_get('totp_enabled', '0') == '1' and bool(self._tfa_get('totp_secret'))
        except Exception:
            return False

    def totp_status(self):
        try:
            return {"enabled": self._totp_is_enabled()}
        except Exception as e:
            ERR(f"[2FA] status error: {e}")
            return {"enabled": False}

    def totp_begin_enroll(self):
        import os as _os, io as _io, base64 as _b64, urllib.parse as _up
        try:
            secret = _b64.b32encode(_os.urandom(20)).decode('ascii').rstrip('=')
            self._tfa_set('totp_pending_secret', secret)
            try:
                biz = (self.db.get_config('business_name', '') or 'owner').strip() or 'owner'
            except Exception:
                biz = 'owner'
            label = _up.quote('AurumOS:' + biz)
            uri = ("otpauth://totp/%s?secret=%s&issuer=AurumOS&algorithm=SHA1&digits=6&period=30"
                   % (label, secret))
            qr_uri = ''
            try:
                import qrcode as _qr
                img = _qr.make(uri)
                buf = _io.BytesIO()
                img.save(buf, format='PNG')
                qr_uri = 'data:image/png;base64,' + _b64.b64encode(buf.getvalue()).decode('ascii')
            except Exception as qe:
                LOG(f"[2FA] QR generation skipped: {qe}")
            LOG("[2FA] Enrollment started")
            return {"status": "success", "secret": secret, "qr": qr_uri, "otpauth": uri}
        except Exception as e:
            ERR(f"[2FA] begin_enroll error: {e}")
            return {"status": "error", "message": str(e)}

    def totp_confirm_enroll(self, code):
        import os as _os, base64 as _b64, hashlib as _hl, json as _j
        try:
            secret = self._tfa_get('totp_pending_secret')
            if not secret:
                return {"status": "error", "message": "Setup expired — please start again."}
            if not self._totp_verify(secret, code):
                return {"status": "error", "message": "Incorrect code. Check your authenticator app and try again."}
            self._tfa_set('totp_secret', secret)
            self._tfa_set('totp_enabled', '1')
            self._tfa_del('totp_pending_secret')
            codes = []
            for _ in range(10):
                raw = _b64.b32encode(_os.urandom(5)).decode('ascii').rstrip('=')[:8].upper()
                codes.append(raw[:4] + '-' + raw[4:8])
            hashed = [_hl.sha256(c.replace('-', '').encode()).hexdigest() for c in codes]
            self._tfa_set('totp_backup_codes', _j.dumps(hashed))
            try:
                self._audit("2FA enabled", "TOTP activated for owner account", "auth")
            except Exception:
                pass
            LOG("[2FA] Enrollment confirmed — 2FA enabled")
            return {"status": "success", "backup_codes": codes}
        except Exception as e:
            ERR(f"[2FA] confirm_enroll error: {e}")
            return {"status": "error", "message": str(e)}

    def totp_disable(self, password):
        try:
            auth = self.db.authenticate_user_by_password(password or "")
            if not auth.get("authenticated") or auth.get("role") == "staff":
                return {"status": "error", "message": "Incorrect owner password."}
            self._tfa_del('totp_secret', 'totp_enabled', 'totp_backup_codes', 'totp_pending_secret')
            try:
                self._audit("2FA disabled", "TOTP removed for owner account", "auth")
            except Exception:
                pass
            LOG("[2FA] Disabled")
            return {"status": "success"}
        except Exception as e:
            ERR(f"[2FA] disable error: {e}")
            return {"status": "error", "message": str(e)}

    def verify_2fa(self, code):
        import json as _j, hashlib as _hl
        try:
            pending = getattr(self, '_pending_2fa', None)
            if not pending:
                return {"status": "error", "message": "Session expired — please sign in again."}
            secret = self._tfa_get('totp_secret')
            raw = ''.join(ch for ch in str(code) if ch.isalnum())
            ok = False
            backup_used = False
            backup_remaining = None
            # 1) TOTP (6 digits)
            if secret and raw.isdigit() and len(raw) == 6:
                ok = self._totp_verify(secret, raw)
            # 2) One-time backup code (8 alphanumerics)
            if not ok and len(raw) >= 8:
                try:
                    codes = _j.loads(self._tfa_get('totp_backup_codes', '[]') or '[]')
                except Exception:
                    codes = []
                h = _hl.sha256(raw[:8].upper().encode()).hexdigest()
                if h in codes:
                    codes.remove(h)
                    self._tfa_set('totp_backup_codes', _j.dumps(codes))
                    ok = True
                    backup_used = True
                    backup_remaining = len(codes)
            if not ok:
                return {"status": "error", "message": "Invalid code. Try again."}
            self._pending_2fa = None
            from datetime import datetime as _dt
            res = self._finalize_login(pending["role"], pending["username"],
                                       pending["landing"], _dt.now(), _dt)
            if backup_used:
                res["backup_used"] = True
                res["backup_remaining"] = backup_remaining
                LOG(f"[2FA] Backup code used — {backup_remaining} remaining")
            return res
        except Exception as e:
            ERR(f"[2FA] verify error: {e}")
            return {"status": "error", "message": str(e)}

    def _finalize_login(self, role, username, landing, now, _dt):
        """Complete a successful owner/staff login (shared by the direct
        password path and the post-2FA path)."""
        self._login_attempts = 0
        self._lockout_until  = None
        self._session_role     = role
        self._session_username = username
        try:
            with self.db._get_connection() as _lc:
                _lc.execute("CREATE TABLE IF NOT EXISTS login_log (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL DEFAULT 'owner', role TEXT NOT NULL DEFAULT 'admin', login_time TEXT NOT NULL DEFAULT (datetime('now')), ip TEXT DEFAULT '')")
                _lc.execute("INSERT INTO login_log(username,role,login_time) VALUES(?,?,?)",
                            (username, role, now.strftime('%Y-%m-%d %H:%M:%S')))
                _lc.commit()
        except Exception as _le:
            LOG(f"[LOGIN] login_log write error: {_le}")
        try:
            lf = self._get_lockout_file()
            if os.path.exists(lf):
                os.remove(lf)
        except Exception:
            pass
        self._audit(f"Login: {username}", f"Role: {role}", "auth")
        try:
            self.bastion.notify_session_active(True)
        except Exception:
            pass
        return {"status": "success", "role": role, "username": username, "landing": landing}

    def verify_login(self, password):
        import json as _json
        try:
            from datetime import datetime as _dt, timedelta as _td
            LOG(f"[LOGIN] verify_login called")
            result = self._do_login_check(password, _dt.now(), _dt, _td)
            LOG(f"[LOGIN] Result: {result.get('status')}")
            def _push():
                time.sleep(0.05)
                try:
                    if self._window:
                        self._window.evaluate_js(
                            f"window.__loginResult && window.__loginResult({_json.dumps(result)})"
                        )
                except: pass
            threading.Thread(target=_push, daemon=True).start()
            return result
        except Exception as e:
            ERR(f"[LOGIN] EXCEPTION: {e}")
            err = {"status":"error","message":str(e)}
            try:
                def _push_err():
                    time.sleep(0.05)
                    try:
                        if self._window:
                            self._window.evaluate_js(
                                f"window.__loginResult && window.__loginResult({_json.dumps(err)})"
                            )
                    except: pass
                threading.Thread(target=_push_err, daemon=True).start()
            except: pass
            return err

    def _do_login_check(self, password, now, _dt, _td):
        # ── BASTION CHECK — comes BEFORE regular lockout logic ──────────
        # Bastion suspension is a separate, more serious state than a
        # normal failed-attempt lockout. Previously this was never
        # actually checked during login at all -- the suspension sat
        # correctly in the DB but no login attempt ever surfaced it,
        # so the BASTION popup never had anything to react to.
        try:
            bastion_status = self.db.bastion_get_status()
        except Exception:
            bastion_status = {'suspended': False}
        if bastion_status.get('suspended'):
            return {
                "status": "bastion_suspended",
                "bastion": bastion_status,
                "message": bastion_status.get('reason', 'Account suspended by BASTION AI.'),
            }

        if self._lockout_until and now < self._lockout_until:
            remaining = int((self._lockout_until - now).total_seconds())
            tier = self._get_rate_tier()
            try:
                _lc2 = self.db._machine_fingerprint()[:8].upper()
            except: _lc2 = "LOCKED01"
            is_permanent = tier["cooldown"] == -1
            return {
                "status": "locked",
                "remaining": remaining,
                "lock_code": _lc2 if tier["severity"] in ("critical", "permanent") else "",
                "tier": self._lockout_tier,
                "severity": tier["severity"],
                "attempts": self._login_attempts,
                "permanent": is_permanent,
                "message": "Account permanently locked. Contact admin." if is_permanent
                           else f"Locked. Try in {remaining // 60}m {remaining % 60:02d}s.",
            }
        elif self._lockout_until and now >= self._lockout_until:
            self._lockout_until = None
        # Check temp password first (one-time unlock)
        try:
            with self.db._get_connection() as _conn:
                _tph = _conn.execute("SELECT value FROM app_config WHERE key='temp_password_hash'").fetchone()
                _tpe = _conn.execute("SELECT value FROM app_config WHERE key='temp_password_expires'").fetchone()
            if _tph and _tpe:
                _expires = float(_tpe['value'])
                if time.time() < _expires and self.db._hash_pw(password) == _tph['value']:
                    # Temp password match — clear it immediately (one-time)
                    with self.db._get_connection() as _conn:
                        _conn.execute("DELETE FROM app_config WHERE key IN ('temp_password_hash','temp_password_expires')")
                        _conn.commit()
                    self._login_attempts=0; self._lockout_until=None
                    self._save_lockout_state()
                    LOG("[RESET] Temp password used — cleared")
                    return {"status":"success","landing":"change_password.html",
                            "role":"admin","username":"owner",
                            "temp_login":True,
                            "message":"Temporary login — please set a new password."}
        except Exception as _e:
            LOG(f"[RESET] Temp check error: {_e}")

        # ── TERMINAL GUARD ────────────────────────────────────────
        # A staff PC holds no local users by design (it joins; it is never
        # set up), so a local password check here can only fail — and each
        # failure burns the shared lockout budget until the login hides its
        # own password box. Staff sign-in is owned by the shop server (see
        # network/cluster_server._login), which throttles per terminal.
        # The one-time temp-password check above stays reachable for all.
        try:
            from network import cluster_client as _cc
            _is_term = _cc.is_terminal()
        except Exception:
            _is_term = False
        if _is_term:
            return {"status": "not_terminal", "attempts": self._login_attempts,
                    "message": "This is a staff terminal — sign-in is checked "
                               "by the main PC. Wait a moment and try again."}
        auth = self.db.authenticate_user_by_password(password)
        if auth["authenticated"]:
            self._record_attempt_audit(True)
            self._login_attempts = 0; self._lockout_tier = 0
            self._lockout_until = None
            self._save_lockout_state()
            role = auth["role"]; username = auth.get("username", "Admin")
            landing = "billing.html" if role == "staff" else "dashboard.html"
            # ── TWO-FACTOR GATE (owner/admin only) ──────────────────────
            # Password is correct; if the owner has 2FA enabled, defer the
            # actual session finalization until verify_2fa() confirms the TOTP
            # or a backup code. Staff logins are never gated by owner 2FA.
            if role != "staff" and self._totp_is_enabled():
                self._pending_2fa = {"role": role, "username": username, "landing": landing}
                LOG(f"[2FA] Password OK for {username}; awaiting TOTP code")
                return {"status": "2fa_required", "username": username}
            return self._finalize_login(role, username, landing, now, _dt)
        self._login_attempts += 1
        self._record_attempt_audit(False)
        tier = self._get_rate_tier()
        cooldown = tier["cooldown"]
        severity = tier["severity"]

        # Find tier index for the response
        tier_idx = 0
        for i, t in enumerate(self._RATE_TIERS):
            if self._login_attempts >= t["threshold"]:
                tier_idx = i
        self._lockout_tier = tier_idx

        if cooldown == -1:
            # Permanent lock — needs admin reset
            self._lockout_until = now + _td(days=365)
            self._save_lockout_state()
            try:
                _lc = self.db._machine_fingerprint()[:8].upper()
            except: _lc = "LOCKED01"
            try:
                with self.db._get_connection() as _cc:
                    _cc.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('lock_code_cache',?)", (_lc,))
                    _cc.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','1')")
                    _cc.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('locked_at',?)",
                                (now.strftime('%Y-%m-%d %H:%M:%S'),))
                    _cc.commit()
            except: pass
            LOG(f"[RATE-LIMIT] PERMANENT LOCK after {self._login_attempts} attempts")
            return {
                "status": "locked", "remaining": -1,
                "attempts": self._login_attempts, "lock_code": _lc,
                "tier": tier_idx, "severity": "permanent", "permanent": True,
                "message": "Account permanently locked. Contact admin for unlock code.",
            }
        elif cooldown > 0:
            # Progressive cooldown
            self._lockout_until = now + _td(seconds=cooldown)
            self._save_lockout_state()
            try:
                _lc = self.db._machine_fingerprint()[:8].upper()
            except: _lc = "LOCKED01"
            if severity in ("critical",):
                try:
                    with self.db._get_connection() as _cc:
                        _cc.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('lock_code_cache',?)", (_lc,))
                        _cc.commit()
                except: pass
            LOG(f"[RATE-LIMIT] Tier {tier_idx} lockout: {cooldown}s after {self._login_attempts} attempts")
            return {
                "status": "locked", "remaining": cooldown,
                "attempts": self._login_attempts,
                "lock_code": _lc if severity in ("critical",) else "",
                "tier": tier_idx, "severity": severity, "permanent": False,
                "message": f"Too many attempts. Locked for {cooldown // 60}m {cooldown % 60:02d}s.",
            }
        else:
            # No cooldown yet — just a warning
            self._save_lockout_state()
            next_tier = self._RATE_TIERS[min(tier_idx + 1, len(self._RATE_TIERS) - 1)]
            left_before_next = next_tier["threshold"] - self._login_attempts
            LOG(f"[RATE-LIMIT] Attempt {self._login_attempts}, tier {tier_idx}, {left_before_next} left before next tier")
            return {
                "status": "error", "attempts": self._login_attempts,
                "left": left_before_next,
                "tier": tier_idx, "severity": severity,
                "next_cooldown": next_tier["cooldown"],
                "message": f"Wrong password. {left_before_next} attempt{'s' if left_before_next != 1 else ''} before lockout.",
            }

    def get_lockout_status(self):
        from datetime import datetime as _dt
        try:
            now = _dt.now()
            if self._lockout_until and now < self._lockout_until:
                remaining = int((self._lockout_until - now).total_seconds())
                tier = self._get_rate_tier()
                try: _lc = self.db._machine_fingerprint()[:8].upper()
                except: _lc = ''
                return {
                    "locked": True, "remaining": remaining,
                    "attempts": self._login_attempts,
                    "lock_code": _lc if tier["severity"] in ("critical", "permanent") else "",
                    "tier": self._lockout_tier,
                    "severity": tier["severity"],
                    "permanent": tier["cooldown"] == -1,
                }
            elif self._lockout_until and now >= self._lockout_until:
                self._lockout_until = None
            tier = self._get_rate_tier()
            next_tier = self._RATE_TIERS[min(self._lockout_tier + 1, len(self._RATE_TIERS) - 1)]
            return {
                "locked": False, "attempts": self._login_attempts,
                "tier": self._lockout_tier, "severity": tier["severity"],
                "next_cooldown": next_tier["cooldown"],
                "left_before_next": next_tier["threshold"] - self._login_attempts,
            }
        except:
            return {"locked": False, "attempts": 0, "tier": 0, "severity": "none"}

    def logout(self):
        self._audit(f"Logout",f"User: {self._session_username}","auth")
        self._session_role=None; self._session_username=None
        self._login_attempts=0; self._lockout_until=None
        try:
            self.bastion.notify_session_active(False)
        except Exception:
            pass
        return {"status":"ok"}

    # -- SETUP -----------------------------------------------------------------
    def save_setup(self, data):
        try:
            if isinstance(data, dict):
                biz_name    = str(data.get('businessName') or data.get('business_name') or '').strip()
                owner_name  = str(data.get('ownerName')    or data.get('owner_name')    or '').strip()
                admin_pass  = str(data.get('adminPass')    or data.get('pin')           or '').strip()
                license_key = str(data.get('licenseKey')   or data.get('license_key')   or '').strip()
                phone       = str(data.get('phone')        or data.get('owner_phone')   or '').strip()
                city        = str(data.get('city')         or '').strip()
                address     = str(data.get('address')      or '').strip()
            else:
                return {"status":"error","message":"Invalid setup data"}
            if not biz_name or not owner_name:
                return {"status":"error","message":"Business name and owner name are required"}
            # GST from the wizard, checked before anything is written. The shop
            # types its registration once, here, instead of discovering the
            # field later in Settings after it has already billed a customer.
            gst_fields = {}
            try:
                from core.gst import build as _gst_build
                gst_fields, gst_err = _gst_build(data)
                if gst_err:
                    return {"status": "error", "message": gst_err, "field": "gstin"}
            except Exception as ge:
                ERR(f"[SETUP] GST block skipped: {ge}")
                gst_fields = {}
            ok = self.db.save_setup(biz_name, owner_name, phone, city, admin_pass, license_key=license_key, address=address)
            if ok:
                # db.save_setup has a fixed signature, so these go in beside it
                # rather than changing a call used elsewhere.
                if gst_fields:
                    try:
                        with self.db._get_connection() as _c:
                            for _k, _v in gst_fields.items():
                                _c.execute(
                                    "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                                    (_k, str(_v).strip()))
                            _c.commit()
                        LOG(f"[SETUP] GST saved (registered={gst_fields.get('gst_registered')})")
                    except Exception as ge2:
                        ERR(f"[SETUP] GST save failed: {ge2}")
                base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath(".")
                # ── Clear old state on new setup ─────────────────────────────
                # Remove .revoked flag so the app doesn't stay locked
                try:
                    flag_path = os.path.join(base, "database", ".revoked")
                    if os.path.exists(flag_path):
                        os.remove(flag_path)
                        LOG("[SETUP] Cleared old .revoked flag")
                except Exception:
                    pass
                # Remove old .license_key cache
                try:
                    old_key_path = os.path.join(base, "database", ".license_key")
                    if os.path.exists(old_key_path):
                        os.remove(old_key_path)
                        LOG("[SETUP] Removed old .license_key cache")
                except Exception:
                    pass
                # ── Save new key ─────────────────────────────────────────────
                if license_key and _is_valid_key_format(license_key):
                    try:
                        key_path = os.path.join(base, "database", ".license_key")
                        open(key_path, "wb").write(encrypt_license_key(license_key))
                        LOG(f"[SETUP] New license key cached: {license_key[:10]}...")
                    except Exception as ke:
                        ERR(f"[SETUP] Key cache error: {ke}")
                    # Start SSE stream with the fresh key
                    threading.Thread(target=self.start_sse_stream, daemon=True).start()
                    # Start subscription sync
                    try:
                        self.sub.set_license_key(license_key)
                        self.sub.set_window(self._window)
                        self.sub.startup_check(license_key)
                        self.sub.start_periodic_sync(30)
                        LOG("[SETUP] Subscription sync started")
                    except Exception as _sub_err:
                        ERR(f"[SETUP] Subscription start error: {_sub_err}")
                return {"status":"success"}
            return {"status":"error","message":"Failed to save setup data"}
        except Exception as e:
            ERR(f"[SETUP] {e}")
            return {"status":"error","message":str(e)}

    def is_setup_done(self):
        try: return self.db.is_setup_done()
        except: return False

    def get_settings(self) -> dict:
        """Return all business settings for the settings page."""
        try:
            keys = ['business_name','owner_name','owner_phone','city','setup_date','license_key',
                    'discount_percent','discount_threshold','address']
            result = {}
            for k in keys:
                result[k] = self.db.get_config(k, '')
            # GST registration. Defaults fill in for a shop set up before these
            # fields existed, so the screen never renders blank boxes with no
            # rates in them.
            try:
                from core.gst import defaults as _gst_defaults
                for k, dflt in _gst_defaults().items():
                    result[k] = self.db.get_config(k, '') or dflt
            except Exception as ge:
                ERR(f"[SETTINGS] GST block unavailable: {ge}")
            return {'status':'success', 'data': result}
        except Exception as e:
            ERR(f"[SETTINGS] get_settings: {e}")
            return {'status':'error', 'message': str(e)}

    def validate_gstin(self, gstin: str) -> dict:
        """Check a GSTIN for the setup and settings screens.

        Offline and instant: the number carries its own check character, plus
        the state and the PAN, so nothing needs to be looked up.
        """
        try:
            from core.gst import validate as _v
            return _v(gstin)
        except Exception as e:
            ERR(f"[GST] validate failed: {e}")
            return {'ok': False, 'error': str(e), 'gstin': str(gstin or ''),
                    'state': '', 'state_code': '', 'pan': '', 'entity_code': ''}

    def update_settings(self, data: dict) -> dict:
        """Update business profile fields."""
        try:
            allowed = ['business_name','owner_name','owner_phone','city','address',
                       'discount_percent','discount_threshold']
            # GST is validated as a block, not field by field: the state and
            # the PAN are read out of the GSTIN, so a number that does not
            # check out must not be stored at all rather than half-stored.
            gst_fields = {}
            if any(k.startswith('gst') or k == 'pan' for k in (data or {})):
                try:
                    from core.gst import build as _gst_build
                    existing = (self.get_settings() or {}).get('data', {})
                    gst_fields, gst_err = _gst_build(data, existing)
                    if gst_err:
                        return {'status':'error','message':gst_err,'field':'gstin'}
                except Exception as ge:
                    ERR(f"[SETTINGS] GST validate failed: {ge}")
                    return {'status':'error','message':f'Could not check the GST details: {ge}'}
            with self.db._get_connection() as conn:
                for k in allowed:
                    if k in data:
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                            (k, str(data[k]).strip())
                        )
                for k, v in gst_fields.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                        (k, str(v).strip())
                    )
                conn.commit()
            LOG(f"[SETTINGS] Updated: {list(data.keys())}")
            return {'status':'success'}
        except Exception as e:
            ERR(f"[SETTINGS] update_settings: {e}")
            return {'status':'error','message':str(e)}

    def get_alert_settings(self) -> dict:
        """Owner security-alert settings for the Settings card."""
        try:
            from database import bastion_ai as _b
            return dict(_b.alert_prefs(self.db))
        except Exception as e:
            ERR(f"[SETTINGS] alert read: {e}")
            return {'owner_alert_email': '', 'alert_min_severity': 'MEDIUM',
                    'quiet_hours': '', 'conduct_watch_enabled': '1'}

    def save_alert_settings(self, data: dict) -> dict:
        """Validate and store the owner's alert settings.

        Rejects a bad email or severity rather than storing what the
        engine cannot use — a saved-but-broken address is how alerts go
        quietly nowhere.
        """
        try:
            d = dict(data or {})
            email = str(d.get('owner_alert_email') or '').strip()
            if email and '@' not in email:
                return {'status': 'error',
                        'message': 'That alert email does not look right.'}
            sev = str(d.get('alert_min_severity') or 'MEDIUM').strip().upper()
            if sev not in ('MEDIUM', 'HIGH', 'CRITICAL'):
                return {'status': 'error',
                        'message': 'Severity must be Medium, High or Critical.'}
            quiet = str(d.get('quiet_hours') or '').strip()
            if quiet:
                import re as _re
                _seen = _re.findall(r'(\d{1,2})(?::(\d{2}))?', quiet)
                _hours = [int(x[0]) for x in _seen[:2]]
                if len(_seen) < 2 or any(h > 23 for h in _hours):
                    return {'status': 'error',
                            'message': 'Quiet hours look like 22-7.'}
            watch = '0' if str(d.get('conduct_watch_enabled',
                                     '1')).strip() == '0' else '1'
            self.db.set_config('owner_alert_email', email)
            self.db.set_config('alert_min_severity', sev)
            self.db.set_config('quiet_hours', quiet)
            self.db.set_config('conduct_watch_enabled', watch)
            LOG("[SETTINGS] Alert settings saved")
            return {'status': 'success'}
        except Exception as e:
            ERR(f"[SETTINGS] alert save: {e}")
            return {'status': 'error', 'message': str(e)}

    def _alert_smtp_creds(self):
        """(sender, password) for alert mail, or ('', '').

        The bastion env sender first; else the shop's own Gmail sender
        from the centre page. One resolution both the test button and
        the engine agree on.
        """
        try:
            sender = str(os.environ.get('AURUM_ALERT_EMAIL_FROM', '') or '')
            pw = str(os.environ.get('AURUM_ALERT_EMAIL_PASSWORD', '') or '')
            if sender and '@' in sender and pw and 'xxxx' not in pw:
                return sender, pw.replace(' ', '')
        except Exception:
            pass
        try:
            sender = str(self.db.get_config('hm_sender_email', '')
                         or '').strip()
            pw = str(self.db.get_config('hm_sender_apppw', '')
                     or '').strip().replace(' ', '')
            if sender and '@' in sender and pw:
                return sender, pw
        except Exception:
            pass
        return '', ''

    def send_test_alert_email(self, email: str) -> dict:
        """Send a real test alert to the given address.

        Says plainly what is missing instead of reporting a sent mail
        that never left.
        """
        try:
            to_addr = str(email or '').strip()
            if '@' not in to_addr:
                return {'status': 'error',
                        'message': 'Enter your alert email first.'}
            sender, pw = self._alert_smtp_creds()
            if not sender:
                return {'status': 'error',
                        'message': 'No sender saved. Save the shop Gmail '
                                   'sender (centre page), then try again.'}
            import smtplib
            import ssl as _ssl
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            text = ('This is a test security alert from AurumOS.\n\n'
                    'Your alert emails are working.')
            msg = MIMEMultipart('alternative')
            msg['From'] = sender
            msg['To'] = to_addr
            msg['Subject'] = 'AurumOS test alert'
            msg.attach(MIMEText(text, 'plain'))
            ctx = _ssl.create_default_context()
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx,
                                  timeout=20) as server:
                server.login(sender, pw)
                server.sendmail(sender, to_addr, msg.as_string())
            LOG(f"[SETTINGS] test alert sent to {to_addr}")
            return {'status': 'success',
                    'message': 'Test sent to %s.' % to_addr}
        except Exception as e:
            ERR(f"[SETTINGS] test alert: {e}")
            return {'status': 'error',
                    'message': 'Could not send: %s' % str(e)[:160]}

    def change_owner_password(self, current_password: str, new_password: str) -> dict:
        """Change the owner (admin) password after verifying the current one."""
        try:
            if len(str(new_password).strip()) < 4:
                return {'status':'error','message':'New password must be at least 4 characters.'}
            # Verify current password
            auth = self.db.authenticate_user('owner', current_password)
            if not auth.get('authenticated'):
                auth2 = self.db.authenticate_user_by_password(current_password)
                if not auth2.get('authenticated'):
                    return {'status':'error','message':'Current password is incorrect.'}
            # Update password
            new_hash = hashlib.sha256(str(new_password).encode('utf-8')).hexdigest()
            with self.db._get_connection() as conn:
                conn.execute(
                    "UPDATE admin_creds SET password=? WHERE id=1",
                    (new_hash,)
                )
                conn.commit()
            LOG("[SETTINGS] Owner password changed")
            return {'status':'success'}
        except Exception as e:
            ERR(f"[SETTINGS] change_owner_password: {e}")
            return {'status':'error','message':str(e)}

    def get_business_name(self):
        try: return self.db.get_business_name()
        except: return "AurumOS"

    # -- STAFF -----------------------------------------------------------------
    def get_all_staff(self):
        try: return self.db.get_all_staff()
        except Exception as e: ERR(f"[STAFF] {e}"); return []

    def add_staff(self, username, password, permissions=None):
        try:
            ok, msg = self.db.add_staff_user(username, password, permissions)
            return {"status":"success","message":msg} if ok else {"status":"error","message":msg}
        except Exception as e:
            return {"status":"error","message":f"Bridge Error: {str(e)}"}

    def delete_staff(self, staff_id):
        try:
            sid = int(staff_id)
            if sid == 1:
                return {"status":"error","message":"Cannot delete the owner account."}
            with self.db._get_connection() as conn:
                conn.execute("DELETE FROM admin_creds WHERE id=?", (sid,))
                conn.commit()
            LOG(f"[STAFF] Deleted staff id={sid}")
            return {"status":"success"}
        except Exception as e:
            ERR(f"[STAFF] delete_staff error: {e}")
            return {"status":"error","message":str(e)}

    def update_staff_password(self, staff_id, new_password, permissions=None):
        try:
            sid = int(staff_id)
            hashed = None
            if new_password and len(str(new_password).strip()) >= 4:
                hashed = hashlib.sha256(str(new_password).encode('utf-8')).hexdigest()
            elif new_password and len(str(new_password).strip()) < 4:
                return {"status":"error","message":"Password must be at least 4 characters."}
            with self.db._get_connection() as conn:
                if hashed is not None:
                    conn.execute(
                        "UPDATE admin_creds SET password=? WHERE id=?",
                        (hashed, sid)
                    )
                if permissions is not None:
                    import json as _json
                    conn.execute(
                        "UPDATE admin_creds SET permissions=? WHERE id=?",
                        (_json.dumps(permissions), sid)
                    )
                conn.commit()
            LOG(f"[STAFF] Password/permissions updated for id={sid}")
            return {"status":"success"}
        except Exception as e:
            ERR(f"[STAFF] update_staff_password error: {e}")
            return {"status":"error","message":str(e)}

    # -- MASTER DATA -----------------------------------------------------------
    def validate_touch_value(self, touch_val):
        try:
            clean = str(touch_val).replace('%','').strip()
            exists = self.db.is_touch_valid(clean)
            if exists: return {"status":"success","valid":True}
            return {"status":"error","valid":False,"message":f"Touch '{clean}' does not exist."}
        except Exception as e:
            return {"status":"error","valid":False,"message":str(e)}

    def add_category(self, code, name):
        return {"status":"success"} if self.db.add_category(code,name) else {"status":"error"}

    def get_categories(self):
        return self.db.get_all_categories()

    def add_touch_group(self, name, value, wastage):
        try:
            return {"status":"success"} if self.db.add_touch_group(name,value,wastage) else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_touch_groups(self):
        return self.db.get_all_touch_groups()

    def add_product_master(self, code, name, category, touch, wastage,
                           making_charge=0, making_type='percent', sale_price=0, hsn=''):
        """Save a product. The retail arguments are optional, so the wholesale
        screens that pass five arguments are unaffected."""
        ok = self.db.add_product_master(code, name, category, touch, wastage,
                                        making_charge, making_type, sale_price, hsn)
        return {"status": "success"} if ok else {"status": "error"}

    # -- CRM ---------------------------------------------------------------
    # The screens call these. Storage is in db_manager, the rules are in
    # core/crm.py; this joins the two and hands back something a page can
    # render without doing arithmetic of its own.

    def crm_dashboard(self, within_days=30, lapsed_days=365):
        """Everything the CRM screen shows, in one call.

        One call rather than eight because the page opens on a shop counter
        over a bridge that is not free, and eight round trips is the
        difference between a screen that appears and one that assembles
        itself while somebody waits.
        """
        try:
            from core import crm
        except Exception as e:
            ERR(f"[CRM] rules unavailable: {e}")
            return {'status': 'error', 'message': str(e)}
        try:
            customers = self.db.crm_customers()
            for c in customers:
                c['segment_computed'] = crm.segment_for(c)

            dues_rows = self.db.crm_dues()
            by_customer = {}
            for row in dues_rows:
                by_customer.setdefault(row['customer'], []).append(row)
            dues = []
            for name, rows in by_customer.items():
                aged = crm.age_dues(rows)
                if aged['total'] <= 0:
                    continue
                aged['customer'] = name
                dues.append(aged)
            dues.sort(key=lambda d: -d['total'])

            overall = {'0-30': 0.0, '31-60': 0.0, '61-90': 0.0, '90+': 0.0}
            for d in dues:
                for k in overall:
                    overall[k] = round(overall[k] + d['buckets'][k], 2)

            enrolments = []
            for e in self.db.crm_enrolments():
                st = crm.scheme_status(e, e.get('payments') or [])
                if st.get('valid'):
                    e.update(st)
                e.pop('payments', None)
                enrolments.append(e)

            spend = sorted(customers, key=lambda c: -float(c.get('lifetime_value') or 0))
            counts = {}
            for c in customers:
                counts[c['segment_computed']] = counts.get(c['segment_computed'], 0) + 1

            return {
                'status': 'success',
                'counts': {
                    'customers': len(customers),
                    'segments': counts,
                    'open_followups': len(self.db.crm_followups('open')),
                    'open_orders': len(self.db.crm_orders('open')),
                    'active_plans': sum(1 for e in enrolments if e.get('status') != 'completed'),
                    'plans_behind': sum(1 for e in enrolments if e.get('status') == 'behind'),
                },
                'occasions': crm.upcoming_occasions(customers, int(within_days or 30)),
                'lapsed': crm.lapsed(customers, int(lapsed_days or 365))[:50],
                'dues': dues[:50],
                'dues_total': overall,
                'followups': self.db.crm_followups('open')[:50],
                'orders': self.db.crm_orders('open')[:50],
                'enrolments': enrolments[:50],
                'top_customers': spend[:10],
            }
        except Exception as e:
            ERR(f"[CRM] dashboard: {e}")
            import traceback as _tb
            _tb.print_exc()
            return {'status': 'error', 'message': str(e)}

    def crm_customers(self):
        try:
            from core import crm
            rows = self.db.crm_customers()
            for c in rows:
                c['segment_computed'] = crm.segment_for(c)
            return rows
        except Exception as e:
            ERR(f"[CRM] customers: {e}")
            return []

    def crm_customer(self, name):
        """One customer with everything attached to them."""
        try:
            from core import crm
            name = str(name or '').strip()
            match = [c for c in self.db.crm_customers()
                     if str(c.get('name') or '').strip() == name]
            if not match:
                return {'status': 'not_found', 'name': name}
            c = match[0]
            c['segment_computed'] = crm.segment_for(c)
            rows = [r for r in self.db.crm_dues() if r['customer'] == name]
            plans = []
            for e in self.db.crm_enrolments(name):
                st = crm.scheme_status(e, e.get('payments') or [])
                if st.get('valid'):
                    e.update(st)
                plans.append(e)
            return {
                'status': 'success',
                'customer': c,
                'ageing': crm.age_dues(rows),
                'over_limit': crm.over_limit(
                    crm.age_dues(rows)['total'], c.get('cash_limit')),
                'purchases': self.db.get_customer_purchases(name, c.get('phone') or ''),
                'notes': self.db.crm_notes(name),
                'plans': plans,
                'loyalty_points': self.db.crm_loyalty_balance(name),
            }
        except Exception as e:
            ERR(f"[CRM] customer {name}: {e}")
            return {'status': 'error', 'message': str(e)}

    def crm_save_customer(self, data):
        return self.db.crm_save_customer(data or {})

    def crm_add_note(self, customer, note, kind='note'):
        who = getattr(self, '_session_username', '') or ''
        ok = self.db.crm_add_note(customer, note, kind, who)
        return {'status': 'success'} if ok else {'status': 'error'}

    def crm_notes(self, customer):
        return self.db.crm_notes(customer)

    def crm_add_followup(self, customer, task, due_date='', owner=''):
        ok = self.db.crm_add_followup(customer, task, due_date, owner)
        return {'status': 'success'} if ok else {'status': 'error'}

    def crm_close_followup(self, followup_id):
        ok = self.db.crm_close_followup(followup_id)
        return {'status': 'success'} if ok else {'status': 'error'}

    def crm_followups(self, status='open'):
        return self.db.crm_followups(status)

    def crm_orders(self, status='open'):
        return self.db.crm_orders(status)

    # -- WHATSAPP ----------------------------------------------------------
    # Every one of these returns a link that opens WhatsApp with the message
    # already written, for a person to read and send. Nothing here sends
    # anything by itself: a number that blasts a customer list unattended gets
    # banned, and it is the number printed on every bill.

    def _wa_shop(self):
        try:
            return self.db.get_config('business_name', '') or 'our shop'
        except Exception:
            return 'our shop'

    # -- PHOTOS ------------------------------------------------------------
    # A wa.me link carries text and nothing else, so a picture cannot ride
    # along with it. The way round that without a paid provider is the
    # clipboard: put the image on it, open WhatsApp with the caption already
    # written, and the shopkeeper presses Ctrl+V then send.

    def _photo_dir(self):
        from core import photos as _ph
        d = _ph.folder(_get_db_base())
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def photo_pick(self, tag_id):
        """Choose a picture for a piece and keep a copy.

        The original is left where it was. A shop moves photos off a phone
        and deletes them a week later, and a picture that vanishes from the
        stock list because somebody tidied their Downloads folder is worse
        than no picture at all.
        """
        try:
            from core import photos as _ph
            import shutil
            tag = str(tag_id or '').strip()
            if not tag:
                return {'status': 'error', 'message': 'No item chosen.'}
            if not self._window:
                return {'status': 'error', 'message': 'No window to open a file dialog from.'}

            try:
                import webview as _wv
                picked = self._window.create_file_dialog(
                    _wv.OPEN_DIALOG, allow_multiple=False,
                    file_types=('Pictures (*.jpg;*.jpeg;*.png;*.webp)',))
            except Exception as de:
                ERR(f"[PHOTO] dialog: {de}")
                return {'status': 'error', 'message': f'Could not open the file chooser: {de}'}
            if not picked:
                return {'status': 'cancelled'}
            src = picked[0] if isinstance(picked, (list, tuple)) else str(picked)

            try:
                size = os.path.getsize(src)
            except Exception:
                return {'status': 'error', 'message': 'Could not read that file.'}
            ok, why = _ph.check(src, size)
            if not ok:
                return {'status': 'error', 'message': why}

            name = _ph.safe_name(tag, src)
            dest = os.path.join(self._photo_dir(), name)
            shutil.copyfile(src, dest)
            self.db.set_item_photo(tag, name)
            LOG(f"[PHOTO] {tag} -> {name}")
            try:
                url = Path(dest).as_uri()
            except Exception:
                url = ''
            # url is returned so the Receipt panel can preview instantly,
            # even when the piece has no saved row yet (photo picked before
            # Save Entry) and photo_path would otherwise find nothing.
            return {'status': 'success', 'photo': name, 'path': dest, 'url': url}
        except Exception as e:
            ERR(f"[PHOTO] pick: {e}")
            return {'status': 'error', 'message': str(e)}

    def photo_remove(self, tag_id):
        """Forget the picture for a piece. The file is left on disk, because
        deleting a shop's only copy of a photograph is not this app's call."""
        ok = self.db.set_item_photo(tag_id, '')
        return {'status': 'success' if ok else 'error'}

    def photo_path(self, tag_id):
        """Where a piece's picture is, for showing it on a screen."""
        try:
            from core import photos as _ph
            tag = str(tag_id or '').strip()
            row = self.db.get_item_by_tag(tag) or {}
            name = str(row.get('photo') or '')
            base = _get_db_base()
            p = _ph.path_for(base, name) if name else ''
            if p and os.path.isfile(p):
                return {'status': 'success', 'path': p,
                        'url': Path(p).as_uri()}
            # Fallback: a photo picked for a piece before its row was saved
            # (or whose row lost the name) still sits in the folder under the
            # tag's name. Find it, heal the row, and serve it, so the file is
            # never orphaned and the preview never blanks for want of a row.
            try:
                stem = _ph.safe_name(tag, 'x.jpg')[:-4]
                d = _ph.folder(base)
                if stem and stem != 'ITEM' and os.path.isdir(d):
                    for f in sorted(os.listdir(d)):
                        fn, ext = os.path.splitext(f)
                        if ext.lower() in _ph.ALLOWED and (
                                fn.upper() == stem or fn.upper().startswith(stem + '-')):
                            fp = os.path.join(d, f)
                            if os.path.isfile(fp):
                                try:
                                    self.db.set_item_photo(tag, f)
                                except Exception:
                                    pass
                                return {'status': 'success', 'path': fp,
                                        'url': Path(fp).as_uri()}
            except Exception:
                pass
            return {'status': 'none' if not name else 'missing', 'path': p}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def photo_to_clipboard(self, tag_id):
        """Put a piece's picture on the Windows clipboard.

        Set-Clipboard with a file path puts the file itself on the clipboard,
        which WhatsApp pastes as an attachment. Copying the pixels instead
        would paste a screenshot-quality image and lose the filename.
        """
        try:
            res = self.photo_path(tag_id)
            if res.get('status') != 'success':
                return {'status': 'error',
                        'message': 'That piece has no picture saved.'}
            p = res['path']
            r = _powershell_hidden(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                 'Set-Clipboard -LiteralPath $env:AURUM_PHOTO'],
                env=dict(os.environ, AURUM_PHOTO=p),
                timeout=30)
            if r.returncode != 0:
                return {'status': 'error',
                        'message': (r.stderr or 'Could not copy the picture.').strip()[:200]}
            return {'status': 'success', 'path': p}
        except Exception as e:
            ERR(f"[PHOTO] clipboard: {e}")
            return {'status': 'error', 'message': str(e)}

    def photo_reveal(self, tag_id):
        """Open the folder with the picture selected, for dragging it in by
        hand when the clipboard is not wanted."""
        try:
            res = self.photo_path(tag_id)
            if res.get('status') != 'success':
                return {'status': 'error', 'message': 'That piece has no picture saved.'}
            import subprocess
            subprocess.Popen(['explorer', '/select,', os.path.normpath(res['path'])])
            return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def wa_send_item(self, customer_name, tag_id, note=''):
        """The whole flow: caption written, picture on the clipboard,
        WhatsApp open. One paste and one send left to do."""
        try:
            from core import photos as _ph
            from core import whatsapp as _wa
            item = self.db.get_item_by_tag(tag_id) or {}
            if not item:
                return {'status': 'error', 'message': 'That piece is not in stock.'}
            name = str(customer_name or '').strip()
            match = [c for c in self.db.crm_customers()
                     if str(c.get('name') or '').strip() == name]
            if not match:
                return {'status': 'not_found', 'name': name}
            ok, why = _wa.can_message(match[0])
            if not ok:
                return {'status': 'blocked', 'reason': why}

            text = _ph.caption(item, self._wa_shop(), note)
            phone = _wa.normalise_phone(match[0].get('whatsapp') or match[0].get('phone'))
            clip = self.photo_to_clipboard(tag_id) if item.get('photo') else {'status': 'none'}
            return {
                'status': 'success',
                'name': name,
                'phone': phone,
                'text': text,
                'url': _wa.link(phone, text),
                'photo_copied': clip.get('status') == 'success',
                'has_photo': bool(item.get('photo')),
                'hint': (('The picture is on the clipboard. In WhatsApp press '
                          'Ctrl+V, then send.') if clip.get('status') == 'success'
                         else ('This piece has a picture but it could not be '
                               'copied: ' + str(clip.get('message') or 'unknown')
                               + '. Use Show file and drag it in.')
                         if item.get('photo')
                         else ('No picture saved for this piece — the message '
                               'will go as text.')),
            }
        except Exception as e:
            ERR(f"[PHOTO] send: {e}")
            return {'status': 'error', 'message': str(e)}

    def items_with_photos(self, limit=200):
        return self.db.items_with_photos(limit)

    def wa_message(self, customer_name, kind, context=None):
        """One message for one customer, ready to review."""
        try:
            from core import whatsapp as _wa
            name = str(customer_name or '').strip()
            match = [c for c in self.db.crm_customers()
                     if str(c.get('name') or '').strip() == name]
            if not match:
                return {'status': 'not_found', 'name': name}
            msg = _wa.build(match[0], kind, context or {}, self._wa_shop())
            msg['status'] = 'success' if msg['ok'] else 'blocked'
            return msg
        except Exception as e:
            ERR(f"[WA] message: {e}")
            return {'status': 'error', 'message': str(e)}

    # ══════════════════════════════════════════════════════════════════
    #  DEAD STOCK
    #  The metal that has not moved. The largest idle sum in most shops and
    #  the one nothing else in the software was measuring.
    # ══════════════════════════════════════════════════════════════════

    def dead_stock_report(self, threshold_days=0, limit=5000):
        """Everything the screen shows, in one call.

        The shop's mode decides what is said out loud, not what is counted:
        a retail owner reads rupees, a wholesaler reads fine weight. Both
        get the same rows underneath.
        """
        try:
            from core import deadstock as _ds
            rows = self.db.dead_stock_rows(limit)
            rate = self.db.latest_gold_rate()
            days = int(threshold_days or 0) or _ds.DEFAULT_DEAD_DAYS
            out = _ds.report(rows, rate, days, None, self._software_type())
            out['headline'] = _ds.headline(out)
            out['choices'] = list(_ds.CHOICES)
            out['status'] = 'success'
            return out
        except Exception as e:
            ERR(f"[DEAD] report: {e}")
            return {'status': 'error', 'message': str(e)}

    def dead_stock_export(self, threshold_days=0):
        """The dead rows as a CSV file the shop can open or hand to a karigar.

        Written to the shop's own folder rather than offered as a download,
        because the window has no download of its own and a path a person can
        find is more use than a file that vanishes.
        """
        try:
            from core import deadstock as _ds
            import csv as _csv, datetime as _dt

            rep = self.dead_stock_report(threshold_days)
            if rep.get('status') != 'success':
                return rep
            rows = rep.get('rows') or []
            if not rows:
                return {'status': 'empty',
                        'message': 'Nothing has been sitting that long.'}

            base = os.path.dirname(sys.executable if getattr(sys, 'frozen', False)
                                   else os.path.abspath(__file__))
            folder = os.path.join(base, 'exports')
            os.makedirs(folder, exist_ok=True)
            name = 'dead_stock_%s.csv' % _dt.datetime.now().strftime('%Y%m%d_%H%M')
            path = os.path.join(folder, name)

            fields = [('tag_id', 'Tag'), ('it_name', 'Item'),
                      ('category', 'Category'), ('karat', 'Purity'),
                      ('gr_wt', 'Gross wt'), ('fine_wt', 'Fine wt'),
                      ('entry_date', 'In stock since'), ('age_days', 'Days'),
                      ('band_label', 'Age'), ('metal_value', 'Metal value'),
                      ('sale_price', 'Ticket price'), ('huid', 'HUID')]
            with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
                w = _csv.writer(fh)
                w.writerow([label for _k, label in fields])
                for r in rows:
                    w.writerow([r.get(k, '') for k, _label in fields])

            return {'status': 'success', 'path': path, 'rows': len(rows),
                    'folder': folder}
        except Exception as e:
            ERR(f"[DEAD] export: {e}")
            return {'status': 'error', 'message': str(e)}

    def dead_stock_reveal(self, path=''):
        """Open the folder the export landed in."""
        try:
            import subprocess
            target = str(path or '').strip()
            if not target or not os.path.exists(target):
                return {'status': 'error', 'message': 'That file is not there.'}
            subprocess.Popen(['explorer', '/select,', os.path.normpath(target)])
            return {'status': 'success'}
        except Exception as e:
            ERR(f"[DEAD] reveal: {e}")
            return {'status': 'error', 'message': str(e)}

    # ══════════════════════════════════════════════════════════════════
    #  HALLMARKING
    #  Gold jewellery may not be sold in India without a hallmark. Stock
    #  goes to an assaying centre, comes back struck with a six-character
    #  number, and only then can be billed. The money sitting in the gap
    #  is what this panel measures.
    # ══════════════════════════════════════════════════════════════════

    def hallmark_dashboard(self, limit=500):
        """Everything the panel shows, in one call.

        One call rather than five because the page opens on a shop counter
        over a bridge that is not free, and because five separate answers
        can disagree with each other about what is still at the centre.
        """
        try:
            from core import hallmark as _hm
            self.db.ensure_hallmark_tables()
            pending = self.db.hm_pending_stock(limit)
            lots = self.db.hm_lots(True, 200)
            out = _hm.dashboard(pending, lots)
            try:
                _sm = self.db.hm_stock_marked()
                out['stock_marked'] = {
                    'pieces': len(_sm),
                    'gross_wt': round(sum(float(r['gr_wt'] or 0)
                                          for r in _sm), 3),
                }
            except Exception:
                out['stock_marked'] = {'pieces': 0, 'gross_wt': 0.0}
            out['status'] = 'success'
            out['pending'] = pending
            out['centres'] = self.db.hm_centres()
            out['registration'] = self.db.get_config('bis_registration', '')
            out['next_lot_no'] = _hm.next_lot_no(self.db.hm_lot_numbers())
            out['defaults'] = {
                'per_piece': _hm.DEFAULT_PER_PIECE,
                'min_charge': _hm.DEFAULT_MIN_CHARGE,
                'turnaround_days': _hm.DEFAULT_TURNAROUND_DAYS,
                'gst_rate': _hm.GST_RATE,
            }
            return out
        except Exception as e:
            ERR(f"[HM] dashboard: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_quote(self, pieces, per_piece='', min_charge=''):
        """What a lot of this size would cost, and how many more pieces
        would ride along inside the minimum for nothing."""
        try:
            from core import hallmark as _hm
            q = _hm.charge_for(pieces, per_piece, min_charge)
            q['free_slots'] = _hm.worth_sending(pieces, per_piece, min_charge)
            q['status'] = 'success'
            return q
        except Exception as e:
            ERR(f"[HM] quote: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_save_centre(self, data):
        """Add or edit an assaying centre."""
        try:
            d = dict(data or {})
            if not str(d.get('name') or '').strip():
                return {'status': 'error', 'message': 'Give the centre a name.'}
            cid = self.db.hm_save_centre(d)
            if not cid:
                return {'status': 'error', 'message': 'Could not save.'}
            return {'status': 'success', 'id': cid}
        except Exception as e:
            ERR(f"[HM] save centre: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_set_registration(self, number):
        """The shop's own BIS registration, which goes on the challan."""
        try:
            self.db.set_config('bis_registration',
                               str(number or '').strip().upper())
            return {'status': 'success'}
        except Exception as e:
            ERR(f"[HM] registration: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_check_pieces(self, tag_ids):
        """Which of these may go, and why the rest may not.

        Answered before the lot is packed, because a centre that refuses a
        piece sends the whole lot back and charges for the trip.
        """
        try:
            from core import hallmark as _hm
            wanted = {str(t).strip() for t in (tag_ids or []) if str(t).strip()}
            ok, refused = [], []
            for it in self.db.hm_pending_stock(2000):
                if it.get('tag_id') not in wanted:
                    continue
                fine, why = _hm.can_send(it)
                row = dict(it)
                row['grade'] = _hm.grade_mark(it.get('karat'))
                if fine:
                    ok.append(row)
                else:
                    row['reason'] = why
                    refused.append(row)
            found = {r['tag_id'] for r in ok} | {r['tag_id'] for r in refused}
            for missing in sorted(wanted - found):
                refused.append({'tag_id': missing,
                                'reason': 'Not in stock, or already away.'})
            return {'status': 'success', 'ok': ok, 'refused': refused}
        except Exception as e:
            ERR(f"[HM] check pieces: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_create_lot(self, centre_id, tag_ids, sent_date='', note=''):
        """Pack a lot and send it. Refuses rather than sending a piece the
        centre would reject."""
        try:
            from core import hallmark as _hm
            checked = self.hallmark_check_pieces(tag_ids)
            if checked.get('status') != 'success':
                return checked
            if checked['refused']:
                first = checked['refused'][0]
                return {'status': 'refused', 'refused': checked['refused'],
                        'message': '%s: %s' % (first.get('tag_id'),
                                               first.get('reason'))}
            if not checked['ok']:
                return {'status': 'error', 'message': 'No pieces chosen.'}

            centre = {}
            for c in self.db.hm_centres():
                if int(c.get('id') or 0) == int(centre_id or 0):
                    centre = c
                    break
            if not centre:
                return {'status': 'error',
                        'message': 'Choose which centre it is going to.'}

            sent = str(sent_date or '').strip() or _hm.today().isoformat()
            days = int(centre.get('turnaround_days')
                       or _hm.DEFAULT_TURNAROUND_DAYS)
            charge = _hm.charge_for(len(checked['ok']), centre.get('per_piece'),
                                    centre.get('min_charge'))
            lot_no = _hm.next_lot_no(self.db.hm_lot_numbers(), sent)
            made = self.db.hm_create_lot(
                lot_no, centre, [r['tag_id'] for r in checked['ok']],
                sent, _hm.due_date(sent, days), charge, note)
            if made.get('status') == 'success':
                made['charge'] = charge
                made['expected_date'] = _hm.due_date(sent, days)
                made['centre'] = centre.get('name', '')
            return made
        except Exception as e:
            ERR(f"[HM] create lot: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_lot(self, lot_id):
        """One lot with its pieces, for the receiving screen."""
        try:
            from core import hallmark as _hm
            lot = self.db.hm_lot(lot_id)
            if not lot:
                return {'status': 'not_found'}
            lot['state'] = _hm.lot_state(lot)
            lot['days_out'] = _hm.days_out(lot.get('sent_date'), None,
                                           lot.get('received_date'))
            for it in lot['items']:
                it['grade'] = _hm.grade_mark(it.get('karat'))
            lot['summary'] = _hm.summarise(lot['items'])
            lot['status_code'] = lot.get('status')
            lot['status'] = 'success'
            return lot
        except Exception as e:
            ERR(f"[HM] lot: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_receive(self, lot_id, rows, received_date=''):
        """Book a lot back in.

        Every row is checked before any of them is written, so a single
        mistyped number cannot leave half a lot recorded and half not.
        """
        try:
            from core import hallmark as _hm
            lot = self.db.hm_lot(lot_id)
            if not lot:
                return {'status': 'not_found'}
            if str(lot.get('status') or '') == 'cancelled':
                return {'status': 'error',
                        'message': 'That lot was cancelled.'}

            rows = list(rows or [])
            dupes = _hm.duplicate_huids(rows)
            if dupes:
                return {'status': 'error',
                        'message': 'The same number is on more than one '
                                   'piece: ' + ', '.join(dupes)}
            seen = self.db.hm_huids_in_use(int(lot_id))
            problems = []
            clean = []
            for r in rows:
                row = dict(r)
                row['huid'] = _hm.normalise_huid(row.get('huid'))
                fine, why = _hm.check_receipt(row, seen)
                if not fine:
                    problems.append({'tag_id': row.get('tag_id'),
                                     'reason': why})
                clean.append(row)
            if problems:
                return {'status': 'refused', 'refused': problems,
                        'message': '%s: %s' % (problems[0]['tag_id'],
                                               problems[0]['reason'])}

            when = str(received_date or '').strip() or _hm.today().isoformat()
            got = self.db.hm_receive(lot_id, clean, when)
            if got.get('status') == 'success':
                got['warnings'] = [
                    {'tag_id': r.get('tag_id'),
                     'note': _hm.huid_warning(r.get('huid'))}
                    for r in clean if _hm.huid_warning(r.get('huid'))]
            return got
        except Exception as e:
            ERR(f"[HM] receive: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_cancel_lot(self, lot_id):
        """A lot that never left. Its pieces go back in the queue."""
        try:
            return self.db.hm_cancel_lot(lot_id)
        except Exception as e:
            ERR(f"[HM] cancel: {e}")
            return {'status': 'error', 'message': str(e)}

    def hallmark_find(self, term):
        """Trace a piece by its number or its tag, which is the question
        a BIS inspector asks."""
        try:
            rows = self.db.hm_find(term)
            return {'status': 'success', 'rows': rows, 'count': len(rows)}
        except Exception as e:
            ERR(f"[HM] find: {e}")
            return {'status': 'error', 'message': str(e)}

    def _hm_portal_base(self):
        """Where portal links point.

        Defaults to the public domain, which the owner points at the shop
        PC (DNS plus an open firewall port); the Portal address card holds
        an override for testing or a custom domain. http, not https: the
        shop server speaks plain HTTP, so https links would open nowhere
        without a proxy terminating TLS in front of it.
        """
        try:
            base = str(self.db.get_config('hm_portal_base', '')
                       or '').strip().rstrip('/')
        except Exception:
            base = ''
        if not base:
            base = 'http://aurumos.in'
        return base

    def hm_portal_base_get(self):
        try:
            return {'status': 'success', 'base': self._hm_portal_base(),
                    'custom': bool(str(self.db.get_config(
                        'hm_portal_base', '') or '').strip())}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def hm_portal_base_save(self, base=''):
        try:
            base = str(base or '').strip().rstrip('/')
            if base and not base.startswith(('http://', 'https://')):
                return {'status': 'error',
                        'message': 'Start the address with http:// or https://.'}
            self.db.set_config('hm_portal_base', base)
            return {'status': 'success', 'base': self._hm_portal_base()}
        except Exception as e:
            ERR(f"[HM] portal base: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_share_url(self, lot_no=''):
        """A web address for the assaying centre.

        Built on the shop's portal address, never on a PC name or LAN
        address. The centre opens it in any browser: one lot with every
        piece, or the whole Kanban board when no lot is named.
        """
        try:
            base = self._hm_portal_base()
        except Exception:
            base = 'http://localhost:58903'
        board = base + '/hm/board'
        lot = str(lot_no or '').strip()
        url = board + ('#lot=' + lot if lot else '')
        return {'status': 'success', 'url': url, 'board': board}

    # ── centre portal: directory, detail, emailed links ──────────────
    def hm_centre_overview(self):
        """Every assaying centre with live lot counts, for the table."""
        try:
            from network import hm_share as _hms
            return _hms.centres_overview(self.db)
        except Exception as e:
            ERR(f"[HM] centres overview: {e}")
            return {'status': 'error', 'message': str(e), 'centres': []}

    def hm_centre_detail(self, centre_id):
        """One centre with its lots split for the tabs."""
        try:
            from network import hm_share as _hms
            return _hms.centre_detail(self.db, centre_id)
        except Exception as e:
            ERR(f"[HM] centre detail: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_save_centre_full(self, data):
        """Save a centre including its email, in one call."""
        try:
            from network import hm_share as _hms
            d = dict(data or {})
            cid = self.db.hm_save_centre(d)
            if not cid:
                return {'status': 'error',
                        'message': 'Could not save.'}
            email = str(d.get('email') or '').strip()
            if email:
                _hms.ensure_stage_column(self.db)
                try:
                    with self.db._get_connection() as conn:
                        conn.execute(
                            "UPDATE hallmark_centres SET email=? WHERE id=?",
                            (email, cid))
                        conn.commit()
                except Exception:
                    pass
            return {'status': 'success', 'id': cid}
        except Exception as e:
            ERR(f"[HM] save centre full: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_email_settings_get(self):
        """The Gmail the shop sends portal links from (app password)."""
        try:
            return {'status': 'success',
                    'sender': self.db.get_config('hm_sender_email', '') or '',
                    'has_password': bool(
                        self.db.get_config('hm_sender_apppw', ''))}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def hm_email_settings_save(self, sender='', app_password=''):
        try:
            sender = str(sender or '').strip()
            if sender and '@' not in sender:
                return {'status': 'error',
                        'message': 'That sender email looks wrong.'}
            self.db.set_config('hm_sender_email', sender)
            if str(app_password or '').strip():
                self.db.set_config('hm_sender_apppw',
                                   str(app_password).strip())
            return {'status': 'success'}
        except Exception as e:
            ERR(f"[HM] email settings: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_centre_pin(self, centre_id, pin=''):
        """Set (or clear, with a blank) a centre's portal login PIN."""
        try:
            from network import hm_share as _hms
            return _hms.set_centre_pin(self.db, centre_id, pin)
        except Exception as e:
            ERR(f"[HM] centre pin: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_centre_pin_history(self, centre_id):
        """Saved logins table for the centre page."""
        try:
            from network import hm_share as _hms
            return {'status': 'success',
                    'pins': _hms.pin_history(self.db, centre_id)}
        except Exception as e:
            ERR(f"[HM] pin history: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_centre_pin_forget(self, centre_id, pin=''):
        """Delete one saved login; clears the active one too if it matches."""
        try:
            from network import hm_share as _hms
            return _hms.pin_forget(self.db, centre_id, pin)
        except Exception as e:
            ERR(f"[HM] pin forget: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_portal_link(self, centre_id):
        """Mint the centre's portal link without emailing it."""
        try:
            from network import hm_share as _hms
            with self.db._get_connection() as conn:
                row = conn.execute(
                    "SELECT email FROM hallmark_centres WHERE id=?",
                    (int(centre_id or 0),)).fetchone()
            issued = _hms.portal_issue(
                self.db, centre_id, row['email'] if row else '')
            if issued.get('status') != 'success':
                # No email saved yet: the page asks for one first.
                return issued
            url = _hms.portal_url(self._hm_portal_base(),
                                  _hms.shop_slug(self.db),
                                  _hms.centre_public_uid(self.db, centre_id))
            return {'status': 'success', 'url': url,
                    'email': issued['email'],
                    'centre': issued.get('centre', '')}
        except Exception as e:
            ERR(f"[HM] portal link: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_portal_send(self, centre_id, email=''):
        """Mint the centre's portal link and email it, in one click.

        The email goes out over Gmail with the shop's own sender address
        and app password (saved once on the centre page). Returns the link
        too, so the owner can paste it into WhatsApp instead.
        """
        try:
            from network import hm_share as _hms
            issued = _hms.portal_issue(self.db, centre_id, email)
            if issued.get('status') != 'success':
                return issued
            url = _hms.portal_url(self._hm_portal_base(),
                                  _hms.shop_slug(self.db),
                                  _hms.centre_public_uid(self.db, centre_id))
            sent = self._hm_send_portal_email(
                issued['email'], issued.get('centre', ''), url)
            out = {'status': 'success' if sent.get('sent') else 'error',
                   'url': url, 'email': issued['email'],
                   'centre': issued.get('centre', '')}
            if not sent.get('sent'):
                out['message'] = sent.get('message',
                                          'Link made, email did not go.')
            return out
        except Exception as e:
            ERR(f"[HM] portal send: {e}")
            return {'status': 'error', 'message': str(e)}

    def _hm_send_portal_email(self, to_addr, centre_name, url):
        """Send one portal link. Returns {'sent': bool, 'message': str}."""
        try:
            sender = str(self.db.get_config('hm_sender_email', '')
                         or '').strip()
            app_pw = str(self.db.get_config('hm_sender_apppw', '')
                         or '').strip().replace(' ', '')
            if '@' not in sender or not app_pw:
                return {'sent': False,
                        'message': 'Save the sender Gmail and app password '
                                   'first (email section below).'}
            shop = self.db.get_config('business_name', '') or 'AurumOS shop'
            subject = f'{shop} hallmark portal'
            text = (f'Hello {centre_name or "team"},\n\n'
                    f'{shop} shares its hallmark lots with you here:\n{url}\n\n'
                    f'Open it in any browser to see each lot and its pieces, '
                    f'mark pieces pass or fail, and move lots across the '
                    f'board. The link is yours alone; ask the shop for a new '
                    f'one if you lose it.\n')
            html = (f'<p>Hello {centre_name or "team"},</p>'
                    f'<p>{shop} shares its hallmark lots with you here:<br>'
                    f'<a href="{url}">{url}</a></p>'
                    f'<p>Open it in any browser to see each lot and its '
                    f'pieces, mark pieces pass or fail, and move lots '
                    f'across the board.</p>')
            import smtplib
            import ssl as _ssl
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            msg = MIMEMultipart('alternative')
            msg['From'] = sender
            msg['To'] = to_addr
            msg['Subject'] = subject
            msg.attach(MIMEText(text, 'plain'))
            msg.attach(MIMEText(html, 'html'))
            ctx = _ssl.create_default_context()
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx,
                                  timeout=20) as server:
                server.login(sender, app_pw)
                server.sendmail(sender, to_addr, msg.as_string())
            LOG(f"[HM] portal link emailed to {to_addr}")
            return {'sent': True, 'message': 'Email sent.'}
        except Exception as e:
            ERR(f"[HM] portal email: {e}")
            return {'sent': False, 'message': str(e)[:200]}

    def wa_wishes_today(self, within_days=0):
        """Today's birthdays and anniversaries, each with a message written.

        Defaults to today only. A wish is a thing you send on the day, and a
        list reaching a month ahead is a list nobody works through.
        """
        try:
            from core import whatsapp as _wa
            from core import crm as _crm
            shop = self._wa_shop()
            customers = self.db.crm_customers()
            out = []
            for occ in _crm.upcoming_occasions(customers, int(within_days or 0)):
                already = self.db.wa_sent_since(occ['occasion'], 300)
                if occ['name'] in already:
                    continue
                match = [c for c in customers
                         if str(c.get('name') or '').strip() == occ['name']]
                if not match:
                    continue
                msg = _wa.build(match[0], occ['occasion'], {}, shop)
                msg['days_until'] = occ['days_until']
                msg['occasion'] = occ['occasion']
                out.append(msg)
            return {'status': 'success', 'wishes': out,
                    'sendable': sum(1 for m in out if m['ok'])}
        except Exception as e:
            ERR(f"[WA] wishes: {e}")
            return {'status': 'error', 'message': str(e), 'wishes': []}

    def wa_campaign(self, kind, context=None, segment='all'):
        """A queue for a message going to more than one customer.

        Returns the whole queue plus everyone left out and why, so the shop
        can see who is missing a number and who has opted out before it starts
        rather than wondering afterwards.
        """
        try:
            from core import whatsapp as _wa
            from core import crm as _crm
            customers = self.db.crm_customers()
            seg = str(segment or 'all').strip().lower()
            if seg and seg != 'all':
                customers = [c for c in customers
                             if _crm.segment_for(c) == seg]
            already = self.db.wa_sent_since(kind, 1) if kind in (
                'birthday', 'anniversary') else set()
            res = _wa.build_many(customers, kind, context or {},
                                 self._wa_shop(), already)
            res['status'] = 'success'
            res['segment'] = seg
            return res
        except Exception as e:
            ERR(f"[WA] campaign: {e}")
            return {'status': 'error', 'message': str(e), 'queue': [], 'skipped': []}

    def wa_mark_sent(self, customer_name, kind, phone='', body=''):
        """Record that one message went out. Called after the link is opened,
        which is the only moment the app can know about."""
        ok = self.db.wa_log_send(customer_name, kind, phone, body)
        return {'status': 'success' if ok else 'error'}

    def wa_opt_out(self, customer_name, opted_out=True):
        ok = self.db.wa_set_opt_out(customer_name, bool(opted_out))
        return {'status': 'success' if ok else 'error'}

    def wa_history(self, customer_name='', limit=200):
        return self.db.wa_history(customer_name, limit)

    def wa_open(self, url):
        """Open a wa.me link in the default browser, which hands it to
        WhatsApp. The message is written; a person still presses send."""
        try:
            u = str(url or '')
            if not u.startswith('https://wa.me/'):
                return {'status': 'error', 'message': 'Not a WhatsApp link.'}
            import webbrowser
            webbrowser.open(u)
            return {'status': 'success'}
        except Exception as e:
            ERR(f"[WA] open: {e}")
            return {'status': 'error', 'message': str(e)}

    def crm_close_order(self, order_id):
        ok = self.db.crm_close_order(order_id)
        return {'status': 'success'} if ok else {'status': 'error'}

    def crm_save_order(self, data):
        return self.db.crm_save_order(data or {})

    def crm_schemes(self, active_only=True):
        return self.db.crm_schemes(bool(active_only))

    def crm_save_scheme(self, data):
        return self.db.crm_save_scheme(data or {})

    def crm_enrol(self, scheme_id, customer, start_date=''):
        return self.db.crm_enrol(scheme_id, customer, start_date)

    def crm_enrolments(self, customer=''):
        try:
            from core import crm
            out = []
            for e in self.db.crm_enrolments(customer):
                st = crm.scheme_status(e, e.get('payments') or [])
                if st.get('valid'):
                    e.update(st)
                out.append(e)
            return out
        except Exception as e:
            ERR(f"[CRM] enrolments: {e}")
            return []

    def crm_add_payment(self, enrolment_id, amount, paid_date='', mode='cash'):
        return self.db.crm_add_payment(enrolment_id, amount, paid_date, mode)

    def crm_scheme_schedule(self, start_date, months, monthly_amount):
        """The instalment dates for a plan, so the screen can show them before
        anyone is enrolled."""
        try:
            from core import crm
            return crm.scheme_schedule(start_date, months, monthly_amount)
        except Exception:
            return []

    def crm_loyalty_balance(self, customer):
        return self.db.crm_loyalty_balance(customer)

    def crm_award_loyalty(self, customer, amount, vch_id=''):
        """Award points for a sale. Returns the new balance.

        The till has shown and redeemed points since it was written and
        nothing ever recorded them, so a balance never accumulated and
        redeemed points were never taken off anything.
        """
        try:
            from core import crm
            pts = crm.points_for_sale(amount)
            if pts > 0:
                self.db.crm_add_loyalty(customer, earned=pts, vch_id=vch_id,
                                        note='sale')
            return {'status': 'success', 'earned': pts,
                    'balance': self.db.crm_loyalty_balance(customer)}
        except Exception as e:
            ERR(f"[CRM] award loyalty: {e}")
            return {'status': 'error', 'message': str(e)}

    def crm_redeem_loyalty(self, customer, points, bill_amount, vch_id=''):
        """Spend points against a bill, capped by balance and by the bill."""
        try:
            from core import crm
            balance = self.db.crm_loyalty_balance(customer)
            allowed, rupees = crm.redeemable(balance, bill_amount)
            use = min(int(points or 0), allowed)
            if use <= 0:
                return {'status': 'error', 'message': 'Nothing to redeem.',
                        'balance': balance}
            self.db.crm_add_loyalty(customer, used=use, vch_id=vch_id, note='redeemed')
            return {'status': 'success', 'used': use,
                    'value': round(use * crm.DEFAULT_RUPEES_PER_POINT, 2),
                    'balance': self.db.crm_loyalty_balance(customer)}
        except Exception as e:
            ERR(f"[CRM] redeem: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_item_by_tag(self, tag_id):
        """One scanned piece, with its own rate and making charge.

        The till used to read one rate and one making charge out of browser
        storage and apply them to everything. A piece can now answer for
        itself, falling back to its product master.
        """
        try:
            row = self.db.get_item_by_tag(tag_id)
            if not row:
                return {"status": "not_found", "tag_id": str(tag_id or '')}
            return {"status": "success", "item": row}
        except Exception as e:
            ERR(f"[TAG] lookup {tag_id}: {e}")
            return {"status": "error", "message": str(e)}

    @staticmethod
    def _price_tag_line(item, gold_rate_10g, qty=1):
        """Price one tagged piece. Single source of truth for the till.

        Formula (rate is per 10g, the shop convention):
          fine   = net_wt × touch / 100
          base   = net_wt / 10 × rate × qty
          making = per-gram: making_rate × net_wt × qty
                   percent:  base × making_rate / 100
                   fixed:    making_rate × qty
          amount = base + making
        """
        try:
            wt = float(item.get('nt_wt') or item.get('gr_wt') or 0)
        except (TypeError, ValueError):
            wt = 0.0
        try:
            touch = float(item.get('touch') or 0)
        except (TypeError, ValueError):
            touch = 0.0
        try:
            rate = float(gold_rate_10g or 0)
        except (TypeError, ValueError):
            rate = 0.0
        try:
            qty = max(int(qty or 1), 1)
        except (TypeError, ValueError):
            qty = 1
        try:
            mk_rate = float(item.get('eff_making_charge')
                            or item.get('making_charge') or 0)
        except (TypeError, ValueError):
            mk_rate = 0.0
        mk_type = str(item.get('eff_making_type')
                      or item.get('making_type') or 'percent').strip().lower()
        if mk_type not in ('per_gram', 'percent', 'fixed'):
            mk_type = 'percent'
        fine = round(wt * touch / 100, 3)
        base = round(wt / 10 * rate * qty, 2)
        if mk_type == 'per_gram':
            making = round(mk_rate * wt * qty, 2)
        elif mk_type == 'fixed':
            making = round(mk_rate * qty, 2)
        else:
            making = round(base * mk_rate / 100, 2)
        return {
            'it_code': str(item.get('it_code') or ''),
            'it_name': str(item.get('it_name') or ''),
            'tag_id': str(item.get('tag_id') or ''),
            'huid': str(item.get('huid') or ''),
            'weight': round(wt, 3),
            'touch': round(touch, 2),
            'rate': rate,
            'qty': qty,
            'fine': fine,
            'making_type': mk_type,
            'making_rate': mk_rate,
            'making': making,
            'amount': round(base + making, 2),
        }

    def scan_tag(self, tag_id, gold_rate_10g=None, qty=1):
        """Scan a tag ID and get back a fully priced bill line.

        Exact match (case/space-insensitive) on tag_id, then HUID. A tag
        absent from stock is reported not_found — sold pieces leave stock
        on sale, so a missing tag means already sold or never existed.
        """
        try:
            tag = str(tag_id or '').strip()
            if not tag:
                return {"status": "error", "message": "Scan or type a tag ID."}
            row = self.db.get_item_by_tag(tag)
            if not row:
                try:
                    prow = self.db.get_product_by_tag(tag)
                except Exception:
                    prow = None
                if isinstance(prow, dict) and (prow.get('tag_id') or prow.get('it_code')):
                    row = prow
                else:
                    return {"status": "not_found", "tag_id": tag,
                            "message": f"Tag {tag} is not in stock — it may be sold already."}
            line = self._price_tag_line(row, gold_rate_10g, qty)
            if line['weight'] <= 0:
                return {"status": "error", "tag_id": tag,
                        "message": f"Tag {tag} has no weight recorded."}
            return {"status": "success", "line": line}
        except Exception as e:
            ERR(f"[TAG] scan {tag_id}: {e}")
            return {"status": "error", "message": str(e)}

    def get_sold_tags(self, from_date='', to_date='', search='', group=''):
        """Pieces already sold. The Sold Tags screen has called this since it
        was written; nothing ever defined it, so the page only ever said
        'Failed to load'."""
        try:
            return self.db.get_sold_tags(from_date, to_date, search, group)
        except Exception as e:
            ERR(f"[SOLD TAGS] {e}")
            return []

    def get_products(self):
        return self.db.get_all_products()

    def get_pos_stock(self):
        return {"status": "success", "stock": self.db.get_pos_stock()}

    def delete_master_entry(self, data_type, entry_id):
        return {"status":"success"} if self.db.delete_master_entry(data_type,entry_id) else {"status":"error"}

    # -- CLIENT ----------------------------------------------------------------
    def add_new_client(self, client_data):
        try:
            res = self.db.add_client(client_data['name'],client_data.get('phone',''),
                                     client_data.get('metal_limit',0),client_data.get('cash_limit',0))
            return {"status":"success"} if res else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_client_list(self):
        return self.db.get_all_clients()

    def delete_new_client(self, client_id):
        try:
            return {"status": "success"} if self.db.delete_client(client_id) else {
                "status": "error", "message": "Customer was not found or could not be deleted."
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def update_new_client(self, client_data):
        try:
            client_id = int(client_data.get("id"))
            ok = self.db.execute_query(
                "UPDATE clients_master SET name=?, phone=?, metal_limit=?, cash_limit=? WHERE id=?",
                (str(client_data.get("name") or "").strip(),
                 str(client_data.get("phone") or "").strip(),
                 float(client_data.get("metal_limit") or 0),
                 float(client_data.get("cash_limit") or 0),
                 client_id)
            )
            return {"status": "success"} if ok else {"status": "error", "message": "Customer update failed."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_customer_purchases(self, customer_name, mobile=''):
        """Return all bills issued to the selected customer."""
        try:
            return self.db.get_customer_purchases(customer_name, mobile)
        except Exception as e:
            ERR(f"[CUSTOMER PURCHASES] {e}")
            return {"status": "error", "message": str(e)}

    def _customer_statement_data(self, customer_name):
        purchases = self.db.get_customer_purchases(customer_name)
        ledger = self.db.get_client_statement(customer_name)
        return purchases, ledger

    def _open_customer_statement(self, customer_name, statement_type):
        try:
            from html import escape as _html_escape
            purchases, ledger = self._customer_statement_data(customer_name)
            shop = self.db.get_config("business_name", "Jewellers")
            address = self.db.get_config("address", "")
            phone = self.db.get_config("owner_phone", "")
            city = self.db.get_config("city", "")
            rows = purchases if statement_type == "purchase" else ledger
            title = "Purchase Statement" if statement_type == "purchase" else "Account Statement"
            safe_shop = _html_escape(str(shop or "Jewellers"))
            safe_address = _html_escape(str(address or ""))
            safe_phone = _html_escape(str(phone or ""))
            safe_city = _html_escape(str(city or ""))
            safe_customer = _html_escape(str(customer_name or ""))
            body_rows = []
            if statement_type == "purchase":
                for row in rows:
                    total = float(row.get("total_amount") or 0)
                    body_rows.append(
                        f"<tr><td>{_html_escape(str(row.get('date','')))}</td><td class='voucher'>{_html_escape(str(row.get('vch_id','')))}</td>"
                        f"<td><span class='paid'>PAID</span></td><td class='num'>Rs. {float(row.get('gold_rate') or 0):,.2f}</td>"
                        f"<td class='num'>Rs. {total:,.2f}</td><td class='num'>Rs. {total:,.2f}</td></tr>"
                    )
                headers = "<th>Date</th><th>Voucher</th><th>Status</th><th>Gold Rate</th><th>Total</th><th>Collected</th>"
            else:
                remaining_fine = 0.0
                for row in rows:
                    remaining_fine += float(row.get('metal_dr') or 0) - float(row.get('metal_cr') or 0)
                    body_rows.append(
                        f"<tr><td>{_html_escape(str(row.get('date','')))}</td><td class='voucher'>{_html_escape(str(row.get('vch_reference','')))}</td>"
                        f"<td>{_html_escape(str(row.get('description','')))}</td><td class='num fine'>{float(row.get('metal_dr') or 0):,.3f} g</td>"
                        f"<td class='num fine'>{float(row.get('metal_cr') or 0):,.3f} g</td><td class='num fine remaining'>{max(remaining_fine, 0.0):,.3f} g</td>"
                        f"<td class='num'>Rs. {float(row.get('cash_dr') or 0):,.2f}</td><td class='num'>Rs. {float(row.get('cash_cr') or 0):,.2f}</td></tr>"
                    )
                headers = "<th>Date</th><th>Reference</th><th>Particulars</th><th>Fine Dr</th><th>Fine Cr / Jama</th><th>Remaining Fine</th><th>Cash Dr</th><th>Cash Cr</th>"
            contact = " &nbsp; • &nbsp; ".join(v for v in (safe_phone, safe_city) if v)
            html = f"""<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>
<style>
@page{{size:A4;margin:0}}*{{box-sizing:border-box}}body{{margin:0;background:#ebe7df;color:#20241f;font-family:Arial,sans-serif}}
.sheet{{position:relative;max-width:900px;min-height:1120px;margin:28px auto;background:#fbf8f2;padding:0 54px 42px;box-shadow:0 8px 28px rgba(18,34,56,.14)}}.sheet:before{{content:"";display:block;height:7px;margin:0 -54px;background:linear-gradient(90deg,#122238 0 62%,#a9803c 62% 100%)}}
.head{{display:flex;align-items:center;justify-content:space-between;padding:32px 0 22px;border-bottom:1px solid #e1dacb}}.identity{{display:flex;align-items:center;gap:14px}}.mark{{width:43px;height:43px;border-radius:8px;background:#122238;color:#d7b46a;display:flex;align-items:center;justify-content:center;font-family:Georgia,serif;font-size:18px;font-weight:bold}}h1{{margin:0;color:#122238;font:700 24px Georgia,serif;letter-spacing:-.3px}}.address{{margin-top:7px;color:#5b5f58;font-size:11px;line-height:1.55}}.brand{{display:flex;align-items:center;gap:9px;border-left:1px solid #d8cdb9;padding-left:18px}}.brand-name{{color:#122238;font:700 15px Georgia,serif;letter-spacing:.14em}}.brand-tag{{margin-top:4px;color:#a9803c;font-size:8px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}}.title-row{{display:flex;justify-content:space-between;align-items:end;padding:24px 0 16px;border-bottom:1.5px solid #122238}}h2{{margin:0;color:#122238;font:italic 22px Georgia,serif}}.doc-type{{color:#a9803c;font-size:10px;font-weight:bold;letter-spacing:.12em;text-transform:uppercase}}.meta{{display:flex;justify-content:space-between;padding:15px 0 18px;color:#5b5f58;font-size:11px}}.meta b{{color:#20241f}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}th{{height:34px;padding:0 8px 10px;background:transparent;border-bottom:1.5px solid #122238;color:#5b5f58;text-align:left;font-size:9px;letter-spacing:.08em;text-transform:uppercase;white-space:nowrap}}td{{height:42px;padding:10px 8px;border-bottom:1px solid #e1dacb;color:#20241f;font-size:11px;white-space:nowrap}}.num{{text-align:right;font-variant-numeric:tabular-nums}}.fine{{text-align:right}}.remaining{{color:#122238;font-weight:700}}.voucher{{color:#122238;font-weight:700;letter-spacing:.03em}}.paid{{display:inline-block;padding:3px 7px;border:1px solid #b9a77f;border-radius:3px;color:#6f5425;font-size:9px;font-weight:bold;letter-spacing:.08em}}.total-row td{{border-top:1.5px solid #122238;border-bottom:0;color:#122238;font-weight:bold}}.foot{{margin-top:34px;padding-top:15px;border-top:1px solid #e1dacb;color:#5b5f58;font-size:10px;line-height:1.6;display:flex;justify-content:space-between}}.foot strong{{color:#122238;font-family:Georgia,serif;font-size:13px}}@media print{{body{{background:#fff}}.sheet{{min-height:0;margin:0;box-shadow:none}}}}
</style></head><body><div class='sheet'><div class='head'><div class='identity'><div class='mark'>JD</div><div><h1>{safe_shop}</h1><div class='address'>{safe_address or 'Jewellers Address'}{('<br>'+contact) if contact else ''}</div></div></div><div class='brand'><div><div class='brand-name'>AURUMOS</div><div class='brand-tag'>Jewelry Management System</div></div></div></div>
<div class='title-row'><h2>{title}</h2><div class='doc-type'>Customer statement</div></div><div class='meta'><div>Customer: <b>{safe_customer}</b></div><div>Generated: <b>{datetime.now().strftime('%d %b %Y, %H:%M')}</b></div></div>
<table><colgroup>{('<col style="width:13%"><col style="width:13%"><col style="width:20%"><col style="width:11%"><col style="width:14%"><col style="width:14%"><col style="width:8%"><col style="width:7%">' if statement_type == 'credit' else '<col style="width:15%"><col style="width:15%"><col style="width:13%"><col style="width:17%"><col style="width:20%"><col style="width:20%">')}</colgroup><thead><tr>{headers}</tr></thead><tbody>{''.join(body_rows) or "<tr><td colspan='8' style='text-align:center;color:#5b5f58'>No records found</td></tr>"}</tbody></table>
<div class='foot'><div><strong>{safe_shop}</strong><br>{safe_address or 'Jewellers Address'}</div><div style='text-align:right'>Computer-generated statement<br>Powered by AURUMOS</div></div></div><script>window.onload=function(){{window.print();}}</script></body></html>"""
            def open_window():
                webview.create_window(title, html=html, js_api=self, width=1050, height=800, resizable=True)
            threading.Thread(target=open_window, daemon=True).start()
            return {"status": "success"}
        except Exception as e:
            ERR(f"[STATEMENT] {e}")
            return {"status": "error", "message": str(e)}

    def print_customer_purchases(self, customer_name):
        return self._open_customer_statement(customer_name, "purchase")

    def print_customer_credit(self, customer_name):
        return self._open_customer_statement(customer_name, "credit")

    def update_client_limits(self, data):
        try:
            return {"status":"success"} if self.db.update_client_limits(data) else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_client_live_outstanding(self, client_name):
        try:
            with self.db._get_connection() as conn:
                res = conn.execute("""
                    SELECT SUM(metal_dr-metal_cr) as metal_bal, SUM(cash_dr-cash_cr) as cash_bal
                    FROM credit_ledger WHERE UPPER(TRIM(client_name))=UPPER(TRIM(?))
                    AND UPPER(TRIM(description)) NOT LIKE '%CASH SETTLEMENT%'
                    AND UPPER(TRIM(description)) NOT LIKE '%UCHAK CASH%'
                """, (client_name.strip(),)).fetchone()
                if res:
                    return {"status":"success",
                            "metal_outstanding":round(res['metal_bal'] or 0.0,3),
                            "cash_outstanding": round(res['cash_bal']  or 0.0,2)}
                return {"status":"success","metal_outstanding":0.000,"cash_outstanding":0.00}
        except Exception as e:
            return {"status":"error","message":str(e)}

    # -- LEDGER ----------------------------------------------------------------
    def get_ledger_summary(self):
        try:
            with self.db._get_connection() as conn:
                res = conn.execute("SELECT SUM(metal_dr) as m_dr,SUM(metal_cr) as m_cr,"
                                   "SUM(cash_dr) as c_dr,SUM(cash_cr) as c_cr FROM credit_ledger").fetchone()
                return {"metal_dr":round(res['m_dr'] or 0.0,3),"metal_cr":round(res['m_cr'] or 0.0,3),
                        "cash_dr":round(res['c_dr'] or 0.0,2),"cash_cr":round(res['c_cr'] or 0.0,2),
                        "metal":round((res['m_dr'] or 0.0)-(res['m_cr'] or 0.0),3),
                        "cash":round((res['c_dr'] or 0.0)-(res['c_cr'] or 0.0),2)}
        except:
            return {"metal_dr":0,"metal_cr":0,"cash_dr":0,"cash_cr":0,"metal":0,"cash":0}

    def get_full_ledger_stream(self):
        try:
            with self.db._get_connection() as conn:
                return [dict(r) for r in conn.execute("SELECT * FROM credit_ledger ORDER BY id DESC").fetchall()]
        except: return []

    def post_journal_entry(self, data):
        try:
            res = self.db.post_ledger_entry(
                client_name=data.get('account_type','MARKET'),
                vch_id="JRNL-"+datetime.now().strftime('%M%S'),
                desc=data.get('description','Journal Entry'),
                metal_dr=float(data.get('m_dr',0)),metal_cr=float(data.get('m_cr',0)),
                cash_dr=float(data.get('c_dr',0)),cash_cr=float(data.get('c_cr',0)),gold_rate=0)
            return {"status":"success"} if res else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def post_to_ledger(self, entry_data):
        try:
            ok = self.db.post_ledger_entry(**entry_data)
            if not ok:
                ERR(f"[LEDGER] posting failed for {entry_data.get('vch_id')!r} "
                    f"({entry_data.get('desc')!r}) — bill stands, ledger missing")
                return {"status":"error","message":"Bill recorded but the ledger posting failed."}
            return {"status":"success"}
        except Exception as e:
            ERR(f"[LEDGER] posting raised for {entry_data.get('vch_id')!r}: {e}")
            return {"status":"error","message":str(e)}

    def get_ledger_statement(self, client_name):
        return self.db.get_client_statement(client_name)

    def get_client_balances(self, client_name):
        try:
            res = self.db.fetch_one(
                "SELECT SUM(metal_dr-metal_cr) as metal_bal,SUM(cash_dr-cash_cr) as cash_bal "
                "FROM credit_ledger WHERE client_name=? "
                "AND UPPER(TRIM(description)) NOT LIKE '%CASH SETTLEMENT%' "
                "AND UPPER(TRIM(description)) NOT LIKE '%UCHAK CASH%'", (client_name,))
            return {"metal":round(res['metal_bal'] or 0.0,3),"cash":round(res['cash_bal'] or 0.0,2)} if res else {"metal":0,"cash":0}
        except: return {"metal":0,"cash":0}

    # -- STOCK LEDGER ----------------------------------------------------------
    def _relink_photo_file(self, tag, photo_name):
        """Point a saved photo at the tag being saved under.

        A photo is picked for the tag showing at the time, but the tag can be
        edited before Save Entry. Saving the old filename under the new tag
        left a file named for one piece pointing at another, which also
        defeated the tag-stem fallback in photo_path. Copy it to the new
        tag's name and return that instead. The original stays on disk.
        """
        try:
            from core import photos as _ph
            import shutil
            tag = str(tag or '').strip()
            name = str(photo_name or '').strip()
            if not tag or not name:
                return photo_name
            stem = _ph.safe_name(tag, 'x.jpg')[:-4]
            fn, ext = os.path.splitext(name)
            if fn.upper() == stem or fn.upper().startswith(stem + '-'):
                return name
            src = _ph.path_for(_get_db_base(), name)
            if not os.path.isfile(src):
                return name
            new_name = _ph.safe_name(tag, name)
            dst = _ph.path_for(_get_db_base(), new_name)
            i = 2
            while os.path.isfile(dst):
                new_name = _ph.safe_name(tag, name, i)
                dst = _ph.path_for(_get_db_base(), new_name)
                i += 1
                if i > 50:
                    return name
            shutil.copyfile(src, dst)
            LOG(f"[PHOTO] relinked {name} -> {new_name} for tag {tag}")
            return new_name
        except Exception as e:
            ERR(f"[PHOTO] relink: {e}")
            return photo_name

    def add_stock_entry(self, data):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            import time as _time
            LOG(f"[STOCK] Received: {data}")
            tag = str(data.get('tag_id') or '').strip()
            if not tag or tag in ('N/A','','---','-'):
                data['tag_id'] = 'OPENING-'+str(int(_time.time()*1000))[-8:]
            if data.get('photo'):
                data['photo'] = self._relink_photo_file(data.get('tag_id'), data.get('photo'))
            # Strip fields not in DB schema
            DB_FIELDS = {'it_code','it_name','tag_id','pkg_wt','para_stone_wt','size','design',
                         'pcs','gr_wt','ls_wt','nt_wt','ghat_wt','touch','wastage','huid',
                         # Retail pricing. Without these listed here the keys
                         # were dropped one layer above the database, so a
                         # piece saved with a making charge arrived with none
                         # and nothing anywhere said so.
                         'category','karat','making_type','making_charge',
                         'sale_price','stone_value',
                         # Tag-print layout for the piece (RING/PARA/KATTI).
                         'tag_variation',
                         # A photo picked in the Receipt panel before the piece
                         # is saved. Without this it was dropped here and the
                         # file sat orphaned on disk with no row pointing at it.
                         'photo'}
            clean = {k:v for k,v in data.items() if k in DB_FIELDS}
            success = self.db.add_stock_entry(**clean)
            if success:
                LOG(f"[STOCK] Saved: {clean.get('it_code')} gr_wt={clean.get('gr_wt')}")
                self._audit(f"Stock added: {clean.get('it_code','')}",
                            f"Wt: {clean.get('gr_wt','')}g | Tag: {clean.get('tag_id','')}","stock")
                return {"status":"success"}
            ERR(f"[STOCK] DB returned False for: {clean}")
            return {"status":"error","message":"DB save returned False"}
        except Exception as e:
            ERR(f"[STOCK] Exception: {e}")
            return {"status":"error","message":str(e)}

    def add_uchak_stock_entry(self, data):
        try:
            success = self.db.add_uchak_stock_entry_raw(
                str(data.get('it_code','')).strip(), str(data.get('it_name','')).strip(),
                int(data.get('pcs') or 1), str(data.get('price','0.00')))
            return {"status":"success"} if success else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def update_stock_entry(self, entry_id, data):
        # Routed through the database's own updater, which checks keys
        # against the real table (PRAGMA) and drops `id`/unknowns. Building
        # the SET clause from raw client keys here meant a mistyped key took
        # the whole update down, and `id` itself could be overwritten.
        try:
            data = dict(data or {})
            if data.get('photo'):
                try:
                    row = self.db.fetch_one(
                        "SELECT tag_id FROM stock_inventory WHERE id=?", (entry_id,))
                    if row:
                        data['photo'] = self._relink_photo_file(
                            row.get('tag_id'), data.get('photo'))
                except Exception:
                    pass
            return {"status":"success"} if self.db.update_stock_entry(entry_id, data) else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def delete_stock_entry(self, entry_id):
        self._audit(f"Stock entry deleted",f"Entry ID: {entry_id}","stock")
        try:
            return {"status":"success"} if self.db.execute_query(
                "DELETE FROM stock_inventory WHERE id=?", (entry_id,)) else {"status":"error"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_stock_ledger(self):                      return self.db.get_stock_ledger()
    def get_opening_stock(self):                     return self.db.get_opening_stock()
    def get_stock_ledger_by_date(self, d):           return self.db.fetch_stock_ledger_by_date(d)
    def get_ledger_dates(self):                      return self.db.get_available_ledger_dates()
    def get_product_by_tag(self, tag_id):            return self.db.get_product_by_tag(tag_id)
    def get_uchak_price_by_code(self, it_code):       return self.db.get_uchak_price_by_code(it_code)

    def get_uchak_item_details(self, it_code):
        """Name + last rate for one Uchak code (uchak_stock_entry autocomplete)."""
        try:
            return self.db.get_uchak_item_details(it_code)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # -- HELD BILLS (POS hold / resume) ------------------------------------
    def get_held_bills(self):
        try:
            return self.db.held_list()
        except Exception as e:
            return {"status": "error", "message": str(e), "bills": []}

    def hold_bill(self, bill):
        try:
            return self.db.held_save(bill if isinstance(bill, dict) else {})
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_held_bill(self, vch_id):
        try:
            return self.db.held_delete(vch_id)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_sync_status(self):
        """Whether this PC can currently see the rest of the shop."""
        return self.cluster('status')

    def get_sync_conflicts(self, include_resolved=False):
        return self.db.get_sync_conflicts(include_resolved)

    def resolve_sync_conflict(self, conflict_id):
        return self.db.resolve_sync_conflict(conflict_id)
    def get_inventory_stats(self):                   return self.db.get_inventory_stats()
    def get_analytics_payload(self):                 return self.db.get_analytics_payload()
    def get_items_by_bin(self, bin_id):            return self.db.get_items_by_bin(bin_id)
    def get_velocity_products(self):                 return self.db.get_velocity_products()
    def get_untagged_items(self):                    return self.db.get_untagged_items()
    def get_uchak_stock(self):                       return self.db.get_uchak_stock()

    # -- KATTI -----------------------------------------------------------------
    def get_katti_vch_id(self):
        return self.db.get_next_vch_id()

    def save_katti_voucher(self, vch_id, total_wt, total_packets, note, items):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            box_id=None
            if items and isinstance(items,list):
                for item in items:
                    if isinstance(item,dict):
                        c=str(item.get('box') or '').strip()
                        if c and c not in ('','-','None','N/A'):
                            box_id=c; break
            success=self.db.save_katti_batch(str(vch_id),float(total_wt or 0),int(total_packets or 0),str(note or ""),items,box_id)
            return {"status":"success"} if success else {"status":"error","message":"DB save failed"}
        except Exception as e:
            return {"status":"error","message":str(e)}


    def get_all_katti_vouchers(self):
        return self.db.get_all_katti_vouchers()

    def delete_katti_voucher(self, vch_id):
        return self.db.delete_katti_voucher(vch_id)

    def update_katti_voucher(self, vch_id, note, items):
        return self.db.update_katti_voucher(vch_id, note, items)

    def get_voucher_history(self, vch_id):
        try:
            data=self.db.get_katti_voucher_details(str(vch_id).strip().zfill(4))
            if data: return {"status":"success","voucher":data.get('voucher'),"items":data.get('items',[])}
            return {"status":"empty"}
        except: return {"status":"error"}

    # -- KARIGAR (job-work register, retail + wholesale) -------------------
    # Screens: karigar.html, karigar_master.html, karigar_order.html,
    # karigar_details.html. Signatures accept the exact positional args
    # each screen sends (add/update take name,phone,specialty,address,
    # ghat_norm,allowance) plus extra *args so a screen passing fewer or
    # more values can never raise TypeError across the bridge.
    def get_all_karigars(self):
        try:
            return self.db.karigar_all()
        except Exception as e:
            return []

    def add_karigar(self, name, phone='', specialty='', address='',
                    ghat_norm=0.5, allowance=1.0, *args):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.karigar_add(name, phone, specialty, address,
                                       ghat_norm, allowance)
        except Exception as e:
            return {"status":"error","message":str(e)}

    def update_karigar(self, kid, name, phone='', specialty='', address='',
                       ghat_norm=0.5, allowance=1.0, *args):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.karigar_update(kid, name, phone, specialty,
                                          address, ghat_norm, allowance)
        except Exception as e:
            return {"status":"error","message":str(e)}

    def delete_karigar(self, kid):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.karigar_delete(kid)
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_karigar_orders(self, karigar_id=None):
        try:
            return self.db.karigar_orders(karigar_id)
        except Exception as e:
            return []

    def create_karigar_order(self, karigar_id, order_no='', issue_date='',
                             due_date='', given_fine_wt=0, given_touch=100,
                             items=None):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.karigar_create_order(
                karigar_id, order_no, issue_date, due_date,
                given_fine_wt, given_touch, items)
        except Exception as e:
            return {"status":"error","message":str(e)}

    def receive_karigar_order(self, order_id, received_items=None):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.karigar_receive_order(order_id, received_items)
        except Exception as e:
            return {"status":"error","message":str(e)}

    # -- FINANCE (cash/bank + chart of accounts, wholesale Finance) ----
    # Screens: cash_bank.html, accounting.html, chart_of_accounts.html.
    # Reads ride the finance bundle; deletes stay owner-only (see
    # core/permissions.py) because wiping heads is destructive.
    def get_cash_bank_accounts(self):
        try:
            return self.db.finance_accounts(cash_bank_only=True)
        except Exception as e:
            return {"status": "error", "message": str(e), "accounts": []}

    def get_chart_of_accounts(self):
        try:
            return self.db.finance_accounts()
        except Exception as e:
            return {"status": "error", "message": str(e), "accounts": []}

    def save_account(self, data):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.finance_save_account(data if isinstance(data, dict) else {})
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def save_cash_bank_account(self, data):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            d = dict(data if isinstance(data, dict) else {})
            if not d.get('kind'):
                d['kind'] = 'CASH'
            return self.db.finance_save_account(d)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_account(self, code):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.finance_delete_account(code)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_all_accounts(self):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.finance_delete_all()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_cash_position(self):
        try:
            return self.db.finance_cash_position()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_daybook(self, limit=500, day=None):
        try:
            return self.db.finance_daybook(limit, day)
        except Exception as e:
            return {"status": "error", "message": str(e), "entries": []}

    def get_trial_balance(self):
        try:
            return self.db.finance_trial_balance()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_next_voucher_no(self, vch_type=''):
        try:
            return self.db.finance_next_voucher_no(vch_type)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def post_voucher(self, vch):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            return self.db.finance_post_voucher(vch if isinstance(vch, dict) else {})
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # -- UCHAK INWARD ----------------------------------------------------------
    def get_next_uchak_inward_vch_id(self):         return self.db.get_last_uchak_inward_vch_id()

    def get_uchak_inward_voucher_details(self, vch_id):
        data=self.db.get_uchak_inward_voucher_details(vch_id)
        if data: return {"status":"success","voucher":data["voucher"],"items":data["items"]}
        return {"status":"error","message":"Voucher not found."}

    def save_uchak_inward_batch(self, payload):
        try:
            vch_id=str(payload.get('vch_id','UCHK-IN-001')).strip()
            items=payload.get('items',[])
            if not items: return {"status":"error","message":"Batch array queue is empty."}
            success=self.db.save_uchak_inward_transaction(
                vch_id,len(items),sum(int(i['pcs'] or 0) for i in items),
                sum((int(i['pcs'] or 0)*float(i['price'] or 0.0)) for i in items),items)
            return {"status":"success"} if success else {"status":"error","message":"Database transaction failed."}
        except Exception as e:
            return {"status":"error","message":str(e)}

    # -- BILLING ---------------------------------------------------------------
    def get_sales_vch_id(self):
        try:
            with self.db._get_connection() as conn:
                res=conn.execute("SELECT MAX(CAST(SUBSTR(vch_id,5) AS INTEGER)) FROM sales_history WHERE vch_id LIKE 'VCH-%'").fetchone()
                return f"VCH-{(res[0] or 0)+1:03d}"
        except: return "VCH-001"

    def get_next_uchak_vch_id(self):
        try:
            with self.db._get_connection() as conn:
                res=conn.execute("SELECT MAX(CAST(SUBSTR(vch_id,6) AS INTEGER)) FROM sales_history WHERE vch_id LIKE 'UCHK-%'").fetchone()
                return f"UCHK-{(res[0] or 0)+1:03d}"
        except: return "UCHK-001"

    def get_live_invoice_print_payload(self, voucher_id):
        try:
            LOG(f"[BILL PRINT] get_live_invoice_print_payload called with voucher_id={voucher_id!r}")
            sh_row=self.db.fetch_one("SELECT * FROM sales_history WHERE vch_id=?",(str(voucher_id).strip(),))
            LOG(f"[BILL PRINT] sh_row={sh_row}")
            if not sh_row: return {"status":"error","message":f"Voucher {voucher_id} not found."}
            try: items_array=json.loads(sh_row.get('items') or '[]')
            except: items_array=[]
            is_retail = (not ('UCHAK' in str(sh_row.get('status','')).upper())
                         and any(isinstance(item, dict) and 'making' in item for item in items_array))
            bill={"vch_id":sh_row.get('vch_id','---'),"customer":sh_row.get('customer','Walking Customer'),
                   "mobile":sh_row.get('mobile',''),"status":sh_row.get('status','PAID'),"is_credit":sh_row.get('status')=='CREDIT',
                  "is_uchak":'UCHAK' in str(sh_row.get('status','')).upper(),
                   "totalLedgerFine":float(sh_row.get('ledger_fine') or 0.0),
                   "remainingFine":float(sh_row.get('remaining_fine') or 0.0),
                   "collectedFine":float(sh_row.get('collected_fine') or 0.0),
                   "fine995":float(sh_row.get('fine_995') or 0.0),
                   "fineDhal":float(sh_row.get('fine_dhal') or 0.0),
                   "goldRate":float(sh_row.get('gold_rate') or 0.0),
                   "totalAmount":float(sh_row.get('total_amount') or 0.0),
                   "discountType":sh_row.get('discount_type','none'),
                   "discountTouch":float(sh_row.get('discount_touch') or 0.0),
                   "discountFine":float(sh_row.get('discount_fine') or 0.0),
                   "discountAmount":float(sh_row.get('discount_amount') or 0.0),
                   "discount":float(sh_row.get('discount_amount') or 0.0),
                   "discount_type":sh_row.get('discount_type','none'),
                   "discount_value":float(sh_row.get('discount_value') or 0.0),
                   "kind":'sale' if is_retail else 'bill',
                   "is_uchak":'UCHAK' in str(sh_row.get('status','')).upper(),
                   "items":items_array}
            # Bill mode + tax breakup as recorded at sale time. The template
            # keys off these, so a GST bill reprints as a GST bill instead of
            # silently falling back to a normal Invoice. dict.get keeps old
            # rows (written before these columns existed) working.
            try:
                _mode = str(sh_row.get('bill_mode') or 'normal').strip().lower()
                if _mode not in ('gst', 'normal'):
                    _mode = 'normal'
                _cgst = float(sh_row.get('cgst') or 0)
                _sgst = float(sh_row.get('sgst') or 0)
                _cgst_rate = float(sh_row.get('cgst_rate') or 0)
                _sgst_rate = float(sh_row.get('sgst_rate') or 0)
                _gst_amt = float(sh_row.get('gst_amount') or 0)
                _gst_rate = float(sh_row.get('gst_rate') or 0)
                bill["_billMode"] = _mode
                bill["bill_mode"] = _mode
                bill["cgst"] = _cgst
                bill["sgst"] = _sgst
                bill["cgst_rate"] = _cgst_rate
                bill["sgst_rate"] = _sgst_rate
                bill["gst_total"] = _cgst + _sgst
                bill["gstAmount"] = _gst_amt
                bill["gstRate"] = _gst_rate
            except Exception:
                pass
            try:
                bill["shop_name"]=self.db.get_config("business_name","AurumOS")
                bill["shop_owner"]=self.db.get_config("owner_name","")
                bill["shop_phone"]=self.db.get_config("owner_phone","")
                bill["shop_city"]=self.db.get_config("city","")
                bill["shop_address"]=self.db.get_config("address","")
            except Exception: pass
            try:
                for _k, _v in self._shop_print_block().items():
                    bill.setdefault(_k, _v)
            except Exception as _se:
                ERR(f"[PRINT] shop block on reprint: {_se}")
            return {"status":"success","bill":bill}
        except Exception as e:
            ERR(f"[BILL PRINT] {e}")
            return {"status":"error","message":str(e)}

    def _shop_print_block(self) -> dict:
        """The shop's own details for the top of a bill, GST included.

        Read here rather than in the template so a reprint months later shows
        what the shop is registered as, instead of whatever a browser happened
        to have cached. An unregistered shop returns no GSTIN at all, which is
        what makes the GST bill unavailable rather than blank.
        """
        block = {}
        try:
            g = self.db.get_config
            block = {
                'shop_name':    g('business_name', ''),
                'shop_owner':   g('owner_name', ''),
                'shop_phone':   g('owner_phone', ''),
                'shop_city':    g('city', ''),
                'shop_address': g('address', ''),
            }
            if str(g('gst_registered', '0')).strip() == '1':
                from core.gst import DEFAULT_CGST_RATE, DEFAULT_SGST_RATE, DEFAULT_HSN
                # The registration number itself is re-validated, not trusted:
                # a shop whose stored GSTIN is missing or no longer checks
                # out (legacy data, partial save) must not print Tax Invoices
                # with blank registration detail.
                try:
                    from core.gst import validate as _gst_validate
                    _gst_ok = bool(_gst_validate(g('gstin', ''))['ok'])
                except Exception:
                    _gst_ok = False
                if not _gst_ok:
                    ERR("[PRINT] shop claims registration but GSTIN is invalid -- treating as unregistered")
                    block['shop_gst_registered'] = '0'
                else:
                    block.update({
                    'shop_gstin':      g('gstin', ''),
                    'shop_legal_name': g('gst_legal_name', ''),
                    'shop_state':      g('gst_state', ''),
                    'shop_state_code': g('gst_state_code', ''),
                    'shop_pan':        g('pan', ''),
                    'shop_hsn':        g('gst_hsn', '') or DEFAULT_HSN,
                    'shop_cgst_rate':  g('gst_cgst_rate', '') or str(DEFAULT_CGST_RATE),
                    'shop_sgst_rate':  g('gst_sgst_rate', '') or str(DEFAULT_SGST_RATE),
                    'shop_gst_registered': '1',
                })
            else:
                block['shop_gst_registered'] = '0'
        except Exception as e:
            ERR(f"[PRINT] shop block unavailable: {e}")
        return block

    def get_shop_print_block(self) -> dict:
        """Same block, for the billing screens: they use it to decide whether
        the GST Bill button can be offered at all."""
        return self._shop_print_block()

    def trigger_print_window(self, voucher_id, copies=1, data=None):
        # `data` is accepted for compatibility with the billing UI, which passes
        # the bill payload as a 3rd argument. bill_print.html fetches its own
        # data via get_live_invoice_print_payload(), so `data` is unused here —
        # but the parameter MUST exist or pywebview raises a TypeError ("critical
        # error") when the UI calls this with 3 arguments.
        try:
            copies=int(copies) if copies else 1
            ui_dir=get_asset_path("ui")
            template_name = _print_template_for(data if isinstance(data, dict) else {})
            print_path=os.path.join(ui_dir, template_name)
            if not os.path.exists(print_path):
                template_name = "bill_print.html"
                print_path=os.path.join(ui_dir, template_name)
            if not os.path.exists(print_path):
                return {"status":"error","message":"bill_print.html not found"}
            LOG(f"[PRINT] template={template_name} vch={voucher_id}")
            with open(print_path,'r',encoding='utf-8') as f:
                html_content=f.read()
            inject = f"<script>window.__VCH_ID__='{voucher_id}';window.__COPIES__={copies};"
            if data is not None:
                printer_name = ''
                preview_only = False
                if isinstance(data, dict):
                    printer_name = str(data.get('_printer') or '').strip()
                    preview_only = bool(data.get('_previewOnly'))
                bill_mode = 'normal'
                if isinstance(data, dict):
                    bill_mode = str(data.get('_billMode') or data.get('bill_mode') or '').strip().lower()
                    shop = self._shop_print_block()
                    # A GST bill needs a registration. Asking for one without
                    # it would print a tax invoice with no number on it, so it
                    # falls back rather than printing something unusable.
                    if bill_mode == 'gst' and shop.get('shop_gst_registered') != '1':
                        LOG("[PRINT] GST bill asked for, but this shop is not registered -- printing the normal bill")
                        bill_mode = 'normal'
                    if bill_mode != 'gst':
                        bill_mode = 'normal'
                    for k, v in shop.items():
                        data.setdefault(k, v)
                    data['_billMode'] = bill_mode
                try:
                    payload = json.dumps(data, ensure_ascii=False)
                except Exception:
                    payload = 'null'
                payload = payload.replace('</script>', '<\\/script>')
                inject += f"window.__BILL_PAYLOAD__={payload};"
                if printer_name:
                    safe_printer = printer_name.replace("'", "\\'")
                    inject += f"window.__PRINTER__='{safe_printer}';"
                if preview_only:
                    inject += "window.__PREVIEW_ONLY__=true;"
                inject += "window.__BILL_MODE__='" + bill_mode + "';"
            inject += '</script>'
            if '<!DOCTYPE html>' in html_content:
                html_content=html_content.replace('<!DOCTYPE html>','<!DOCTYPE html>'+inject,1)
            else:
                html_content=inject+html_content
            def open_window():
                try:
                    _wv = webview if webview is not None else __import__('webview')
                    _wv.create_window(f"Bill -- {voucher_id}",html=html_content,js_api=self,width=850,height=1100,resizable=True)
                except Exception as e:
                    ERR(f"[PRINT WIN] {e}")
            threading.Thread(target=open_window,daemon=True).start()
            return {"status":"success"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def send_bill_whatsapp(self, voucher_id):
        """Generate WhatsApp sharing link for a bill."""
        try:
            LOG(f"[WHATSAPP] send_bill_whatsapp called with voucher_id={voucher_id!r}")
            bill_res = self.get_live_invoice_print_payload(voucher_id)
            LOG(f"[WHATSAPP] get_live_invoice_print_payload returned: {bill_res}")
            if bill_res.get('status') != 'success':
                return {"status":"error","message":"Bill not found"}
            bill = bill_res.get('bill', {})
            customer_mobile = str(bill.get('mobile', '')).strip()
            if not customer_mobile:
                return {"status":"error","message":"Customer mobile number not available"}
            owner_phone = self.db.get_config("owner_phone", "")
            if not owner_phone:
                return {"status":"error","message":"Owner phone number not set in settings"}
            def fmt_phone(p):
                p = re.sub(r'[^\d]', '', str(p).strip())
                if p.startswith('91') and len(p) == 12:
                    return '+' + p
                elif p.startswith('+'):
                    return p
                elif len(p) == 10:
                    return '+91' + p
                elif len(p) == 12:
                    return '+' + p
                return p
            dest_phone = fmt_phone(customer_mobile)
            biz_name = self.db.get_config("business_name", "AurumOS")
            vch_id = bill.get('vch_id', '---')
            customer = bill.get('customer', 'Walking Customer')
            total_amt = float(bill.get('totalAmount') or 0)
            gold_rate = float(bill.get('goldRate') or 0)
            status = bill.get('status', 'PAID')
            bill_date = bill.get('date', '---')
            discount_fine = float(bill.get('discountFine') or 0.0)
            wa_text = (
                "*" + biz_name + " - Invoice*" + "\n"
                "*Voucher:* " + str(vch_id) + "\n"
                "*Customer:* " + str(customer) + "\n"
                "*Total:* Rs." + str(int(total_amt)) + "\n"
                "*Rate:* Rs." + str(int(gold_rate)) + "/g" + "\n"
            )
            if discount_fine > 0:
                wa_text += "*Discount:* " + str(discount_fine) + "g\n"
            wa_text += (
                "*Status:* " + str(status) + "\n"
                "Bill generated on: " + str(bill_date)
            )
            encoded_text = urllib.parse.quote(wa_text)
            wa_url = "https://wa.me/" + str(dest_phone) + "?text=" + str(encoded_text)
            pdf_path = ""
            try:
                pdf_res = self.generate_bill_pdf(voucher_id)
                pdf_path = pdf_res.get("pdf_path", "")
            except Exception:
                pass
            return {
                "status": "success",
                "wa_url": wa_url,
                "pdf_path": pdf_path,
                "customer_mobile": dest_phone,
                "sender_phone": fmt_phone(owner_phone),
                "message": wa_text
            }
        except Exception as e:
            ERR("[WHATSAPP] " + str(e))
            return {"status":"error","message":str(e)}

    def generate_bill_pdf(self, voucher_id):
        """Generate a PDF of the bill from the bill print template."""
        import subprocess, sys, os
        bill_res = self.get_live_invoice_print_payload(voucher_id)
        if bill_res.get('status') != 'success':
            return {"status": "error", "message": "Bill not found"}
        bill = bill_res.get('bill', {})
        ui_dir = get_asset_path('ui')
        template_name = _print_template_for(bill if isinstance(bill, dict) else {})
        template_path = os.path.join(ui_dir, template_name)
        if not os.path.exists(template_path):
            template_name = 'bill_print.html'
            template_path = os.path.join(ui_dir, template_name)
        if not os.path.exists(template_path):
            return {"status": "error", "message": "bill_print.html not found"}
        LOG(f"[PDF] template={template_name} vch={voucher_id}")

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        try:
            payload_json = json.dumps(bill, ensure_ascii=False)
        except Exception as e:
            return {"status": "error", "message": f"Failed to encode bill payload: {e}"}

        payload_json = payload_json.replace('</script>', '<\\/script>')
        safe_vch_id = str(voucher_id).replace("'", "\\'")
        inject = (
            f"<script>window.__VCH_ID__='{safe_vch_id}';"
            f"window.__COPIES__=1;window.__BILL_PAYLOAD__={payload_json};</script>"
        )

        try:
            base_href = Path(ui_dir).resolve().as_uri()
            if '<head>' in template_content.lower():
                template_content = template_content.replace('<head>', '<head><base href="' + base_href + '">', 1)
            else:
                template_content = '<base href="' + base_href + '">' + template_content
        except Exception:
            pass

        if '<!DOCTYPE html>' in template_content:
            html_content = template_content.replace('<!DOCTYPE html>', '<!DOCTYPE html>' + inject, 1)
        else:
            html_content = inject + template_content

        base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
        pdf_dir = os.path.join(base, 'exports')
        os.makedirs(pdf_dir, exist_ok=True)
        safe_id = str(voucher_id).replace('/', '-').replace('\\', '-').strip()
        pdf_path = os.path.join(pdf_dir, f"Bill_{safe_id}.pdf")
        html_tmp = os.path.join(pdf_dir, f"Bill_{safe_id}.html")

        with open(html_tmp, 'w', encoding='utf-8') as f:
            f.write(html_content)
        LOG("[PDF] Bill HTML saved: " + html_tmp)

        generated = False
        try:
            from weasyprint import HTML as WH
            WH(filename=html_tmp).write_pdf(pdf_path)
            generated = True
            LOG("[PDF] Bill generated via weasyprint")
        except ImportError:
            pass
        except Exception as ex:
            LOG(f"[PDF] weasyprint generation failed: {ex}")

        if not generated:
            wk = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
            if os.path.exists(wk):
                try:
                    r = subprocess.run(
                        [
                            wk, '--enable-javascript', '--no-stop-slow-scripts',
                            '--javascript-delay', '500', '--enable-local-file-access',
                            html_tmp, pdf_path
                        ],
                        capture_output=True, timeout=60
                    )
                    if r.returncode == 0 and os.path.exists(pdf_path):
                        generated = True
                        LOG("[PDF] Bill generated via wkhtmltopdf")
                    else:
                        LOG(f"[PDF] wkhtmltopdf failed: {r.returncode} {r.stderr.decode(errors='replace')}" )
                except Exception as ex:
                    LOG(f"[PDF] wkhtmltopdf execution failed: {ex}")

        if generated and os.path.exists(pdf_path):
            try:
                subprocess.Popen(['explorer', '/select,', pdf_path])
            except Exception:
                pass
            return {"status": "success", "pdf_path": pdf_path}

        return {
            "status": "success",
            "pdf_path": html_tmp,
            "message": "Bill HTML saved. Install weasyprint or wkhtmltopdf to generate PDF."
        }

    def get_bill_details(self, vch_id):
        try:
            res=self.db.fetch_one("SELECT * FROM sales_history WHERE UPPER(TRIM(vch_id))=?",(str(vch_id).strip().upper(),))
            if res:
                try: items_list=json.loads(res.get('items','[]'))
                except: items_list=[]
            return {"status":"success","voucher":{
                     "vch_id":res.get('vch_id'),"customer":res.get('customer'),"mobile":res.get('mobile',''),
                     "status":res.get('status'),
                    "ledger_fine":float(res.get('ledger_fine') or 0.0),
                    "collected_fine":float(res.get('collected_fine') or 0.0),
                    "fine_995":float(res.get('fine_995') or 0.0),"fine_dhal":float(res.get('fine_dhal') or 0.0),
                    "remaining_fine":float(res.get('remaining_fine') or 0.0),
                    "gold_rate":float(res.get('gold_rate') or 0.0),
                    "total_amount":float(res.get('total_amount') or 0.0),
                    "discount_type":res.get('discount_type','none'),
                    "discount_touch":float(res.get('discount_touch') or 0.0),
                    "discount_fine":float(res.get('discount_fine') or 0.0),
                    "discount_amount":float(res.get('discount_amount') or 0.0),
                    "date":res.get('date'),"time_stamp":res.get('time_stamp')},"items":items_list}
            return {"status":"error","message":"Sales record not found."}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def fetch_history(self):
        try: return self.db.fetch_history()
        except: return []

    def get_old_gold_register(self, limit=1000):
        try:
            return {"status": "success",
                    "entries": self.db.get_old_gold_register(limit)}
        except Exception as e:
            return {"status": "error", "message": str(e), "entries": []}

    def get_voucher_history_list(self, limit=1000):
        try:
            return {"status":"success","vouchers":self.db.get_voucher_history_list(limit)}
        except Exception as e:
            return {"status":"error","message":str(e),"vouchers":[]}

    def get_touch_details(self, touch_val):
        try:
            d=self.db.get_touch_details(touch_val)
            if d: return {"status":"success","wastage":d['wastage'],"value":d['value'],"name":d['name']}
            return {"status":"not_found","message":f"Touch {touch_val} not configured."}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def check_uchak_stock_available(self, items):
        try:
            short=self.db.check_uchak_stock_available(items)
            if short: return {"status":"insufficient","items":short}
            return {"status":"success","items":[]}
        except Exception as e:
            return {"status":"error","message":str(e),"items":[]}

    def generate_bill(self, bill_data):
        try:
            LOG(f"[BILL] generate_bill called with vch_id={bill_data.get('vch_id')!r}")
            # Notify BASTION AI this is a legitimate write
            try: self.bastion.notify_write()
            except Exception: pass
            vch_id=str(bill_data.get('vch_id','VCH-000')).strip()
            # SECURITY: staff cannot choose their own voucher ID, even by
            # bypassing the UI lock (e.g. devtools). Force the real
            # server-generated next ID regardless of what was submitted.
            if getattr(self, '_session_role', 'admin') == 'staff':
                try:
                    is_uchak_check = str(bill_data.get('is_uchak') or '').lower() in ('1','true','yes')
                    real_id = (self.get_next_uchak_vch_id() if is_uchak_check else self.get_sales_vch_id())
                    if real_id:
                        vch_id = str(real_id).strip()
                        LOG(f"[BILL] staff vch_id override: {bill_data.get('vch_id')!r} -> {vch_id!r}")
                except Exception as _ve:
                    LOG(f"[BILLING] staff vch_id override failed, using submitted value: {_ve}")
            customer=str(bill_data.get('customer','Walking Customer')).strip()
            mobile=str(bill_data.get('mobile','')).strip()
            status=str(bill_data.get('status','CREDIT')).upper().strip()
            l_fine=float(bill_data.get('totalLedgerFine') or 0.0)
            coll=float(bill_data.get('collectedFine') or 0.0)
            f995=float(bill_data.get('fine995') or 0.0)
            dhal=float(bill_data.get('fineDhal') or 0.0)
            rem=float(bill_data.get('remainingFine') or 0.0)
            rate=float(bill_data.get('goldRate') or 0.0)
            is_uchak=bool(bill_data.get('is_uchak',False)) or vch_id.startswith('UCHK-')
            clean_cash_amt=float(str(bill_data.get('totalAmount') or '0.00').replace('?','').replace(',','').strip())
            items_json=json.dumps(bill_data.get('items',[]))
            disc_type=str(bill_data.get('discountType') or 'none')
            disc_touch=float(bill_data.get('discountTouch') or 0.0)
            disc_fine=float(bill_data.get('discountFine') or 0.0)
            disc_amount=float(bill_data.get('discountAmount') or 0.0)
            resolved_status=('UCHAK_UNPAID' if (status=='CREDIT' and is_uchak) else
                             'UCHAK_PAID'   if (status=='PAID'   and is_uchak) else status)
            stock_check = self.db.validate_wholesale_items(json.loads(items_json) if isinstance(items_json, str) else items_json)
            if not stock_check.get("valid"):
                return {"status":"error","code":"INVENTORY_VALIDATION",
                        "message":"\n".join(stock_check.get("errors") or ["Inventory validation failed."])}
            if not self.db.record_sale(vch_id,customer,mobile,resolved_status,l_fine,coll,f995,dhal,rem,rate,clean_cash_amt,items_json,disc_type,disc_touch,disc_fine,disc_amount,
                                       pay_mode=str(bill_data.get('payment_mode') or bill_data.get('pay_mode') or 'cash'),
                                       bill_mode=str(bill_data.get('_billMode') or bill_data.get('bill_mode') or 'normal'),
                                       gst_amount=float(bill_data.get('gst_amount') or bill_data.get('gstAmount') or 0),
                                       gst_rate=float(bill_data.get('gst_rate') or bill_data.get('gstRate') or 0)):
                return {"status":"error","message":"Failed to record sale in database"}
            if resolved_status != 'ESTIMATE':
                try:
                    _deducted = self.db.deduct_stock_after_sale(items_json)
                except Exception as _de:
                    LOG(f"[BILL] Stock deduction failed (sale still recorded): {_de}")
                    _deducted = False
                if not _deducted:
                    return {"status":"error","message":"Sale was not completed because inventory could not be updated."}
            is_cash_settled=(status in ('PAID','CASH','UCHAK_PAID','UCHAK_MAINTAINED'))
            if resolved_status == 'ESTIMATE':
                self._audit(f"Estimate: {vch_id}",f"Customer: {customer} | Status: ESTIMATE","billing")
                LOG(f"[BILL] generate_bill returning estimate vch_id={vch_id!r}")
                return {"status":"success","vch_id":vch_id}
            if is_uchak:
                if is_cash_settled: metal_debit=0.0;metal_credit=0.0;cash_debit=0.0;cash_credit=clean_cash_amt;ledger_desc="Uchak Cash Invoice Paid"
                else: metal_debit=0.0;metal_credit=0.0;cash_debit=clean_cash_amt;cash_credit=0.0;ledger_desc="Uchak Credit Udhar"
            else:
                if is_cash_settled: metal_debit=0.0;metal_credit=float(coll or 0);cash_debit=0.0;cash_credit=clean_cash_amt;ledger_desc=f"Sales Invoice Paid -- Rs{clean_cash_amt:.0f}"
                elif rate>0: metal_debit=0.0;metal_credit=0.0;cash_debit=clean_cash_amt;cash_credit=0.0;ledger_desc="Sales Credit -- Cash Due"
                else: metal_debit=float(rem or 0);metal_credit=0.0;cash_debit=0.0;cash_credit=0.0;ledger_desc=f"Sales Credit -- Fine Due {rem:.3f}g"
            self.post_to_ledger({"client_name":customer,"vch_id":vch_id,"gold_rate":rate,"desc":ledger_desc,
                                 "metal_dr":metal_debit,"metal_cr":metal_credit,"cash_dr":cash_debit,"cash_cr":cash_credit})
            self._audit(f"Bill: {vch_id}",f"Customer: {customer} | Status: {status}","billing")
            self._notify_staff_bill(vch_id, customer, clean_cash_amt)
            LOG(f"[BILL] generate_bill returning vch_id={vch_id!r}")
            return {"status":"success","vch_id":vch_id}
        except Exception as e:
            ERR(f"[BILL] {e}")
            return {"status":"error","message":str(e)}

    def generate_retail_bill(self, data):
        """Handle POS retail bill — saves to DB and triggers print."""
        try:
            inv = data.get('invoice', data) if isinstance(data, dict) else {}
            LOG(f"[RETAIL BILL] generate_retail_bill called with vch_id={inv.get('vch_id')!r}")
            try: self.bastion.notify_write()
            except Exception: pass
            vch_id = str(inv.get('vch_id','R-0000000')).strip()
            customer = str(inv.get('customer','Walk-in')).strip()
            mobile = str(inv.get('phone','')).strip()
            status = str(inv.get('status','PAID')).upper().strip()
            items = inv.get('items',[])
            stock_check = self.db.validate_retail_items(items)
            if not stock_check.get("valid"):
                return {"status": "error", "code": "INVENTORY_VALIDATION",
                        "message": "\n".join(stock_check.get("errors") or ["Inventory validation failed."])}
            items_json = json.dumps(items, ensure_ascii=False)
            total_amount = float(inv.get('grand_total') or inv.get('sub_total') or 0)
            disc_type = str(inv.get('discount_type') or 'none')
            disc_value = float(inv.get('discount_value') or 0)
            disc_amount = float(inv.get('discount') or 0)
            gold_rate = float(inv.get('gold_rate') or 0)
            if not gold_rate and items:
                gold_rate = float(items[0].get('rate') or items[0].get('gold_rate') or 0)
            retail_fine = 0.0
            for item in items if isinstance(items, list) else []:
                try:
                    retail_fine += float(item.get('fine') if item.get('fine') not in (None, '') else (
                        float(item.get('weight') or item.get('gr_wt') or 0) *
                        float(item.get('touch') or 0) / 100
                    ))
                except (TypeError, ValueError):
                    pass
            l_fine = 0; coll = retail_fine if status in ('PAID', 'UCHAK_PAID') else 0; f995 = 0; dhal = 0; rem = 0
            pay_mode = str(inv.get('payment_mode') or inv.get('pay_mode') or 'cash').strip().lower()
            try:
                og_value = float(inv.get('old_gold') or inv.get('oldGold') or 0)
            except (TypeError, ValueError):
                og_value = 0.0
            try:
                og_wt = float(inv.get('old_gold_weight') or inv.get('oldGoldWt') or inv.get('old_gold_wt') or 0)
            except (TypeError, ValueError):
                og_wt = 0.0
            og_desc = str(inv.get('old_gold_desc') or inv.get('oldGoldDesc') or '').strip()
            try:
                og_touch = float(inv.get('old_gold_touch') or inv.get('oldGoldTouch') or 0)
            except (TypeError, ValueError):
                og_touch = 0.0
            try:
                og_rate = float(inv.get('old_gold_rate') or inv.get('oldGoldRate') or 0)
            except (TypeError, ValueError):
                og_rate = 0.0
            if not self.db.record_sale(vch_id, customer, mobile, status, l_fine, coll, f995, dhal, rem, gold_rate, total_amount, items_json, disc_type, 0, 0, disc_amount, pay_mode, og_value, og_wt, og_desc, og_touch, og_rate,
                                       bill_mode=str(inv.get('bill_mode') or inv.get('_billMode') or 'normal'),
                                       cgst=float(inv.get('cgst') or 0), sgst=float(inv.get('sgst') or 0),
                                       cgst_rate=float(inv.get('cgst_rate') or inv.get('cgstRate') or 0),
                                       sgst_rate=float(inv.get('sgst_rate') or inv.get('sgstRate') or 0),
                                       disc_value=float(inv.get('discount_value') or 0)):
                return {"status":"error","message":"Failed to record retail sale."}
            if status != 'ESTIMATE':
                if not self.db.deduct_stock_after_sale(items_json):
                    return {"status":"error","message":"Sale was not completed because inventory could not be updated."}
            # Loyalty, recorded rather than displayed. The till has shown and
            # redeemed points since it was written and nothing ever stored
            # them, so a balance never grew and redeemed points came off
            # nothing. A walk-in with no name earns nothing, because there is
            # nobody to credit.
            loyalty = {}
            if status != 'ESTIMATE' and customer and customer.lower() != 'walk-in':
                try:
                    used = int(float(inv.get('loyalty_used') or 0))
                    if used > 0:
                        self.crm_redeem_loyalty(customer, used, total_amount, vch_id)
                    loyalty = self.crm_award_loyalty(customer, total_amount, vch_id)
                except Exception as _le:
                    ERR(f"[RETAIL BILL] loyalty skipped: {_le}")
            self._audit(f"Retail Bill: {vch_id}", f"Customer: {customer} | Status: {status}", "billing")
            self._notify_staff_bill(vch_id, customer, total_amount)
            LOG(f"[RETAIL BILL] generate_retail_bill returning vch_id={vch_id!r}")
            return {"status":"success","vch_id":vch_id,
                    "loyalty_earned": loyalty.get('earned', 0),
                    "loyalty_balance": loyalty.get('balance', 0)}
        except Exception as e:
            ERR(f"[RETAIL BILL] {e}")
            return {"status":"error","message":str(e)}

    def get_retail_estimate(self, vch_id):
        """Fetch a saved retail estimate by voucher ID for conversion to paid."""
        try:
            safe = str(vch_id).strip().upper()
            res = self.db.fetch_one(
                "SELECT * FROM sales_history WHERE UPPER(TRIM(vch_id))=? AND UPPER(TRIM(status))='ESTIMATE'",
                (safe,)
            )
            if not res:
                return {"status": "error", "message": f"No estimate found with ID: {safe}"}
            try:
                items_list = json.loads(res.get('items', '[]'))
            except:
                items_list = []
            return {
                "status": "success",
                "voucher": {
                    "vch_id": res.get('vch_id'),
                    "customer": res.get('customer'),
                    "mobile": res.get('mobile', ''),
                    "status": res.get('status'),
                    "total_amount": float(res.get('total_amount') or 0.0),
                    "discount_type": res.get('discount_type', 'none'),
                    "discount_amount": float(res.get('discount_amount') or 0.0),
                    "discount_value": float(res.get('discount_value') or 0.0),
                    "date": res.get('date'),
                    "time_stamp": res.get('time_stamp')
                },
                "items": items_list
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def convert_estimate_to_paid(self, data):
        """Convert a saved estimate to PAID, deduct stock, and return for printing.

        Carries everything a direct bill carries — payment mode, rate,
        discount, old gold and loyalty — because the customer pays now, not
        when the estimate was written. Stock is validated BEFORE the bill
        flips: an estimate outlives the shelf, and a PAID bill with nothing
        deducted is money recorded for goods still sitting there.
        """
        try:
            inv = data.get('invoice', data) if isinstance(data, dict) else {}
            vch_id = str(inv.get('vch_id', '')).strip()
            if not vch_id:
                return {"status": "error", "message": "Missing voucher ID."}
            # Only an estimate can be converted. Without this check a second
            # convert of the same voucher (double-click, retry, Enter) would
            # re-record the PAID bill, deduct the stock a second time and
            # award loyalty again, because record_sale overwrites by voucher.
            try:
                _cur = self.db.fetch_one(
                    "SELECT status FROM sales_history WHERE UPPER(TRIM(vch_id))=?",
                    (vch_id.strip().upper(),))
            except Exception:
                _cur = None
            if _cur and str(_cur.get('status') or '').strip().upper() != 'ESTIMATE':
                return {"status": "error", "code": "ALREADY_PAID",
                        "message": f"{vch_id} is already {_cur.get('status')}. Re-open it from history instead of converting again."}
            try:
                self.bastion.notify_write()
            except Exception:
                pass
            customer = str(inv.get('customer', 'Walk-in')).strip()
            mobile = str(inv.get('phone', '')).strip()
            items = inv.get('items', [])
            stock_check = self.db.validate_retail_items(items)
            if not stock_check.get("valid"):
                return {"status": "error", "code": "INVENTORY_VALIDATION",
                        "message": "\n".join(stock_check.get("errors") or ["Inventory validation failed."])}
            items_json = json.dumps(items, ensure_ascii=False)
            total_amount = float(inv.get('grand_total') or inv.get('sub_total') or 0)
            disc_type = str(inv.get('discount_type') or 'none')
            disc_value = float(inv.get('discount_value') or 0)
            disc_amount = float(inv.get('discount') or 0)
            gold_rate = float(inv.get('gold_rate') or 0)
            if not gold_rate and items:
                gold_rate = float(items[0].get('rate') or items[0].get('gold_rate') or 0)
            try:
                _rf = 0.0
                for _it in items if isinstance(items, list) else []:
                    if isinstance(_it, dict):
                        if _it.get('fine') not in (None, ''):
                            _rf += float(_it.get('fine') or 0)
                        else:
                            _rf += float(_it.get('weight') or _it.get('gr_wt') or 0) * float(_it.get('touch') or 0) / 100.0
            except (TypeError, ValueError):
                _rf = 0.0
            l_fine = 0; coll = round(_rf, 3); f995 = 0; dhal = 0; rem = 0
            pay_mode = str(inv.get('payment_mode') or inv.get('pay_mode') or 'cash').strip().lower()
            try:
                og_value = float(inv.get('old_gold') or inv.get('oldGold') or 0)
            except (TypeError, ValueError):
                og_value = 0.0
            try:
                og_wt = float(inv.get('old_gold_weight') or inv.get('oldGoldWt') or inv.get('old_gold_wt') or 0)
            except (TypeError, ValueError):
                og_wt = 0.0
            og_desc = str(inv.get('old_gold_desc') or inv.get('oldGoldDesc') or '').strip()
            try:
                og_touch = float(inv.get('old_gold_touch') or inv.get('oldGoldTouch') or 0)
            except (TypeError, ValueError):
                og_touch = 0.0
            try:
                og_rate = float(inv.get('old_gold_rate') or inv.get('oldGoldRate') or 0)
            except (TypeError, ValueError):
                og_rate = 0.0
            if not self.db.record_sale(vch_id, customer, mobile, 'PAID', l_fine, coll, f995, dhal, rem, gold_rate, total_amount, items_json, disc_type, 0, 0, disc_amount, pay_mode, og_value, og_wt, og_desc, og_touch, og_rate,
                                       bill_mode=str(inv.get('bill_mode') or inv.get('_billMode') or 'normal'),
                                       cgst=float(inv.get('cgst') or 0), sgst=float(inv.get('sgst') or 0),
                                       cgst_rate=float(inv.get('cgst_rate') or inv.get('cgstRate') or 0),
                                       sgst_rate=float(inv.get('sgst_rate') or inv.get('sgstRate') or 0),
                                       disc_value=float(inv.get('discount_value') or 0)):
                return {"status": "error", "message": "Failed to update estimate to PAID."}
            if not self.db.deduct_stock_after_sale(items_json):
                return {"status": "error", "message": "Estimate was not completed because inventory could not be updated."}
            loyalty = {}
            if customer and customer.lower() != 'walk-in':
                try:
                    used = int(float(inv.get('loyalty_used') or 0))
                    if used > 0:
                        self.crm_redeem_loyalty(customer, used, total_amount, vch_id)
                    loyalty = self.crm_award_loyalty(customer, total_amount, vch_id)
                except Exception as _le:
                    ERR(f"[ESTIMATE CONVERT] loyalty skipped: {_le}")
            self._audit(f"Estimate Converted: {vch_id}", f"Customer: {customer} | Status: PAID", "billing")
            self._notify_staff_bill(vch_id, customer, total_amount)
            LOG(f"[ESTIMATE] Converted {vch_id} to PAID")
            return {"status": "success", "vch_id": vch_id,
                    "loyalty_earned": loyalty.get('earned', 0),
                    "loyalty_balance": loyalty.get('balance', 0)}
        except Exception as e:
            ERR(f"[ESTIMATE CONVERT] {e}")
            return {"status": "error", "message": str(e)}

    def get_estimate_customers(self):
        """Return distinct customers who have ESTIMATE-status bills."""
        try:
            rows = self.db.fetch_all(
                "SELECT customer as name, vch_id, date, total_amount "
                "FROM sales_history WHERE UPPER(TRIM(status))='ESTIMATE' "
                "ORDER BY id DESC"
            )
            customers = []
            seen = set()
            for r in (rows or []):
                name = (r.get('name') or '').strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                customers.append({
                    "name": name,
                    "vch_id": r.get('vch_id', ''),
                    "date": r.get('date', ''),
                    "total_amount": float(r.get('total_amount') or 0)
                })
            return {"status": "success", "customers": customers}
        except Exception as e:
            return {"status": "error", "message": str(e), "customers": []}

    def get_customer_estimate(self, customer_name):
        """Get the latest estimate for a specific customer."""
        try:
            safe = str(customer_name).strip()
            res = self.db.fetch_one(
                "SELECT * FROM sales_history WHERE UPPER(TRIM(customer))=UPPER(?) AND UPPER(TRIM(status))='ESTIMATE' "
                "ORDER BY id DESC LIMIT 1",
                (safe,)
            )
            if not res:
                return {"status": "error", "message": f"No estimate found for {safe}"}
            try:
                items_list = json.loads(res.get('items', '[]'))
            except:
                items_list = []
            return {
                "status": "success",
                "voucher": {
                    "vch_id": res.get('vch_id'),
                    "customer": res.get('customer'),
                    "mobile": res.get('mobile', ''),
                    "status": res.get('status'),
                    "total_amount": float(res.get('total_amount') or 0.0),
                    "discount_type": res.get('discount_type', 'none'),
                    "discount_amount": float(res.get('discount_amount') or 0.0),
                    "discount_value": float(res.get('discount_value') or 0.0),
                    "date": res.get('date'),
                    "time_stamp": res.get('time_stamp')
                },
                "items": items_list
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_bill(self, vch_id):
        try:
            safe_vch_id=str(vch_id).strip()
            res=self.db.fetch_one("SELECT items FROM sales_history WHERE vch_id=?",(safe_vch_id,))
            if not res: return {"status":"error","message":"Bill not found."}
            try: items=json.loads(res.get('items') or '[]')
            except: items=[]
            with self.db._get_connection() as conn:
                cursor=conn.cursor()
                for item in items:
                    tag_id=str(item.get('tag_id') or '').strip()
                    it_code=str(item.get('it_code') or item.get('code') or '').strip()
                    weight=float(item.get('weight') or item.get('gr_wt') or 0.0)
                    touch=float(item.get('touch') or 0.0)
                    pcs=int(item.get('pcs') or 1)
                    is_weight=(not tag_id or tag_id in ('','N/A') or tag_id.startswith('KATTI-%'))
                    is_uchak='amount' in item or 'price' in item
                    if is_weight and weight>0:
                        base_code=it_code.strip()
                        if base_code.upper().startswith('KATTI-RESTORE-'):
                            base_code=base_code[len('KATTI-RESTORE-'):]
                        if base_code.upper().startswith('KATTI-'):
                            base_code=base_code[6:]
                        search_codes=[]
                        if base_code:
                            canonical_code=f"KATTI-{base_code}"
                            search_codes.append(canonical_code)
                            search_codes.append(base_code)
                            search_codes.append(f"KATTI-RESTORE-{base_code}")
                            if it_code and it_code.upper().startswith('KATTI-') and not it_code.upper().startswith('KATTI-RESTORE-'):
                                search_codes.insert(0,it_code)
                        row=None
                        for code in search_codes:
                            row=cursor.execute(
                                "SELECT id,tag_id,gr_wt FROM stock_inventory WHERE TRIM(it_code)=? AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%') LIMIT 1",
                                (code,)).fetchone()
                            if row:
                                break
                        if row:
                            restored=round((row['gr_wt'] or 0)+weight,3)
                            cursor.execute("UPDATE stock_inventory SET gr_wt=?,nt_wt=? WHERE id=?",(restored,restored,row['id']))
                        else:
                            cursor.execute("INSERT INTO stock_inventory (it_code,it_name,tag_id,pcs,gr_wt,ls_wt,nt_wt,touch,wastage,is_tagged,entry_date) VALUES (?,?,?,0,?,0,?,?,0,0,date('now'))",
                                (it_code,item.get('it_name') or it_code,f"KATTI-RESTORE-{it_code}",weight,weight,touch))
                    elif is_uchak and pcs>0:
                        piece_code=str(item.get('it_code') or item.get('name') or '').strip()
                        row=cursor.execute("SELECT id,pcs FROM stock_inventory WHERE TRIM(it_code)=? AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A') LIMIT 1",(piece_code,)).fetchone()
                        if row:
                            cursor.execute("UPDATE stock_inventory SET pcs=? WHERE id=?",((row['pcs'] or 0)+pcs,row['id']))
                cursor.execute("DELETE FROM sales_history WHERE vch_id=?",(safe_vch_id,))
                cursor.execute("DELETE FROM credit_ledger WHERE vch_reference=?",(safe_vch_id,))
                conn.commit()
            return {"status":"success","message":f"Bill {safe_vch_id} deleted and stock restored."}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_low_stock_items(self, threshold=10.0):
        try: return self.db.get_low_stock_items(float(threshold))
        except Exception as e: ERR(f"[LOW STOCK] {e}"); return []

    def get_low_stock_uchak_items(self, threshold=5):
        try:
            result=self.db.get_low_stock_uchak_items(int(threshold))
            return result if result is not None else []
        except Exception as e: ERR(f"[UCHAK LOW STOCK] {e}"); return []

    def get_out_of_stock_items(self):
        try: return self.db.get_out_of_stock_items()
        except Exception as e: ERR(f"[OOS] {e}"); return []

    # -- UPDATE ----------------------------------------------------------------
    def check_for_update(self):
        result = check_for_update(timeout=8)
        if result:
            self._last_update_files = result.pop("_changed", None)
        return result

    # ── UPDATE STATE (polled by JS every 500ms) ──────────────────────────────
    _update_state = {"pct": 0, "status": "idle", "done": False,
                     "success": False, "message": ""}

    def get_update_state(self):
        """JS polls this every 500ms to get progress. No push needed."""
        return dict(self._update_state)

    def start_update_with_info(self, version_info):
        """
        Download changed files directly — no _run() wrapper, no bat file complexity.
        Progress fires via threading.Timer every 200ms into JS.
        """
        import queue as _q_mod
        import urllib.request as _ur
        import hashlib as _hl

        try:
            from updater import sha256 as _sha256, get_app_root, _is_protected, set_installed_version
        except Exception as e:
            ERR(f"[UPDATE] import error: {e}")
            return {"status": "error", "message": str(e)}

        q = _q_mod.Queue()

        def fire(js):
            try:
                if self._window:
                    self._window.evaluate_js(js)
            except Exception:
                pass

        def safe_str(s):
            return (str(s)
                    .encode('ascii', errors='replace')
                    .decode('ascii')
                    .replace("'", " ")
                    .replace('"', ' ')
                    .replace('\n', ' '))

        def push_progress(pct, msg):
            LOG(f"[UPDATE] {int(pct)}% {msg}")
            q.put(('p', int(pct), safe_str(msg)))

        def push_done(ok, msg):
            LOG(f"[UPDATE] done ok={ok} {msg}")
            q.put(('d', ok, safe_str(msg)))

        def drain():
            try:
                while True:
                    item = q.get_nowait()
                    if item[0] == 'p':
                        _, pct, msg = item
                        fire(
                            "var f=document.getElementById('uprog-fill'),"
                            "s=document.getElementById('uprog-status');"
                            "if(f)f.style.width='" + str(pct) + "%';"
                            "if(s)s.innerText='" + msg + "';"
                        )
                    elif item[0] == 'd':
                        _, ok, msg = item
                        val = 'true' if ok else 'false'
                        fire(
                            "window.dispatchEvent(new CustomEvent('aurum-update-done',"
                            "{detail:{success:" + val + ",message:'" + msg + "'}}))"
                        )
                        self._update_running = False
                        return
            except _q_mod.Empty:
                pass
            if getattr(self, '_update_running', False):
                threading.Timer(0.25, drain).start()

        def download_all(ui_files, backend_files, app_root):
            all_files = ui_files + backend_files
            total = len(all_files)
            staging = app_root / '_update_staging'
            LOG(f"[UPDATE] _download_all: {len(ui_files)} UI + {len(backend_files)} backend, app_root={app_root}")

            for i, entry in enumerate(all_files, 1):
                rel  = entry.get('path', '').replace('\\', '/')
                url  = entry.get('url', '')
                size = entry.get('size', entry.get('size_bytes', 0))
                is_backend = entry in backend_files

                push_progress(
                    int(5 + (i - 1) / total * 85),
                    f"[{i}/{total}] {'⚙️ ' if is_backend else ''}{rel}"
                )

                LOG(f"[UPDATE] Downloading [{i}/{total}] {rel} from {url[:60]}")

                # Simple direct download
                try:
                    req = _ur.Request(
                        url,
                        headers={
                            'User-Agent': f'AurumOS/{CURRENT_VERSION}',
                            'Accept': '*/*',
                        }
                    )
                    with _ur.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                    LOG(f"[UPDATE]   {len(data):,} bytes OK")
                except Exception as e:
                    err = safe_str(str(e))
                    ERR(f"[UPDATE]   FAILED: {err}")
                    push_done(False, f"Download failed [{i}/{total}]: {err}")
                    return

                # UI files → write directly (live reload on next page load)
                # Backend files → write to _update_staging/ (apply on restart)
                if is_backend:
                    dst = staging / rel
                else:
                    dst = app_root / rel

                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(data)
                    LOG(f"[UPDATE]   Written: {dst}")
                except Exception as e:
                    err = safe_str(str(e))
                    ERR(f"[UPDATE]   Write failed: {err}")
                    push_done(False, f"Write failed {rel}: {err}")
                    return

                push_progress(
                    int(5 + i / total * 85),
                    f"[{i}/{total}] OK {rel}"
                )

            # Migrations downloaded above apply immediately — they are plain
            # SQL the database runs idempotently, so no restart is needed.
            try:
                mig_rels = [e.get('path', '') for e in all_files
                            if e.get('_migration')]
                if mig_rels:
                    applied = self.db.apply_migrations(str(app_root))
                    LOG(f"[UPDATE] migrations applied now: {applied}")
            except Exception as _me:
                LOG(f"[UPDATE] migration apply deferred to restart: {_me}")

            # Set new version — read from version_info passed by JS
            # NEVER use installed_version from DB (could be stale 1.1.0)
            push_progress(95, "Saving version...")
            try:
                info_dict  = version_info if isinstance(version_info, dict) else {}
                remote_ver = str(info_dict.get('version', '') or '').strip()

                # Validate: must be X.Y.Z format and NEWER than current
                import re as _re
                if _re.match(r'^\d+\.\d+\.\d+$', remote_ver):
                    new_ver = remote_ver
                else:
                    new_ver = CURRENT_VERSION
                    LOG(f"[UPDATE] Bad remote version {remote_ver!r}, keeping {CURRENT_VERSION}")

                set_installed_version(new_ver)
                LOG(f"[UPDATE] version.lock written for {new_ver}")
                LOG(f"[UPDATE] Version set to {new_ver}")
            except Exception as e:
                new_ver = CURRENT_VERSION
                LOG(f"[UPDATE] Version save error: {e}")

            backend_count = len(backend_files)
            restart_note = f" {backend_count} backend file(s) will apply on restart." if backend_count else ""
            push_progress(100, f"Done! {total} file(s) updated.{restart_note}")
            push_done(True, f"Update ready. v{new_ver} installed.{restart_note} Click Restart.")
            # Update badge immediately
            try:
                if self._window:
                    self._window.evaluate_js(
                        "var _ve=document.getElementById('sb-ver');"
                        "if(_ve)_ve.innerText='v" + new_ver + " ↻';"
                    )
            except Exception:
                pass

        # Build changed list
        try:
            info      = version_info if isinstance(version_info, dict) else {}
            all_files = info.get('files', [])
            app_root  = get_app_root()

            LOG(f"[UPDATE] -- start_update_with_info -----------------")
            LOG(f"[UPDATE] files count: {len(all_files)}")
            LOG(f"[UPDATE] app_root: {app_root}")
            LOG(f"[UPDATE] app_root exists: {app_root.exists() if hasattr(app_root,'exists') else 'n/a'}")

            # ── File categorization ──────────────────────────────────────
            # UI files: _internal/ui/* → download directly (live reload)
            # Backend: core/*.py, database/*.py, network/*.py → stage, apply on restart
            # Migrations: migrations/*.sql → download directly; the database
            # applies each one exactly once at startup (see
            # DBManager.apply_migrations), so schema fixes ride the same
            # release that needs them instead of waiting for a rebuild.
            _UI_PREFIX = '_internal/ui/'
            _UI_EXTS = {'.html', '.js', '.css', '.png', '.jpg', '.svg', '.json',
                        '.woff', '.woff2', '.ttf', '.map'}
            _BACKEND_PREFIXES = ('core/', 'database/', 'network/')
            _BACKEND_EXTS = {'.py'}
            _MIGRATION_PREFIX = 'migrations/'
            _MIGRATION_EXTS = {'.sql'}

            def _categorize(rel):
                """Return 'ui', 'backend', 'migration', or None (skip)."""
                ext = os.path.splitext(rel)[1].lower()
                if rel.startswith(_UI_PREFIX) and ext in _UI_EXTS:
                    return 'ui'
                if rel.startswith(_MIGRATION_PREFIX) and ext in _MIGRATION_EXTS:
                    return 'migration'
                for pfx in _BACKEND_PREFIXES:
                    if rel.startswith(pfx) and ext in _BACKEND_EXTS:
                        return 'backend'
                return None

            ui_files = []
            backend_files = []
            for entry in all_files:
                rel         = entry.get('path', '').replace(chr(92), '/')
                remote_hash = entry.get('sha256', '')
                url         = entry.get('url', '')
                size        = entry.get('size_bytes', 0)
                if not rel or not url:
                    continue
                if _is_protected(rel):
                    LOG(f"[UPDATE]   PROTECTED: {rel}")
                    continue
                cat = _categorize(rel)
                if cat is None:
                    LOG(f"[UPDATE]   SKIPPED: {rel}")
                    continue
                local_hash = _sha256(app_root / rel)
                if local_hash != remote_hash:
                    tag = 'NEW' if local_hash == '' else 'CHANGED'
                    entry_data = {'path': rel, 'sha256': remote_hash, 'url': url, 'size': size}
                    LOG(f"[UPDATE]   [{tag}] {rel}  local={local_hash[:12] or 'MISSING'}  remote={remote_hash[:12]}")
                    if cat == 'ui':
                        ui_files.append(entry_data)
                    elif cat == 'migration':
                        ui_files.append(dict(entry_data, _migration=True))
                    else:
                        backend_files.append(entry_data)
                else:
                    LOG(f"[UPDATE]   [OK]      {rel}")

            total_changed = len(ui_files) + len(backend_files)
            LOG(f"[UPDATE] --- result: {len(ui_files)} UI + {len(backend_files)} backend = {total_changed} of {len(all_files)} need update ---")

            if not ui_files and not backend_files:
                push_done(False, "Already up to date.")
                return {"status": "nothing_to_do"}

            self._last_update_files = ui_files + backend_files
            self._update_running    = True
            LOG(f"[UPDATE] Starting download thread for {total_changed} file(s)")

            threading.Thread(
                target=download_all,
                args=(ui_files, backend_files, app_root),
                daemon=True
            ).start()

            threading.Timer(0.25, drain).start()
            return {"status": "started", "files": total_changed, "backend": len(backend_files)}

        except Exception as e:
            err = safe_str(str(e))
            ERR(f"[UPDATE] EXCEPTION: {err}")
            import traceback; traceback.print_exc()
            push_done(False, f"Error: {err}")
            return {"status": "error"}


    # ── AUTO-UPDATE CHECK (shows update card, user clicks Install) ──────
    # Runs in background at startup + every 30 min. Only CHECKS for updates
    # and shows the update card. The user clicks "Install" to start the
    # actual download via start_update_with_info().
    _auto_update_busy = False

    def auto_update_check(self):
        """Check for updates and show the update card if available."""
        if getattr(self, '_auto_update_busy', False):
            return {"status": "busy"}
        self._auto_update_busy = True
        try:
            from updater import check_for_update as _check
        except Exception as e:
            self._auto_update_busy = False
            return {"status": "error", "message": str(e)}
        try:
            try:
                info = _check(timeout=8)
            except Exception:
                info = None
            if not info or not info.get('available'):
                return {"status": "nothing_to_do"}
            _applied = getattr(self, '_update_applied_version', None)
            if _applied and _applied == info.get('version'):
                LOG(f"[AUTO-UPDATE] Already applied v{_applied} -- skip card")
                return {"status": "nothing_to_do"}
            import json as _j
            r_js = {k: v for k, v in info.items() if k != "_changed"}
            json_str = _j.dumps(r_js)
            js = ("(function(){var d=" + json_str + ";"
                  "if(window.__showUpdate) window.__showUpdate(d);"
                  "window.dispatchEvent(new CustomEvent('aurum-update-available',{detail:d}));})()")
            new_ver = str(info.get('version', '') or '').strip()
            LOG(f"[AUTO-UPDATE] v{new_ver} available, showing update card")
            self._last_update_files = info.get("_changed", [])
            try:
                if self._window:
                    self._window.evaluate_js(js)
            except Exception:
                pass
            return {"status": "update_available", "version": new_ver}
        finally:
            self._auto_update_busy = False

    def auto_update_loop(self):
        """Background loop: check at startup, then every 30 minutes. Shows card only."""
        import time as _t
        try:
            _t.sleep(45)
            self.auto_update_check()
        except Exception as e:
            LOG(f"[AUTO-UPDATE] {e}")
        while not getattr(self, '_app_closing', False):
            try:
                for _ in range(30 * 60):
                    _t.sleep(1)
                    if getattr(self, '_app_closing', False):
                        break
                if getattr(self, '_app_closing', False):
                    break
                self.auto_update_check()
            except Exception:
                pass


    def reset_lockout(self) -> dict:
        """
        Developer/admin unlock — resets failed attempts and clears lockout.
        Call from Python console: app_api.reset_lockout()
        Or via pywebview bridge for an admin unlock page.
        """
        try:
            self._login_attempts = 0
            self._lockout_until  = None
            self._lockout_tier   = 0
            self._save_lockout_state()
            # Also delete the lockout file entirely for clean state
            try:
                lk_path = self._get_lockout_file()
                if os.path.exists(lk_path):
                    os.remove(lk_path)
            except Exception:
                pass
            LOG("[LOGIN] Lockout manually reset by admin/developer")
            return {"status": "success", "message": "Lockout cleared. Account unlocked."}
        except Exception as e:
            LOG(f"[LOGIN] reset_lockout error: {e}")
            return {"status": "error", "message": str(e)}


    def admin_lockout_reset(self, license_key='') -> dict:
        """
        Owner reset — verify license key against server and clear lockout.
        Called from the lock screen admin reset section.
        """
        import urllib.request, urllib.error, json as _j, uuid as _uuid
        key = str(license_key or '').strip().upper()
        if not key or not _is_valid_key_format(key):
            return {"status": "error", "message": "Invalid license key format"}
        try:
            machine_id = str(_uuid.getnode())
            url = self._get_license_check_url() + '/api/check'
            payload = _j.dumps({'key': key, 'machine_id': machine_id}).encode()
            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'User-Agent': f'AurumOS/{CURRENT_VERSION}'
            }, method='POST')
            with self._safe_urlopen(req, timeout=10) as resp:
                data = _j.loads(resp.read().decode())
            if data.get('valid'):
                LOG(f"[LOCK] Admin reset via license key ({key[:10]}...)")
                return self.reset_lockout()
            else:
                reason = data.get('status', 'invalid')
                LOG(f"[LOCK] Admin reset rejected: {reason}")
                return {"status": "error", "message": f"License key is {reason}. Cannot reset."}
        except urllib.error.URLError:
            return {"status": "error", "message": "No internet. Connect and retry."}
        except Exception as e:
            LOG(f"[LOCK] admin_lockout_reset error: {e}")
            return {"status": "error", "message": str(e)}





    def _start_remote_reset_poller(self):
        """
        Polls private GitHub Gist every 30s.
        Only processes commands WHERE target_machine == MY machine_id.
        Temp password is E2E encrypted — only this machine can decrypt.
        """
        GIST_ID    = os.environ.get('AURUM_GIST_ID',    '').strip()
        GIST_TOKEN = os.environ.get('AURUM_GIST_TOKEN', '').strip()
        POLL_FILE  = 'aurum_reset_commands.json'
        INTERVAL   = 30

        if not GIST_ID or not GIST_TOKEN:
            LOG("[RESET] Env vars not set — poller disabled"); return

        my_id = self.get_machine_id()
        LOG(f"[RESET] Poller started — machine={my_id[:8]}…")

        def poll():
            while True:
                try:
                    time.sleep(INTERVAL)
                    req = urllib.request.Request(
                        f'https://api.github.com/gists/{GIST_ID}',
                        headers={'Authorization':f'token {GIST_TOKEN}',
                                 'Accept':'application/vnd.github.v3+json',
                                 'User-Agent':'AurumOS/1.0'})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        gist = json.loads(r.read().decode())

                    files = gist.get('files', {})
                    if POLL_FILE not in files: continue
                    all_cmds = json.loads(files[POLL_FILE].get('content','[]'))
                    if not isinstance(all_cmds, list): all_cmds = [all_cmds]

                    # Find MY command
                    my_cmd = next((c for c in all_cmds
                                   if c.get('target_machine') == my_id
                                   and c.get('command') == 'temp_reset'
                                   and not c.get('used', False)), None)
                    if not my_cmd: continue

                    # Decrypt — only works on THIS machine
                    temp_pass = _decrypt_temp_password(
                        my_cmd.get('encrypted_password',''), my_id)
                    if not temp_pass:
                        LOG("[RESET] Decryption failed"); continue

                    # Save temp password (one-time, expires 1h)
                    ph = self.db._hash_pw(temp_pass)
                    with self.db._get_connection() as conn:
                        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('temp_password_hash',?)",(ph,))
                        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('temp_password_expires',?)",(str(time.time()+3600),))
                        conn.commit()

                    # Clear lockout
                    self._login_attempts = 0; self._lockout_until = None
                    self._save_lockout_state()
                    try:
                        lk = self._get_lockout_file()
                        if os.path.exists(lk): os.remove(lk)
                    except Exception: pass

                    # Mark used in Gist
                    my_cmd['used'] = True
                    my_cmd['used_at'] = datetime.now().isoformat()
                    patch = urllib.request.Request(
                        f'https://api.github.com/gists/{GIST_ID}',
                        data=json.dumps({'files':{POLL_FILE:{'content':json.dumps(all_cmds,indent=2)}}}).encode(),
                        method='PATCH',
                        headers={'Authorization':f'token {GIST_TOKEN}',
                                 'Accept':'application/vnd.github.v3+json',
                                 'Content-Type':'application/json',
                                 'User-Agent':'AurumOS/1.0'})
                    with urllib.request.urlopen(patch, timeout=10): pass

                    LOG("[RESET] Temp password applied ✓")

                    # Tell login screen — pass the temp password for display
                    try:
                        if self._window:
                            import json as _j2
                            safe_pass = _j2.dumps(temp_pass)  # safely escaped
                            self._window.evaluate_js(
                                f"if(typeof window.showTempPasswordUnlock==='function')"
                                f"window.showTempPasswordUnlock({safe_pass});"
                                f"else window.location.reload();"
                            )
                    except Exception: pass

                except Exception as e:
                    LOG(f"[RESET] Poll error: {e}")

        threading.Thread(target=poll, daemon=True, name="ResetPoller").start()

    def get_last_login_info(self) -> dict:
        """
        Return last successful login info for login screen display.
        - Admin: shows real owner name from app_config
        - Staff: shows their username from admin_creds
        - Time:  local machine time, formatted as "13 Jun 2026, 11:45 AM"
        """
        try:
            with self.db._get_connection() as conn:

                # Ensure table exists
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS login_log ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username TEXT NOT NULL DEFAULT 'owner', "
                    "role TEXT NOT NULL DEFAULT 'admin', "
                    "login_time TEXT NOT NULL DEFAULT (datetime('now')), "
                    "ip TEXT DEFAULT '')"
                )
                conn.commit()

                # Get last login row
                row = conn.execute(
                    "SELECT username, role, login_time "
                    "FROM login_log ORDER BY id DESC LIMIT 1"
                ).fetchone()

                if not row:
                    return {}

                db_username = str(row['username'] or '').strip()
                role        = str(row['role']     or 'admin').strip()
                login_time  = str(row['login_time'] or '').strip()

                # ── Resolve display name ───────────────────────────────
                # Admin (id=1) → show owner_name from setup (e.g. "Jenil Dholakiya")
                # Staff        → show their username from admin_creds as-is
                display_name = db_username

                if role == 'admin':
                    # First try: owner_name from app_config (set during setup)
                    cfg_row = conn.execute(
                        "SELECT value FROM app_config WHERE key='owner_name' LIMIT 1"
                    ).fetchone()
                    if cfg_row and str(cfg_row['value'] or '').strip():
                        display_name = cfg_row['value'].strip()
                    else:
                        # Fallback: business_name
                        biz_row = conn.execute(
                            "SELECT value FROM app_config WHERE key='business_name' LIMIT 1"
                        ).fetchone()
                        if biz_row and str(biz_row['value'] or '').strip():
                            display_name = biz_row['value'].strip()
                        else:
                            display_name = db_username or 'Owner'
                else:
                    # Staff — use their actual username stored in login_log
                    # If username is blank/generic use db_username
                    display_name = db_username if db_username else 'Staff'

                # ── Format time (local machine time) ──────────────────
                # Stored as: "2026-06-13 11:30:45" (Python local time)
                # Display as: "13 Jun 2026, 11:30 AM"
                friendly_time = login_time
                try:
                    from datetime import datetime as _dt
                    # Handle both formats just in case
                    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
                        try:
                            dt = _dt.strptime(login_time, fmt)
                            friendly_time = dt.strftime('%d %b %Y, %I:%M %p')
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

                LOG(f"[LOGIN_INFO] name={display_name} role={role} time={friendly_time}")

                return {
                    'username':      display_name,
                    'role':          role,
                    'time':          login_time,
                    'friendly_time': friendly_time,
                    'display':       'Last login: ' + display_name + ' on ' + friendly_time
                }

        except Exception as e:
            LOG(f"[API] get_last_login_info error: {e}")
        return {}

    def check_weight_stock_available(self, items_json: str) -> dict:
        """Check if weight-based stock is sufficient before committing bill."""
        try:
            return self.db.check_weight_stock_available(items_json)
        except Exception as e:
            LOG(f"[API] check_weight_stock_available error: {e}")
            return {'status': 'ok'}

    def get_current_version(self):
        """Return effective version: version.lock if newer, else baked CURRENT_VERSION."""
        try:
            from updater import get_installed_version
            return get_installed_version()
        except Exception:
            return CURRENT_VERSION

    def get_version_info(self):
        """Everything the screens show about versions, in one call.

        installed  the number this copy runs and displays
        built      the number frozen into the EXE, for support questions
        latest     the newest published release, when it could be reached
        available  true when latest is newer and a download is waiting

        The pages do not depend on this method existing: boot.js asks GitHub
        directly when the bridge cannot answer, so the version on screen is
        right on copies built before this method was added.
        """
        try:
            from updater import BUILD_STAMP as _STAMP
        except Exception:
            _STAMP = ''
        info = {'installed': CURRENT_VERSION, 'built': CURRENT_VERSION,
                'build': _STAMP,
                'latest': '', 'available': False, 'release_date': '',
                'changelog': [], 'checked': False}
        try:
            from updater import get_installed_version, fetch_latest_manifest, is_newer
            info['installed'] = get_installed_version()
        except Exception as e:
            LOG(f"[VERSION] local read failed: {e}")
            return info
        try:
            manifest = fetch_latest_manifest(timeout=6)
            if manifest:
                info['checked'] = True
                info['latest'] = str(manifest.get('version', '') or '')
                info['release_date'] = str(manifest.get('release_date', '') or '')
                info['changelog'] = list(manifest.get('changelog') or [])[:8]
                info['available'] = bool(
                    is_newer(info['latest'], info['installed']) or manifest.get('force'))
        except Exception as e:
            LOG(f"[VERSION] remote check failed: {e}")
        return info

    def restart_app(self):
        """Close this instance and start a fresh one (used after restore).

        The old code spawned the new process then called sys.exit(0), which
        only exits the API thread — the pywebview loop kept the old window
        alive, so two copies ended up running against the same database.
        Now: stop background services, destroy the window, launch the new
        process detached, then hard-exit so the old one is definitely gone.
        """
        import subprocess
        import time as _t
        LOG("[RESTART] Restarting AurumOS...")
        try:
            # 1) Free ports/sockets first so the new instance can bind them.
            try:
                self._app_closing = True
            except Exception:
                pass
            try:
                from network import cluster_server as _cs, cluster_client as _cc
                _cc.stop_sync()
                _cs.stop_server()
            except Exception:
                pass
            try:
                self.bastion.notify_session_active(False)
                self.bastion.stop()
            except Exception:
                pass
            try:
                self.db._cleanup_session()
            except Exception:
                pass
            # 2) Close the window (fires the normal closing cleanup too).
            try:
                if getattr(self, '_window', None) is not None:
                    self._window.destroy()
            except Exception:
                pass
            _t.sleep(0.8)
            # 3) Launch the replacement, detached from this process.
            if getattr(sys, 'frozen', False):
                cmd = [sys.executable]
                cwd = os.path.dirname(os.path.abspath(sys.executable))
            else:
                script = os.path.abspath(sys.argv[0]) if sys.argv else os.path.abspath('main.py')
                cmd = [sys.executable, script] + (list(sys.argv[1:]) if len(sys.argv) > 1 else [])
                cwd = os.path.abspath('.')
            kw = {}
            if os.name == 'nt':
                try:
                    kw['creationflags'] = subprocess.DETACHED_PROCESS
                except Exception:
                    pass
            subprocess.Popen(cmd, cwd=cwd, close_fds=True, **kw)
            LOG(f"[RESTART] launched: {cmd}")
        except Exception as e:
            ERR(f"[RESTART] relaunch failed: {e}")
            return {"status": "error", "message": str(e)}
        # 4) Guarantee the old process is gone — sys.exit() alone only
        # stops the calling thread and leaves the window running. Reached
        # only when the replacement is already launched.
        import os as _os
        _os._exit(0)

    def get_stocksync_snapshot(self):
        try:    return self.db.get_stocksync_snapshot()
        except Exception as e: return {'status':'error','message':str(e),'data':[]}

    def get_touch_stock_report(self):
        try: return self.db.get_touch_stock_report()
        except Exception as e: ERR(f"[TOUCH REPORT] {e}"); return []

    def get_stagnant_report(self, threshold):
        try: return {"status":"success","data":self.db.get_stagnant_report(threshold)}
        except Exception as e: return {"status":"error","message":str(e)}

    def scale_connect(self, port, baud=9600):
        LOG(f"[SCALE_API] scale_connect called: port={port!r} baud={baud}")
        try:
            _scale.set_window(self._window)
            result = _scale.start(str(port).strip(), int(baud))
            LOG(f"[SCALE_API] scale_connect result: {result}")
            # Save last used port/baud to app_config for all pages to read
            if result.get('status') == 'success':
                try:
                    with self.db._get_connection() as conn:
                        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('scale_port',?)", (str(port).strip(),))
                        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('scale_baud',?)", (str(result.get('baud', baud)),))
                        conn.commit()
                    LOG(f"[SCALE_API] Saved port={port} baud={result.get('baud',baud)} to app_config")
                except: pass
            return result
        except Exception as e:
            ERR(f"[SCALE_API] scale_connect exception: {e}")
            import traceback; ERR(traceback.format_exc())
            return {"status": "error", "message": str(e)}

    def scale_disconnect(self):
        LOG("[SCALE_API] scale_disconnect called")
        if getattr(self, '_session_role', '') == 'staff':
            return {"status": "error", "message": "Staff users cannot disconnect the scale."}
        _scale.stop()
        # Clear saved port so it does not auto-reconnect next startup
        try:
            with self.db._get_connection() as conn:
                conn.execute("DELETE FROM app_config WHERE key IN ('scale_port','scale_baud')")
                conn.commit()
        except Exception: pass
        return {"status": "ok"}

    def js_log(self, msg, level='INFO'):
        """Called from JS via pywebview.api.js_log(msg) to log JS events in terminal."""
        safe = str(msg).encode('ascii', errors='replace').decode('ascii')
        if level == 'ERROR':
            ERR(f"[JS] {safe}")
        else:
            LOG(f"[JS] {safe}")

    def scale_get_ports(self):
        LOG("[SCALE_API] scale_get_ports called")
        try:
            ports = _scale.list_ports()
            LOG(f"[SCALE_API] scale_get_ports result: {ports}")
            return ports
        except Exception as e:
            ERR(f"[SCALE_API] scale_get_ports exception: {e}")
            return []

    def scale_is_connected(self):
        """Check if scale is currently running."""
        running = _scale._running and _scale._serial is not None
        LOG(f"[SCALE_API] is_connected: {running} port={_scale._port} baud={_scale._baud}")
        return {
            "connected": running,
            "port": _scale._port or "",
            "baud": _scale._baud or 1200,
        }

    def scale_get_saved_config(self):
        """Return last successfully used port and baud."""
        try:
            with self.db._get_connection() as conn:
                rows = {r['key']:r['value'] for r in conn.execute(
                    "SELECT key,value FROM app_config WHERE key IN ('scale_port','scale_baud')"
                ).fetchall()}
            port = rows.get('scale_port','')
            baud = int(rows.get('scale_baud', 1200))
            LOG(f"[SCALE_API] saved config: port={port} baud={baud}")
            return {"port": port, "baud": baud}
        except Exception as e:
            ERR(f"[SCALE_API] scale_get_saved_config: {e}")
            return {"port": "", "baud": 1200}

    def scale_get_last(self):
        w = _scale.get_last()
        connected = _scale._running
        LOG(f"[SCALE_API] scale_get_last: w={w} connected={connected}")
        return {"weight": w, "stable": True, "connected": connected} if w else {"weight": None, "stable": False, "connected": connected}

    def get_weight_stock_it_codes(self):    return self.db.get_weight_stock_it_codes()
    def get_touch_ledger_details(self, touch_value, mode='weight', from_date='', to_date=''):
        return self.db.get_touch_ledger_details(touch_value, mode, from_date, to_date)
    def mark_as_tagged(self, entry_id):     return self.db.mark_as_tagged(entry_id)

    def get_machine_id(self):
        try:
            import uuid
            return str(uuid.getnode())
        except: return 'unknown'

    def open_log_folder(self):
        """Open the logs folder in Windows Explorer."""
        try:
            import subprocess
            base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath('.')
            log_dir = os.path.join(base, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            subprocess.Popen(['explorer', log_dir])
            return {"status": "ok", "path": log_dir}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── BASTION SECURITY API WRAPPERS ────────────────────────────────────────
    def bastion_get_status(self):
        return self.db.bastion_get_status()

    def bastion_unlock(self, admin_key, lock_code=None):
        """
        Clear BASTION suspension using BASTION-specific 16-char admin key.
        Regular 12-char unlock key does NOT work here — different salt.
        Only the BASTION key from unlock_keygen.py (BASTION mode) works.
        """
        # If key is 12 chars, also try regular unlock verification (for web dashboard keys)
        entered = str(admin_key).strip().upper()
        if len(entered) == 12:
            regular_result = self.verify_unlock_key(admin_key, lock_code)
            if regular_result.get('status') == 'success':
                LOG("[BASTION] Suspension cleared via regular unlock key")
                return regular_result
        
        result = self.db.bastion_clear(admin_key, lock_code)
        if result.get('status') == 'success':
            LOG("[BASTION] Suspension cleared by admin")
        else:
            LOG(f"[BASTION] Clear failed: {result.get('message')}")
        return result

        # ── SECURITY API WRAPPERS ─────────────────────────────────────────────────
    def do_stock_med(self, data):
        return self.db.do_stock_med(data)

    def get_lock_status(self):
        return self.db.get_lock_status()

    def record_failed_attempt(self):
        return self.db.record_failed_attempt()

    def verify_unlock_key(self, unlock_key, lock_code=None):
        result = self.db.verify_unlock_key(unlock_key, lock_code)
        if result and result.get('status') == 'success':
            self._lockout_until  = None
            self._login_attempts = 0
            self._lockout_tier   = 0
            try: self._save_lockout_state()
            except Exception: pass
            LOG("[LOCK] In-memory lock state cleared after unlock")
        return result

    def verify_web_unlock_key(self, unlock_key, lock_code=None, is_bastion=False):
        """Web dashboard unlock — delegates to verify_unlock_key (same logic)."""
        if is_bastion:
            return self.bastion_unlock(unlock_key, lock_code)
        return self.verify_unlock_key(unlock_key, lock_code)

    def bastion_get_weekly_report(self):
        try:
            return self.bastion.get_weekly_report()
        except Exception as e:
            return {'error': str(e)}

    def generate_protected_pdf(self, html_content, password, audit_id='AUDIT'):
        """
        Generate password-protected PDF from HTML content.
        Uses reportlab + PyPDF2 or pikepdf if available.
        Falls back to saving HTML file with password hint if no PDF lib.
        """
        import os, sys, tempfile, subprocess, hashlib

        base     = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
        pdf_dir  = os.path.join(base, 'exports')
        os.makedirs(pdf_dir, exist_ok=True)
        safe_id  = str(audit_id).replace('/', '-').replace('\\', '-').strip()
        pdf_path = os.path.join(pdf_dir, f"StockSync_{safe_id}.pdf")
        html_tmp = os.path.join(pdf_dir, f"StockSync_{safe_id}.html")

        try:
            # Step 1: Save HTML to temp file
            with open(html_tmp, 'w', encoding='utf-8') as f:
                f.write(html_content)
            LOG(f"[PDF] HTML saved: {html_tmp}")

            # Step 2: Try pikepdf for password protection
            try:
                import pikepdf
                # First generate PDF without password using weasyprint or wkhtmltopdf
                pdf_tmp = pdf_path + '.tmp.pdf'
                generated = False

                # Try weasyprint
                try:
                    from weasyprint import HTML as WH
                    WH(filename=html_tmp).write_pdf(pdf_tmp)
                    generated = True
                    LOG("[PDF] Generated via weasyprint")
                except ImportError:
                    pass

                # Try wkhtmltopdf
                if not generated:
                    wk = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
                    if os.path.exists(wk):
                        r = subprocess.run([wk, html_tmp, pdf_tmp],
                            capture_output=True, timeout=30)
                        if r.returncode == 0:
                            generated = True
                            LOG("[PDF] Generated via wkhtmltopdf")

                if generated and os.path.exists(pdf_tmp):
                    # Apply password with pikepdf
                    if password:
                        pdf = pikepdf.open(pdf_tmp)
                        pdf.save(pdf_path, encryption=pikepdf.Encryption(
                            owner=password, user=password, R=4
                        ))
                        pdf.close()
                        LOG(f"[PDF] Password protected: {pdf_path}")
                    else:
                        import shutil
                        shutil.copy2(pdf_tmp, pdf_path)
                    try: os.remove(pdf_tmp)
                    except: pass
                    # Open folder
                    try: subprocess.Popen(['explorer', '/select,', pdf_path])
                    except: pass
                    return {'status': 'success', 'path': pdf_path}

            except ImportError:
                LOG("[PDF] pikepdf not available — falling back")

            # Step 3: Fallback — save as HTML, open print window
            # Write password hint into HTML if provided
            if password:
                pw_hash = hashlib.sha256(password.encode()).hexdigest()[:12].upper()
                hint_html = html_content.replace(
                    '</body>',
                    f'<div style="display:none" data-pw-hash="{pw_hash}"></div></body>'
                )
                with open(html_tmp, 'w', encoding='utf-8') as f:
                    f.write(hint_html)

            # Open print window — user prints to PDF manually
            result = self.open_print_window(html_content)
            note   = ''
            if password:
                note = f' Password hint saved. Set password "{password}" when saving PDF from print dialog.'
            return {
                'status':  'success',
                'path':    html_tmp,
                'message': 'PDF opened for printing.' + note
            }

        except Exception as e:
            ERR(f"[PDF] generate_protected_pdf error: {e}")
            return {'status': 'error', 'message': str(e)}

    # ── NETWORK BRIDGE — PC2 tag print via PC1 ───────────────────────────
    def print_tag_network(self, tags):
        """Kept so an older page calling this still prints.

        This used to hand the job to a bridge on the main PC, but that client
        was a stub that always refused, so every call already fell through to
        the local printer. Printing across the shop network is not built yet;
        when it is, it belongs on the cluster server.
        """
        return self.print_tag_local(tags)

    def print_tag_local(self, tags):
        """Print TSC jewellery tag locally (USB)."""
        try:
            results = []
            for tag in (tags if isinstance(tags, list) else [tags]):
                tspl = self._build_tspl(tag)
                self._send_to_tsc(tspl)
                results.append({'ok': True, 'tag_id': tag.get('tag_id','')})
            return {'status': 'success', 'printed': len(results), 'results': results}
        except Exception as e:
            ERR(f'[PRINT] {e}')
            return {'status': 'error', 'message': str(e)}

    def _build_tspl(self, tag: dict) -> bytes:
        """Build TSPL command for TSC jewellery tag (40x20mm)."""
        item_name = str(tag.get('item_name', '')).upper()[:18]
        tag_id    = str(tag.get('tag_id',    ''))[:16]
        touch     = str(tag.get('touch',     ''))
        gr_wt     = str(tag.get('gr_wt',     ''))
        nt_wt     = str(tag.get('nt_wt',     ''))
        huid      = str(tag.get('huid',      ''))[:14]
        price     = str(tag.get('price',     ''))
        shop      = str(tag.get('shop_name', 'AURUM JEWELS')).upper()[:20]
        lines = [
            "SIZE 40 mm, 20 mm",
            "GAP 2 mm, 0 mm",
            "DIRECTION 0",
            "REFERENCE 0,0",
            "SET PEEL OFF",
            "SET CUTTER OFF",
            "SET TEAR ON",
            "CLS",
            f'TEXT 10,4,"2",0,1,1,"{shop}"',
            "BAR 0,22,320,1",
            f'TEXT 10,26,"1",0,1,1,"{item_name}"',
            f'TEXT 10,42,"1",0,1,1,"Touch:{touch}%  GW:{gr_wt}g NW:{nt_wt}g"',
        ]
        if huid:
            lines.append(f'TEXT 10,56,"1",0,1,1,"HUID:{huid}"')
        if tag_id:
            lines.append(f'BARCODE 220,26,"39",30,1,0,2,2,"{tag_id}"')
        if price:
            lines.append(f'TEXT 10,70,"2",0,1,1,"Rs.{price}"')
        lines += ["PRINT 1,1", ""]
        return "\r\n".join(lines).encode("ascii", errors="replace")

    def _send_to_tsc(self, tspl: bytes):
        import win32print
        printer_name = self._find_tsc_printer()

        # Open the printer
        hp = win32print.OpenPrinter(printer_name)
        try:
            # Start the document
            job_id = win32print.StartDocPrinter(hp, 1, ('AurumOS Tag', None, 'RAW'))
            win32print.StartPagePrinter(hp)

            # Write the data once
            win32print.WritePrinter(hp, tspl)

            # END the page and document BEFORE closing
            win32print.EndPagePrinter(hp)
            win32print.EndDocPrinter(hp)
            LOG(f'[PRINT] Successfully sent to: {printer_name}')
        except Exception as e:
            ERR(f'[PRINT] Error sending to printer: {e}')
        finally:
            win32print.ClosePrinter(hp)

    # ── SHOP NETWORK ──────────────────────────────────────────────────
    # One method for the whole multi-PC feature, on purpose. main.py is the
    # one file the auto-updater will never ship, so anything named here is
    # frozen into the EXE until every shop is reinstalled by hand. Routing
    # through core/cluster_api.py means new actions arrive with an ordinary
    # release instead.
    def cluster(self, action, payload=None):
        """Every multi-PC call. See core/cluster_api.py for the actions."""
        try:
            from core.cluster_api import dispatch
            return dispatch(self, action, payload or {})
        except Exception as e:
            ERR(f"[CLUSTER] {action} failed: {e}")
            return {"status": "error", "message": str(e)}

    def _software_type(self):
        """Retail or wholesale, decided by the licence prefix."""
        try:
            return 'retail' if str(self._read_license_key() or '').upper().startswith('AR-')                    else 'wholesale'
        except Exception:
            return 'wholesale'

    def _start_cluster(self):
        """Serve the shop network from this PC, unless it is a staff terminal.

        A terminal starts nothing and only ever calls out, so two PCs can never
        both believe they are the shop server.
        """
        try:
            from network import cluster_client, cluster_server
        except Exception as e:
            ERR(f"[CLUSTER] modules unavailable: {e}")
            return
        try:
            if cluster_client.is_terminal():
                where = cluster_client.load_config().get('server_ip', '?')
                # A terminal keeps its own database so a router hiccup does not
                # stop a sale. This is what makes that safe: what it writes goes
                # to the shop, and what the shop knows comes back.
                cluster_client.start_sync(self.db)
                LOG(f"[CLUSTER] Terminal mode -- syncing with shop server {where}")
                return
            started = cluster_server.start_server(self.db, context={
                'business_name': self.get_business_name,
                'version':       lambda: CURRENT_VERSION,
                'plan':          lambda: self.sub.get_state_json().get('effective_plan', 'lite'),
                'features':      lambda: self.sub.get_state_json().get('features', []),
                'software_type': self._software_type,
            })
            if started:
                LOG(f"[CLUSTER] Serving the shop on port {cluster_server.CLUSTER_PORT}")
            else:
                LOG("[CLUSTER] Port busy -- staff PCs cannot connect to this one")
        except Exception as e:
            ERR(f"[CLUSTER] start failed: {e}")

    def get_network_config(self):
        import json as _j
        try:
            base = os.path.dirname(sys.executable if getattr(sys,'frozen',False) else __file__)
            cfg_path = os.path.join(base, 'config.json')
            if os.path.exists(cfg_path):
                return _j.load(open(cfg_path,'r'))
            return {'mode':'local','server_ip':'','server_port':7272}
        except Exception as e:
            return {'mode':'local','server_ip':'','server_port':7272}

    def save_network_config(self, cfg: dict):
        import json as _j
        try:
            base = os.path.dirname(sys.executable if getattr(sys,'frozen',False) else __file__)
            cfg_path = os.path.join(base, 'config.json')
            _j.dump(cfg, open(cfg_path,'w'), indent=2)
            return {'status':'success'}
        except Exception as e:
            return {'status':'error','message':str(e)}

    def get_local_ip(self):
        # One implementation, in network/lan_scan: dial-out fast path with
        # adapter-enumeration fallback, so an offline PC reports its real
        # LAN address instead of 127.0.0.1.
        try:
            from network.lan_scan import local_ip as _lan_ip
            return _lan_ip()
        except Exception:
            pass
        import socket as _s
        try:
            sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            sock.connect(('8.8.8.8', 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            return '127.0.0.1'

    def fetch_live_network_nodes(self):
        """Shop servers answering on this LAN right now (setup satellite mode).

        Returns {status:'success', nodes:[{id, business, version, status}]}
        as setup.html:639 reads it — the first 'pending' node pre-fills the
        server IP field. Scan runs off-thread-safe with a hard timeout so a
        big or firewalled network cannot hang the setup screen.
        """
        try:
            import threading
            from network import lan_scan
            try:
                port = 58903
                try:
                    cfg = self.get_network_config()
                    if isinstance(cfg, dict) and cfg.get('server_port'):
                        port = int(cfg.get('server_port'))
                except Exception:
                    pass
            except Exception:
                port = 58903
            result = {}
            def _run():
                try:
                    result.update(lan_scan.find_servers(port=port) or {})
                except Exception as e:
                    result.update({'status': 'error', 'message': str(e)})
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=25)
            if not result:
                return {"status": "error", "message": "Scan timed out.", "nodes": []}
            if result.get('status') != 'success':
                return {"status": "error",
                        "message": result.get('message') or 'Scan failed.',
                        "nodes": []}
            nodes = [{"id": s.get('ip'), "business": s.get('business', ''),
                      "version": s.get('version', ''), "status": "pending"}
                     for s in (result.get('servers') or []) if s.get('ip')]
            return {"status": "success", "nodes": nodes,
                    "network": result.get('network', ''),
                    "this_pc": result.get('this_pc', '')}
        except Exception as e:
            return {"status": "error", "message": str(e), "nodes": []}

    def get_printer_status(self):
        try:
            name = self._find_tsc_printer()
            return {'ready': bool(name), 'printer_name': name or 'Not found'}
        except Exception as e:
            return {'ready': False, 'printer_name': 'Error', 'error': str(e)}

    def _find_tsc_printer(self):
        """Auto-discovers any installed printer that matches our keywords."""
        try:
            import win32print
            # Flag both local and network-connected printers
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers = win32print.EnumPrinters(flags, None, 1)

            # Look for keywords in any installed printer name
            keywords = ['TSC', 'TA210', 'JEWEL', 'TTP', 'LABEL']
            for p in printers:
                name = p[2]
                if any(k in name.upper() for k in keywords):
                    LOG(f"[PRINT] Auto-discovered printer: {name}")
                    return name

            # Fallback to system default if no keyword match
            default = win32print.GetDefaultPrinter()
            LOG(f"[PRINT] No match found, using system default: {default}")
            return default
        except Exception as e:
            ERR(f"[PRINT] Auto-discovery error: {e}")
            return ''

    def get_db_path(self):
        """Return current DB path."""
        try:
            return str(self.db.db_path) if hasattr(self.db, 'db_path') else ''
        except Exception:
            return ''

    def change_db_location(self):
        """Show folder picker and move/repoint DB to new location."""
        import shutil as _sh
        try:
            chosen = None
            try:
                # In-process dialog first: no child process, no console flash.
                native = _native_pick_folder("Select folder for AurumOS database")
                if native is not None:
                    chosen = native
            except Exception as _ne:
                LOG(f"[DB] native picker failed ({_ne}); using fallback")
            if chosen is None:
                r = _powershell_hidden(
                    ['powershell', '-Command',
                     '[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")|Out-Null;'
                     '$f=New-Object System.Windows.Forms.FolderBrowserDialog;'
                     '$f.Description="Select folder for AurumOS database";'
                     '$f.ShowNewFolderButton=$true;'
                     'if($f.ShowDialog() -eq "OK"){$f.SelectedPath}else{""}'],
                    timeout=60
                )
                chosen = r.stdout.strip()
            if not chosen or not os.path.isdir(chosen):
                return {'status': 'cancelled'}

            new_db = os.path.join(chosen, 'aurum_local.db')
            cur_db = self.get_db_path()

            # Copy existing DB to new location if it exists
            if cur_db and os.path.exists(cur_db) and cur_db != new_db:
                _sh.copy2(cur_db, new_db)
                LOG(f"[DB] Copied DB to {new_db}")

            # Save new preference
            if getattr(sys, 'frozen', False):
                _exe_dir = os.path.dirname(sys.executable)
            else:
                _exe_dir = os.path.abspath('.')
            pref = os.path.join(_exe_dir, 'db_path.txt')
            open(pref, 'w', encoding='utf-8').write(new_db)

            LOG(f"[DB] DB location changed to {new_db}")
            return {
                'status':  'success',
                'new_path': new_db,
                'message': f'Database moved to {chosen}. Restart AurumOS to apply.'
            }
        except Exception as e:
            ERR(f"[DB] change_db_location: {e}")
            return {'status': 'error', 'message': str(e)}

    def reset_db_location(self):
        """Reset to default DB location (next to EXE)."""
        try:
            if getattr(sys, 'frozen', False):
                _exe_dir = os.path.dirname(sys.executable)
            else:
                _exe_dir = os.path.abspath('.')
            pref = os.path.join(_exe_dir, 'db_path.txt')
            if os.path.exists(pref):
                os.remove(pref)
            default = os.path.join(_exe_dir, 'database', 'aurum_local.db')
            return {'status': 'success', 'path': default,
                    'message': 'Reset to default. Restart AurumOS to apply.'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    # ── FULL BACKUP (export everything / restore from zip) ──────────
    # Export: consistent DB snapshot (sqlite backup API, so WAL content is
    # included) + config + subscription state + item photos + a manifest,
    # zipped to exports/ with live progress. Restore: validates the zip
    # (manifest, DB integrity, expected tables), safety-copies the current
    # DB to database/backups/, swaps the files, and asks for a restart.
    # Progress for both runs through _backup_state / _restore_state so the
    # page can draw a bar while the work happens on a thread.
    _backup_state = {"pct": 0, "msg": "", "done": False, "ok": False, "file": ""}
    _restore_state = {"pct": 0, "msg": "", "done": False, "ok": False,
                      "needs_restart": False, "backup_of_old": ""}

    def _backup_set(self, pct, msg, done=False, ok=False, file=""):
        try:
            st = dict(getattr(self, '_backup_state', {}) or {})
            st.update({"pct": int(pct), "msg": str(msg or ""),
                       "done": bool(done), "ok": bool(ok)})
            if file:
                st["file"] = str(file)
            self._backup_state = st
        except Exception:
            pass

    def _restore_set(self, pct, msg, done=False, ok=False,
                     needs_restart=False, backup_of_old=""):
        try:
            st = dict(getattr(self, '_restore_state', {}) or {})
            st.update({"pct": int(pct), "msg": str(msg or ""),
                       "done": bool(done), "ok": bool(ok),
                       "needs_restart": bool(needs_restart)})
            if backup_of_old:
                st["backup_of_old"] = str(backup_of_old)
            self._restore_state = st
        except Exception:
            pass

    def get_backup_progress(self):
        try:
            return dict(getattr(self, '_backup_state', {}) or
                        {"pct": 0, "msg": "", "done": False})
        except Exception as e:
            return {"pct": 0, "msg": str(e), "done": True, "ok": False}

    def get_restore_progress(self):
        try:
            return dict(getattr(self, '_restore_state', {}) or
                        {"pct": 0, "msg": "", "done": False})
        except Exception as e:
            return {"pct": 0, "msg": str(e), "done": True, "ok": False}

    def _app_base_dir(self):
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.abspath('.')

    def export_backup(self):
        """Start a full export on a thread. Poll get_backup_progress."""
        try:
            if getattr(self, '_backup_running', False):
                return {"status": "busy"}
            self._backup_running = True
            self._backup_set(1, "Starting export…")
            threading.Thread(target=self._export_worker, daemon=True,
                             name="AurumBackup").start()
            return {"status": "started"}
        except Exception as e:
            try:
                self._backup_running = False
            except Exception:
                pass
            return {"status": "error", "message": str(e)}

    def _export_worker(self):
        import sqlite3 as _sq
        import zipfile as _zf
        from datetime import datetime as _dt
        try:
            base = self._app_base_dir()
            stamp = _dt.now().strftime('%Y%m%d-%H%M%S')
            out_dir = os.path.join(base, 'exports')
            os.makedirs(out_dir, exist_ok=True)
            zpath = os.path.join(out_dir, f'AurumOS-backup-{stamp}.zip')
            self._backup_set(5, "Snapshotting database…")

            # Consistent snapshot even mid-write (WAL included).
            snap = os.path.join(out_dir, f'.backup-snap-{stamp}.db')
            try:
                src = _sq.connect(self.db.db_path, timeout=30)
                dst = _sq.connect(snap, timeout=30)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
            except Exception as _se:
                self._backup_set(100, f"Snapshot failed: {_se}", True, False)
                try:
                    self._backup_running = False
                except Exception:
                    pass
                return

            # Collect: snapshot DB, config, subscription state, photos.
            files = []
            files.append((snap, 'database/aurum_local.db'))
            cfg = os.path.join(base, 'config.json')
            if os.path.isfile(cfg):
                files.append((cfg, 'config.json'))
            db_dir = os.path.join(base, 'database')
            for _n in ('.subscription_cache', '.subscription_state.json',
                       '.sse_cache'):
                _p = os.path.join(db_dir, _n)
                if os.path.isfile(_p):
                    files.append((_p, 'database/' + _n))
            try:
                photo_dir = self._photo_dir()
            except Exception:
                photo_dir = ''
            if photo_dir and os.path.isdir(photo_dir):
                for _dp, _dns, _fns in os.walk(photo_dir):
                    for _f in _fns:
                        _full = os.path.join(_dp, _f)
                        _rel = os.path.relpath(_full, base).replace(os.sep, '/')
                        files.append((_full, _rel))

            manifest = {
                'app': 'AurumOS', 'kind': 'full-backup', 'version': 1,
                'created': _dt.now().isoformat(timespec='seconds'),
                'aurum_version': str(CURRENT_VERSION),
                'files': [a for _, a in files],
            }
            total = len(files) + 1
            self._backup_set(10, f"Zipping {len(files)} files…")
            try:
                with _zf.ZipFile(zpath, 'w', _zf.ZIP_DEFLATED) as _z:
                    _z.writestr('backup-manifest.json',
                                json.dumps(manifest, indent=2))
                    self._backup_set(12, "Manifest written…")
                    for _i, (_full, _arc) in enumerate(files):
                        try:
                            _z.write(_full, _arc)
                        except Exception as _we:
                            LOG(f"[BACKUP] skip {_arc}: {_we}")
                        self._backup_set(
                            12 + int(85 * (_i + 1) / max(total, 1)),
                            f"Adding {_arc} ({_i + 1}/{len(files)})…")
            finally:
                try:
                    os.remove(snap)
                except Exception:
                    pass
            size_mb = round(os.path.getsize(zpath) / 1048576, 2)
            try:
                self._audit('Backup exported', os.path.basename(zpath), 'backup')
            except Exception:
                pass
            LOG(f"[BACKUP] exported {zpath} ({size_mb} MB)")
            self._backup_set(100, f"Done — {size_mb} MB", True, True, zpath)
        except Exception as e:
            ERR(f"[BACKUP] export failed: {e}")
            self._backup_set(100, f"Export failed: {e}", True, False)
        finally:
            try:
                self._backup_running = False
            except Exception:
                pass

    def reveal_backup(self, path=""):
        """Open the folder containing the backup file."""
        try:
            p = str(path or (getattr(self, '_backup_state', {}) or {}).get('file') or '')
            folder = os.path.dirname(p) if os.path.isfile(p) else self._app_base_dir()
            os.startfile(folder)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def export_shop_data(self):
        """Quick JSON export of core shop data for subscription screens."""
        import sqlite3 as _sq
        from datetime import datetime as _dt
        try:
            base = self._app_base_dir()
            stamp = _dt.now().strftime('%Y%m%d-%H%M%S')
            out_dir = os.path.join(base, 'exports')
            os.makedirs(out_dir, exist_ok=True)
            db_path = self.db.db_path
            conn = _sq.connect(db_path, timeout=30)
            conn.row_factory = _sq.Row
            cur = conn.cursor()
            tables = ['products', 'customers', 'transactions',
                      'inventory', 'karigar', 'ledger']
            data = {}
            for t in tables:
                try:
                    cur.execute(f'SELECT * FROM {t}')
                    rows = [dict(r) for r in cur.fetchall()]
                    data[t] = rows
                except Exception:
                    pass
            conn.close()
            data['_meta'] = {
                'app': 'AurumOS', 'kind': 'shop-data-export',
                'created': _dt.now().isoformat(timespec='seconds'),
                'tables': list(data.keys()),
            }
            out_path = os.path.join(out_dir, f'AurumOS-shopdata-{stamp}.json')
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, default=str, indent=2)
            LOG(f"[EXPORT] shop data exported to {out_path}")
            try:
                os.startfile(out_dir)
            except Exception:
                pass
            return True
        except Exception as e:
            ERR(f"[EXPORT] shop data export failed: {e}")
            return False

    def pick_backup_file(self):
        """Windows file picker limited to backup zips.

        In-process WinForms dialog first: no child process exists, so no
        console window can ever flash. Hidden PowerShell is only a fallback
        for machines where pythonnet/CLR dialogs are unavailable.
        """
        try:
            native = _native_pick_file('AurumOS backup (*.zip)|*.zip',
                                       'Select AurumOS backup to restore')
            if native is not None:
                if native and os.path.isfile(native):
                    return {"status": "success", "path": native}
                return {"status": "cancelled"}
        except Exception as _ne:
            LOG(f"[BACKUP] native picker failed ({_ne}); using fallback")
        try:
            r = _powershell_hidden(
                ['powershell', '-Command',
                 '[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")|Out-Null;'
                 '$f=New-Object System.Windows.Forms.OpenFileDialog;'
                 '$f.Filter="AurumOS backup (*.zip)|*.zip";'
                 '$f.Title="Select AurumOS backup to restore";'
                 'if($f.ShowDialog() -eq "OK"){$f.FileName}else{""}'],
                timeout=120)
            chosen = (r.stdout or '').strip()
            if not chosen or not os.path.isfile(chosen):
                return {"status": "cancelled"}
            return {"status": "success", "path": chosen}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def import_backup(self, zip_path):
        """Validate + restore a backup zip on a thread. Poll get_restore_progress."""
        try:
            if getattr(self, '_restore_running', False):
                return {"status": "busy"}
            zpath = str(zip_path or '').strip()
            if not zpath or not os.path.isfile(zpath):
                return {"status": "error", "message": "Backup file not found."}
            self._restore_running = True
            self._restore_set(1, "Starting restore…")
            threading.Thread(target=self._restore_worker, args=(zpath,),
                             daemon=True, name="AurumRestore").start()
            return {"status": "started"}
        except Exception as e:
            try:
                self._restore_running = False
            except Exception:
                pass
            return {"status": "error", "message": str(e)}

    def _restore_worker(self, zpath):
        import sqlite3 as _sq
        import zipfile as _zf
        from datetime import datetime as _dt
        try:
            base = self._app_base_dir()
            self._restore_set(5, "Reading backup…")
            try:
                _z = _zf.ZipFile(zpath, 'r')
                names = _z.namelist()
            except Exception as _ze:
                self._restore_set(100, f"Not a valid zip: {_ze}", True, False)
                try:
                    self._restore_running = False
                except Exception:
                    pass
                return
            # 1) Manifest must declare a full backup.
            try:
                manifest = json.loads(_z.read('backup-manifest.json').decode('utf-8'))
            except Exception:
                manifest = {}
            if not isinstance(manifest, dict) or manifest.get('kind') != 'full-backup' \
                    or 'database/aurum_local.db' not in (names or []):
                try:
                    _z.close()
                except Exception:
                    pass
                self._restore_set(100, "This zip is not an AurumOS full backup.", True, False)
                try:
                    self._restore_running = False
                except Exception:
                    pass
                return
            # 2) Extract DB to temp and verify it opens + looks like ours.
            self._restore_set(15, "Verifying backup database…")
            tmpdir = os.path.join(base, 'exports', '.restore-tmp')
            os.makedirs(tmpdir, exist_ok=True)
            tmpdb = os.path.join(tmpdir, 'check.db')
            try:
                if os.path.exists(tmpdb):
                    os.remove(tmpdb)
                with open(tmpdb, 'wb') as _f:
                    _f.write(_z.read('database/aurum_local.db'))
            except Exception as _ee:
                try:
                    _z.close()
                except Exception:
                    pass
                self._restore_set(100, f"Cannot read database from zip: {_ee}", True, False)
                try:
                    self._restore_running = False
                except Exception:
                    pass
                return
            try:
                _c = _sq.connect(tmpdb, timeout=10)
                try:
                    integ = _c.execute("PRAGMA integrity_check").fetchone()
                    tables = {r[0] for r in _c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                finally:
                    _c.close()
                if not integ or str(integ[0]).lower() != 'ok':
                    raise ValueError("integrity check failed")
                for _need in ('stock_inventory', 'sales_history', 'app_config',
                              'admin_creds', 'clients_master'):
                    if _need not in tables:
                        raise ValueError(f"missing table {_need}")
            except Exception as _ve:
                try:
                    _z.close()
                except Exception:
                    pass
                try:
                    os.remove(tmpdb)
                except Exception:
                    pass
                self._restore_set(100, f"Backup database is corrupt: {_ve}", True, False)
                try:
                    self._restore_running = False
                except Exception:
                    pass
                return
            # 3) Safety-copy the live database first.
            self._restore_set(35, "Safety copy of current data…")
            live_db = self.db.db_path
            bdir = os.path.join(base, 'database', 'backups')
            os.makedirs(bdir, exist_ok=True)
            stamp = _dt.now().strftime('%Y%m%d-%H%M%S')
            safety = os.path.join(bdir, f'pre-restore-{stamp}.db')
            try:
                _src = _sq.connect(live_db, timeout=30)
                _dst = _sq.connect(safety, timeout=30)
                try:
                    _src.backup(_dst)
                finally:
                    _dst.close()
                    _src.close()
            except Exception as _se:
                try:
                    _z.close()
                except Exception:
                    pass
                self._restore_set(100, f"Could not safeguard current data: {_se}", True, False)
                try:
                    self._restore_running = False
                except Exception:
                    pass
                return
            # 4) Swap files in (DB + companions), with progress per file.
            self._restore_set(50, "Extracting backup…")
            targets = [n for n in names
                       if n == 'database/aurum_local.db'
                       or n == 'config.json'
                       or n.startswith('database/.subscription')
                       or n.startswith('database/.sse_cache')
                       or n.startswith('photos/')
                       or '/photos/' in n]
            # Photos may live under any folder containing a photos dir;
            # accept them by name while refusing anything executable.
            safe = []
            for n in targets:
                _low = n.lower()
                if _low.endswith(('.exe', '.dll', '.bat', '.cmd', '.ps1',
                                   '.py', '.pyc')):
                    continue
                if '..' in n or n.startswith('/'):
                    continue
                safe.append(n)
            # DB file itself goes through sqlite (WAL-safe), never raw copy.
            rest = [n for n in safe if n != 'database/aurum_local.db']
            total = max(len(rest), 1)
            for _i, _arc in enumerate(rest):
                try:
                    _dst_path = os.path.join(base, _arc.replace('/', os.sep))
                    os.makedirs(os.path.dirname(_dst_path), exist_ok=True)
                    with open(_dst_path, 'wb') as _f:
                        _f.write(_z.read(_arc))
                except Exception as _we:
                    LOG(f"[RESTORE] skip {_arc}: {_we}")
                self._restore_set(50 + int(35 * (_i + 1) / total),
                                  f"Extracting {_arc} ({_i + 1}/{len(rest)})…")
            try:
                _z.close()
            except Exception:
                pass
            try:
                os.remove(tmpdb)
            except Exception:
                pass
            # 5) Load into the LIVE file via the sqlite backup API: never by
            # replacing the file itself, which Windows refuses while the
            # running app holds the database open. backup() streams the
            # verified content into place, WAL-safe, past any open reader.
            self._restore_set(88, "Loading data…")
            try:
                # Re-read the verified DB bytes from the zip into place.
                _z2 = _zf.ZipFile(zpath, 'r')
                try:
                    _blob = _z2.read('database/aurum_local.db')
                finally:
                    _z2.close()
                _tmp2 = os.path.join(tmpdir, 'load.db')
                with open(_tmp2, 'wb') as _f:
                    _f.write(_blob)
                _s2 = _sq.connect(_tmp2, timeout=30)
                _d2 = _sq.connect(live_db, timeout=30)
                try:
                    try:
                        _d2.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except Exception:
                        pass
                    _s2.backup(_d2)
                finally:
                    try:
                        _s2.close()
                    except Exception:
                        pass
                    try:
                        _d2.close()
                    except Exception:
                        pass
                try:
                    os.remove(_tmp2)
                except Exception:
                    pass
            except Exception as _le:
                self._restore_set(100, f"Could not load data: {_le}. "
                                  f"Your current data is safe at {safety}.",
                                  True, False)
                try:
                    self._restore_running = False
                except Exception:
                    pass
                return
            try:
                self._audit('Backup restored', os.path.basename(zpath), 'backup')
            except Exception:
                pass
            LOG(f"[RESTORE] done from {zpath}; safety copy at {safety}")
            self._restore_set(100, "Done — restart AurumOS to use the restored data.",
                              True, True, True, safety)
        except Exception as e:
            ERR(f"[RESTORE] failed: {e}")
            self._restore_set(100, f"Restore failed: {e}", True, False)
        finally:
            try:
                self._restore_running = False
            except Exception:
                pass

    # ── TAG AUDIT API ────────────────────────────────────────────────────
    def tag_audit_start(self, started_by='Admin', touch_filter='ALL'):
        try:    return self.db.tag_audit_start(started_by, touch_filter)
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_scan(self, session_id, tag_id, book_tags):
        try:    return self.db.tag_audit_scan(int(session_id), tag_id, book_tags)
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_get_status(self, session_id):
        try:    return self.db.tag_audit_get_status(int(session_id))
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_mark_absent(self, session_id, tag_id, reason, marked_by='Admin'):
        try:    return self.db.tag_audit_mark_absent(int(session_id), tag_id, reason, marked_by)
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_remove_absence(self, session_id, tag_id):
        try:    return self.db.tag_audit_remove_absence(int(session_id), tag_id)
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_remove_scan(self, session_id, tag_id):
        try:    return self.db.tag_audit_remove_scan(int(session_id), tag_id)
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_close(self, session_id, closed_by='Admin'):
        try:    return self.db.tag_audit_close(int(session_id), closed_by)
        except Exception as e: return {'status':'error','message':str(e)}

    def tag_audit_start_snapshot(self, session_id, touch_filter='ALL'):
        try:    return self.db.tag_audit_start_snapshot(int(session_id), touch_filter)
        except Exception as e: return {'status':'error','message':str(e),'snapshot':[]}

    def tag_audit_get_sessions(self, limit=20):
        try:    return self.db.tag_audit_get_sessions(int(limit))
        except Exception as e: return {'status':'error','message':str(e),'sessions':[]}

    def tag_audit_get_available_touches(self):
        try:    return self.db.tag_audit_get_available_touches()
        except Exception as e: return {'status':'error','message':str(e),'touches':[]}

    def tag_audit_delete_session(self, session_id):
        try:    return self.db.tag_audit_delete_session(int(session_id))
        except Exception as e: return {'status':'error','message':str(e)}

    # ── YEAR-END BALANCE TRANSFER API ──────────────────────────────
    def get_year_list(self):
        try:    return self.db.get_year_list()
        except Exception as e: return {'status':'error','message':str(e)}

    def get_year_close_preview(self):
        try:    return self.db.get_year_close_preview()
        except Exception as e: return {'status':'error','message':str(e)}

    def do_year_close(self, new_year, closing_note=''):
        try:    return self.db.do_year_close(str(new_year).strip(), str(closing_note).strip())
        except Exception as e: return {'status':'error','message':str(e)}

    def get_archive_data(self, year, table, limit=500):
        try:    return self.db.get_archive_data(str(year), str(table), int(limit))
        except Exception as e: return {'status':'error','message':str(e)}

    def get_archive_summary(self, year):
        try:    return self.db.get_archive_summary(str(year))
        except Exception as e: return {'status':'error','message':str(e)}

    def get_active_year(self):
        """Return currently active financial year and DB path."""
        try:
            yr = None
            try:
                with self.db._get_connection() as conn:
                    row = conn.execute(
                        "SELECT value FROM app_config WHERE key='financial_year'"
                    ).fetchone()
                    if row and row['value']:
                        yr = row['value']
            except Exception:
                pass
            if not yr:
                yr = self.db._guess_financial_year() if hasattr(self.db, '_guess_financial_year') else ''
            return {
                'status':     'success',
                'year':       yr,
                'db_path':    self.db.db_path,
                'is_archive': False,
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def switch_year(self, year):
        """
        Switch active DB to a different year.
        'current' = live DB, anything else = archived DB.
        DBManager takes no args — uses AURUM_DB_PATH env var.
        """
        import os as _os
        try:
            if getattr(sys, 'frozen', False):
                _exe_dir = _os.path.dirname(sys.executable)
            else:
                _exe_dir = _os.path.abspath('.')

            if year == 'current':
                # Reset to live DB path
                pref_file = _os.path.join(_exe_dir, 'db_path.txt')
                if _os.path.exists(pref_file):
                    live_path = open(pref_file, 'r').read().strip()
                else:
                    live_path = _os.path.join(_exe_dir, 'database', 'aurum_local.db')
                target_path = live_path
            else:
                # Resolve archive path
                pref_file = _os.path.join(_exe_dir, 'db_path.txt')
                if _os.path.exists(pref_file):
                    live_path = open(pref_file, 'r').read().strip()
                    base_dir  = _os.path.dirname(live_path)
                else:
                    base_dir = _os.path.join(_exe_dir, 'database')
                target_path = _os.path.join(base_dir, 'archives', year, 'aurum_local.db')
                if not _os.path.exists(target_path):
                    return {'status': 'error', 'message': f'Archive for {year} not found at {target_path}'}

            # Switch DB — set env var BEFORE instantiating DBManager
            # DBManager.__init__ reads AURUM_DB_PATH on startup
            _os.environ['AURUM_DB_PATH'] = target_path
            from database.db_manager import DBManager
            self.db = DBManager()

            # Get active year label
            try:
                with self.db._get_connection() as _c:
                    _r = _c.execute("SELECT value FROM app_config WHERE key='financial_year'").fetchone()
                    active_year = _r['value'] if _r else (year if year != 'current' else 'current')
            except Exception:
                active_year = year if year != 'current' else 'current'
            # Regenerate session token on new DBManager instance
            try:
                self.db._generate_session_token()
                LOG(f"[SESSION] Token regenerated after year switch")
            except Exception as _te:
                ERR(f"[SESSION] Token regen failed: {_te}")

            LOG(f"[YEAR] Switched to {year}: {target_path}")
            return {
                'status':     'success',
                'year':       active_year,
                'is_archive': year != 'current',
                'landing':    'dashboard.html',
            }
        except Exception as e:
            ERR(f"[YEAR] switch_year: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_log_path(self):
        """Return the log file path so JS can display it."""
        base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath('.')
        return os.path.join(base, 'logs', 'aurumos.log')

    def get_recent_logs(self, lines=50):
        """Return last N lines of the log file for in-app display."""
        try:
            log_path = self.get_log_path()
            if not os.path.exists(log_path):
                return []
            with open(log_path, encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
            return [l.rstrip() for l in all_lines[-lines:]]
        except Exception as e:
            return [f"Error reading log: {e}"]

    def log_input_diag(self, source, msg):
        """Input-health self-report from a page, written to the app log.

        The page sends state and event COUNTS only (first focus, keydown /
        input ordinals, value lengths) — never values, never key names — so
        no credential can land in the log through this method. The bridge
        appends which OS window holds the foreground at that moment: if the
        foreground is not AurumOS while the user types, the keystrokes were
        never ours to receive, and no page code could ever fix that.
        """
        try:
            extra = ''
            try:
                import ctypes
                from ctypes import wintypes
                u32 = ctypes.windll.user32
                u32.GetForegroundWindow.restype = wintypes.HWND
                u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
                u32.GetWindowTextLengthW.restype = ctypes.c_int
                u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                               ctypes.c_int]
                u32.GetWindowTextW.restype = ctypes.c_int
                fg = u32.GetForegroundWindow()
                n = u32.GetWindowTextLengthW(fg)
                buf = ctypes.create_unicode_buffer(max(n + 1, 1))
                if n > 0:
                    u32.GetWindowTextW(fg, buf, n + 1)
                extra = ' fg=' + (buf.value or '-')
            except Exception:
                pass
            LOG(f"[INPUT-DIAG][{source}] {msg}{extra}")
            return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def ensure_web_focus(self):
        """Re-aim keyboard focus at the WebView2 child (same process).

        Called whenever an app window regains OS activation. If the host
        form (or anything else in-process) ended up holding keyboard focus
        instead of the web child, every keystroke dies while the page looks
        ready — blinking caret, dead keys — on every input of that page.
        Same-process windows only, no activation/z-order changes, best
        effort: a no-op when focus is already correct.
        """
        try:
            placed = _push_focus_to_webview()
            return {'status': 'success', 'focused': bool(placed)}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}


def _is_dev_mode():
    """True when this copy is a developer run, not the shop's live copy.

    PERMANENT RULE: only the frozen EXE is ever the live copy. Anything
    run from source (`python main.py`, with or without flags) is dev, so
    plain `python main.py` can never collide with the installed app — no
    flag to remember. `--dev` / `AURUM_DEV=1` are kept as explicit
    overrides. Two dev copies still block each other, same as two live
    copies.
    """
    try:
        if not getattr(sys, 'frozen', False):
            return True
    except Exception:
        pass
    try:
        if '--dev' in (sys.argv or []):
            return True
    except Exception:
        pass
    try:
        if str(os.environ.get('AURUM_DEV', '')).strip().lower() in (
                '1', 'true', 'yes', 'dev'):
            return True
    except Exception:
        pass
    return False


def _focus_existing_window():
    """Bring the already-running copy to the front; True if it worked.

    A duplicate launch used to pop a modal "already running" dialog. Those
    dialogs linger behind the main window holding OS keyboard focus while
    the login screen *looks* focused (blinking caret, dead keys) — the
    classic "can't type anything" trap. Focusing the live window instead
    removes the whole class: no stray dialog ever traps the keyboard.

    Matched by title *prefix* ("AurumOS Executive Dashboard…") via window
    enumeration, so dev-suffixed titles ("· DEV") and any document-title
    retitling still resolve. Our own (not yet created) windows cannot match
    because this process owns none at guard time.
    """
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        try:
            u32.IsWindowVisible.argtypes = [wintypes.HWND]
            u32.IsWindowVisible.restype = wintypes.BOOL
            u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            u32.GetWindowTextLengthW.restype = ctypes.c_int
            u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                           ctypes.c_int]
            u32.GetWindowTextW.restype = ctypes.c_int
        except Exception:
            pass
        found = []
        _ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)

        def _enum_cb(hwnd, _lp):
            try:
                if not u32.IsWindowVisible(hwnd):
                    return True
                n = u32.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                if (buf.value or '').startswith('AurumOS Executive Dashboard'):
                    found.append(hwnd)
            except Exception:
                pass
            return True

        _enum_cb_c = _ENUM_CB(_enum_cb)
        # Hold the callback for the duration of the enumeration.
        _focus_existing_window._cb = _enum_cb_c
        try:
            u32.EnumWindows.argtypes = [_ENUM_CB, wintypes.LPARAM]
            u32.EnumWindows.restype = wintypes.BOOL
        except Exception:
            pass
        try:
            u32.EnumWindows(_enum_cb_c, 0)
        except Exception:
            return False
        finally:
            try:
                _focus_existing_window._cb = None
            except Exception:
                pass
        if not found:
            return False
        hwnd = found[0]
        try:
            u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE: un-minimize if minimized
        except Exception:
            pass
        try:
            u32.SetForegroundWindow.argtypes = [wintypes.HWND]
            u32.SetForegroundWindow.restype = wintypes.BOOL
            u32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _ensure_single_instance():
    """Only one copy of the app per PC (per channel: live vs dev).

    A second launch — the classic way a stale window survives next to a
    new build, each believing it is the shop server — gets a message, not
    a window. Named mutexes die with the process, so a crash can never
    wedge this shut.
    """
    if os.name != 'nt':
        return True
    dev = _is_dev_mode()
    mutex_name = ("AurumOS_SingleInstance_Dev" if dev
                  else "AurumOS_SingleInstance")
    try:
        import ctypes
        from ctypes import wintypes
        _k = ctypes.windll.kernel32
        _k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                    wintypes.LPCWSTR]
        _k.CreateMutexW.restype = wintypes.HANDLE
        h = _k.CreateMutexW(None, False, mutex_name)
        if not h:
            return True  # could not create: never block startup on it
        if _k.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # Usual case: focus the running copy and leave quietly. The old
            # modal popup stayed on screen trapping keyboard focus, which
            # read as "can't type in the login box". Only fall back to the
            # popup when the live window cannot be found at all.
            try:
                if _focus_existing_window():
                    return False
            except Exception:
                pass
            try:
                msg = ("A dev copy is already running.\n\nClose it first, "
                       "then start dev again."
                       if dev else
                       "AurumOS is already running.\n\nClose the open window "
                       "first, then open it again.")
                ctypes.windll.user32.MessageBoxW(None, msg, "AurumOS", 0x40)
            except Exception:
                pass
            return False
        _ensure_single_instance._h = h  # hold it for the life of the process
        return True
    except Exception:
        return True


def _push_focus_to_webview(*_a, **_k):
    """End every finished page load with keyboard focus inside web content.

    If the host form (or anything else in our process) ever ends up holding
    keyboard focus instead of the WebView2 child, every keystroke dies while
    the page looks ready (caret blinking, dead keys) — on EVERY input of
    that page, which reads exactly like "can't type anything here". There is
    no page code that can fix that from the inside, so the host re-asserts
    it on each load: at worst one page view, never a stuck box.

    Same-process windows only (our PID), no activation or z-order changes —
    this never yanks a background/minimized window forward, it only aims
    focus at the web child for when the window is (or returns) active.
    Best-effort: returns True when focus was placed or already correct.
    """
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        try:
            u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                     ctypes.POINTER(wintypes.DWORD)]
            u32.GetWindowThreadProcessId.restype = wintypes.DWORD
            u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            u32.GetWindowTextLengthW.restype = ctypes.c_int
            u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                           ctypes.c_int]
            u32.GetWindowTextW.restype = ctypes.c_int
            u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                           ctypes.c_int]
            u32.GetClassNameW.restype = ctypes.c_int
            u32.IsWindow.argtypes = [wintypes.HWND]
            u32.IsWindow.restype = wintypes.BOOL
            u32.IsWindowVisible.argtypes = [wintypes.HWND]
            u32.IsWindowVisible.restype = wintypes.BOOL
            u32.IsWindowEnabled.argtypes = [wintypes.HWND]
            u32.IsWindowEnabled.restype = wintypes.BOOL
            u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                              wintypes.BOOL]
            u32.AttachThreadInput.restype = wintypes.BOOL
            try:
                u32.PeekMessageW.argtypes = [wintypes.LPVOID, wintypes.HWND,
                                             ctypes.c_uint, ctypes.c_uint,
                                             ctypes.c_uint]
                u32.PeekMessageW.restype = wintypes.BOOL
            except Exception:
                pass
        except Exception:
            pass
        me = os.getpid()
        hosts = []

        _ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _collect(hwnd, _lp):
            try:
                pid = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) != me:
                    return True
                n = u32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(max(n + 1, 1))
                if n > 0:
                    u32.GetWindowTextW(hwnd, buf, n + 1)
                if (buf.value or '').startswith('AurumOS Executive Dashboard'):
                    hosts.append(hwnd)
            except Exception:
                pass
            return True

        _collect_c = _ENUM(_collect)
        _push_focus_to_webview._cb = _collect_c
        try:
            u32.EnumWindows.argtypes = [_ENUM, wintypes.LPARAM]
            u32.EnumWindows.restype = wintypes.BOOL
            u32.EnumWindows(_collect_c, 0)
        except Exception:
            return False
        finally:
            try:
                _push_focus_to_webview._cb = None
            except Exception:
                pass
        if not hosts:
            return False

        _CHILD = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        children = []

        def _collect_child(hwnd, _lp):
            try:
                buf = ctypes.create_unicode_buffer(256)
                u32.GetClassNameW(hwnd, buf, 256)
                if (buf.value or '') == 'Chrome_WidgetWin_1':
                    children.append(hwnd)
            except Exception:
                pass
            return True

        _collect_child_c = _CHILD(_collect_child)
        _push_focus_to_webview._cb2 = _collect_child_c
        try:
            u32.EnumChildWindows.argtypes = [wintypes.HWND, _CHILD, wintypes.LPARAM]
            u32.EnumChildWindows.restype = wintypes.BOOL
            u32.SetFocus.argtypes = [wintypes.HWND]
            u32.SetFocus.restype = wintypes.HWND
            for h in hosts:
                try:
                    u32.EnumChildWindows(h, _collect_child_c, 0)
                except Exception:
                    continue
        except Exception:
            return False
        finally:
            try:
                _push_focus_to_webview._cb2 = None
            except Exception:
                pass
        placed = False
        try:
            u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                     ctypes.POINTER(wintypes.DWORD)]
            u32.GetWindowThreadProcessId.restype = wintypes.DWORD
            u32.GetFocus.restype = wintypes.HWND
            _k32 = ctypes.windll.kernel32
            _k32.GetCurrentThreadId.argtypes = []
            _k32.GetCurrentThreadId.restype = wintypes.DWORD
            u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                              wintypes.BOOL]
            u32.AttachThreadInput.restype = wintypes.BOOL
        except Exception:
            _k32 = None
        for ch in children:
            # Bridge calls typically run on a worker thread, but SetFocus
            # only lands from the target window's own input queue. Attach
            # first or the call silently no-ops — the exact false
            # confidence this fix exists to remove. Verified via GetFocus.
            attached = False
            diag = ''
            got = 0
            # One shot is not enough: right after load/activation the
            # window may not be ready for focus yet. Retry briefly.
            for _try in range(3):
                try:
                    try:
                        target_tid = int(u32.GetWindowThreadProcessId(ch, None))
                    except Exception:
                        target_tid = 0
                    try:
                        me_tid = int(_k32.GetCurrentThreadId()) if _k32 is not None else 0
                    except Exception:
                        me_tid = 0
                    try:
                        valid = bool(u32.IsWindow(ch))
                    except Exception:
                        valid = '?'
                    try:
                        vis = bool(u32.IsWindowVisible(ch))
                    except Exception:
                        vis = '?'
                    try:
                        enb = bool(u32.IsWindowEnabled(ch))
                    except Exception:
                        enb = '?'
                    diag = ('me=%s target=%s valid=%s vis=%s enb=%s'
                            % (me_tid, target_tid, valid, vis, enb))
                    if me_tid and target_tid and me_tid != target_tid:
                        try:
                            # A thread with no message queue yet cannot
                            # attach - prime ours first or the call fails
                            # silently.
                            try:
                                u32.PeekMessageW(None, 0, 0, 0, 0)
                            except Exception:
                                pass
                            attached = bool(u32.AttachThreadInput(me_tid, target_tid, True))
                        except Exception:
                            attached = False
                    elif me_tid and target_tid:
                        attached = 'same-thread'
                    try:
                        u32.SetFocus(ch)
                    except Exception:
                        pass
                    try:
                        got = int(u32.GetFocus() or 0)
                    except Exception:
                        got = 0
                    if got and got == int(ch):
                        break
                    try:
                        import time as _ft
                        _ft.sleep(0.3)
                    except Exception:
                        pass
                except Exception:
                    pass
                finally:
                    if attached is True:
                        try:
                            u32.AttachThreadInput(me_tid, target_tid, False)
                        except Exception:
                            pass
                        attached = False
            if got and got == int(ch):
                placed = True
            else:
                try:
                    LOG(f"[FOCUS] SetFocus unverified for child={int(ch)} "
                        f"(focus={got}, attached={attached}) {diag}")
                except Exception:
                    pass
        try:
            if placed and not getattr(_push_focus_to_webview, '_ok_once', False):
                _push_focus_to_webview._ok_once = True
                LOG("[FOCUS] WebView2 child focus verified")
        except Exception:
            pass
        return placed
    except Exception:
        return False


def run_aur_os():
    global webview
    if not _ensure_single_instance():
        LOG("[STARTUP] another copy is already running — exiting")
        raise SystemExit(0)
    LOG("[STARTUP] run_aur_os() called")
    _ensure_webview()
    api      = AurumAPI()
    is_ready = api.db.is_setup_done()
    LOG(f"[STARTUP] is_setup_done={is_ready}")

    if not is_ready:
        initial_file="setup.html"; startup_reason=None
    else:
        LOG("[LICENSE] === PRE-LAUNCH REVOCATION CHECK (local-only, fast) ===")
        # Local-only: instant. The remote revocation check runs on the
        # background thread after the window opens (_bg_check), so a slow/cold
        # license server no longer delays the window by several seconds.
        revoke_status = api.check_license_revoked(network=False)
        LOG(f"[LICENSE] Result: {revoke_status}")

        # NOTE: Subscription state file check removed from startup.
        # Stale .subscription_state.json with expired/revoked status was causing
        # false expiry screens on every launch. The background check (_bg_check)
        # and subscription.js startupCheck() handle subscription validation
        # authoritatively via server calls.

        if revoke_status in ("revoked","invalid","not_found","expired"):
            initial_file = "expiry.html" if revoke_status == "expired" else "revoked.html"
            startup_reason=revoke_status
        else:
            initial_file="login.html"; startup_reason=None
            LOG(f"[LICENSE] Opening login (status={revoke_status})")

    ui_dir       = get_asset_path("ui")
    initial_path = os.path.join(ui_dir, initial_file)
    initial_url  = Path(initial_path).as_uri()
    LOG(f"[STARTUP] Loading: {initial_url}")

    _wv = webview if webview is not None else __import__('webview')
    # A dev copy next to the live copy must be unmistakable at a glance —
    # two identical login windows are how keystrokes end up aimed at the
    # wrong one while the other one "can't type".
    _title = ("AurumOS Executive Dashboard"
              + ("" if getattr(sys, 'frozen', False) else " · DEV"))
    window = _wv.create_window(
        _title, initial_url, js_api=api,
        width=1350, height=950, background_color='#ffffff'
    )
    api.set_window(window)

    def _on_start(w):
        w.maximize()
        LOG("[STARTUP] Window started and maximized")

        # ── Inject license key + machine_id into localStorage for direct-fetch fallback ──
        try:
            import uuid as _uuid2, json as _jj
            _key = api._read_license_key()
            _mid = str(_uuid2.getnode())
            _inject = (
                f"try{{"
                f"localStorage.setItem('aurum_key',{_jj.dumps(_key)});"
                f"localStorage.setItem('aurum_mid',{_jj.dumps(_mid)});"
                f"}}catch(e){{}}"
            )
            w.evaluate_js(_inject)
            LOG(f"[STARTUP] Injected license key into localStorage (key={_key[:10] if _key else 'N/A'}...)")
        except Exception as _ki:
            ERR(f"[STARTUP] Key injection error: {_ki}")

        # ── Auto-connect scale from saved config ──────────────────
        def _auto_connect_scale():
            import time as _sc_t
            _sc_t.sleep(2)  # Let page load first
            try:
                saved = api.scale_get_saved_config()
                port  = saved.get('port', '').strip()
                baud  = int(saved.get('baud', 1200) or 1200)
                if not port:
                    LOG("[STARTUP] No saved scale port — skipping auto-connect")
                    return
                LOG(f"[STARTUP] Auto-connecting scale: {port} @ {baud}")
                _scale.set_window(w)
                result = _scale.start(port, baud)
                LOG(f"[STARTUP] Scale auto-connect result: {result}")
                if result.get('status') == 'success':
                    # Notify all open pages
                    try:
                        w.evaluate_js(
                            "window.__onScaleConnected && window.__onScaleConnected("
                            + repr({'port': port, 'baud': baud})
                            + ")"
                        )
                    except: pass
                else:
                    LOG(f"[STARTUP] Scale auto-connect failed: {result.get('message')}")
            except Exception as e:
                ERR(f"[STARTUP] Scale auto-connect error: {e}")

        threading.Thread(target=_auto_connect_scale, daemon=True).start()

        # ── Auto-update check loop (shows card, user clicks Install) ──
        try:
            threading.Thread(target=api.auto_update_loop, daemon=True).start()
            LOG("[STARTUP] Auto-update check loop started")
        except Exception as _au:
            ERR(f"[STARTUP] auto-update loop failed to start: {_au}")

        # ── Silent encrypted recovery backup (every 5 minutes) ──
        try:
            from core import auto_backup as _abk
            _abk.start(lambda: api.db.db_path, api._app_base_dir(),
                       api._photo_dir)
            LOG("[STARTUP] Encrypted recovery backup started")
        except Exception as _bk:
            ERR(f"[STARTUP] recovery backup failed to start: {_bk}")

        if initial_file=="revoked.html" and startup_reason:
            import time as _ti; _ti.sleep(1.2)
            js = (
                "try{localStorage.setItem('aurum_revoke_reason','"+startup_reason+"');}catch(e){};"
                "var _r='"+startup_reason+"';"
                "var _msgs={revoked:'Your AurumOS license has been revoked. Please contact support.',"
                "invalid:'Your license is no longer valid. Please contact AurumOS support.',"
                "not_found:'License key not found on server. Please contact support.',"
                "expired:'Your license has expired. Please renew to continue.'};"
                "var _lbls={revoked:'License Revoked',invalid:'License Invalid',"
                "not_found:'License Not Found',expired:'License Expired'};"
                "var _el=document.getElementById('reason-text');if(_el)_el.innerText=_lbls[_r]||_lbls.revoked;"
                "var _ml=document.getElementById('revoke-msg');if(_ml)_ml.innerHTML=_msgs[_r]||_msgs.revoked;"
            )
            try: w.evaluate_js(js)
            except Exception as je: ERR(f"[LICENSE] Revoked JS error: {je}")

        def _bg_check():
            import time as _t, json as _j
            _t.sleep(3)

            _sse_started = [False]

            def run_revoke_check():
                status = api.check_license_revoked()
                if status in ('revoked','invalid','not_found','expired'):
                    LOG(f'[LICENSE] Background: Revoked! {status}')
                    _t.sleep(1)
                    api.fire_revoked_screen(status)
                elif status == 'ok':
                    # License valid — clear stale revoked state and navigate to login
                    try:
                        cur = (api._window and api._window.get_current_url()) or ''
                        if 'revoked.html' in cur or 'expiry.html' in cur:
                            LOG('[LICENSE] License valid — navigating to login')
                            # Clear stale localStorage revoked state
                            clear_js = (
                                "try{"
                                "localStorage.removeItem('aurum_revoke_reason');"
                                "var s=JSON.parse(localStorage.getItem('aurum_sub_state')||'{}');"
                                "if(s&&(s.status==='expired'||s.status==='revoked'||!s.valid)){"
                                "  s.status='active';s.valid=true;s._ts=Date.now();"
                                "  localStorage.setItem('aurum_sub_state',JSON.stringify(s));"
                                "}"
                                "}catch(e){}"
                            )
                            if api._window:
                                api._window.evaluate_js(clear_js)
                                api._window.evaluate_js("window.location.href='login.html'")
                    except: pass
                    if not _sse_started[0]:
                        _sse_started[0] = True
                        LOG('[LICENSE] Background: Valid — starting SSE stream')
                        try:
                            api.start_sse_stream()
                        except Exception as _sse_err:
                            ERR(f'[LICENSE] SSE start failed: {_sse_err}')
                        # Start subscription periodic sync
                        try:
                            api.sub.set_license_key(api._read_license_key())
                            api.sub.set_window(api._window)
                            api.sub.startup_check()
                            api.sub.start_periodic_sync(30)
                            api.sub.start_reactivation_polling()
                            LOG('[SUB] Periodic sync + reactivation polling started from bg_check')
                        except Exception as _sub_err:
                            ERR(f'[SUB] bg_check sync start failed: {_sub_err}')
                        # Start auto-revoke rule engine
                        try:
                            from core.auto_revoke import start as _ar_start
                            _ar_key = api._read_license_key()
                            _ar_mid = ''
                            try:
                                _ar_mid = api.db._machine_fingerprint()
                            except Exception:
                                import uuid as _ar_uuid
                                _ar_mid = str(_ar_uuid.getnode())
                            _ar_start(
                                license_key=_ar_key,
                                machine_id=_ar_mid,
                                db_manager=api.db,
                                api_ref=api,
                                interval=30,
                            )
                            LOG('[AUTO-REVOKE] Engine started from bg_check')
                        except Exception as _ar_err:
                            ERR(f'[AUTO-REVOKE] Start failed: {_ar_err}')

                        # ── Geo-fence country check ──
                        try:
                            from core.geo_fence import enforce as _gf_enforce
                            _gf_result = _gf_enforce(
                                license_key=_ar_key,
                                api_ref=api,
                            )
                            LOG(f'[GEO-FENCE] Result: allowed={_gf_result.get("allowed")}, '
                                f'country={_gf_result.get("country_code")}')
                        except Exception as _gf_err:
                            ERR(f'[GEO-FENCE] Check failed: {_gf_err}')

                        # ── Trial system check ──
                        try:
                            from core.trial_manager import start as _trial_start
                            _trial_result = _trial_start(
                                license_key=_ar_key,
                                machine_id=_ar_mid,
                                api_ref=api,
                            )
                            LOG(f'[TRIAL] Manager started: is_trial={_trial_result.get("is_trial")}, '
                                f'days={_trial_result.get("days_remaining")}')
                        except Exception as _trial_err:
                            ERR(f'[TRIAL] Start failed: {_trial_err}')

            run_revoke_check()

            threading.Thread(
                target=lambda: [_t.sleep(6*60*60) or run_revoke_check()],
                daemon=True
            ).start()

            _t.sleep(5)
            try:
                LOG("[UPDATE] Checking for updates...")
                r = check_for_update(timeout=10)
                # Full debug log so we can see exactly why banner shows or not
                LOG(f"[UPDATE] check result: available={r.get('available') if r else None} "
                    f"version={r.get('version') if r else None} "
                    f"file_count={r.get('file_count') if r else None} "
                    f"current={r.get('current') if r else None}")
                if r:
                    LOG(f"[UPDATE] full result keys: {list(r.keys())}")
                    for k,v in r.items():
                        if k != '_changed':
                            LOG(f"[UPDATE]   {k} = {v!r}")
                # Skip banner if update already applied (session flag OR marker file)
                _applied = getattr(api, '_update_applied_version', None)
                if not _applied:
                    try:
                        from updater import get_app_root as _gar2
                        _mf = _gar2() / '.update_applied'
                        if _mf.exists():
                            _applied = _mf.read_text(encoding='utf-8').strip()
                            LOG(f"[UPDATE] Marker file found: applied={_applied}")
                    except Exception:
                        pass
                if _applied and r and _applied == r.get('version'):
                    LOG(f"[UPDATE] Already applied v{_applied} -- skip banner")
                    r['available'] = False

                if r and r.get("available"):
                    r_js     = {k:v for k,v in r.items() if k!="_changed"}
                    json_str = _j.dumps(r_js)
                    js = ("(function(){var d="+json_str+";"
                          "if(window.__showUpdate) window.__showUpdate(d);"
                          "window.dispatchEvent(new CustomEvent('aurum-update-available',{detail:d}));})()")
                    LOG(f"[UPDATE] v{r['version']} available, {r.get('file_count',0)} files")
                    api._last_update_files = r.get("_changed",[])
                    for attempt in range(5):
                        try:
                            if api._window:
                                api._window.evaluate_js(js)
                                LOG(f"[UPDATE] Banner fired (attempt {attempt+1})")
                                break
                        except Exception as e:
                            ERR(f"[UPDATE] Attempt {attempt+1} failed: {e}")
                            _t.sleep(3)
                else:
                    LOG(f"[UPDATE] Up to date. local={CURRENT_VERSION} remote={r and r.get('version')} changed={r and r.get('file_count',0)}")
            except Exception as e:
                ERR(f"[UPDATE] Check error: {e}")
                import traceback as _tb; _tb.print_exc()

        threading.Thread(target=_bg_check, daemon=True).start()

    def _on_closing():
        try:
            api._app_closing = True
        except Exception:
            pass
        try:
            api.bastion.notify_session_active(False)
            api.bastion.stop()
            LOG("[BASTION_AI] Stopped")
        except Exception:
            pass
        try:
            from network import cluster_server as _cs, cluster_client as _cc
            _cc.stop_sync()
            _cs.stop_server()
            LOG("[CLUSTER] Shop network stopped on close")
        except Exception:
            pass
        try:
            api.db._cleanup_session()
            LOG("[SESSION] Session cleaned up on close")
        except Exception:
            pass
    window.events.closing += _on_closing
    # Every finished page load ends with keyboard focus inside the web
    # content — see _push_focus_to_webview. Without this, a host-side
    # focus slip reads as "can't type anything" on every input of the page.
    try:
        window.events.loaded += _push_focus_to_webview
    except Exception:
        pass

    # Persistent WebView2 profile — reuse the browser profile/cache across
    # launches instead of building a throwaway temp profile every time. Creating
    # a fresh WebView2 user-data folder on each start is a major cold-start cost
    # (and caused the "Failed to delete user data folder" warnings on exit).
    # private_mode=False + a fixed storage_path makes startup much faster after
    # the first run.
    #
    # Every page's localStorage (cached plan, session, software type) lives in
    # this profile, and all file:// pages share one origin. A dev copy running
    # beside the installed EXE on the same PC must not share it, or each copy
    # overwrites the other's plan and every sidebar click flips it.
    try:
        _wv_store = os.path.join(
            os.environ.get('LOCALAPPDATA') or _get_db_base(), 'AurumOS',
            'webview-dev' if _is_dev_mode() else 'webview')
        os.makedirs(_wv_store, exist_ok=True)
    except Exception:
        _wv_store = None

    # Did the CLR start? The rthook sets AURUM_CLR_FAILED=1 when it could not,
    # and AURUM_CLR_ERROR to whatever was actually raised.
    #
    # This used to show one message for every failure: "install .NET Desktop
    # Runtime 8.0". The hook asks for the .NET Framework backend FIRST, and
    # .NET Framework 4.x ships inside Windows, so on nearly every shop PC that
    # message named something already installed. The customer went away,
    # installed .NET 8, came back, and got the identical dialog. Look at what
    # is genuinely on the machine before saying anything about it.
    clr_failed = os.environ.get("AURUM_CLR_FAILED") == "1"
    clr_error = os.environ.get("AURUM_CLR_ERROR", "")
    try:
        from core import dotnet as _dotnet
        verdict = _dotnet.diagnose(clr_error if clr_failed else "")
        LOG(_dotnet.summary_line(verdict.get('facts')))
    except Exception as _de:
        ERR(f"[.NET] could not check what is installed: {_de}")
        verdict = None

    if clr_failed:
        if verdict is None:
            # The check itself is unavailable, so word it for both cases rather
            # than asserting one of them.
            title = "AurumOS — Cannot Start"
            body = ("AurumOS could not start the Microsoft .NET runtime.\n\n"
                    "If .NET Desktop Runtime 8.0 is not installed, get it from:\n"
                    "https://dotnet.microsoft.com/download/dotnet/8.0\n\n"
                    "If it is already installed, reinstall AurumOS keeping the "
                    "_internal folder next to AurumOS.exe.")
        else:
            title = verdict['title']
            body = verdict['message']
            LOG(f"[.NET] refusing to start: {verdict['reason']} :: {verdict.get('detail', '')}")
        if clr_error:
            ERR(f"[.NET] runtime load error: {clr_error}")
        try:
            import ctypes
            MB_OK = 0x00000000
            MB_ICONERROR = 0x00000010
            ctypes.windll.user32.MessageBoxW(0, body, title, MB_OK | MB_ICONERROR)
        except Exception:
            pass
        sys.exit(1)

    # The runtime did start, even though the check found nothing it recognises.
    # Note it and carry on: blocking a machine the app is about to work fine on
    # is the worse mistake of the two.
    if verdict is not None and not verdict.get('ok'):
        LOG(f"[.NET] {verdict.get('reason')} by inspection, but the runtime loaded; continuing")

    # CLR is available — now safely import pywebview and apply monkey-patch
    _ensure_webview()

    webview.start(_on_start, window, gui='edgechromium', debug=True,
                  private_mode=False, storage_path=_wv_store)


if __name__ == '__main__':
    run_aur_os()