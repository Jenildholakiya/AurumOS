# -*- coding: utf-8 -*-
"""
sysconf_aurum.py
================
AurumOS Master Diagnostic & Repair Tool
DEVELOPER USE ONLY — DO NOT SHARE WITH CLIENTS

Place this file at:
  E:\AurumOs_Client\core\sysconf_aurum.py

Run with:
  python core\sysconf_aurum.py

What it does (in order):
  1.  Hardware DNA         — Shows all 5 hardware components + fingerprint
  2.  DB Integrity         — Checks if SQLite DB is healthy or corrupt
  3.  DB Fingerprint Lock  — Checks if DB is locked to correct machine
  4.  Account Lock Status  — Shows if account is locked + lock code
  5.  Session Token        — Checks all 3 token stores (RAM/Registry/File)
  6.  Login Log            — Shows last 5 logins with timestamps
  7.  Allowed Machines     — Lists all whitelisted machines
  8.  Stock Med Sessions   — Shows all audit/period-reset sessions
  9.  Audit Archive        — Count of archived records
  10. Backup Health        — Checks stealth backup file at ProgramData
  11. License Key          — Reads and validates cached license
  12. Scale Config         — Shows saved COM port + baud
  13. Update Version       — Shows current vs installed version
  14. Registry Session     — Reads session cache from Windows registry
  15. Temp Token File      — Finds and reads temp session file

REPAIRS available:
  R1. Force unlock account (reset login_attempts + account_locked)
  R2. Whitelist this machine (add DNA to allowed_machines.txt)
  R3. Clear session tokens (registry + temp files)
  R4. Reset DB fingerprint (re-lock DB to current machine)
  R5. Generate unlock key for client (from their lock code)
  R6. Restore DB from stealth backup
  R7. Clear stock med sessions (dangerous — confirm required)
"""

import os, sys, sqlite3, hashlib, subprocess, json, winreg
import uuid, shutil, tempfile
from datetime import datetime, date, timedelta

# ── CONFIG — update these if paths change ────────────────────────────────────
SECRET_SALT     = 'AurumOS@Jewel#2024$Prof'
SESSION_SALT    = 'AurumOS@Session@Jenil#9x7z@2026'
REG_PATH        = r'SOFTWARE\Microsoft\InputMethod\AOS'
BACKUP_DIR      = r'C:\ProgramData\AurumOS'
BACKUP_DB       = os.path.join(BACKUP_DIR, 'aurum_backup.db')
ALLOWED_FILE    = 'allowed_machines.txt'   # relative to database/
TOOL_VERSION    = '1.1.2'

# ── COLORS (Windows cmd supports ANSI in Win10+) ─────────────────────────────
R  = '\033[91m'   # red
G  = '\033[92m'   # green
Y  = '\033[93m'   # yellow
B  = '\033[94m'   # blue
M  = '\033[95m'   # magenta
C  = '\033[96m'   # cyan
W  = '\033[97m'   # white bold
X  = '\033[0m'    # reset
BOLD = '\033[1m'


def _enable_ansi():
    """Enable ANSI colors on Windows terminal."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        pass


def _banner():
    print(f"""
{M}{BOLD}{'=' * 62}
  AurumOS Master Diagnostic Tool  v{TOOL_VERSION}
  DEVELOPER USE ONLY
{'=' * 62}{X}
  Run Date : {datetime.now().strftime('%d %b %Y  %I:%M %p')}
  Machine  : {_get_hostname()}
{'=' * 62}
""")


def _get_hostname():
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return 'unknown'


def _get_db_path():
    """Find aurum_local.db relative to this script or EXE."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'database', 'aurum_local.db'),
        os.path.join(os.path.dirname(sys.executable), 'database', 'aurum_local.db'),
        os.path.join(os.path.abspath('.'), 'database', 'aurum_local.db'),
        r'E:\AurumOs_Client\database\aurum_local.db',
    ]
    for p in candidates:
        p = os.path.normpath(p)
        if os.path.exists(p):
            return p
    return os.path.normpath(candidates[0])


def _get_database_dir():
    return os.path.dirname(_get_db_path())


def _conn(db_path):
    c = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _ok(msg):   print(f"  {G}OK{X}    {msg}")
def _warn(msg): print(f"  {Y}WARN{X}  {msg}")
def _fail(msg): print(f"  {R}FAIL{X}  {msg}")
def _info(msg): print(f"  {C}INFO{X}  {msg}")
def _sep(title=''):
    if title:
        print(f"\n{B}{BOLD}── {title} {'─' * max(0, 50 - len(title))}{X}")
    else:
        print(f"  {B}{'─' * 56}{X}")


# ══════════════════════════════════════════════════════════════
# CHECK 1 — HARDWARE DNA
# ══════════════════════════════════════════════════════════════
def check_hardware_dna():
    _sep("1. Hardware DNA (Layer 4)")

    def _wmic(query):
        try:
            out = subprocess.check_output(
                'wmic ' + query + ' get /value',
                shell=True, timeout=5,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000
            ).decode('ascii', errors='ignore')
            vals = [
                l.split('=', 1)[1].strip()
                for l in out.splitlines()
                if '=' in l and l.split('=', 1)[1].strip()
                and l.split('=', 1)[1].strip() not in ('', 'None', 'To Be Filled By O.E.M.')
            ]
            return vals[0] if vals else '[NOT FOUND]'
        except Exception as e:
            return f'[ERROR: {e}]'

    mac      = str(uuid.getnode())
    cpu_id   = _wmic('cpu get ProcessorId')
    disk_id  = _wmic('diskdrive get SerialNumber')
    bios_id  = _wmic('bios get SerialNumber')
    board_id = _wmic('baseboard get SerialNumber')

    raw = '|'.join([mac, cpu_id, disk_id, bios_id, board_id])
    dna = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]

    print(f"  MAC Address  : {W}{mac}{X}")
    print(f"  CPU ID       : {W}{cpu_id}{X}")
    print(f"  Disk SN      : {W}{disk_id}{X}")
    print(f"  BIOS SN      : {W}{bios_id}{X}")
    print(f"  Board SN     : {W}{board_id}{X}")
    _sep()
    print(f"  {BOLD}FINGERPRINT  : {G}{dna}{X}")
    print(f"  {BOLD}LOCK CODE    : {M}{dna[:8].upper()}{X}  (show this on lock screen)")

    # Check for weak components
    missing = [n for n, v in [('CPU', cpu_id), ('Disk', disk_id), ('BIOS', bios_id), ('Board', board_id)]
               if '[' in v]
    if missing:
        _warn(f"Missing hardware IDs: {', '.join(missing)} — fingerprint uses MAC fallback for these")
    else:
        _ok("All 5 hardware components found — DNA is strong")

    return dna


# ══════════════════════════════════════════════════════════════
# CHECK 2 — DB INTEGRITY
# ══════════════════════════════════════════════════════════════
def check_db_integrity(db_path):
    _sep("2. DB Integrity")
    print(f"  Path  : {db_path}")

    if not os.path.exists(db_path):
        _fail("DB file NOT FOUND")
        return False

    size = os.path.getsize(db_path)
    print(f"  Size  : {size:,} bytes")

    if size < 1024:
        _fail("DB is suspiciously small — possibly corrupt or empty")
        return False

    try:
        with _conn(db_path) as c:
            result = c.execute("PRAGMA integrity_check").fetchone()
            if result[0] == 'ok':
                _ok("SQLite integrity check passed")
            else:
                _fail(f"Integrity check FAILED: {result[0]}")
                return False

            # Check WAL files
            for ext in ['-wal', '-shm']:
                p = db_path + ext
                if os.path.exists(p):
                    _info(f"WAL file exists: {p} ({os.path.getsize(p):,} bytes)")

            # Check key tables exist
            tables = [r['name'] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            required = ['stock_inventory', 'sales_history', 'katti_vouchers',
                        'admin_creds', 'app_config', 'mac_lock',
                        'stock_med_sessions', 'audit_archive']
            for t in required:
                if t in tables:
                    count = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    _ok(f"Table {t}: {count} rows")
                else:
                    _warn(f"Table {t}: MISSING")
        return True
    except Exception as e:
        _fail(f"DB connection error: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# CHECK 3 — DB FINGERPRINT LOCK
# ══════════════════════════════════════════════════════════════
def check_db_fingerprint(db_path, dna):
    _sep("3. DB Fingerprint Lock (Layer 4)")
    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT value FROM app_config WHERE key='machine_fingerprint'"
            ).fetchone()
            stored = row['value'] if row else ''

            ml = c.execute("SELECT fingerprint, hostname FROM mac_lock WHERE id=1").fetchone()
            ml_fp = ml['fingerprint'] if ml else ''
            ml_host = ml['hostname'] if ml else ''

        if not stored:
            _warn("No fingerprint stored in DB — setup not completed or fingerprint cleared")
        elif stored == dna:
            _ok(f"DB fingerprint MATCHES this machine: {stored[:12]}...")
        else:
            _fail(f"MISMATCH — DB fingerprint: {stored[:12]}...")
            _fail(f"           This machine  : {dna[:12]}...")
            _warn("If client reports DB wipe on new PC — this is why")

        if ml_fp:
            if ml_fp == dna:
                _ok(f"mac_lock table matches — hostname: {ml_host}")
            else:
                _warn(f"mac_lock mismatch: {ml_fp[:12]}... (hostname: {ml_host})")

        return stored
    except Exception as e:
        _fail(f"Fingerprint check error: {e}")
        return ''


# ══════════════════════════════════════════════════════════════
# CHECK 4 — ACCOUNT LOCK STATUS
# ══════════════════════════════════════════════════════════════
def check_account_lock(db_path, dna):
    _sep("4. Account Lock Status (Layer 2+3)")
    try:
        with _conn(db_path) as c:
            rows = {r['key']: r['value'] for r in c.execute(
                "SELECT key,value FROM app_config WHERE key IN "
                "('account_locked','login_attempts','locked_at')"
            ).fetchall()}

        locked   = rows.get('account_locked', '0') == '1'
        attempts = int(rows.get('login_attempts', '0'))
        locked_at = rows.get('locked_at', '')
        lock_code = dna[:8].upper()

        print(f"  Locked        : {R + 'YES' + X if locked else G + 'NO' + X}")
        print(f"  Attempts      : {attempts}/3")
        print(f"  Locked At     : {locked_at or 'N/A'}")
        print(f"  Lock Code     : {M}{lock_code}{X}")

        if locked:
            _fail("Account is PERMANENTLY LOCKED — client needs unlock key")
            unlock = _generate_unlock_key(lock_code)
            print(f"\n  {BOLD}{G}TODAY's Unlock Key : {unlock}{X}")
            tmr = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
            unlock_tmr = hashlib.sha256(
                (lock_code + SECRET_SALT + tmr).encode()
            ).hexdigest()[:12].upper()
            print(f"  {BOLD}TOMORROW's Key     : {unlock_tmr}{X}")
        elif attempts > 0:
            _warn(f"{attempts} failed attempt(s) recorded — {3 - attempts} remaining before lock")
        else:
            _ok("Account is unlocked and clean")

        return locked
    except Exception as e:
        _fail(f"Account lock check error: {e}")
        return False


def _generate_unlock_key(lock_code):
    today = date.today().strftime('%Y-%m-%d')
    return hashlib.sha256(
        (lock_code.upper() + SECRET_SALT + today).encode('utf-8')
    ).hexdigest()[:12].upper()


# ══════════════════════════════════════════════════════════════
# CHECK 5 — SESSION TOKEN (Layer 10)
# ══════════════════════════════════════════════════════════════
def check_session_token():
    _sep("5. Session Token — Kerberos Layer (Layer 10)")
    _info("Session token only exists while AurumOS.exe is running")
    _info("If app is closed, all 3 stores should be empty/missing")

    # Registry
    reg_token = None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        reg_token, _ = winreg.QueryValueEx(key, 'SessionCache')
        winreg.CloseKey(key)
        if reg_token:
            _ok(f"Registry token found: {reg_token[:12]}...")
        else:
            _info("Registry key exists but token is empty (app closed cleanly)")
    except FileNotFoundError:
        _ok("Registry key not found — app is closed (expected)")
    except Exception as e:
        _warn(f"Registry read error: {e}")

    # Temp files
    temp_dir = os.environ.get('TEMP', os.getcwd())
    token_files = [
        f for f in os.listdir(temp_dir)
        if f.startswith('.~') and f.endswith('.tmp')
    ]
    if token_files:
        for tf in token_files:
            full = os.path.join(temp_dir, tf)
            try:
                content = open(full, 'r').read().strip()
                if content:
                    _ok(f"Temp token file: {tf} → {content[:12]}...")
                    if reg_token and content == reg_token:
                        _ok("Registry + File tokens MATCH — session is valid")
                    elif reg_token:
                        _fail("Registry + File tokens DO NOT MATCH — session compromised")
                else:
                    _info(f"Temp file empty: {tf}")
            except Exception:
                _info(f"Cannot read temp file: {tf}")
    else:
        _ok("No temp token files — app is closed (expected)")

    if not reg_token and not token_files:
        _ok("Session completely clean — no active session")


# ══════════════════════════════════════════════════════════════
# CHECK 6 — LOGIN LOG
# ══════════════════════════════════════════════════════════════
def check_login_log(db_path):
    _sep("6. Login Log — Last 5 Logins")
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT username, role, login_time FROM login_log ORDER BY id DESC LIMIT 5"
            ).fetchall()
        if not rows:
            _warn("No login records found")
            return
        for r in rows:
            role_color = G if r['role'] == 'admin' else Y
            print(f"  {r['login_time']}  {role_color}{r['role']:8}{X}  {W}{r['username']}{X}")
    except Exception as e:
        _fail(f"Login log error: {e}")


# ══════════════════════════════════════════════════════════════
# CHECK 7 — ALLOWED MACHINES WHITELIST
# ══════════════════════════════════════════════════════════════
def check_allowed_machines(db_dir, dna):
    _sep("7. Allowed Machines Whitelist")
    fp_file = os.path.join(db_dir, ALLOWED_FILE)
    if not os.path.exists(fp_file):
        _warn(f"allowed_machines.txt not found at: {fp_file}")
        _info("Create this file to whitelist machines that bypass DNA check")
        return set()
    try:
        with open(fp_file, 'r') as f:
            machines = set(line.strip() for line in f if line.strip())
        print(f"  File: {fp_file}")
        print(f"  Whitelisted machines: {len(machines)}")
        for m in machines:
            mark = G + ' ← THIS MACHINE' + X if m == dna else ''
            print(f"    {C}{m}{X}{mark}")
        if dna in machines:
            _ok("This machine IS whitelisted")
        else:
            _info("This machine is NOT whitelisted — uses normal DNA lock")
        return machines
    except Exception as e:
        _fail(f"Whitelist read error: {e}")
        return set()


# ══════════════════════════════════════════════════════════════
# CHECK 8 — STOCK MED SESSIONS
# ══════════════════════════════════════════════════════════════
def check_stock_med(db_path):
    _sep("8. Stock Med Sessions (Audit Trail)")
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, med_date, signed_by, period_from, period_to, status, created_at "
                "FROM stock_med_sessions ORDER BY id DESC LIMIT 5"
            ).fetchall()
            archive_count = c.execute(
                "SELECT COUNT(*) FROM audit_archive"
            ).fetchone()[0]

        if not rows:
            _info("No stock med sessions found — audit never run")
        else:
            print(f"  Last {len(rows)} session(s):")
            for r in rows:
                print(f"    #{r['id']}  {r['med_date']}  by {W}{r['signed_by']}{X}  "
                      f"({r['period_from']} → {r['period_to']})  {G}{r['status']}{X}")
        _ok(f"Audit archive: {archive_count:,} total archived records")
    except Exception as e:
        _fail(f"Stock med check error: {e}")


# ══════════════════════════════════════════════════════════════
# CHECK 9 — STEALTH BACKUP HEALTH (Layer 8)
# ══════════════════════════════════════════════════════════════
def check_backup_health(db_path):
    _sep("9. Stealth Backup Health (Layer 8)")
    print(f"  Backup path : {BACKUP_DIR}")

    if not os.path.exists(BACKUP_DIR):
        _fail("Backup directory does NOT exist — backup never ran or was deleted")
        return

    if not os.path.exists(BACKUP_DB):
        _warn("Backup DB file not found — backup may not have run yet")
        return

    main_size   = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    backup_size = os.path.getsize(BACKUP_DB)
    main_mtime  = datetime.fromtimestamp(os.path.getmtime(db_path)).strftime('%d %b %Y %I:%M %p') if os.path.exists(db_path) else 'N/A'
    back_mtime  = datetime.fromtimestamp(os.path.getmtime(BACKUP_DB)).strftime('%d %b %Y %I:%M %p')

    print(f"  Main DB     : {main_size:,} bytes  (modified: {main_mtime})")
    print(f"  Backup DB   : {backup_size:,} bytes  (modified: {back_mtime})")

    diff_pct = abs(main_size - backup_size) / max(main_size, 1) * 100
    if diff_pct < 5:
        _ok(f"Backup is fresh — size diff: {diff_pct:.1f}%")
    elif diff_pct < 20:
        _warn(f"Backup may be slightly stale — size diff: {diff_pct:.1f}%")
    else:
        _warn(f"Large size difference: {diff_pct:.1f}% — backup may be old")

    # Check if hidden
    try:
        result = subprocess.run(['attrib', BACKUP_DIR], capture_output=True, text=True)
        if 'H' in result.stdout:
            _ok("Backup directory is HIDDEN from Windows Explorer")
        else:
            _warn("Backup directory is NOT hidden — run attrib +H to hide it")
    except Exception:
        pass

    # Verify backup integrity
    try:
        with sqlite3.connect(BACKUP_DB, timeout=5) as bc:
            result = bc.execute("PRAGMA integrity_check").fetchone()
            if result[0] == 'ok':
                _ok("Backup DB integrity: OK")
            else:
                _fail(f"Backup DB corrupt: {result[0]}")
    except Exception as e:
        _fail(f"Backup DB unreadable: {e}")


# ══════════════════════════════════════════════════════════════
# CHECK 10 — LICENSE KEY
# ══════════════════════════════════════════════════════════════
def check_license(db_dir, db_path):
    _sep("10. License Key")
    key_path = os.path.join(db_dir, '.license_key')

    if not os.path.exists(key_path):
        _warn("No .license_key file found")
    else:
        try:
            raw = open(key_path, 'rb').read()
            _info(f"License file: {len(raw)} bytes")
            if raw.startswith(b'\xAA\x01'):
                _ok("License file has correct AurumOS header")
            else:
                _warn("License file header mismatch")
        except Exception as e:
            _fail(f"License file read error: {e}")

    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT value FROM app_config WHERE key='license_key'"
            ).fetchone()
            db_key = row['value'] if row else ''
        if db_key:
            _ok(f"DB license key: {db_key[:10]}...")
        else:
            _warn("No license key in DB")
    except Exception as e:
        _fail(f"DB license check: {e}")

    revoked_path = os.path.join(db_dir, '.revoked')
    if os.path.exists(revoked_path):
        reason = open(revoked_path).read().strip()
        _fail(f"REVOKED flag found — reason: {reason}")
        _info("Delete .revoked file to allow retry")
    else:
        _ok("No .revoked flag — license not blocked locally")


# ══════════════════════════════════════════════════════════════
# CHECK 11 — SCALE CONFIG
# ══════════════════════════════════════════════════════════════
def check_scale_config(db_path):
    _sep("11. Scale Configuration")
    try:
        with _conn(db_path) as c:
            rows = {r['key']: r['value'] for r in c.execute(
                "SELECT key,value FROM app_config WHERE key IN ('scale_port','scale_baud')"
            ).fetchall()}
        port = rows.get('scale_port', '')
        baud = rows.get('scale_baud', '')
        if port:
            _ok(f"Scale port: {W}{port}{X}  baud: {W}{baud}{X}")
        else:
            _info("No scale port saved — client hasn't connected a scale")
    except Exception as e:
        _fail(f"Scale config check: {e}")


# ══════════════════════════════════════════════════════════════
# CHECK 12 — VERSION
# ══════════════════════════════════════════════════════════════
def check_version():
    _sep("12. Version Status")
    # Look for version.json and updater.py
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'version.json'),
        os.path.join(os.path.abspath('.'), 'version.json'),
        r'E:\AurumOs_Client\version.json',
    ]
    for p in candidates:
        p = os.path.normpath(p)
        if os.path.exists(p):
            try:
                v = json.load(open(p, encoding='utf-8'))
                _ok(f"version.json: v{v.get('version', '?')} ({v.get('release_date', '?')})")
                _info(f"Download URL: {v.get('download_url', 'N/A')}")
            except Exception as e:
                _warn(f"version.json read error: {e}")
            break
    else:
        _warn("version.json not found in expected locations")

    # Check updater.py
    updater_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'updater.py'),
        os.path.join(os.path.abspath('.'), 'updater.py'),
        r'E:\AurumOs_Client\updater.py',
    ]
    for p in updater_candidates:
        p = os.path.normpath(p)
        if os.path.exists(p):
            import re
            content = open(p, encoding='utf-8').read()
            m = re.search(r"CURRENT_VERSION\s*=\s*['\"]([^'\"]+)['\"]", content)
            if m:
                _ok(f"updater.py CURRENT_VERSION: v{m.group(1)}")
            break


# ══════════════════════════════════════════════════════════════
# REPAIRS
# ══════════════════════════════════════════════════════════════
def repair_menu(db_path, db_dir, dna):
    print(f"""
{BOLD}{Y}═══ REPAIR OPTIONS ══════════════════════════════════════{X}
  {W}R1{X}  Force unlock account (clear login_attempts + lock)
  {W}R2{X}  Whitelist this machine in allowed_machines.txt
  {W}R3{X}  Clear session tokens (registry + temp files)
  {W}R4{X}  Reset DB fingerprint to this machine
  {W}R5{X}  Generate unlock key for a client lock code
  {W}R6{X}  Restore DB from stealth backup
  {W}R7{X}  Delete .revoked flag (re-enable license check)
  {W}R8{X}  Show full DB config dump
  {W}Q{X}   Quit
{Y}═════════════════════════════════════════════════════════{X}""")

    while True:
        choice = input(f"\n{W}Enter option: {X}").strip().upper()

        if choice == 'R1':
            _repair_unlock_account(db_path)
        elif choice == 'R2':
            _repair_whitelist_machine(db_dir, dna)
        elif choice == 'R3':
            _repair_clear_session()
        elif choice == 'R4':
            _repair_reset_fingerprint(db_path, dna)
        elif choice == 'R5':
            _repair_generate_unlock_key()
        elif choice == 'R6':
            _repair_restore_backup(db_path)
        elif choice == 'R7':
            _repair_clear_revoked(db_dir)
        elif choice == 'R8':
            _repair_dump_config(db_path)
        elif choice in ('Q', 'QUIT', 'EXIT'):
            print(f"\n{G}Done. Exiting.{X}\n")
            break
        else:
            _warn("Unknown option")


def _repair_unlock_account(db_path):
    print(f"\n  {Y}Unlocking account...{X}")
    try:
        with sqlite3.connect(db_path) as c:
            c.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','0')")
            c.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('login_attempts','0')")
            c.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('locked_at','')")
            c.commit()
        _ok("Account unlocked — login_attempts=0, account_locked=0")
    except Exception as e:
        _fail(f"Unlock failed: {e}")


def _repair_whitelist_machine(db_dir, dna):
    fp_file = os.path.join(db_dir, ALLOWED_FILE)
    try:
        existing = set()
        if os.path.exists(fp_file):
            with open(fp_file, 'r') as f:
                existing = set(line.strip() for line in f if line.strip())
        if dna in existing:
            _ok(f"Already whitelisted: {dna[:12]}...")
            return
        existing.add(dna)
        with open(fp_file, 'w') as f:
            f.write('\n'.join(sorted(existing)) + '\n')
        _ok(f"Added to whitelist: {dna[:12]}...")
        _ok(f"File: {fp_file}")
    except Exception as e:
        _fail(f"Whitelist error: {e}")


def _repair_clear_session():
    print(f"\n  {Y}Clearing session tokens...{X}")
    # Registry
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, 'SessionCache')
        winreg.CloseKey(key)
        _ok("Registry session token cleared")
    except FileNotFoundError:
        _info("Registry token already absent")
    except Exception as e:
        _warn(f"Registry clear error: {e}")
    # Temp files
    temp_dir = os.environ.get('TEMP', os.getcwd())
    cleared = 0
    for f in os.listdir(temp_dir):
        if f.startswith('.~') and f.endswith('.tmp'):
            try:
                os.remove(os.path.join(temp_dir, f))
                cleared += 1
            except Exception:
                pass
    if cleared:
        _ok(f"Removed {cleared} temp token file(s)")
    else:
        _info("No temp token files found")


def _repair_reset_fingerprint(db_path, dna):
    confirm = input(f"\n  {R}WARNING: Re-locking DB to this machine. Type YES to confirm: {X}").strip()
    if confirm != 'YES':
        _info("Cancelled")
        return
    try:
        import socket
        hostname = socket.gethostname()
        with sqlite3.connect(db_path) as c:
            c.execute(
                "INSERT OR REPLACE INTO app_config(key,value) VALUES('machine_fingerprint',?)",
                (dna,)
            )
            c.execute(
                "INSERT OR REPLACE INTO app_config(key,value) VALUES('setup_done','1')"
            )
            c.execute(
                "INSERT OR REPLACE INTO mac_lock(id,fingerprint,locked_at,hostname) "
                "VALUES(1,?,datetime('now'),?)",
                (dna, hostname)
            )
            c.commit()
        _ok(f"DB fingerprint reset to this machine: {dna[:12]}...")
    except Exception as e:
        _fail(f"Fingerprint reset error: {e}")


def _repair_generate_unlock_key():
    lock_code = input(f"\n  {W}Enter client lock code (8 chars from screen): {X}").strip().upper()
    if not lock_code:
        _warn("No lock code entered")
        return
    today = date.today().strftime('%Y-%m-%d')
    tmr   = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    key_today = hashlib.sha256(
        (lock_code + SECRET_SALT + today).encode()
    ).hexdigest()[:12].upper()
    key_tmr = hashlib.sha256(
        (lock_code + SECRET_SALT + tmr).encode()
    ).hexdigest()[:12].upper()
    print(f"\n  {BOLD}{G}TODAY    ({today}) : {key_today}{X}")
    print(f"  {BOLD}{Y}TOMORROW ({tmr}) : {key_tmr}{X}")
    print(f"\n  Send today's key to client via WhatsApp.")
    print(f"  Key expires at midnight.")


def _repair_restore_backup(db_path):
    if not os.path.exists(BACKUP_DB):
        _fail("No backup found at " + BACKUP_DB)
        return
    confirm = input(f"\n  {R}WARNING: This will overwrite your current DB. Type YES to confirm: {X}").strip()
    if confirm != 'YES':
        _info("Cancelled")
        return
    try:
        for ext in ['', '-wal', '-shm']:
            p = db_path + ext
            if os.path.exists(p):
                os.remove(p)
        for ext in ['', '-wal', '-shm']:
            src = BACKUP_DB + ext
            dst = db_path + ext
            if os.path.exists(src):
                shutil.copy2(src, dst)
        _ok("DB restored from backup successfully")
    except Exception as e:
        _fail(f"Restore failed: {e}")


def _repair_clear_revoked(db_dir):
    revoked_path = os.path.join(db_dir, '.revoked')
    if os.path.exists(revoked_path):
        os.remove(revoked_path)
        _ok(".revoked flag deleted — license check will retry on next launch")
    else:
        _info("No .revoked flag found — nothing to clear")


def _repair_dump_config(db_path):
    print(f"\n  {C}app_config dump:{X}")
    try:
        with _conn(db_path) as c:
            rows = c.execute("SELECT key,value FROM app_config ORDER BY key").fetchall()
        HIDE_KEYS = {'owner_pin', 'temp_password_hash'}
        for r in rows:
            val = '***HIDDEN***' if r['key'] in HIDE_KEYS else r['value']
            print(f"    {Y}{r['key']:30}{X} = {W}{val}{X}")
    except Exception as e:
        _fail(f"Config dump error: {e}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    _enable_ansi()
    _banner()

    db_path = _get_db_path()
    db_dir  = _get_database_dir()

    print(f"  {C}DB Path:{X} {db_path}")
    print(f"  {C}DB Dir: {X} {db_dir}\n")

    # Run all checks
    dna = check_hardware_dna()
    db_ok = check_db_integrity(db_path)

    if db_ok:
        check_db_fingerprint(db_path, dna)
        check_account_lock(db_path, dna)
        check_session_token()
        check_login_log(db_path)
        check_allowed_machines(db_dir, dna)
        check_stock_med(db_path)
        check_backup_health(db_path)
        check_license(db_dir, db_path)
        check_scale_config(db_path)
        check_version()
    else:
        _fail("DB integrity failed — skipping further checks")
        _info("Run R6 to restore from backup")

    # Repair menu
    print(f"\n{G}All checks complete.{X}")
    go = input(f"\n{W}Open repair menu? (Y/N): {X}").strip().upper()
    if go == 'Y':
        repair_menu(db_path, db_dir, dna)


if __name__ == '__main__':
    main()
    input(f"\n{C}Press Enter to close...{X}")