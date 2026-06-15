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

import threading
import time
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import base64
import uuid
import hmac
import struct
import webview
import os
import sys

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
from database.db_manager import DBManager
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

try:
    from updater import check_for_update, download_and_install, CURRENT_VERSION
except ImportError:
    CURRENT_VERSION = '1.0.6'
    def check_for_update(**kw): return None
    def download_and_install(*a, **kw): pass
from core.tag_engine import TagFactory

HOT_RELOAD = False
webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
webview.settings['ALLOW_DOWNLOADS'] = True


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


def get_asset_path(relative_path):
    """
    Locate UI/asset files.
    Priority:
      1. Project root (parent of dist/) -- updated files live here
      2. sys._MEIPASS                   -- original bundled files (fallback)
      3. cwd                            -- dev mode
    """
    if getattr(sys, 'frozen', False):
        exe_dir      = os.path.dirname(sys.executable)
        # Go up to project root if EXE is inside dist/ or dist/AurumOS/
        parent_name  = os.path.basename(exe_dir).lower()
        if parent_name in ('dist', 'aurumos'):
            project_root = os.path.dirname(exe_dir)
        else:
            project_root = exe_dir
        # Check project root first (updated files)
        root_path = os.path.join(project_root, relative_path)
        if os.path.exists(root_path):
            return root_path
        # Fallback to bundled original
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

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
        enc = data[3:]
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


class AurumAPI:
    def __init__(self):
        # Always use DB next to EXE (dist/database/) — never project root
        # This is the ONLY correct path for client installs
        if getattr(sys, 'frozen', False):
            _db_base = os.path.dirname(sys.executable)
        else:
            _db_base = os.path.abspath('.')
        _db_dir  = os.path.join(_db_base, 'database')
        os.makedirs(_db_dir, exist_ok=True)
        _db_path = os.path.join(_db_dir, 'aurum_local.db')
        LOG(f"[DB] Database path: {_db_path}")
        try:
            self.db = DBManager(_db_path)
        except TypeError:
            # DBManager doesn't accept a path arg — use env var approach
            os.environ['AURUM_DB_PATH'] = _db_path
            self.db = DBManager()
        LOG(f"[DB] DBManager initialized, setup_done={self.db.is_setup_done()}")

        # Ensure backup dir exists
        self.ensure_backup_structure()

        # ── BASTION: EXE integrity check (Layer 9) ─────────────────────────
        try:
            if not self.db.bastion_verify_exe():
                LOG("[BASTION] EXE tampered — suspension triggered")
            else:
                LOG("[BASTION] EXE integrity OK")
        except Exception as _be:
            LOG(f"[BASTION] EXE check skipped: {_be}")

        # Layer 10: Generate session token
        try:
            self.db._generate_session_token()
            LOG("[SESSION] Session token generated at startup")
        except Exception as _se:
            LOG(f"[SESSION] Token generation skipped: {_se}")

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
        global _api_db_ref
        _api_db_ref = self.db
        self._session_role     = None
        self._session_username = None
        self._login_attempts   = 0
        self._lockout_until    = None
        self._MAX_ATTEMPTS     = 3
        self._LOCKOUT_SECONDS  = 5 * 60
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
            ui_dir = get_asset_path("ui")
            target_path = os.path.join(ui_dir, html_file)
            url = Path(target_path).as_uri()
            if self._window:
                safe_url = url.replace("'", "\\'")
                self._window.evaluate_js(f"window.location.href='{safe_url}';")
            return None
        except Exception as e:
            ERR(f"[NAV] Error: {e}")
            return None

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
            rows = safe(lambda: list(self.db._get_connection().__enter__().execute(
                "SELECT date, COALESCE(SUM(total_amount),0.0) as rev, COALESCE(SUM(collected_fine),0.0) as fine "
                "FROM sales_history WHERE date>=? GROUP BY date", (spine[0],)).fetchall()), [])
            rev_map  = {r['date']: float(r['rev'])  for r in rows}
            fine_map = {r['date']: float(r['fine']) for r in rows}
            chart_labels=[]; chart_revenue=[]; chart_fine=[]
            for d in spine:
                try: label = datetime.strptime(d,'%Y-%m-%d').strftime('%a %d')
                except: label = d[-5:]
                chart_labels.append(label)
                chart_revenue.append(round(rev_map.get(d,0.0),2))
                chart_fine.append(round(fine_map.get(d,0.0),3))
            credit_risk_list=[]
            for cl in self.db.get_all_clients():
                bal = self.get_client_balances(cl['name'])
                cash_out = float(bal.get('cash',0.0))
                cash_lim = float(cl.get('cash_limit',0.0))
                if cash_out > 0:
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
                fine_row=conn.execute("SELECT COALESCE(SUM(collected_fine),0.0) as cf FROM sales_history").fetchone()
                total_fine=float(fine_row['cf'] or 0.0)
                inv_rows=conn.execute(
                    "SELECT CAST(touch AS TEXT) || '%' as it_code, "
                    "COALESCE(SUM(gr_wt),0) as total_wt FROM stock_inventory "
                    "WHERE gr_wt>0 AND touch IS NOT NULL AND touch>0 "
                    "AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%' OR tag_id LIKE 'OPENING-%') "
                    "GROUP BY CAST(touch AS TEXT) ORDER BY total_wt DESC LIMIT 10"
                ).fetchall()
            return {
                "status":"success",
                "accumulated_sales": rev_map.get(today_str,0.0),
                "total_fine_collected": total_fine,
                "tracked_units": int(db_stats.get("pcs",0)+db_stats.get("uchak_pcs",0)),
                "metallic_weight": float(db_stats.get("net",0.0)),
                "huid_status":"100% Verified","sync_node":"Operational",
                "chart":{"labels":chart_labels,"revenue":chart_revenue,"fine":chart_fine},
                "inventory_chart":{"labels":[r['it_code'] for r in inv_rows],"weights":[round(float(r['total_wt']),3) for r in inv_rows]},
                "risk_monitor":credit_risk_list,"audit_logs":live_audit_logs
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
                    webview.create_window(
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

    def print_multiple_tags(self, items_list):
        LOG(f"[PRINT] print_multiple_tags called with {len(items_list)} item(s)")
        try:
            is_ok, msg = self.tag_factory.check_printer_status()
            LOG(f"[PRINT] Printer status: {msg}")
            if not is_ok:
                return {"status":"error","message":f"Printer not ready: {msg}"}
            success_count=0; errors=[]
            for item_data in items_list:
                try:
                    item_data['tag_id'] = self._extract_tag_id(item_data)
                    item_data = normalize_tag_item(item_data)
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
            is_ok, msg = self.tag_factory.check_printer_status()
            LOG(f"[PRINTER] {msg}")
            if not is_ok:
                return {"status":"error","message":msg}
            item_data['tag_id'] = self._extract_tag_id(item_data)
            item_data = normalize_tag_item(item_data)
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

    # -- LICENSE ---------------------------------------------------------------
    def check_license_revoked(self):
        import urllib.request, json as _j, uuid as _uuid
        base = os.path.dirname(sys.executable) if getattr(sys,"frozen",False) else os.path.abspath(".")
        flag_path = os.path.join(base,"database",".revoked")
        key_path  = os.path.join(base,"database",".license_key")
        if os.path.exists(flag_path):
            try:
                reason = open(flag_path,"r").read().strip() or "revoked"
                LOG(f"[LICENSE] .revoked flag: {reason}")
                return reason if reason in ("revoked","invalid","not_found","expired") else "revoked"
            except: return "revoked"
        if not os.path.exists(key_path):
            LOG("[LICENSE] No .license_key -- skip check")
            return "ok"
        try:
            enc = open(key_path,"rb").read()
            key = decrypt_license_key(enc).strip().upper()
        except Exception as e:
            ERR(f"[LICENSE] Key read error: {e}"); key = ""
        if not key or not key.startswith("AU-"):
            LOG("[LICENSE] Key decrypt failed -- trying DB recovery")
            try:
                db_key = self.db.get_config("license_key","").strip().upper()
                if db_key and db_key.startswith("AU-"):
                    enc2 = encrypt_license_key(db_key)
                    open(key_path,"wb").write(enc2)
                    key = db_key
                    LOG("[LICENSE] .license_key re-encrypted for this machine")
                else:
                    try: os.remove(key_path)
                    except: pass
                    return "ok"
            except Exception as dbe:
                ERR(f"[LICENSE] DB recovery failed: {dbe}")
                try: os.remove(key_path)
                except: pass
                return "ok"
        try:
            machine_id = str(_uuid.getnode())
            CHECK_URL  = "https://aurum-os-admin.vercel.app/api/check"
            payload    = _j.dumps({"key":key,"machine_id":machine_id}).encode()
            req = urllib.request.Request(CHECK_URL,data=payload,
                headers={"Content-Type":"application/json","User-Agent":f"AurumOS/{CURRENT_VERSION}"},
                method="POST")
            with urllib.request.urlopen(req,timeout=10) as resp:
                data = _j.loads(resp.read().decode())
            if data.get("valid"):
                LOG(f"[LICENSE] VALID ({key[:10]}...)")
                try:
                    if os.path.exists(flag_path): os.remove(flag_path)
                except: pass
                return "ok"
            else:
                reason = data.get("status","revoked")
                LOG(f"[LICENSE] REVOKED reason={reason}")
                try: open(flag_path,"w").write(reason)
                except: pass
                try: os.remove(key_path)
                except: pass
                return reason if reason in ("revoked","invalid","not_found","expired") else "revoked"
        except urllib.error.URLError as e:
            LOG(f"[LICENSE] Network unavailable -- offline grace")
            return "offline"
        except Exception as e:
            ERR(f"[LICENSE] Check error: {e}")
            return "error"

    def fire_revoked_screen(self, reason='revoked'):
        try:
            msg_map={'revoked':'Your license has been revoked. Please contact AurumOS support.',
                     'not_found':'License key not found on server. Please contact AurumOS support.',
                     'invalid':'Your license is no longer valid. Please contact AurumOS support.',
                     'expired':'Your license has expired. Please renew to continue.'}
            msg = msg_map.get(reason,'License issue detected. Please contact AurumOS support.')
            js  = f"window.location.href='revoked.html?reason={reason}&msg={msg.replace(chr(39),'')}'",
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
        if not key or not key.startswith('AU-'):
            try:
                enc = open(key_path,'rb').read()
                key = decrypt_license_key(enc).strip().upper()
            except: key = ''
        if not key or not key.startswith('AU-'):
            return 'error'
        try:
            machine_id = str(_uuid.getnode())
            CHECK_URL  = 'https://aurum-os-admin.vercel.app/api/check'
            payload    = _j.dumps({'key':key,'machine_id':machine_id}).encode()
            req = urllib.request.Request(CHECK_URL,data=payload,
                headers={'Content-Type':'application/json','User-Agent':f'AurumOS/{CURRENT_VERSION}'},
                method='POST')
            with urllib.request.urlopen(req,timeout=10) as resp:
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
                try:
                    if self._window:
                        self._window.evaluate_js("try{localStorage.removeItem('aurum_revoke_reason');}catch(e){}")
                except: pass
                return 'ok'
            else:
                reason = data.get('status','revoked')
                try: open(flag_path,'w').write(reason)
                except: pass
                return reason if reason in ('revoked','invalid','not_found','expired') else 'revoked'
        except urllib.error.URLError:
            return 'offline'
        except Exception as e:
            ERR(f'[REACTIVATE] {e}'); return 'error'

    def quit_app(self):
        try:
            if self._window: self._window.destroy()
            sys.exit(0)
        except: pass

    def verify_key(self, key):
        import urllib.request, json as _json, uuid as _uuid
        key = str(key).strip().upper()
        if key.lower() == self.TEMP_KEY:
            return {"status":"success","business":"Dev Mode","owner":"Developer"}
        if not key.startswith("AU-") or len(key) != 22:
            return {"status":"error","message":"Invalid key format. Expected AU-XXXX-XXXX-XXXX-XXXX"}
        try: machine_id = str(_uuid.getnode())
        except: machine_id = "unknown"
        CHECK_URL = "https://aurum-os-admin.vercel.app/api/check"
        LOG(f"[LICENSE] Checking key={key[:10]}... machine={machine_id[:8]}")
        try:
            payload = _json.dumps({"key":key,"machine_id":machine_id}).encode()
            req = urllib.request.Request(CHECK_URL,data=payload,
                headers={"Content-Type":"application/json","User-Agent":f"AurumOS/{CURRENT_VERSION}"},
                method="POST")
            with urllib.request.urlopen(req,timeout=10) as resp:
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

    # -- LOCKOUT ---------------------------------------------------------------
    def _get_lockout_file(self):
        base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath('.')
        return os.path.join(base,'database','.lockout_state')

    def _save_lockout_state(self):
        try:
            import json as _j
            data={'attempts':self._login_attempts,
                  'until':self._lockout_until.isoformat() if self._lockout_until else None}
            open(self._get_lockout_file(),'w').write(_j.dumps(data))
        except: pass

    def _load_lockout_state(self):
        try:
            import json as _j
            from datetime import datetime as _dt
            path = self._get_lockout_file()
            if not os.path.exists(path): return
            data = _j.loads(open(path).read())
            self._login_attempts = data.get('attempts',0)
            until_str = data.get('until')
            if until_str:
                until = _dt.fromisoformat(until_str)
                if until > _dt.now():
                    self._lockout_until = until
                    LOG(f"[LOGIN] Lockout restored -- {int((until-_dt.now()).total_seconds())}s remaining")
                else:
                    self._login_attempts=0; self._lockout_until=None; os.remove(path)
        except: pass

    # -- LOGIN -----------------------------------------------------------------
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
        if self._lockout_until and now < self._lockout_until:
            remaining = int((self._lockout_until-now).total_seconds())
            try:
                with self.db._get_connection() as _cc2:
                    _r2 = _cc2.execute("SELECT value FROM app_config WHERE key='lock_code_cache'").fetchone()
                    _lc2 = _r2["value"] if _r2 else "LOCKED01"
            except: _lc2 = "LOCKED01"
            return {"status":"locked","remaining":remaining,
                    "lock_code": _lc2,
                    "message":f"Locked. Try in {remaining//60}m {remaining%60:02d}s."}
        elif self._lockout_until and now >= self._lockout_until:
            self._lockout_until=None; self._login_attempts=0
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

        auth = self.db.authenticate_user_by_password(password)
        if auth["authenticated"]:
            self._login_attempts = 0
            self._lockout_until  = None
            self._session_role     = auth.get("role", "staff")
            self._session_username = auth.get("username", "owner")
            # Record successful login
            try:
                with self.db._get_connection() as _lc:
                    _lc.execute("CREATE TABLE IF NOT EXISTS login_log (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL DEFAULT 'owner', role TEXT NOT NULL DEFAULT 'admin', login_time TEXT NOT NULL DEFAULT (datetime('now')), ip TEXT DEFAULT '')")
                    _lc.execute("INSERT INTO login_log(username,role,login_time) VALUES(?,?,?)",
                                (self._session_username, self._session_role,
                                 now.strftime('%Y-%m-%d %H:%M:%S')))
                    _lc.commit()
            except Exception as _le:
                LOG(f"[LOGIN] login_log write error: {_le}")
            try:
                lf = self._get_lockout_file()
                if os.path.exists(lf): os.remove(lf)
            except: pass
            role=auth["role"]; username=auth.get("username","Admin")
            landing="billing.html" if role=="staff" else "dashboard.html"
            self._audit(f"Login: {username}",f"Role: {role}","auth")
            try: self.bastion.notify_session_active(True)
            except Exception: pass
            return {"status":"success","role":role,"username":username,"landing":landing}
        self._login_attempts += 1
        left = self._MAX_ATTEMPTS - self._login_attempts
        if self._login_attempts >= self._MAX_ATTEMPTS:
            self._lockout_until = now + _td(seconds=self._LOCKOUT_SECONDS)
            self._save_lockout_state()
            # Generate lock code for unlock key generation
            try:
                _lc = self.db.generate_lock_code() if hasattr(self.db,'generate_lock_code') else None
                if not _lc:
                    import hashlib, platform
                    _lc = hashlib.sha256(platform.node().encode()).hexdigest()[:8].upper()
            except:
                _lc = "LOCKED01"
            # Also save to app_config for health page
            try:
                with self.db._get_connection() as _cc:
                    _cc.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('lock_code_cache',?)", (_lc,))
                    _cc.commit()
            except: pass
            return {"status":"locked","remaining":self._LOCKOUT_SECONDS,
                    "attempts":self._login_attempts,
                    "lock_code": _lc,
                    "message":"Too many attempts. Locked for 15 minutes."}
        word = "attempt" if left==1 else "attempts"
        return {"status":"error","attempts":self._login_attempts,"left":left,
                "message":f"Wrong password. {left} {word} remaining."}

    def get_lockout_status(self):
        from datetime import datetime as _dt
        try:
            if self._lockout_until and _dt.now() < self._lockout_until:
                remaining = int((self._lockout_until-_dt.now()).total_seconds())
                return {"locked":True,"remaining":remaining,"attempts":self._login_attempts}
            return {"locked":False,"attempts":self._login_attempts,"max":self._MAX_ATTEMPTS}
        except:
            return {"locked":False,"attempts":0,"max":3}

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
            else:
                return {"status":"error","message":"Invalid setup data"}
            if not biz_name or not owner_name:
                return {"status":"error","message":"Business name and owner name are required"}
            ok = self.db.save_setup(biz_name, owner_name, phone, city, admin_pass, license_key=license_key)
            if ok:
                if license_key and license_key.startswith("AU-"):
                    try:
                        base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.abspath(".")
                        key_path = os.path.join(base,"database",".license_key")
                        open(key_path,"wb").write(encrypt_license_key(license_key))
                        LOG("[SETUP] License key cached")
                    except Exception as ke: ERR(f"[SETUP] Key cache error: {ke}")
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
            keys = ['business_name','owner_name','owner_phone','city','setup_date','license_key']
            result = {}
            for k in keys:
                result[k] = self.db.get_config(k, '')
            return {'status':'success', 'data': result}
        except Exception as e:
            ERR(f"[SETTINGS] get_settings: {e}")
            return {'status':'error', 'message': str(e)}

    def update_settings(self, data: dict) -> dict:
        """Update business profile fields."""
        try:
            allowed = ['business_name','owner_name','owner_phone','city']
            with self.db._get_connection() as conn:
                for k in allowed:
                    if k in data:
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                            (k, str(data[k]).strip())
                        )
                conn.commit()
            LOG(f"[SETTINGS] Updated: {list(data.keys())}")
            return {'status':'success'}
        except Exception as e:
            ERR(f"[SETTINGS] update_settings: {e}")
            return {'status':'error','message':str(e)}

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
        try: return self.db.get_config("business_name","AurumOS")
        except: return "AurumOS"

    # -- STAFF -----------------------------------------------------------------
    def get_all_staff(self):
        try: return self.db.get_all_staff()
        except Exception as e: ERR(f"[STAFF] {e}"); return []

    def add_staff(self, username, password):
        try:
            ok, msg = self.db.add_staff_user(username, password)
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

    def update_staff_password(self, staff_id, new_password):
        try:
            sid  = int(staff_id)
            if not new_password or len(str(new_password).strip()) < 4:
                return {"status":"error","message":"Password must be at least 4 characters."}
            hashed = hashlib.sha256(str(new_password).encode('utf-8')).hexdigest()
            with self.db._get_connection() as conn:
                conn.execute(
                    "UPDATE admin_creds SET password=? WHERE id=?",
                    (hashed, sid)
                )
                conn.commit()
            LOG(f"[STAFF] Password updated for id={sid}")
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

    def add_product_master(self, code, name, category, touch, wastage):
        return {"status":"success"} if self.db.add_product_master(code,name,category,touch,wastage) else {"status":"error"}

    def get_products(self):
        return self.db.get_all_products()

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
            return {"status":"success"} if self.db.post_ledger_entry(**entry_data) else {"status":"error"}
        except Exception as e:
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
    def add_stock_entry(self, data):
        try:
            try: self.bastion.notify_write()
            except Exception: pass
            import time as _time
            LOG(f"[STOCK] Received: {data}")
            tag = str(data.get('tag_id') or '').strip()
            if not tag or tag in ('N/A','','---','-'):
                data['tag_id'] = 'OPENING-'+str(int(_time.time()*1000))[-8:]
            # Strip fields not in DB schema
            DB_FIELDS = {'it_code','it_name','tag_id','pkg_wt','para_stone_wt','size','design',
                         'pcs','gr_wt','ls_wt','nt_wt','ghat_wt','touch','wastage','huid'}
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
        try:
            cols   = ", ".join([f"{k}=?" for k in data.keys()])
            values = list(data.values())+[entry_id]
            return {"status":"success"} if self.db.execute_query(
                f"UPDATE stock_inventory SET {cols} WHERE id=?", tuple(values)) else {"status":"error"}
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
    def get_inventory_stats(self):                   return self.db.get_inventory_stats()
    def get_analytics_payload(self):                 return self.db.get_analytics_payload()
    def get_velocity_products(self):                 return self.db.get_velocity_products()
    def get_untagged_items(self):                    return self.db.get_untagged_items()

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
            sh_row=self.db.fetch_one("SELECT * FROM sales_history WHERE vch_id=?",(str(voucher_id).strip(),))
            if not sh_row: return {"status":"error","message":f"Voucher {voucher_id} not found."}
            try: items_array=json.loads(sh_row.get('items') or '[]')
            except: items_array=[]
            bill={"vch_id":sh_row.get('vch_id','---'),"customer":sh_row.get('customer','Walking Customer'),
                  "status":sh_row.get('status','PAID'),"is_credit":sh_row.get('status')=='CREDIT',
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
                  "discountAmount":float(sh_row.get('discount_amount') or 0.0),"items":items_array}
            return {"status":"success","bill":bill}
        except Exception as e:
            ERR(f"[BILL PRINT] {e}")
            return {"status":"error","message":str(e)}

    def trigger_print_window(self, voucher_id, copies=1):
        try:
            copies=int(copies) if copies else 1
            ui_dir=get_asset_path("ui")
            print_path=os.path.join(ui_dir,"bill_print.html")
            if not os.path.exists(print_path):
                return {"status":"error","message":"bill_print.html not found"}
            with open(print_path,'r',encoding='utf-8') as f:
                html_content=f.read()
            inject=f"<script>window.__VCH_ID__='{voucher_id}';window.__COPIES__={copies};</script>"
            if '<!DOCTYPE html>' in html_content:
                html_content=html_content.replace('<!DOCTYPE html>','<!DOCTYPE html>'+inject,1)
            else:
                html_content=inject+html_content
            def open_window():
                try:
                    webview.create_window(f"Bill -- {voucher_id}",html=html_content,js_api=self,width=600,height=820,resizable=True)
                except Exception as e:
                    ERR(f"[PRINT WIN] {e}")
            threading.Thread(target=open_window,daemon=True).start()
            return {"status":"success"}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def get_bill_details(self, vch_id):
        try:
            res=self.db.fetch_one("SELECT * FROM sales_history WHERE UPPER(TRIM(vch_id))=?",(str(vch_id).strip().upper(),))
            if res:
                try: items_list=json.loads(res.get('items','[]'))
                except: items_list=[]
                return {"status":"success","voucher":{
                    "vch_id":res.get('vch_id'),"customer":res.get('customer'),"status":res.get('status'),
                    "ledger_fine":float(res.get('ledger_fine') or 0.0),
                    "collected_fine":float(res.get('collected_fine') or 0.0),
                    "fine_995":float(res.get('fine_995') or 0.0),"fine_dhal":float(res.get('fine_dhal') or 0.0),
                    "remaining_fine":float(res.get('remaining_fine') or 0.0),
                    "gold_rate":float(res.get('gold_rate') or 0.0),
                    "total_amount":float(res.get('total_amount') or 0.0),
                    "date":res.get('date'),"time_stamp":res.get('time_stamp')},"items":items_list}
            return {"status":"error","message":"Sales record not found."}
        except Exception as e:
            return {"status":"error","message":str(e)}

    def fetch_history(self):
        try: return self.db.fetch_history()
        except: return []

    def generate_bill(self, bill_data):
        try:
            # Notify BASTION AI this is a legitimate write
            try: self.bastion.notify_write()
            except Exception: pass
            vch_id=str(bill_data.get('vch_id','VCH-000')).strip()
            customer=str(bill_data.get('customer','Walking Customer')).strip()
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
            self.db.record_sale(vch_id,customer,resolved_status,l_fine,coll,f995,dhal,rem,rate,clean_cash_amt,items_json,disc_type,disc_touch,disc_fine,disc_amount)
            self.db.deduct_stock_after_sale(items_json)
            is_cash_settled=(status in ('PAID','CASH','UCHAK_PAID','UCHAK_MAINTAINED'))
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
            return {"status":"success"}
        except Exception as e:
            ERR(f"[BILL] {e}")
            return {"status":"error","message":str(e)}

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
                        row=cursor.execute(
                            "SELECT id,gr_wt FROM stock_inventory WHERE TRIM(it_code)=? AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%') LIMIT 1",
                            (it_code,)).fetchone()
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

        def download_all(changed, app_root):
            total = len(changed)
            LOG(f"[UPDATE] _download_all: {total} files, app_root={app_root}")

            for i, entry in enumerate(changed, 1):
                rel  = entry.get('path', '').replace('\\', '/')
                url  = entry.get('url', '')
                size = entry.get('size', entry.get('size_bytes', 0))

                push_progress(
                    int(5 + (i - 1) / total * 85),
                    f"[{i}/{total}] {rel}"
                )

                LOG(f"[UPDATE] Downloading [{i}/{total}] {rel} from {url[:60]}")

                # Simple direct download — no wrapper, no retries complexity
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

                # Write directly to app_root (no temp dir)
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

            push_progress(100, f"Done! {total} file(s) updated. Click Restart.")
            push_done(True, f"Update ready. v{new_ver} installed. Click Restart.")
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

            changed = []
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
                local_hash = _sha256(app_root / rel)
                if local_hash != remote_hash:
                    tag = 'NEW' if local_hash == '' else 'CHANGED'
                    LOG(f"[UPDATE]   [{tag}] {rel}  local={local_hash[:12] or 'MISSING'}  remote={remote_hash[:12]}")
                    changed.append({'path': rel, 'sha256': remote_hash, 'url': url, 'size': size})
                else:
                    LOG(f"[UPDATE]   [OK]      {rel}")

            LOG(f"[UPDATE] --- result: {len(changed)} of {len(all_files)} need update ---")

            if not changed:
                push_done(False, "Already up to date.")
                return {"status": "nothing_to_do"}

            self._last_update_files = changed
            self._update_running    = True
            LOG(f"[UPDATE] Starting download thread for {len(changed)} file(s)")

            threading.Thread(
                target=download_all,
                args=(changed, app_root),
                daemon=True
            ).start()

            threading.Timer(0.25, drain).start()
            return {"status": "started", "files": len(changed)}

        except Exception as e:
            err = safe_str(str(e))
            ERR(f"[UPDATE] EXCEPTION: {err}")
            import traceback; traceback.print_exc()
            push_done(False, f"Error: {err}")
            return {"status": "error"}


    def reset_lockout(self) -> dict:
        """
        Developer/admin unlock — resets failed attempts and clears lockout.
        Call from Python console: app_api.reset_lockout()
        Or via pywebview bridge for an admin unlock page.
        """
        try:
            self._login_attempts = 0
            self._lockout_until  = None
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

    def restart_app(self):
        import subprocess
        LOG("[UPDATE] Restarting AurumOS...")
        subprocess.Popen([sys.executable]+sys.argv[:])
        sys.exit(0)

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
        _scale.stop()
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

    def scale_is_connected(self):
        """Check if scale is currently running without reconnecting."""
        return {"connected": _scale._running, "port": _scale._port, "baud": _scale._baud}

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
            # Reset in-memory lock state so login works immediately
            self._lockout_until  = None
            self._login_attempts = 0
            try: self._save_lockout_state()
            except Exception: pass
            LOG("[LOCK] In-memory lock state cleared after unlock")
        return result

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


def run_aur_os():
    LOG("[STARTUP] run_aur_os() called")
    api      = AurumAPI()
    is_ready = api.db.is_setup_done()
    LOG(f"[STARTUP] is_setup_done={is_ready}")

    if not is_ready:
        initial_file="setup.html"; startup_reason=None
    else:
        LOG("[LICENSE] === PRE-LAUNCH REVOCATION CHECK ===")
        revoke_status = api.check_license_revoked()
        LOG(f"[LICENSE] Result: {revoke_status}")
        if revoke_status in ("revoked","invalid","not_found","expired"):
            initial_file="revoked.html"; startup_reason=revoke_status
        else:
            initial_file="login.html"; startup_reason=None
            LOG(f"[LICENSE] Opening login (status={revoke_status})")

    ui_dir       = get_asset_path("ui")
    initial_path = os.path.join(ui_dir, initial_file)
    initial_url  = Path(initial_path).as_uri()
    LOG(f"[STARTUP] Loading: {initial_url}")

    window = webview.create_window(
        "AurumOS Executive Dashboard", initial_url, js_api=api,
        width=1350, height=950, background_color='#ffffff'
    )
    api.set_window(window)

    def _on_start(w):
        w.maximize()
        LOG("[STARTUP] Window started and maximized")

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

            def run_revoke_check():
                status = api.check_license_revoked()
                if status in ('revoked','invalid','not_found','expired'):
                    LOG(f'[LICENSE] Background: Revoked! {status}')
                    _t.sleep(1)
                    api.fire_revoked_screen(status)
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
            api.bastion.notify_session_active(False)
            api.bastion.stop()
            LOG("[BASTION_AI] Stopped")
        except Exception:
            pass
        try:
            api.db._cleanup_session()
            LOG("[SESSION] Session cleaned up on close")
        except Exception:
            pass
    window.events.closing += _on_closing
    webview.start(_on_start, window, gui='edgechromium', debug=False)


if __name__ == '__main__':
    run_aur_os()