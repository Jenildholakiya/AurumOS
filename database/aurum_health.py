# -*- coding: utf-8 -*-
"""
AurumOS Health Check — Internal Developer Tool
Run this on any client PC to diagnose all known issues.
Usage: python aurum_health.py
"""
import os, sys, sqlite3, hashlib, json, subprocess, datetime, platform

# ── CONFIG ─────────────────────────────────────────────────────────────────
TOOL_VERSION = "1.0.0"
LINE  = "-" * 60
DLINE = "=" * 60

# ── HELPERS ────────────────────────────────────────────────────────────────
def ok(msg):   print(f"  [OK]   {msg}")
def warn(msg): print(f"  [WARN] {msg}")
def fail(msg): print(f"  [FAIL] {msg}")
def info(msg): print(f"  [INFO] {msg}")
def head(msg): print(f"\n{DLINE}\n  {msg}\n{LINE}")

def _wmic(query):
    try:
        out = subprocess.check_output(
            'wmic ' + query + ' get /value',
            shell=True, timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000
        ).decode('ascii', errors='ignore')
        vals = [
            l.split('=',1)[1].strip()
            for l in out.splitlines()
            if '=' in l and l.split('=',1)[1].strip()
            and l.split('=',1)[1].strip() not in ('','None','To Be Filled By O.E.M.')
        ]
        return vals[0] if vals else '[NOT FOUND]'
    except Exception as e:
        return f'[ERROR: {e}]'

def get_db_path():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.abspath('.')
    # Walk up if inside dist/
    if os.path.basename(base).lower() in ('dist', 'aurumos'):
        base = os.path.dirname(base)
    return os.path.join(base, 'database', 'aurum_local.db')


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 1 — HARDWARE DNA
# ══════════════════════════════════════════════════════════════════════════════
def check_hardware_dna():
    head("CHECK 1: Hardware DNA (Machine Identity)")
    info("Problem: If any of 5 hardware parts change, DB will auto-wipe.")
    info("Use this to get fingerprint before replacing hardware.\n")

    import uuid
    mac      = str(uuid.getnode())
    cpu_id   = _wmic('cpu get ProcessorId')
    disk_id  = _wmic('diskdrive get SerialNumber')
    bios_id  = _wmic('bios get SerialNumber')
    board_id = _wmic('baseboard get SerialNumber')

    print(f"  MAC      : {mac}")
    print(f"  CPU ID   : {cpu_id}")
    print(f"  Disk SN  : {disk_id}")
    print(f"  BIOS SN  : {bios_id}")
    print(f"  Board SN : {board_id}")

    raw = '|'.join([mac, cpu_id, disk_id, bios_id, board_id])
    dna = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]
    lc  = dna[:8].upper()

    print(f"\n  FINGERPRINT : {dna}")
    print(f"  LOCK CODE   : {lc}")

    # Check if components are real (not placeholders)
    missing = []
    for name, val in [('CPU', cpu_id), ('Disk', disk_id), ('BIOS', bios_id), ('Board', board_id)]:
        if 'NOT FOUND' in val or 'ERROR' in val:
            missing.append(name)

    if missing:
        warn(f"Missing components: {', '.join(missing)} — fingerprint may be unstable")
        warn("These are often missing in VMs — DNA will differ on each boot")
    else:
        ok("All 5 hardware components found — DNA is stable")

    return dna, lc


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 2 — DATABASE INTEGRITY
# ══════════════════════════════════════════════════════════════════════════════
def check_database(dna):
    head("CHECK 2: Database Health")
    info("Problem: DB corruption, missing tables, or fingerprint mismatch.\n")

    db_path = get_db_path()
    print(f"  DB Path : {db_path}")

    # Existence
    if not os.path.exists(db_path):
        fail("DB file does NOT exist")
        info("Fix: Run AurumOS.exe and complete setup")
        return False
    ok("DB file exists")

    # Size
    size_kb = os.path.getsize(db_path) // 1024
    print(f"  DB Size : {size_kb} KB")
    if size_kb < 1:
        fail("DB is suspiciously small — may be corrupt")
        return False
    ok("DB size looks normal")

    # SQLite integrity
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] == 'ok':
            ok("SQLite integrity check PASSED")
        else:
            fail(f"Integrity check FAILED: {result[0]}")
            info("Fix: Restore from C:\\ProgramData\\AurumOS\\aurum_backup.db")
    except Exception as e:
        fail(f"Cannot open DB: {e}")
        return False

    # Required tables
    tables = [r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    required = ['stock_inventory','sales_history','katti_vouchers',
                'admin_creds','app_config','mac_lock']
    for t in required:
        if t in tables:
            ok(f"Table exists: {t}")
        else:
            fail(f"Table MISSING: {t}")

    # Row counts
    print()
    for t in ['stock_inventory','sales_history','katti_vouchers','katti_voucher_items']:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            info(f"  {t}: {n} rows")
        except: pass

    # Fingerprint check
    print()
    info("Checking stored fingerprint vs current hardware DNA...")
    try:
        rows = {r['key']: r['value'] for r in conn.execute(
            "SELECT key,value FROM app_config WHERE key IN ('setup_done','machine_fingerprint')"
        ).fetchall()}
        stored_fp = rows.get('machine_fingerprint', '')
        setup_done = rows.get('setup_done', '0')

        print(f"  Setup done     : {setup_done}")
        print(f"  Stored DNA     : {stored_fp}")
        print(f"  Current DNA    : {dna}")

        if stored_fp == dna:
            ok("Fingerprint MATCHES — this PC is authorized")
        elif not stored_fp:
            warn("No fingerprint stored — setup not completed or wiped")
        else:
            fail("Fingerprint MISMATCH — app will WIPE data on next launch")
            info("Fix: Add this DNA to allowed_machines.txt OR re-run setup on THIS PC")
    except Exception as e:
        warn(f"Could not read app_config: {e}")

    # Account lock status
    print()
    info("Checking account lock status...")
    try:
        lock_rows = {r['key']: r['value'] for r in conn.execute(
            "SELECT key,value FROM app_config WHERE key IN ('account_locked','login_attempts','locked_at')"
        ).fetchall()}
        locked   = lock_rows.get('account_locked', '0') == '1'
        attempts = lock_rows.get('login_attempts', '0')
        locked_at= lock_rows.get('locked_at', '')

        if locked:
            fail(f"Account is LOCKED (after {attempts} failed attempts at {locked_at})")
            dna_lc = dna[:8].upper()
            info(f"Unlock code to give client: {dna_lc}")
            # Generate today's unlock key
            SECRET_SALT = 'AurumOS@Jewel#2024$Prof'
            today = datetime.date.today().strftime('%Y-%m-%d')
            unlock = hashlib.sha256(
                (dna_lc + SECRET_SALT + today).encode('utf-8')
            ).hexdigest()[:12].upper()
            print(f"\n  *** TODAY'S UNLOCK KEY: {unlock} ***")
            print(f"  *** VALID FOR: {today} ONLY ***\n")
        else:
            ok(f"Account is UNLOCKED (failed attempts: {attempts})")
    except Exception as e:
        warn(f"Could not read lock status: {e}")

    conn.close()
    return True


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 3 — BACKUP STATUS
# ══════════════════════════════════════════════════════════════════════════════
def check_backup():
    head("CHECK 3: Stealth Backup")
    info("Problem: Backup missing or stale = data loss risk on crash.\n")

    backup_dir  = r"C:\ProgramData\AurumOS"
    backup_path = os.path.join(backup_dir, 'aurum_backup.db')
    db_path     = get_db_path()

    if not os.path.exists(backup_dir):
        fail("Backup directory does not exist")
        info("Fix: Run AurumOS.exe — it creates the dir on startup")
        return

    ok(f"Backup directory exists: {backup_dir}")

    # Hidden attribute
    try:
        result = subprocess.run(
            ['attrib', backup_dir],
            capture_output=True, text=True
        )
        if 'H' in result.stdout:
            ok("Backup directory is HIDDEN")
        else:
            warn("Backup directory is NOT hidden")
            info("Fix: Run: attrib +H \"C:\\ProgramData\\AurumOS\"")
    except: pass

    if not os.path.exists(backup_path):
        fail("Backup DB does NOT exist")
        info("Fix: Open AurumOS and perform any action — backup triggers automatically")
        return

    ok("Backup DB exists")

    # Age check
    mod_time = os.path.getmtime(backup_path)
    mod_dt   = datetime.datetime.fromtimestamp(mod_time)
    age_mins = (datetime.datetime.now() - mod_dt).total_seconds() / 60
    print(f"  Last backup : {mod_dt.strftime('%d %b %Y %I:%M %p')}")
    print(f"  Age         : {int(age_mins)} minutes ago")

    if age_mins < 60:
        ok("Backup is RECENT (< 1 hour old)")
    elif age_mins < 1440:
        warn(f"Backup is {int(age_mins//60)} hours old — make a sale to trigger fresh backup")
    else:
        fail(f"Backup is {int(age_mins//1440)} days old — very stale")

    # Size comparison
    if os.path.exists(db_path):
        live_kb   = os.path.getsize(db_path)     // 1024
        backup_kb = os.path.getsize(backup_path) // 1024
        print(f"  Live DB     : {live_kb} KB")
        print(f"  Backup DB   : {backup_kb} KB")
        diff = abs(live_kb - backup_kb)
        if diff < 10:
            ok("Backup size matches live DB")
        else:
            warn(f"Size difference: {diff} KB — backup may be slightly behind")


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 4 — SESSION TOKEN TRACES
# ══════════════════════════════════════════════════════════════════════════════
def check_session_token():
    head("CHECK 4: Session Token (Layer 10)")
    info("Problem: Stale token traces left from crash can block writes.\n")

    # Registry check
    try:
        import winreg
        REG_PATH = 'SOFTWARE\\Microsoft\\InputMethod\\AOS'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH)
            val, _ = winreg.QueryValueEx(key, 'SessionCache')
            winreg.CloseKey(key)
            warn(f"Stale registry token found: {val[:12]}...")
            info("Fix: This clears automatically on next AurumOS launch")
            info("     Or run this tool with --clear flag")
            # Offer to clear
            ans = input("\n  Clear stale registry token now? (y/n): ").strip().lower()
            if ans == 'y':
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
                    winreg.DeleteValue(key, 'SessionCache')
                    winreg.CloseKey(key)
                    ok("Registry token cleared")
                except Exception as e:
                    fail(f"Could not clear: {e}")
        except FileNotFoundError:
            ok("No stale registry token — clean state")
    except ImportError:
        info("winreg not available (non-Windows) — skipping registry check")

    # Temp files check
    import glob, tempfile
    tmp_dir   = os.environ.get('TEMP', tempfile.gettempdir())
    tmp_files = glob.glob(os.path.join(tmp_dir, '.~*.tmp'))
    if tmp_files:
        warn(f"Found {len(tmp_files)} AurumOS temp token file(s)")
        for f in tmp_files:
            age_s = (datetime.datetime.now() - datetime.datetime.fromtimestamp(
                os.path.getmtime(f))).total_seconds()
            info(f"  {f} — {int(age_s//60)} min old")
        info("These clear automatically when AurumOS closes normally")
        info("If AurumOS crashed, delete them manually")
    else:
        ok("No stale temp token files found")


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 5 — ALLOWED MACHINES WHITELIST
# ══════════════════════════════════════════════════════════════════════════════
def check_whitelist(dna):
    head("CHECK 5: Allowed Machines Whitelist")
    info("Problem: PC not in whitelist = locked out after hardware change.\n")

    db_path  = get_db_path()
    wl_path  = os.path.join(os.path.dirname(db_path), 'allowed_machines.txt')
    print(f"  Whitelist path : {wl_path}")

    if not os.path.exists(wl_path):
        warn("allowed_machines.txt does NOT exist")
        info("Fix: Create it and add DNA fingerprints (one per line)")
        print(f"\n  This PC's fingerprint to add:\n  {dna}\n")
        ans = input("  Add this PC to whitelist now? (y/n): ").strip().lower()
        if ans == 'y':
            try:
                with open(wl_path, 'w') as f:
                    f.write(dna + '\n')
                ok(f"Added to whitelist: {dna}")
            except Exception as e:
                fail(f"Could not write: {e}")
        return

    with open(wl_path, 'r') as f:
        entries = [l.strip() for l in f if l.strip()]

    ok(f"Whitelist exists with {len(entries)} entry(ies)")
    for entry in entries:
        marker = " ← THIS PC" if entry == dna else ""
        print(f"    {entry}{marker}")

    if dna in entries:
        ok("THIS PC is in the whitelist — hardware lock bypassed")
    else:
        warn("THIS PC is NOT in the whitelist")
        info("Fix: Add this DNA to allowed_machines.txt")
        print(f"  Add this line: {dna}")
        ans = input("\n  Add this PC to whitelist now? (y/n): ").strip().lower()
        if ans == 'y':
            try:
                with open(wl_path, 'a') as f:
                    f.write('\n' + dna)
                ok(f"Added: {dna}")
            except Exception as e:
                fail(f"Could not write: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 6 — LOG FILE
# ══════════════════════════════════════════════════════════════════════════════
def check_logs():
    head("CHECK 6: Log File")
    info("Problem: Log missing or too large = can't debug issues.\n")

    db_path  = get_db_path()
    base     = os.path.dirname(os.path.dirname(db_path))
    log_path = os.path.join(base, 'logs', 'aurumos.log')
    print(f"  Log path : {log_path}")

    if not os.path.exists(log_path):
        warn("Log file does not exist — AurumOS has not been run yet")
        return

    size_kb = os.path.getsize(log_path) // 1024
    print(f"  Log size : {size_kb} KB")

    if size_kb > 500:
        warn("Log file is large (>500KB) — will auto-clear on next launch")
    else:
        ok("Log size is fine")

    # Show last 10 lines
    print("\n  Last 10 log lines:")
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        for line in lines[-10:]:
            print(f"    {line.rstrip()}")
    except Exception as e:
        warn(f"Could not read log: {e}")

    # Check for recent errors
    error_lines = [l for l in lines[-50:] if 'ERROR' in l or 'FAIL' in l or 'WIPE' in l]
    if error_lines:
        print(f"\n  Recent errors ({len(error_lines)}):")
        for l in error_lines[-5:]:
            print(f"    {l.rstrip()}")
    else:
        ok("No recent errors in last 50 log lines")


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 7 — UNLOCK KEY GENERATOR
# ══════════════════════════════════════════════════════════════════════════════
def generate_unlock_key(lc):
    head("CHECK 7: Unlock Key Generator")
    info("Two types of keys:")
    info("  Regular (12 chars) — clears wrong-password lock")
    info("  BASTION  (16 chars) — clears BASTION security suspension\n")

    REGULAR_SALT = 'AurumOS@Jewel#2024$Prof'
    BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'
    today     = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    tomorrow  = today + datetime.timedelta(days=1)

    def make_regular(lock_code, date_str):
        lc = str(lock_code).strip().upper()[:8]
        return hashlib.sha256((lc + REGULAR_SALT + date_str).encode()).hexdigest()[:12].upper()

    def make_bastion(lock_code, date_str):
        lc = str(lock_code).strip().upper()[:8]
        return hashlib.sha256((lc + BASTION_SALT + date_str).encode()).hexdigest()[:16].upper()

    # Try to read lock_code from DB first (most reliable)
    db_lock_code = None
    try:
        db_path = get_db_path()
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM app_config WHERE key='lock_code_cache'"
            ).fetchone()
            if row and row['value']:
                db_lock_code = str(row['value']).strip().upper()
                info(f"Lock code from DB cache: {db_lock_code}")

            # Also check if account is BASTION suspended
            b_row = conn.execute(
                "SELECT value FROM app_config WHERE key='bastion_suspended'"
            ).fetchone()
            if b_row and b_row['value'] == '1':
                b_rec = conn.execute(
                    "SELECT value FROM app_config WHERE key='bastion_record'"
                ).fetchone()
                if b_rec and b_rec['value']:
                    import json as _j
                    try:
                        rec = _j.loads(b_rec['value'])
                        print()
                        warn(f"BASTION ACTIVE: {rec.get('title','Unknown')}")
                        warn(f"Reason: {rec.get('reason','')}")
                        warn(f"At: {rec.get('timestamp','')}")
                        info("Use BASTION admin key (16 chars) to clear this suspension")
                    except Exception: pass
            conn.close()
    except Exception as e:
        warn(f"Could not read DB: {e}")

    effective_lc = db_lock_code if db_lock_code else lc
    print(f"\n  Using lock code : {effective_lc}")

    print("\n  REGULAR UNLOCK KEY (12 chars) — for wrong-password lock:")
    print("  " + "-" * 46)
    for label, d in [("YESTERDAY", yesterday), ("TODAY    ", today), ("TOMORROW ", tomorrow)]:
        key = make_regular(effective_lc, d.strftime('%Y-%m-%d'))
        print(f"  {label} ({d})  ->  {key}")

    print("\n  BASTION ADMIN KEY (16 chars) — for BASTION suspension:")
    print("  " + "-" * 46)
    for label, d in [("YESTERDAY", yesterday), ("TODAY    ", today), ("TOMORROW ", tomorrow)]:
        key = make_bastion(effective_lc, d.strftime('%Y-%m-%d'))
        print(f"  {label} ({d})  ->  {key}")

    # Manual override
    print()
    custom = input(
        "  Enter lock code manually (from client screen) or Enter to skip: "
    ).strip().upper()

    if custom and len(custom) >= 6:
        lc_clean = custom[:8].upper()
        print(f"\n  Keys for manual lock code: {lc_clean}")
        print("  REGULAR:")
        for label, d in [("YESTERDAY", yesterday), ("TODAY    ", today), ("TOMORROW ", tomorrow)]:
            print(f"    {label} ({d})  ->  {make_regular(lc_clean, d.strftime('%Y-%m-%d'))}")
        print("  BASTION:")
        for label, d in [("YESTERDAY", yesterday), ("TODAY    ", today), ("TOMORROW ", tomorrow)]:
            print(f"    {label} ({d})  ->  {make_bastion(lc_clean, d.strftime('%Y-%m-%d'))}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print(DLINE)
    print(f"  AurumOS Internal Health Check  v{TOOL_VERSION}")
    print(f"  Run on : {platform.node()}")
    print(f"  Time   : {datetime.datetime.now().strftime('%d %b %Y %I:%M:%S %p')}")
    print(f"  Python : {sys.version.split()[0]}")
    print(DLINE)

    dna, lc = check_hardware_dna()
    check_database(dna)
    check_backup()
    check_session_token()
    check_whitelist(dna)
    check_logs()
    generate_unlock_key(lc)

    print(f"\n{DLINE}")
    print("  Health check complete.")
    print(f"  Fingerprint : {dna}")
    print(f"  Lock Code   : {lc}")
    print(DLINE)
    input("\n  Press Enter to exit...")

if __name__ == '__main__':
    main()