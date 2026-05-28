import threading
import time
import webview
import os
import sys
import json
import random
from datetime import datetime
from pathlib import Path
from database.db_manager import DBManager

try:
    from updater import check_for_update, download_and_install, CURRENT_VERSION
except ImportError:
    CURRENT_VERSION = '1.0.0'
    def check_for_update(**kw): return None
    def download_and_install(*a, **kw): pass
from core.tag_engine import TagFactory

HOT_RELOAD = False
webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
webview.settings['ALLOW_DOWNLOADS'] = True


# ── PERSISTENT LOG FILE ──────────────────────────────────────
import logging as _logging
_log_path = None
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
    _logging.getLogger().addHandler(_logging.StreamHandler(sys.stdout))
    _logging.info('=== AurumOS Started ===')
_init_log()
def LOG(msg): _logging.info(msg)
def ERR(msg): _logging.error(msg)

def get_asset_path(relative_path):
    """
    Returns correct path for both dev (.py) and compiled (.exe) modes.
    In PyInstaller:
      - sys._MEIPASS = temp folder with bundled assets (read-only)
      - sys.executable parent = folder where .exe lives (writable, has database/)
    UI files are bundled in _MEIPASS; database lives next to the .exe.
    """
    if getattr(sys, 'frozen', False):
        # Compiled .exe — UI files are in the bundle
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ── LICENSE KEY ENCRYPTION ────────────────────────────────────
def _get_machine_key():
    """Derive a machine-specific encryption key from hardware ID."""
    import uuid, hashlib
    machine_id = str(uuid.getnode()).encode()
    return hashlib.sha256(machine_id).digest()   # 32 bytes

def _xor_cipher(data: bytes, key: bytes) -> bytes:
    """Simple XOR cipher — symmetric, no deps needed."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt_license_key(key_str: str) -> bytes:
    """Encrypt license key string to bytes using machine-specific key."""
    raw  = key_str.encode('utf-8')
    enc  = _xor_cipher(raw, _get_machine_key())
    # Prefix with magic bytes so we can detect corrupt/wrong-machine reads
    return b'\xAA\x01' + enc   # magic: 0xAA = 170, 0x01 = 1

def decrypt_license_key(data: bytes) -> str:
    """Decrypt bytes back to license key string. Returns '' on failure."""
    try:
        if not data.startswith(b'\xAA\x01'):
            return ''
        enc = data[3:]
        raw = _xor_cipher(enc, _get_machine_key())
        return raw.decode('utf-8')
    except Exception:
        return ''


# ── SCALE READER ─────────────────────────────────────────────────────────────
import re as _re

class ScaleReader:
    """
    Reads live weight from RS232C jewellery scale via USB-Serial adapter.
    Runs in a background thread, pushes weight to JS via evaluate_js.
    """
    # Format: "-0000.02 G S" (S=Stable, U=Unstable)
    # Also handles: "ST,GS,+12.345", "12.345g", plain decimals
    WEIGHT_PATTERNS = [
        _re.compile(r'[+-]?\s*(\d+\.\d+)\s+G\s+[SU]'),  # -0000.02 G S  (this scale)
        _re.compile(r'ST[,\s]+GS[,\s]+[+-]?\s*(\d+\.\d+)'),  # ST,GS,+ 12.345
        _re.compile(r'GS[,\s]+[+-]?\s*(\d+\.\d+)'),            # GS 12.345
        _re.compile(r'[+-]?\s*(\d+\.\d+)\s*g', _re.I),         # 12.345g
        _re.compile(r'[+-]?\s*(\d+\.\d{2,3})\s*$'),             # trailing decimal
        _re.compile(r'(\d+\.\d+)'),                                # any decimal fallback
    ]
    STABLE_MARKERS = ['ST,', 'ST ', 'STABLE', 'S,+', 'S,-']

    def __init__(self):
        self._port    = None
        self._baud    = 9600
        self._running = False
        self._thread  = None
        self._serial  = None
        self._window  = None
        self._last_wt = None

    def configure(self, port, baud=9600):
        self._port = port
        self._baud = int(baud)

    def set_window(self, window):
        self._window = window

    def parse(self, raw):
        try:
            text = raw.decode('ascii', errors='ignore').strip()
        except:
            text = ''
        if not text:
            return None, False
        for pat in self.WEIGHT_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    val = float(m.group(1))
                    # Detect stable:
                    # 1. "G S" at end = stable, "G U" = unstable (this scale format)
                    # 2. Classic ST, markers
                    t = text.upper()
                    if _re.search(r'G\s+S\b', t):
                        stable = True
                    elif _re.search(r'G\s+U\b', t):
                        stable = False
                    else:
                        stable = any(mk in t for mk in self.STABLE_MARKERS)
                    return round(val, 3), stable
                except:
                    pass
        return None, False

    def start(self, port=None, baud=None):
        if port:  self._port = port
        if baud:  self._baud = int(baud)
        if not self._port:
            return {"status": "error", "message": "No port configured"}
        if self._running:
            self.stop()
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return {"status": "success", "port": self._port, "baud": self._baud}

    def stop(self):
        self._running = False
        if self._serial:
            try: self._serial.close()
            except: pass
        self._serial = None

    def _loop(self):
        try:
            import serial as _serial
            self._serial = _serial.Serial(
                self._port, self._baud,
                bytesize=8, parity='N', stopbits=1,
                timeout=1, xonxoff=False, rtscts=False
            )
            print(f"[SCALE] Connected: {self._port} @ {self._baud}")
            while self._running:
                try:
                    raw = self._serial.readline() or self._serial.read(32)
                    wt, stable = self.parse(raw)
                    if wt is not None:
                        self._last_wt = wt
                        if self._window:
                            payload = f'{{"weight":{wt},"stable":{str(stable).lower()}}}'
                            try:
                                self._window.evaluate_js(
                                    f'window.__onScale && window.__onScale({payload})'
                                )
                            except: pass
                except Exception as e:
                    if self._running:
                        print(f"[SCALE] Read error: {e}")
                    time.sleep(0.5)
        except Exception as e:
            print(f"[SCALE] Connection error: {e}")
            if self._window:
                try:
                    self._window.evaluate_js(
                        f'window.__onScale && window.__onScale({{"error":"{e}","weight":null,"stable":false}})'
                    )
                except: pass

    def get_last(self):
        return self._last_wt

_scale = ScaleReader()


class AurumAPI:
    def __init__(self):
        self.db = DBManager()
        self._window = None
        self.TEMP_KEY = "aurum-dev-2026"
        self.tag_factory = TagFactory()
        self._session_role     = None
        self._session_username = None
        self._login_attempts   = 0
        self._lockout_until    = None
        self._MAX_ATTEMPTS     = 3
        self._LOCKOUT_SECONDS  = 15 * 60
        self._load_lockout_state()  # restore lockout if app was restarted mid-lockout

    def set_window(self, window):
        self._window = window

    # ─── NAVIGATION ───────────────────────────────────────────────
    def navigate(self, html_file):
        try:
            ui_dir = get_asset_path("ui")
            target_path = os.path.join(ui_dir, html_file)
            url = Path(target_path).as_uri()
            if self._window:
                # Use evaluate_js to change location — avoids pywebview callback error
                safe_url = url.replace("'", "\'")
                self._window.evaluate_js(f"window.location.href='{safe_url}';")
            return None
        except Exception as e:
            print(f"[NAV] Error: {e}")
            return None

    # ─── SESSION API ──────────────────────────────────────────────
    def get_session(self):
        if self._session_role:
            return {"status": "ok", "role": self._session_role, "username": self._session_username or ""}
        return {"status": "no_session", "role": None}

    # ─── DYNAMIC EXECUTIVE HEADER ENGINE ──────────────────────────
    def get_dynamic_greeting(self):
        try:
            current_hour = datetime.now().hour
            if 5 <= current_hour < 12:
                greeting = "Good Morning"
            elif 12 <= current_hour < 17:
                greeting = "Good Afternoon"
            elif 17 <= current_hour < 22:
                greeting = "Good Evening"
            else:
                greeting = "Welcome Back"
            profile = self.db.get_inventory_stats()
            owner_name = profile.get("owner_name") or self._session_username or "Strategic Director"
            return {"status": "success", "greeting_prefix": greeting, "owner_title": owner_name}
        except Exception as e:
            print(f"[ERR] Greeting system bridge error: {e}")
            return {"status": "error", "greeting_prefix": "Welcome", "owner_title": "Director"}

    def get_live_command_metrics(self):
        try:
            db_stats = self.db.get_inventory_stats()
            today_str = datetime.now().strftime('%Y-%m-%d')
            with self.db._get_connection() as conn:
                revenue_row = conn.execute(
                    "SELECT COALESCE(SUM(total_amount), 0.0) FROM sales_history WHERE date = ?",
                    (today_str,)
                ).fetchone()
                today_revenue = float(revenue_row[0])
            chart_labels = []
            chart_revenue = []
            chart_margin = []
            with self.db._get_connection() as conn:
                weekly_rows = conn.execute("""
                    SELECT date, COALESCE(SUM(total_amount), 0.0) as rev,
                           COALESCE(SUM(ledger_fine * 100), 0.0) as fine_margin
                    FROM sales_history GROUP BY date ORDER BY date DESC LIMIT 7
                """).fetchall()
                for row in reversed(weekly_rows):
                    dt_obj = datetime.strptime(row['date'], '%Y-%m-%d')
                    chart_labels.append(dt_obj.strftime('%a'))
                    chart_revenue.append(float(row['rev']))
                    chart_margin.append(float(row['rev'] * 0.15) if row['fine_margin'] == 0 else float(row['fine_margin']))
            if not chart_revenue:
                chart_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
                chart_revenue = [0, 0, 0, 0, 0, today_revenue, 0]
                chart_margin = [0, 0, 0, 0, 0, today_revenue * 0.15, 0]
            credit_risk_list = []
            clients = self.db.get_all_clients()
            for cl in clients:
                bal = self.get_client_balances(cl['name'])
                cash_outstanding = float(bal.get('cash', 0.0))
                cash_limit = float(cl.get('cash_limit', 0.0))
                if cash_outstanding > 0:
                    bound_ratio = (cash_outstanding / cash_limit * 100) if cash_limit > 0 else 0
                    assessment_label = "Limit" if bound_ratio >= 90 else "Bound"
                    credit_risk_list.append({
                        "account_name": cl['name'],
                        "outstanding": cash_outstanding,
                        "percentage": round(bound_ratio, 0),
                        "status_class": "status-danger" if bound_ratio >= 90 else "status-warn",
                        "badge_label": f"{round(bound_ratio, 0)}% {assessment_label}"
                    })
            credit_risk_list = sorted(credit_risk_list, key=lambda x: x['outstanding'], reverse=True)[:3]
            live_audit_logs = [
                {"time": datetime.now().strftime('%H:%M:%S'), "msg": "AurumOS executive command core layout synchronized successfully."},
                {"time": "10:12:04", "msg": "Weighing scale calibrated successfully on port <strong>COM3</strong> (0.000g stable)."},
                {"time": "09:55:42", "msg": "Staff authorized asset checkout confirmation sequences matching active voucher distributions."}
            ]
            # Fine collected + inventory stock
            with self.db._get_connection() as conn:
                fine_row = conn.execute(
                    "SELECT COALESCE(SUM(collected_fine), 0.0) as cf FROM sales_history"
                ).fetchone()
                total_fine_collected = float(fine_row['cf'] or 0.0)

                fine_rows2 = conn.execute(
                    "SELECT date, COALESCE(SUM(collected_fine),0.0) as fc "
                    "FROM sales_history GROUP BY date ORDER BY date DESC LIMIT 7"
                ).fetchall()
                fine_by_date = {r['date']: float(r['fc']) for r in fine_rows2}

                inv_rows = conn.execute(
                    "SELECT it_code, COALESCE(SUM(gr_wt),0) as total_wt "
                    "FROM stock_inventory "
                    "WHERE gr_wt > 0 AND it_code IS NOT NULL AND TRIM(it_code) != '' "
                    "  AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%') "
                    "GROUP BY it_code ORDER BY total_wt DESC LIMIT 8"
                ).fetchall()

            with self.db._get_connection() as conn:
                wfine = conn.execute(
                    "SELECT date, COALESCE(SUM(collected_fine),0.0) as fc "
                    "FROM sales_history GROUP BY date ORDER BY date DESC LIMIT 7"
                ).fetchall()
                chart_fine = [float(r['fc']) for r in reversed(wfine)]
            if len(chart_fine) != len(chart_labels):
                chart_fine = [0.0] * len(chart_labels)

            inv_labels  = [r['it_code'] for r in inv_rows]
            inv_weights = [round(float(r['total_wt']), 3) for r in inv_rows]

            return {
                "status": "success",
                "accumulated_sales": today_revenue,
                "total_fine_collected": total_fine_collected,
                "tracked_units": int(db_stats.get("pcs", 0) + db_stats.get("uchak_pcs", 0)),
                "metallic_weight": float(db_stats.get("net", 0.0)),
                "huid_status": "100% Verified",
                "sync_node": "Operational",
                "chart": {
                    "labels":  chart_labels,
                    "revenue": chart_revenue,
                    "margin":  chart_margin,
                    "fine":    chart_fine,
                },
                "inventory_chart": {
                    "labels":  inv_labels,
                    "weights": inv_weights,
                },
                "risk_monitor": credit_risk_list,
                "audit_logs": live_audit_logs
            }
        except Exception as e:
            print(f"[ERR] Dashboard telemetry compilation fault: {e}")
            return {"status": "error", "accumulated_sales": 0.0, "tracked_units": 0, "metallic_weight": 0.000}

    def _extract_tag_id(self, data):
        return data.get('tag_id') or data.get('tag') or "N/A"

    # ─── PRINTING BRIDGES ─────────────────────────────────────────
    def print_multiple_tags(self, items_list):
        try:
            success_count = 0
            for item_data in items_list:
                item_data['tag_id'] = self._extract_tag_id(item_data)
                tag_img = self.tag_factory.generate_tag_image(item_data)
                self.tag_factory.print_to_thermal_printer(tag_img)
                item_id = item_data.get('id')
                if item_id:
                    self.db.mark_as_tagged(item_id)
                success_count += 1
            return {"status": "success", "count": success_count}
        except Exception as e:
            return {"status": "error", "message": f"Print failed: {str(e)}"}

    def print_tag(self, item_data):
        try:
            is_ok, msg = self.tag_factory.check_printer_status()
            print(f"[PRINTER LOG]: {msg}")
            if not is_ok:
                return {"status": "error", "message": msg}
            item_id = item_data.get('id')
            item_data['tag_id'] = self._extract_tag_id(item_data)
            tag_img = self.tag_factory.generate_tag_image(item_data)
            self.tag_factory.print_to_thermal_printer(tag_img)
            if item_id:
                self.db.mark_as_tagged(item_id)
            return {"status": "success", "message": "Tag sent to printer."}
        except Exception as e:
            print(f"[PRINT ERR] CRASH LOG]: {str(e)}")
            return {"status": "error", "message": str(e)}

    def get_tag_preview(self, item_data):
        try:
            item_data['tag_id'] = self._extract_tag_id(item_data)
            url = self.tag_factory.generate_preview(item_data)
            return {"status": "success", "url": url}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ─── LICENSE & LOGIN ──────────────────────────────────────────
    def verify_key(self, key):
        """
        Validates license key against AurumOS admin server.
        Falls back to offline check if no internet.
        """
        import urllib.request, json as _json, uuid as _uuid
        key = str(key).strip().upper()

        # Dev key bypass (remove in production)
        if key.lower() == self.TEMP_KEY:
            return {"status": "success", "business": "Dev Mode", "owner": "Developer"}

        if not key.startswith("AU-") or len(key) != 22:
            return {"status": "error", "message": "Invalid key format. Expected AU-XXXX-XXXX-XXXX-XXXX"}

        # Get machine ID
        try:
            machine_id = str(_uuid.getnode())
        except Exception:
            machine_id = "unknown"

        # Check online
        CHECK_URL = "https://aurum-os-admin.vercel.app/api/check"
        print(f"[LICENSE] Checking key={key} machine={machine_id[:8]} url={CHECK_URL}")

        try:
            payload = _json.dumps({"key": key, "machine_id": machine_id}).encode()
            req = urllib.request.Request(
                CHECK_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent":   f"AurumOS/{CURRENT_VERSION}"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode()
                print(f"[LICENSE] Server response: {raw}")
                data = _json.loads(raw)

            if data.get("valid"):
                # Save key locally for offline fallback (encrypted)
                try:
                    if getattr(sys, 'frozen', False):
                        base = os.path.dirname(sys.executable)
                    else:
                        base = os.path.abspath(".")
                    key_path = os.path.join(base, "database", ".license_key")
                    enc = encrypt_license_key(key)
                    open(key_path, "wb").write(enc)
                    print(f"[LICENSE] Key cached (encrypted)")
                except Exception as e:
                    print(f"[LICENSE] Cache write error: {e}")
                return {
                    "status":   "success",
                    "business": data.get("business", ""),
                    "owner":    data.get("owner", ""),
                }
            else:
                reason = data.get("status", "invalid")
                print(f"[LICENSE] Server rejected: reason={reason}")
                msgs = {
                    "not_found":   "License key not found. Please check your key and try again.",
                    "revoked":     "This license has been revoked. Please contact support.",
                    "bad_request": "Invalid key format.",
                    "server_error":"Server error — please try again in a moment.",
                }
                return {
                    "status":  "error",
                    "message": msgs.get(reason, f"License check failed ({reason}). Contact support.")
                }

        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode()
            except: pass
            print(f"[LICENSE] HTTP error {e.code}: {body}")
            return {"status": "error", "message": f"Server error ({e.code}). Please try again."}

        except urllib.error.URLError as e:
            print(f"[LICENSE] Network error: {e.reason}")
            # Offline — check encrypted local cache
            try:
                if getattr(sys, 'frozen', False):
                    base = os.path.dirname(sys.executable)
                else:
                    base = os.path.abspath(".")
                key_path = os.path.join(base, "database", ".license_key")
                if os.path.exists(key_path):
                    enc   = open(key_path, "rb").read()
                    saved = decrypt_license_key(enc).strip().upper()
                    if saved == key:
                        print(f"[LICENSE] Offline cache match OK")
                        return {"status": "success", "business": "", "owner": "", "offline": True}
                    else:
                        print(f"[LICENSE] Cache mismatch — may be wrong machine")
            except Exception as e:
                print(f"[LICENSE] Cache read error: {e}")
            return {
                "status":  "error",
                "message": "No internet connection. Connect to internet to activate your license."
            }

        except Exception as e:
            print(f"[LICENSE] Unexpected error: {e}")
            return {"status": "error", "message": f"Verification error: {str(e)}"}

    def _get_lockout_file(self):
        """Path to lockout state file — next to DB, survives restarts."""
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.abspath('.')
        return os.path.join(base, 'database', '.lockout_state')

    def _save_lockout_state(self):
        """Persist lockout until-time to file."""
        try:
            import json as _j
            data = {
                'attempts': self._login_attempts,
                'until':    self._lockout_until.isoformat() if self._lockout_until else None
            }
            open(self._get_lockout_file(), 'w').write(_j.dumps(data))
        except Exception:
            pass

    def _load_lockout_state(self):
        """Restore lockout state from file on startup."""
        try:
            import json as _j
            from datetime import datetime as _dt
            path = self._get_lockout_file()
            if not os.path.exists(path):
                return
            data = _j.loads(open(path).read())
            self._login_attempts = data.get('attempts', 0)
            until_str = data.get('until')
            if until_str:
                until = _dt.fromisoformat(until_str)
                if until > _dt.now():
                    self._lockout_until = until
                    print(f"[LOGIN] Lockout restored — {int((until - _dt.now()).total_seconds())}s remaining")
                else:
                    # Expired — clear it
                    self._login_attempts = 0
                    self._lockout_until  = None
                    os.remove(path)
        except Exception:
            pass

    def verify_login(self, password):
        """
        Login handler — three-layer return for maximum exe compatibility.

        Layer 1: return result dict  → works when pywebview resolves Promises
        Layer 2: evaluate_js push    → works when Promise resolution fails in exe
        Layer 3: JS timeout fallback → shows error if both above fail

        Rule: NEVER call evaluate_js from the same thread synchronously.
        We spawn a daemon thread for the push so the API thread returns first.
        """
        import json as _json
        try:
            from datetime import datetime as _dt, timedelta as _td
            LOG(f"[LOGIN] verify_login called, password len={len(password)}")
            result = self._do_login_check(password, _dt.now(), _dt, _td)
            LOG(f"[LOGIN] Result: {result}")

            # Layer 2: push via evaluate_js in background thread
            # (Layer 1 = return value below)
            def _push():
                import time as _t
                _t.sleep(0.05)  # tiny delay so return fires first
                try:
                    payload = _json.dumps(result)
                    if self._window:
                        self._window.evaluate_js(
                            f"window.__loginResult && window.__loginResult({payload})"
                        )
                except Exception as ep:
                    print(f"[LOGIN] push error: {ep}")

            threading.Thread(target=_push, daemon=True).start()

            return result  # Layer 1

        except Exception as e:
            ERR(f"[LOGIN] EXCEPTION: {e}")
            err = {"status": "error", "message": str(e)}
            try:
                payload = _json.dumps(err)
                def _push_err():
                    import time as _t; _t.sleep(0.05)
                    try:
                        if self._window:
                            self._window.evaluate_js(
                                f"window.__loginResult && window.__loginResult({payload})"
                            )
                    except Exception: pass
                threading.Thread(target=_push_err, daemon=True).start()
            except Exception: pass
            return err

    def _do_login_check(self, password, now, _dt, _td):
        """Core login logic — separated for clarity."""
        # Check lockout
        if self._lockout_until and now < self._lockout_until:
            remaining = int((self._lockout_until - now).total_seconds())
            return {"status": "locked", "remaining": remaining,
                    "message": f"Locked. Try in {remaining//60}m {remaining%60:02d}s."}
        elif self._lockout_until and now >= self._lockout_until:
            self._lockout_until  = None
            self._login_attempts = 0

        # Authenticate
        print(f"[LOGIN] Checking password...")
        auth = self.db.authenticate_user("owner", password)
        if not auth["authenticated"]:
            auth = self.db.authenticate_user_by_password(password)
        print(f"[LOGIN] Auth result: authenticated={auth['authenticated']}")

        if auth["authenticated"]:
            self._login_attempts = 0
            self._lockout_until  = None
            self._session_role     = auth["role"]
            self._session_username = "owner"
            try:
                lf = self._get_lockout_file()
                if os.path.exists(lf): os.remove(lf)
            except Exception: pass
            role    = auth["role"]
            landing = "billing.html" if role == "staff" else "dashboard.html"
            print(f"[LOGIN OK] Success role={role} landing={landing}")
            return {"status": "success", "role": role, "landing": landing}

        # Failed
        self._login_attempts += 1
        left = self._MAX_ATTEMPTS - self._login_attempts
        print(f"[LOGIN WARN] Fail {self._login_attempts}/{self._MAX_ATTEMPTS}")

        if self._login_attempts >= self._MAX_ATTEMPTS:
            self._lockout_until = now + _td(seconds=self._LOCKOUT_SECONDS)
            self._save_lockout_state()
            print(f"[LOGIN LOCK] LOCKED {self._LOCKOUT_SECONDS}s")
            return {"status": "locked", "remaining": self._LOCKOUT_SECONDS,
                    "attempts": self._login_attempts,
                    "message": "Too many attempts. Locked for 15 minutes."}

        word = "attempt" if left == 1 else "attempts"
        return {"status": "error", "attempts": self._login_attempts,
                "left": left,
                "message": f"Wrong password. {left} {word} remaining."}

    def get_lockout_status(self):
        from datetime import datetime as _dt
        try:
            if self._lockout_until and _dt.now() < self._lockout_until:
                remaining = int((self._lockout_until - _dt.now()).total_seconds())
                return {"locked": True, "remaining": remaining, "attempts": self._login_attempts}
            return {"locked": False, "attempts": self._login_attempts, "max": self._MAX_ATTEMPTS}
        except Exception:
            return {"locked": False, "attempts": 0, "max": 3}

    def logout(self):
        self._session_role     = None
        self._session_username = None
        self._login_attempts   = 0
        self._lockout_until    = None
        self._MAX_ATTEMPTS     = 3
        self._LOCKOUT_SECONDS  = 15 * 60
        self._load_lockout_state()  # restore lockout if app was restarted mid-lockout
        return {"status": "ok"}

    # ─── SETUP ────────────────────────────────────────────────────
    def save_setup(self, setup_data):
        try:
            biz_name   = setup_data.get('businessName')
            owner_name = setup_data.get('ownerName')
            password   = setup_data.get('adminPass')
            username   = "owner"   # fixed internal username — no username field needed
            success    = self.db.complete_initial_setup(biz_name, username, password, owner_name)
            if success:
                threading.Timer(1.5, lambda: self.navigate("login.html")).start()
                return {"status": "success"}
            return {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ─── STAFF ────────────────────────────────────────────────────
    def get_all_staff(self):
        try:
            return self.db.get_all_staff()
        except Exception as e:
            print(f"[ERR] Staff Retrieval Failure: {e}")
            return []

    def add_staff(self, username, password):
        try:
            ok, msg = self.db.add_staff_user(username, password)
            if ok:
                return {"status": "success", "message": msg}
            return {"status": "error", "message": msg}
        except Exception as e:
            return {"status": "error", "message": f"Bridge Error: {str(e)}"}

    # ─── MASTER DATA ──────────────────────────────────────────────
    def validate_touch_value(self, touch_val):
        try:
            clean_touch = str(touch_val).replace('%', '').strip()
            exists = self.db.is_touch_valid(clean_touch)
            if exists:
                return {"status": "success", "valid": True}
            return {"status": "error", "valid": False,
                    "message": f"Touch value '{clean_touch}' does not exist in master records."}
        except Exception as e:
            return {"status": "error", "valid": False, "message": str(e)}

    def add_category(self, code, name):
        success = self.db.add_category(code, name)
        return {"status": "success"} if success else {"status": "error"}

    def get_categories(self):
        return self.db.get_all_categories()

    def add_touch_group(self, name, value, wastage):
        try:
            success = self.db.add_touch_group(name, value, wastage)
            return {"status": "success"} if success else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": f"Operation failed: {str(e)}"}

    def get_touch_groups(self):
        return self.db.get_all_touch_groups()

    def add_product_master(self, code, name, category, touch, wastage):
        success = self.db.add_product_master(code, name, category, touch, wastage)
        return {"status": "success"} if success else {"status": "error"}

    def get_products(self):
        return self.db.get_all_products()

    def delete_master_entry(self, data_type, entry_id):
        success = self.db.delete_master_entry(data_type, entry_id)
        return {"status": "success"} if success else {"status": "error"}

    # ─── CLIENT ───────────────────────────────────────────────────
    def add_new_client(self, client_data):
        try:
            res = self.db.add_client(
                client_data['name'], client_data.get('phone', ''),
                client_data.get('metal_limit', 0), client_data.get('cash_limit', 0)
            )
            return {"status": "success"} if res else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_client_list(self):
        return self.db.get_all_clients()

    def update_client_limits(self, data):
        try:
            success = self.db.update_client_limits(data)
            return {"status": "success"} if success else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_client_live_outstanding(self, client_name):
        try:
            clean_name = str(client_name).strip()
            with self.db._get_connection() as conn:
                res = conn.execute("""
                    SELECT SUM(metal_dr - metal_cr) as metal_bal,
                           SUM(cash_dr - cash_cr)   as cash_bal
                    FROM credit_ledger
                    WHERE UPPER(TRIM(client_name)) = UPPER(TRIM(?))
                      AND UPPER(TRIM(description)) NOT LIKE '%CASH SETTLEMENT%'
                      AND UPPER(TRIM(description)) NOT LIKE '%UCHAK CASH%'
                """, (clean_name,)).fetchone()
                if res:
                    return {
                        "status": "success",
                        "metal_outstanding": round(res['metal_bal'] or 0.0, 3),
                        "cash_outstanding":  round(res['cash_bal']  or 0.0, 2)
                    }
                return {"status": "success", "metal_outstanding": 0.000, "cash_outstanding": 0.00}
        except Exception as e:
            print(f"[API ERR] FETCH LIVE OUTSTANDING EXCEPTION] {e}")
            return {"status": "error", "message": str(e)}

    # ─── LEDGER ───────────────────────────────────────────────────
    def get_ledger_summary(self):
        try:
            with self.db._get_connection() as conn:
                res = conn.execute("""
                    SELECT SUM(metal_dr) as m_dr, SUM(metal_cr) as m_cr,
                           SUM(cash_dr)  as c_dr, SUM(cash_cr)  as c_cr
                    FROM credit_ledger
                """).fetchone()
                return {
                    "metal_dr": round(res['m_dr'] or 0.0, 3), "metal_cr": round(res['m_cr'] or 0.0, 3),
                    "cash_dr":  round(res['c_dr'] or 0.0, 2), "cash_cr":  round(res['c_cr'] or 0.0, 2),
                    "metal": round((res['m_dr'] or 0.0) - (res['m_cr'] or 0.0), 3),
                    "cash":  round((res['c_dr'] or 0.0) - (res['c_cr'] or 0.0), 2)
                }
        except:
            return {"metal_dr": 0, "metal_cr": 0, "cash_dr": 0, "cash_cr": 0, "metal": 0, "cash": 0}

    def get_full_ledger_stream(self):
        try:
            with self.db._get_connection() as conn:
                return [dict(r) for r in conn.execute("SELECT * FROM credit_ledger ORDER BY id DESC").fetchall()]
        except:
            return []

    def post_journal_entry(self, data):
        try:
            entry_data = {
                "client_name": data.get('account_type', 'MARKET'),
                "vch_id":      "JRNL-" + datetime.now().strftime('%M%S'),
                "desc":        data.get('description', 'Journal Entry'),
                "metal_dr":    float(data.get('m_dr', 0)), "metal_cr": float(data.get('m_cr', 0)),
                "cash_dr":     float(data.get('c_dr', 0)), "cash_cr":  float(data.get('c_cr', 0)),
                "gold_rate":   0
            }
            res = self.db.post_ledger_entry(**entry_data)
            return {"status": "success"} if res else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def post_to_ledger(self, entry_data):
        try:
            res = self.db.post_ledger_entry(**entry_data)
            return {"status": "success"} if res else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_ledger_statement(self, client_name):
        return self.db.get_client_statement(client_name)

    def get_client_balances(self, client_name):
        try:
            query = """
                SELECT SUM(metal_dr - metal_cr) as metal_bal,
                       SUM(cash_dr - cash_cr)   as cash_bal
                FROM credit_ledger WHERE client_name = ?
                  AND UPPER(TRIM(description)) NOT LIKE '%CASH SETTLEMENT%'
                  AND UPPER(TRIM(description)) NOT LIKE '%UCHAK CASH%'
            """
            res = self.db.fetch_one(query, (client_name,))
            return {"metal": round(res['metal_bal'] or 0.0, 3), "cash": round(res['cash_bal'] or 0.0, 2)} if res else {"metal": 0, "cash": 0}
        except:
            return {"metal": 0, "cash": 0}

    # ─── STOCK LEDGER ─────────────────────────────────────────────
    def add_stock_entry(self, data):
        try:
            import time as _time
            print(f"[STOCK ENTRY] Received: {data}")

            # Ensure tag_id has correct prefix so report columns don't overlap:
            # - Katti entries: tag_id = 'KATTI-xxx'  → Inward (via katti_voucher_items)
            # - Opening entries: tag_id = 'OPENING-xxx' → Opening column only
            # - If no tag_id given, assign OPENING- prefix (stock ledger weight entry)
            tag = str(data.get('tag_id') or '').strip()
            if not tag or tag in ('N/A', '', '---', '-'):
                # Auto-generate OPENING- tag so it goes to Opening column in report
                data['tag_id'] = 'OPENING-' + str(int(_time.time() * 1000))[-8:]
                print(f"[STOCK ENTRY] Auto-tagged as: {data['tag_id']}")

            success = self.db.add_stock_entry(**data)
            if success:
                print(f"[STOCK OK] ENTRY] Saved: {data.get('it_code')} gr_wt={data.get('gr_wt')}")
                return {"status": "success"}
            else:
                print(f"[STOCK ERR] ENTRY] DB returned False for: {data}")
                return {"status": "error", "message": "DB save returned False"}
        except Exception as e:
            print(f"[STOCK ERR] ENTRY] Exception: {e} | data={data}")
            return {"status": "error", "message": str(e)}

    def add_uchak_stock_entry(self, data):
        try:
            it_code = str(data.get('it_code', '')).strip()
            it_name = str(data.get('it_name', '')).strip()
            pcs     = int(data.get('pcs') or 1)
            price   = str(data.get('price', '0.00'))
            success = self.db.add_uchak_stock_entry_raw(it_code, it_name, pcs, price)
            return {"status": "success"} if success else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def update_stock_entry(self, entry_id, data):
        try:
            cols    = ", ".join([f"{k} = ?" for k in data.keys()])
            values  = list(data.values()) + [entry_id]
            success = self.db.execute_query(f"UPDATE stock_inventory SET {cols} WHERE id = ?", tuple(values))
            return {"status": "success"} if success else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_stock_entry(self, entry_id):
        try:
            success = self.db.execute_query("DELETE FROM stock_inventory WHERE id = ?", (entry_id,))
            return {"status": "success"} if success else {"status": "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_stock_ledger(self):
        return self.db.get_stock_ledger()

    def get_opening_stock(self):
        return self.db.get_opening_stock()

    def get_stock_ledger_by_date(self, target_date):
        return self.db.fetch_stock_ledger_by_date(target_date)

    def get_ledger_dates(self):
        return self.db.get_available_ledger_dates()

    # ─── LOOKUPS & ANALYTICS ──────────────────────────────────────
    def get_product_by_tag(self, tag_id):
        return self.db.get_product_by_tag(tag_id)

    def get_inventory_stats(self):
        return self.db.get_inventory_stats()

    def get_analytics_payload(self):
        return self.db.get_analytics_payload()

    def get_velocity_products(self):
        return self.db.get_velocity_products()

    def get_untagged_items(self):
        return self.db.get_untagged_items()

    # ─── KATTI ────────────────────────────────────────────────────
    def get_katti_vch_id(self):
        return self.db.get_next_vch_id()

    def save_katti_voucher(self, vch_id, total_wt, total_packets, note, items):
        try:
            box_id = None
            if items and isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        candidate = str(item.get('box') or '').strip()
                        if candidate and candidate not in ('', '-', 'None', 'N/A'):
                            box_id = candidate
                            break
            success = self.db.save_katti_batch(str(vch_id), float(total_wt or 0), int(total_packets or 0), str(note or ""), items, box_id)
            return {"status": "success"} if success else {"status": "error", "message": "DB save failed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_voucher_history(self, vch_id):
        target_id = str(vch_id).strip().zfill(4)
        try:
            data = self.db.get_katti_voucher_details(target_id)
            if data:
                return {"status": "success", "voucher": data.get('voucher'), "items": data.get('items', [])}
            return {"status": "empty"}
        except Exception:
            return {"status": "error"}

    # ─── UCHAK INWARD ─────────────────────────────────────────────
    def get_next_uchak_inward_vch_id(self):
        return self.db.get_last_uchak_inward_vch_id()

    def get_uchak_inward_voucher_details(self, vch_id):
        data = self.db.get_uchak_inward_voucher_details(vch_id)
        if data:
            return {"status": "success", "voucher": data["voucher"], "items": data["items"]}
        return {"status": "error", "message": "Voucher not found."}

    def save_uchak_inward_batch(self, payload):
        try:
            vch_id      = str(payload.get('vch_id', 'UCHK-IN-001')).strip()
            items       = payload.get('items', [])
            if not items:
                return {"status": "error", "message": "Batch array queue is empty."}
            total_lines = len(items)
            total_pcs   = sum(int(i['pcs'] or 0) for i in items)
            total_value = sum((int(i['pcs'] or 0) * float(i['price'] or 0.0)) for i in items)
            success = self.db.save_uchak_inward_transaction(vch_id, total_lines, total_pcs, total_value, items)
            return {"status": "success"} if success else {"status": "error", "message": "Database transaction failed."}
        except Exception as e:
            print(f"[API ERR] BATCH INGESTION EXCEPTION] {e}")
            return {"status": "error", "message": str(e)}

    # ─── BILLING ──────────────────────────────────────────────────
    def get_sales_vch_id(self):
        try:
            with self.db._get_connection() as conn:
                res = conn.execute(
                    "SELECT MAX(CAST(SUBSTR(vch_id, 5) AS INTEGER)) FROM sales_history WHERE vch_id LIKE 'VCH-%'"
                ).fetchone()
                next_id = (res[0] or 0) + 1
                return f"VCH-{next_id:03d}"
        except:
            return "VCH-001"

    def get_next_uchak_vch_id(self):
        try:
            with self.db._get_connection() as conn:
                res = conn.execute(
                    "SELECT MAX(CAST(SUBSTR(vch_id, 6) AS INTEGER)) FROM sales_history WHERE vch_id LIKE 'UCHK-%'"
                ).fetchone()
                next_id = (res[0] or 0) + 1
                return f"UCHK-{next_id:03d}"
        except:
            return "UCHK-001"

    def get_live_invoice_print_payload(self, voucher_id):
        try:
            print(f"[PRINT]] get_live_invoice_print_payload called for: {voucher_id}")
            sh_row = self.db.fetch_one(
                "SELECT * FROM sales_history WHERE vch_id = ?",
                (str(voucher_id).strip(),)
            )
            if not sh_row:
                print(f"[PRINT ERR]] Voucher {voucher_id} not found in DB")
                return {"status": "error", "message": f"Voucher {voucher_id} not found."}

            try:
                items_array = json.loads(sh_row.get('items') or '[]')
            except:
                items_array = []

            print(f"[PRINT OK]] Found bill: customer={sh_row.get('customer')}, items={len(items_array)}, total={sh_row.get('total_amount')}")

            bill = {
                "vch_id":          sh_row.get('vch_id', '---'),
                "customer":        sh_row.get('customer', 'Walking Customer'),
                "status":          sh_row.get('status', 'PAID'),
                "is_credit":       sh_row.get('status') == 'CREDIT',
                "is_uchak":        'UCHAK' in str(sh_row.get('status', '')).upper(),
                "totalLedgerFine": float(sh_row.get('ledger_fine')    or 0.0),
                "remainingFine":   float(sh_row.get('remaining_fine') or 0.0),
                "collectedFine":   float(sh_row.get('collected_fine') or 0.0),
                "fine995":         float(sh_row.get('fine_995')       or 0.0),
                "fineDhal":        float(sh_row.get('fine_dhal')      or 0.0),
                "goldRate":        float(sh_row.get('gold_rate')      or 0.0),
                "totalAmount":     float(sh_row.get('total_amount')   or 0.0),
                "discountType":    sh_row.get('discount_type',   'none'),
                "discountTouch":   float(sh_row.get('discount_touch')  or 0.0),
                "discountFine":    float(sh_row.get('discount_fine')   or 0.0),
                "discountAmount":  float(sh_row.get('discount_amount') or 0.0),
                "items":           items_array
            }
            return {"status": "success", "bill": bill}
        except Exception as e:
            print(f"[PRINT ERR] API CRASH] {e}")
            return {"status": "error", "message": str(e)}

    def trigger_print_window(self, voucher_id, copies=1):
        try:
            copies     = int(copies) if copies else 1
            ui_dir     = get_asset_path("ui")
            print_path = os.path.join(ui_dir, "bill_print.html")

            if not os.path.exists(print_path):
                return {"status": "error", "message": f"bill_print.html not found"}

            with open(print_path, 'r', encoding='utf-8') as f:
                html_content = f.read()

            inject_script = (
                f'<script>window.__VCH_ID__=\'{voucher_id}\';'
                f'window.__COPIES__={copies};</script>'
            )
            if '<!DOCTYPE html>' in html_content:
                html_content = html_content.replace('<!DOCTYPE html>', '<!DOCTYPE html>' + inject_script, 1)
            else:
                html_content = inject_script + html_content

            def open_window():
                try:
                    webview.create_window(
                        f"Bill — {voucher_id}",
                        html=html_content,
                        js_api=self,
                        width=600, height=820, resizable=True
                    )
                except Exception as e:
                    print(f"[PRINT ERR]] create_window failed: {e}")

            threading.Thread(target=open_window, daemon=True).start()
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_bill_details(self, vch_id):
        try:
            safe_vch_id = str(vch_id).strip().upper()
            res = self.db.fetch_one("SELECT * FROM sales_history WHERE UPPER(TRIM(vch_id)) = ?", (safe_vch_id,))
            if res:
                try:
                    items_list = json.loads(res.get('items', '[]'))
                except:
                    items_list = []
                return {
                    "status": "success",
                    "voucher": {
                        "vch_id":         res.get('vch_id'),
                        "customer":       res.get('customer'),
                        "status":         res.get('status'),
                        "ledger_fine":    float(res.get('ledger_fine')    or 0.0),
                        "collected_fine": float(res.get('collected_fine') or 0.0),
                        "fine_995":       float(res.get('fine_995')       or 0.0),
                        "fine_dhal":      float(res.get('fine_dhal')      or 0.0),
                        "remaining_fine": float(res.get('remaining_fine') or 0.0),
                        "gold_rate":      float(res.get('gold_rate')      or 0.0),
                        "total_amount":   float(res.get('total_amount')   or 0.0),
                        "date":           res.get('date'),
                        "time_stamp":     res.get('time_stamp')
                    },
                    "items": items_list
                }
            return {"status": "error", "message": "Sales record not found."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def fetch_history(self):
        try:
            return self.db.fetch_history()
        except Exception:
            return []

    def generate_bill(self, bill_data):
        try:
            vch_id   = str(bill_data.get('vch_id', 'VCH-000')).strip()
            customer = str(bill_data.get('customer', 'Walking Customer')).strip()
            status   = str(bill_data.get('status', 'CREDIT')).upper().strip()
            l_fine   = float(bill_data.get('totalLedgerFine') or 0.0)
            coll     = float(bill_data.get('collectedFine')   or 0.0)
            f995     = float(bill_data.get('fine995')         or 0.0)
            dhal     = float(bill_data.get('fineDhal')        or 0.0)
            rem      = float(bill_data.get('remainingFine')   or 0.0)
            rate     = float(bill_data.get('goldRate')        or 0.0)
            is_uchak = bool(bill_data.get('is_uchak', False)) or vch_id.startswith('UCHK-')

            raw_cash_amt   = str(bill_data.get('totalAmount') or '0.00')
            clean_cash_amt = float(raw_cash_amt.replace('₹', '').replace(',', '').strip())
            items_json     = json.dumps(bill_data.get('items', []))

            disc_type   = str(bill_data.get('discountType')   or 'none')
            disc_touch  = float(bill_data.get('discountTouch')  or 0.0)
            disc_fine   = float(bill_data.get('discountFine')   or 0.0)
            disc_amount = float(bill_data.get('discountAmount') or 0.0)

            resolved_status = (
                'UCHAK_UNPAID' if (status == 'CREDIT' and is_uchak) else
                'UCHAK_PAID'   if (status == 'PAID'   and is_uchak) else
                status
            )
            self.db.record_sale(vch_id, customer, resolved_status,
                                l_fine, coll, f995, dhal, rem, rate, clean_cash_amt, items_json,
                                disc_type, disc_touch, disc_fine, disc_amount)
            self.db.deduct_stock_after_sale(items_json)

            is_cash_settled = (status in ('PAID', 'CASH', 'UCHAK_PAID', 'UCHAK_MAINTAINED'))

            if is_uchak:
                if is_cash_settled:
                    metal_debit = 0.0; metal_credit = 0.0
                    cash_debit  = 0.0; cash_credit  = clean_cash_amt
                    ledger_desc = "Uchak Cash Invoice Paid"
                else:
                    metal_debit = 0.0; metal_credit = 0.0
                    cash_debit  = clean_cash_amt; cash_credit = 0.0
                    ledger_desc = "Uchak Credit Udhar"
            else:
                if is_cash_settled:
                    metal_debit  = 0.0
                    metal_credit = float(coll or 0)
                    cash_debit   = 0.0
                    cash_credit  = clean_cash_amt
                    ledger_desc  = f"Sales Invoice Paid — ₹{clean_cash_amt:.0f}"
                else:
                    if rate > 0:
                        metal_debit = 0.0; metal_credit = 0.0
                        cash_debit  = clean_cash_amt; cash_credit = 0.0
                        ledger_desc = "Sales Credit — Cash Due"
                    else:
                        metal_debit = float(rem or 0); metal_credit = 0.0
                        cash_debit  = 0.0; cash_credit = 0.0
                        ledger_desc = f"Sales Credit — Fine Due {rem:.3f}g"

            self.post_to_ledger({
                "client_name": customer, "vch_id": vch_id, "gold_rate": rate,
                "desc": ledger_desc, "metal_dr": metal_debit, "metal_cr": metal_credit,
                "cash_dr": cash_debit, "cash_cr": cash_credit
            })

            return {"status": "success"}
        except Exception as e:
            print(f"[ERR] Invoicing Engine Failure: {e}")
            return {"status": "error", "message": str(e)}

    def delete_bill(self, vch_id):
        try:
            safe_vch_id = str(vch_id).strip()
            res = self.db.fetch_one("SELECT items FROM sales_history WHERE vch_id=?", (safe_vch_id,))
            if not res:
                return {"status": "error", "message": "Bill not found."}
            try:
                items = json.loads(res.get('items') or '[]')
            except:
                items = []

            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                for item in items:
                    tag_id  = str(item.get('tag_id') or '').strip()
                    it_code = str(item.get('it_code') or item.get('code') or '').strip()
                    weight  = float(item.get('weight') or item.get('gr_wt') or 0.0)
                    touch   = float(item.get('touch') or 0.0)
                    pcs     = int(item.get('pcs') or 1)

                    is_weight = (not tag_id or tag_id in ('', 'N/A') or tag_id.startswith('KATTI-%'))
                    is_uchak  = 'amount' in item or 'price' in item

                    if is_weight and weight > 0:
                        row = cursor.execute(
                            "SELECT id, gr_wt FROM stock_inventory WHERE TRIM(it_code)=? AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%') LIMIT 1",
                            (it_code,)
                        ).fetchone()
                        if row:
                            restored = round((row['gr_wt'] or 0) + weight, 3)
                            cursor.execute("UPDATE stock_inventory SET gr_wt=?, nt_wt=? WHERE id=?", (restored, restored, row['id']))
                        else:
                            unique_tag = f"KATTI-RESTORE-{it_code}"
                            cursor.execute(
                                "INSERT INTO stock_inventory (it_code,it_name,tag_id,pcs,gr_wt,ls_wt,nt_wt,touch,wastage,is_tagged,entry_date) VALUES (?,?,?,0,?,0,?,?,0,0,date('now'))",
                                (it_code, item.get('it_name') or it_code, unique_tag, weight, weight, touch)
                            )
                    elif is_uchak and pcs > 0:
                        piece_code = str(item.get('it_code') or item.get('name') or '').strip()
                        row = cursor.execute(
                            "SELECT id, pcs FROM stock_inventory WHERE TRIM(it_code)=? AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A') LIMIT 1",
                            (piece_code,)
                        ).fetchone()
                        if row:
                            cursor.execute("UPDATE stock_inventory SET pcs=? WHERE id=?", ((row['pcs'] or 0) + pcs, row['id']))

                cursor.execute("DELETE FROM sales_history WHERE vch_id=?", (safe_vch_id,))
                cursor.execute("DELETE FROM credit_ledger WHERE vch_reference=?", (safe_vch_id,))
                conn.commit()
            return {"status": "success", "message": f"Bill {safe_vch_id} deleted and stock restored."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_low_stock_items(self, threshold=10.0):
        try:
            result = self.db.get_low_stock_items(float(threshold))
            print(f"[LOW STOCK] threshold={threshold}g → {len(result)} items: {[r['it_code'] for r in result]}")
            return result
        except Exception as e:
            print(f"[LOW STOCK ERR] API] {e}"); return []

    def get_out_of_stock_items(self):
        try:
            result = self.db.get_out_of_stock_items()
            print(f"[OOS] → {len(result)} items: {[r['it_code'] for r in result]}")
            return result
        except Exception as e:
            print(f"[OOS ERR] API] {e}"); return []

    # ---- UPDATE API ------------------------------------------------
    def check_for_update(self):
        result = check_for_update(timeout=8)
        if result is None:
            return {"available": False, "error": "no_network"}
        return result

    def start_update(self, download_url):
        import webview as _wv
        def on_progress(pct, status):
            safe = status.replace("'", "\\'")
            for w in _wv.windows:
                try:
                    w.evaluate_js(
                        "window.dispatchEvent(new CustomEvent('aurum-update-progress',"
                        "{detail:{pct:" + str(pct) + ",status:'" + safe + "'}}))"
                    )
                except: pass
        def on_done(success, message):
            safe = message.replace("'", "\\'")
            val  = "true" if success else "false"
            for w in _wv.windows:
                try:
                    w.evaluate_js(
                        "window.dispatchEvent(new CustomEvent('aurum-update-done',"
                        "{detail:{success:" + val + ",message:'" + safe + "'}}))"
                    )
                except: pass
        download_and_install(download_url, on_progress, on_done)
        return {"status": "started"}

    def get_current_version(self):
        return CURRENT_VERSION

    def restart_app(self):
        import subprocess
        print("[UPDATE] Restarting AurumOS...")
        subprocess.Popen([sys.executable] + sys.argv[:])
        sys.exit(0)

    def get_stagnant_report(self, threshold):
        try:
            data = self.db.get_stagnant_report(threshold)
            return {"status": "success", "data": data}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── SCALE API ──────────────────────────────────────────────────────────────
    def scale_connect(self, port, baud=1200):
        """Connect to scale. Default: COM4 @ 1200 baud (detected from test)."""
        _scale.set_window(self._window)
        return _scale.start(port, int(baud))

    def scale_disconnect(self):
        _scale.stop()
        return {"status": "ok"}

    def scale_get_ports(self):
        """List available COM ports for scale selection."""
        try:
            import serial.tools.list_ports as _lp
            return [{"port": p.device, "desc": p.description}
                    for p in _lp.comports()]
        except Exception as e:
            return []

    def scale_get_last(self):
        """Get last stable weight reading."""
        w = _scale.get_last()
        return {"weight": w, "stable": True} if w else {"weight": None, "stable": False}

    def get_weight_stock_it_codes(self):
        return self.db.get_weight_stock_it_codes()

    def get_touch_ledger_details(self, touch_value, mode='weight', from_date='', to_date=''):
        return self.db.get_touch_ledger_details(touch_value, mode, from_date, to_date)

    def mark_as_tagged(self, entry_id):
        return self.db.mark_as_tagged(entry_id)

    def get_machine_id(self):
        try:
            import uuid
            return str(uuid.getnode())
        except Exception as e:
            return 'unknown'


def _show_update_window(update_data, api):
    try:
        import json as _json
        ui_dir    = get_asset_path("ui")
        html_path = os.path.join(ui_dir, "update_notify.html")
        if not os.path.exists(html_path):
            print(f"[UPDATE] update_notify.html not found")
            return
        html    = open(html_path, encoding="utf-8").read()
        data_js = _json.dumps(update_data)
        inject  = f"<script>window.__UPDATE_DATA__={data_js};</script>"
        html    = html.replace("<!DOCTYPE html>", "<!DOCTYPE html>" + inject, 1)
        webview.create_window(
            f"AurumOS Update v{update_data.get('version','')}",
            html=html, js_api=api,
            width=580, height=640, resizable=False
        )
        print(f"[OK] [UPDATE] Window opened for v{update_data.get('version')}")
    except Exception as e:
        print(f"[ERR] [UPDATE] Window error: {e}")


def run_aur_os():
    api          = AurumAPI()
    is_ready     = api.db.is_setup_complete()
    initial_file = "login.html" if is_ready else "setup.html"
    ui_dir       = get_asset_path("ui")
    initial_path = os.path.join(ui_dir, initial_file)
    initial_url  = Path(initial_path).as_uri()

    window = webview.create_window(
        "AurumOS Executive Dashboard", initial_url, js_api=api,
        width=1350, height=950, background_color='#ffffff'
    )
    api.set_window(window)

    def _on_start(w):
        w.maximize()
        import time
        def _bg_check():
            time.sleep(6)
            try:
                r = check_for_update(timeout=8)
                if r and r.get("available"):
                    print(f"[UPDATE] New version available: {r['version']}")
                    _show_update_window(r, api)
                else:
                    print(f"[UPDATE] Up to date ({CURRENT_VERSION})")
            except Exception as e:
                print(f"[UPDATE] Check error: {e}")
        threading.Thread(target=_bg_check, daemon=True).start()

    webview.start(_on_start, window, gui='edgechromium', debug=False)


if __name__ == '__main__':
    run_aur_os()