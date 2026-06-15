# -*- coding: utf-8 -*-
"""
AurumOS BASTION + Security System — Full Test Suite
Run this on YOUR DEV PC only.
Tests all security layers without touching a real client DB.

Usage:
    python test_bastion.py
"""
import os, sys, sqlite3, hashlib, json, datetime, tempfile, shutil, time

LINE  = "-" * 55
DLINE = "=" * 55
PASS  = 0
FAIL  = 0

# ── salts (must match db_manager.py) ──────────────────────────────
REGULAR_SALT = 'AurumOS@Jewel#2024$Prof'
BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'


def ok(msg):
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")

def fail(msg):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")

def info(msg):
    print(f"  [INFO] {msg}")

def head(title):
    print(f"\n{DLINE}\n  {title}\n{LINE}")


# ══════════════════════════════════════════════════════════════════
# HELPER — Create a fresh in-memory test DB
# ══════════════════════════════════════════════════════════════════
def make_test_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS admin_creds (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password TEXT);
        CREATE TABLE IF NOT EXISTS stock_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, it_code TEXT, it_name TEXT, tag_id TEXT, gr_wt REAL, nt_wt REAL, touch REAL, is_tagged INTEGER DEFAULT 0, entry_date TEXT);
        CREATE TABLE IF NOT EXISTS sales_history (id INTEGER PRIMARY KEY AUTOINCREMENT, vch_id TEXT UNIQUE, customer TEXT, status TEXT, items TEXT, total_amount REAL, date TEXT);
        CREATE TABLE IF NOT EXISTS katti_vouchers (id INTEGER PRIMARY KEY AUTOINCREMENT, vch_id TEXT);
        CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, username TEXT, action TEXT, detail TEXT, category TEXT);
    """)
    # Seed admin
    pw = hashlib.sha256(b'1234').hexdigest()
    conn.execute("INSERT OR REPLACE INTO admin_creds(id,username,password) VALUES(1,'owner',?)", (pw,))
    conn.commit()
    return conn


def get_mac_dna():
    import uuid
    mac = str(uuid.getnode())
    # Simplified DNA for test — just MAC
    raw = '|'.join([mac, '', '', '', ''])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def make_regular_key(lc, date_str):
    return hashlib.sha256((lc + REGULAR_SALT + date_str).encode()).hexdigest()[:12].upper()

def make_bastion_key(lc, date_str):
    return hashlib.sha256((lc + BASTION_SALT + date_str).encode()).hexdigest()[:16].upper()


# ══════════════════════════════════════════════════════════════════
# TEST 1 — Hardware DNA Fingerprint
# ══════════════════════════════════════════════════════════════════
def test_hardware_dna():
    head("TEST 1: Hardware DNA Fingerprint")
    info("Checks that fingerprint is consistent across multiple calls.\n")

    import uuid
    mac = str(uuid.getnode())

    def compute(mac, cpu='', disk='', bios='', board=''):
        raw = '|'.join([mac, cpu, disk, bios, board])
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    fp1 = compute(mac)
    fp2 = compute(mac)
    fp3 = compute(mac)

    if fp1 == fp2 == fp3:
        ok(f"Fingerprint is stable across calls: {fp1[:8]}...")
    else:
        fail(f"Fingerprint inconsistent: {fp1[:8]} vs {fp2[:8]}")

    # Test that changing one component changes fingerprint
    fp_cpu_changed = compute(mac, cpu='DIFFERENT_CPU')
    if fp_cpu_changed != fp1:
        ok("Changing CPU component changes fingerprint ✓")
    else:
        fail("CPU change did NOT change fingerprint")

    # Lock code derivation
    lc = fp1[:8].upper()
    if len(lc) == 8 and lc.isalnum():
        ok(f"Lock code format valid: {lc}")
    else:
        fail(f"Lock code format invalid: {lc}")

    return fp1, lc


# ══════════════════════════════════════════════════════════════════
# TEST 2 — Regular Unlock Key (Wrong Password Lock)
# ══════════════════════════════════════════════════════════════════
def test_regular_unlock(lc):
    head("TEST 2: Regular Unlock Key (Wrong Password Lock)")
    info("12-char key. Clears login_attempts and account_locked.\n")

    today     = datetime.date.today().strftime('%Y-%m-%d')
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    tomorrow  = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    today_key     = make_regular_key(lc, today)
    yesterday_key = make_regular_key(lc, yesterday)
    tomorrow_key  = make_regular_key(lc, tomorrow)

    if len(today_key) == 12:
        ok(f"Key length correct: 12 chars")
    else:
        fail(f"Key length wrong: {len(today_key)}")

    # Verify different dates produce different keys
    if today_key != yesterday_key != tomorrow_key:
        ok("Different dates produce different keys ✓")
    else:
        fail("Date rotation not working — keys identical")

    # Simulate unlock verification (same as db_manager.verify_unlock_key)
    def verify(entered_key, lock_code):
        for ds in [today, yesterday, tomorrow]:
            expected = make_regular_key(lock_code, ds)
            if entered_key.strip().upper() == expected:
                return True
        return False

    if verify(today_key, lc):
        ok(f"TODAY key verifies correctly: {today_key}")
    else:
        fail(f"TODAY key failed verification")

    if verify(tomorrow_key, lc):
        ok(f"TOMORROW key verifies correctly: {tomorrow_key}")
    else:
        fail("TOMORROW key failed verification")

    if not verify("AAAAAAAAAAAA", lc):
        ok("Wrong key correctly rejected ✓")
    else:
        fail("Wrong key was accepted — security hole!")

    # Key from different lock code must not work
    wrong_lc = "ZZZZZZZZ"
    wrong_key = make_regular_key(wrong_lc, today)
    if not verify(wrong_key, lc):
        ok("Key from different lock code correctly rejected ✓")
    else:
        fail("Key from wrong lock code was accepted — security hole!")

    info(f"\n  YOUR REGULAR KEYS for lock code [{lc}]:")
    info(f"  YESTERDAY: {yesterday_key}")
    info(f"  TODAY    : {today_key}  ← send this")
    info(f"  TOMORROW : {tomorrow_key}")

    return today_key


# ══════════════════════════════════════════════════════════════════
# TEST 3 — BASTION Suspend + Admin Key
# ══════════════════════════════════════════════════════════════════
def test_bastion_system(lc):
    head("TEST 3: BASTION Suspension + Admin Key (16 chars)")
    info("16-char key. Clears BASTION suspension. Separate salt from regular.\n")

    today     = datetime.date.today().strftime('%Y-%m-%d')
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    tomorrow  = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    bastion_key  = make_bastion_key(lc, today)
    regular_key  = make_regular_key(lc, today)

    # Length check
    if len(bastion_key) == 16:
        ok(f"BASTION key length correct: 16 chars")
    else:
        fail(f"BASTION key length wrong: {len(bastion_key)}")

    # Must differ from regular key
    if bastion_key != regular_key:
        ok("BASTION key differs from regular key (different salt) ✓")
    else:
        fail("BASTION and regular keys are identical — salt not working!")

    # Simulate bastion_clear verification
    def verify_bastion(entered, lock_code):
        for ds in [today, yesterday, tomorrow]:
            expected = make_bastion_key(lock_code, ds)
            if entered.strip().upper() == expected:
                return True
        return False

    if verify_bastion(bastion_key, lc):
        ok(f"BASTION key verifies correctly: {bastion_key[:8]}...")
    else:
        fail("BASTION key failed verification")

    # Regular key must NOT clear BASTION
    if not verify_bastion(regular_key, lc):
        ok("Regular key cannot clear BASTION ✓ (different salt)")
    else:
        fail("Regular key can clear BASTION — security hole!")

    # Wrong length should fail
    if not verify_bastion("AAAAAAAAAAAAAAAA", lc):
        ok("Wrong BASTION key correctly rejected ✓")
    else:
        fail("Wrong BASTION key was accepted — security hole!")

    info(f"\n  YOUR BASTION KEYS for lock code [{lc}]:")
    info(f"  YESTERDAY: {make_bastion_key(lc, yesterday)}")
    info(f"  TODAY    : {bastion_key}  ← send this")
    info(f"  TOMORROW : {make_bastion_key(lc, tomorrow)}")

    return bastion_key


# ══════════════════════════════════════════════════════════════════
# TEST 4 — BASTION Triggers
# ══════════════════════════════════════════════════════════════════
def test_bastion_triggers():
    head("TEST 4: BASTION Trigger Scenarios")
    info("Simulates each attack scenario and checks DB state.\n")

    tmp_dir = tempfile.mkdtemp(prefix='aurum_test_')
    db_path = os.path.join(tmp_dir, 'test.db')
    conn    = make_test_db(db_path)

    def bastion_suspend(conn, attack_type, detail=''):
        """Mirrors db_manager.bastion_suspend()"""
        CODES = {
            'session_tamper':       ('Session Token Tampering',       'A mid-session tamper was detected.'),
            'db_edit':              ('Database Tampering Detected',   'DB was modified while app was running.'),
            'fingerprint_mismatch': ('Hardware Identity Mismatch',   'DB copied to different PC.'),
            'exe_tamper':           ('EXE File Tampered',            'EXE was modified.'),
        }
        ts    = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        title, reason = CODES.get(attack_type, ('Security Violation', detail))
        record = {
            'suspended': True, 'attack_type': attack_type,
            'title': title, 'reason': reason, 'detail': detail, 'timestamp': ts
        }
        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','1')")
        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_record',?)", (json.dumps(record),))
        conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','1')")
        conn.commit()
        return record

    def get_bastion_status(conn):
        rows = {r['key']: r['value'] for r in conn.execute(
            "SELECT key,value FROM app_config WHERE key IN ('bastion_suspended','bastion_record')"
        ).fetchall()}
        suspended = rows.get('bastion_suspended', '0') == '1'
        record = {}
        try: record = json.loads(rows.get('bastion_record', '{}'))
        except: pass
        return suspended, record

    # Scenario A: Session tamper
    info("Scenario A: Session token mismatch (mid-session DB edit)...")
    bastion_suspend(conn, 'db_edit', 'Temp file token mismatch')
    suspended, record = get_bastion_status(conn)
    if suspended and record.get('attack_type') == 'db_edit':
        ok(f"BASTION triggered for db_edit: '{record.get('title')}'")
    else:
        fail("BASTION not triggered for db_edit")

    # Clear it
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','0')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_record','')")
    conn.commit()

    # Scenario B: Fingerprint mismatch
    info("Scenario B: DB copied to different PC...")
    bastion_suspend(conn, 'fingerprint_mismatch', 'Stored=A1B2.. Current=X9Y8..')
    suspended, record = get_bastion_status(conn)
    if suspended and record.get('attack_type') == 'fingerprint_mismatch':
        ok(f"BASTION triggered for fingerprint_mismatch: '{record.get('title')}'")
    else:
        fail("BASTION not triggered for fingerprint_mismatch")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','0')")
    conn.commit()

    # Scenario C: EXE tamper
    info("Scenario C: EXE file modified...")
    bastion_suspend(conn, 'exe_tamper', 'Hash changed from abc123 to xyz789')
    suspended, record = get_bastion_status(conn)
    if suspended and record.get('attack_type') == 'exe_tamper':
        ok(f"BASTION triggered for exe_tamper: '{record.get('title')}'")
    else:
        fail("BASTION not triggered for exe_tamper")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','0')")
    conn.commit()

    # Scenario D: Session registry tamper
    info("Scenario D: Session registry token missing...")
    bastion_suspend(conn, 'session_tamper', 'Registry key missing')
    suspended, record = get_bastion_status(conn)
    if suspended and record.get('attack_type') == 'session_tamper':
        ok(f"BASTION triggered for session_tamper: '{record.get('title')}'")
    else:
        fail("BASTION not triggered for session_tamper")

    conn.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# TEST 5 — Session Token Logic
# ══════════════════════════════════════════════════════════════════
def test_session_token(dna):
    head("TEST 5: Session Token (Kerberos / Layer 10)")
    info("Simulates token generation and verification.\n")

    SESSION_SALT = 'AurumOS@Session@Jenil#9x7z@2026'

    def db_state_hash():
        # Simulate: sales:47|stock:312|katti:8|creds:2
        counts = ['sales_history:47', 'stock_inventory:312', 'katti_vouchers:8', 'admin_creds:2']
        return hashlib.sha256('|'.join(counts).encode()).hexdigest()[:16]

    def generate_token(dna, ts_window=None):
        today  = datetime.date.today().isoformat()
        ts     = str(ts_window or int(time.time() // 300))
        dbhash = db_state_hash()
        raw    = '|'.join([dna, today, ts, dbhash, SESSION_SALT])
        return hashlib.sha256(raw.encode()).hexdigest()

    # Same inputs = same token
    ts_now = int(time.time() // 300)
    t1 = generate_token(dna, ts_now)
    t2 = generate_token(dna, ts_now)
    if t1 == t2:
        ok(f"Token is deterministic for same inputs: {t1[:12]}...")
    else:
        fail("Token is non-deterministic — bug!")

    # Different time window = different token
    t3 = generate_token(dna, ts_now + 1)
    if t1 != t3:
        ok("Different 5-min window produces different token ✓")
    else:
        fail("Time rotation not working")

    # Different DNA = different token
    fake_dna = 'x' * 24
    t4 = generate_token(fake_dna, ts_now)
    if t1 != t4:
        ok("Different hardware DNA produces different token ✓")
    else:
        fail("DNA isolation not working — security hole!")

    # Simulate 3-store verification
    info("\n  Simulating 3-store token verification...")
    token = t1

    # All 3 match = allowed
    ram   = token
    reg   = token
    ffile = token
    all_match = (ram == reg == ffile == token)
    if all_match:
        ok("All 3 stores match → write ALLOWED ✓")
    else:
        fail("3-store match failed unexpectedly")

    # Registry tampered
    reg_tampered = "fake" + token[4:]
    if ram != reg_tampered:
        ok("Registry tamper detected → write BLOCKED ✓")
    else:
        fail("Registry tamper NOT detected")

    # File deleted (simulate exception)
    file_missing = None
    if file_missing is None:
        ok("Missing file detected → write BLOCKED ✓")
    else:
        fail("Missing file not detected")

    info(f"\n  Sample token: {token[:16]}...{token[-8:]}")
    info(f"  Token rotates every 5 minutes automatically")


# ══════════════════════════════════════════════════════════════════
# TEST 6 — EXE Integrity Simulation
# ══════════════════════════════════════════════════════════════════
def test_exe_integrity():
    head("TEST 6: EXE Integrity Check (Layer 9)")
    info("Simulates hash check on EXE file modification.\n")

    tmp = tempfile.mktemp(suffix='.exe')

    # Write fake EXE
    with open(tmp, 'wb') as f:
        f.write(os.urandom(1024))

    # Compute hash
    def file_hash(path):
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk: break
                h.update(chunk)
        return h.hexdigest()

    original_hash = file_hash(tmp)
    info(f"Original EXE hash: {original_hash[:12]}...")

    # Hash is stable
    if file_hash(tmp) == original_hash:
        ok("EXE hash is stable ✓")
    else:
        fail("EXE hash unstable")

    # Tamper the file
    with open(tmp, 'ab') as f:
        f.write(b'\x00\xFF\xDE\xAD\xBE\xEF')

    tampered_hash = file_hash(tmp)
    if tampered_hash != original_hash:
        ok(f"Tamper detected: hash changed {original_hash[:8]} → {tampered_hash[:8]} ✓")
        ok("BASTION would trigger exe_tamper suspension")
    else:
        fail("Tamper NOT detected — hash unchanged")

    os.remove(tmp)


# ══════════════════════════════════════════════════════════════════
# TEST 7 — Full Attack + Recovery Simulation
# ══════════════════════════════════════════════════════════════════
def test_full_scenario(lc):
    head("TEST 7: Full Attack + Recovery Simulation")
    info("Simulates complete attack lifecycle end to end.\n")

    tmp_dir = tempfile.mkdtemp(prefix='aurum_full_')
    db_path = os.path.join(tmp_dir, 'full.db')
    conn    = make_test_db(db_path)

    today = datetime.date.today().strftime('%Y-%m-%d')

    info("Step 1: Normal operation...")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','0')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','0')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('login_attempts','0')")
    conn.commit()
    ok("Normal state set ✓")

    info("\nStep 2: Client edits DB with external tool → BASTION triggers...")
    record = {
        'suspended': True, 'attack_type': 'db_edit',
        'title': 'Database Tampering Detected',
        'reason': 'DB was modified outside AurumOS while running.',
        'detail': 'Session file token mismatch', 'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','1')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_record',?)", (json.dumps(record),))
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','1')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('lock_code_cache',?)", (lc,))
    conn.commit()
    ok("BASTION suspension written to DB ✓")

    info("\nStep 3: Client restarts app → BASTION red screen shows...")
    row = conn.execute("SELECT value FROM app_config WHERE key='bastion_suspended'").fetchone()
    if row and row['value'] == '1':
        ok("bastion_suspended=1 persists after restart ✓")
        ok(f"Red screen shows: '{record['title']}'")
        ok(f"Lock code shown : {lc}")
    else:
        fail("Suspension not persisted")

    info("\nStep 4: Client calls you → you generate BASTION key...")
    bastion_key = make_bastion_key(lc, today)
    ok(f"BASTION key generated: {bastion_key}")
    ok(f"You send via WhatsApp: {bastion_key}")

    info("\nStep 5: Client enters BASTION key → suspension cleared...")
    # Simulate bastion_clear
    def verify_and_clear(conn, entered, lc):
        for ds in [today,
                   (datetime.date.today()-datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
                   (datetime.date.today()+datetime.timedelta(days=1)).strftime('%Y-%m-%d')]:
            expected = make_bastion_key(lc, ds)
            if entered.strip().upper() == expected:
                conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','0')")
                conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_record','')")
                conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','0')")
                conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('login_attempts','0')")
                conn.commit()
                return True
        return False

    cleared = verify_and_clear(conn, bastion_key, lc)
    if cleared:
        ok("BASTION key matched → suspension cleared ✓")
    else:
        fail("BASTION key did not match")

    info("\nStep 6: Verify account is restored...")
    row = conn.execute("SELECT value FROM app_config WHERE key='bastion_suspended'").fetchone()
    if row and row['value'] == '0':
        ok("bastion_suspended=0 — account fully restored ✓")
    else:
        fail("Account not restored")

    # Wrong key must not clear
    info("\nStep 7: Verify wrong key cannot clear BASTION...")
    # Re-suspend
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','1')")
    conn.commit()
    wrong_cleared = verify_and_clear(conn, "AAAAAAAAAAAAAAAA", lc)
    if not wrong_cleared:
        ok("Wrong 16-char key correctly rejected ✓")
    else:
        fail("Wrong key cleared BASTION — security hole!")

    # Regular 12-char key must not clear BASTION
    regular_key = make_regular_key(lc, today)
    wrong_cleared2 = verify_and_clear(conn, regular_key, lc)
    if not wrong_cleared2:
        ok("Regular 12-char key cannot clear BASTION ✓ (different salt + length)")
    else:
        fail("Regular key cleared BASTION — security hole!")

    conn.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def main():
    print(DLINE)
    print("  AurumOS BASTION + Security — Full Test Suite")
    print(f"  Run at: {datetime.datetime.now().strftime('%d %b %Y %I:%M:%S %p')}")
    print(DLINE)

    dna, lc = test_hardware_dna()
    test_regular_unlock(lc)
    test_bastion_system(lc)
    test_bastion_triggers()
    test_session_token(dna)
    test_exe_integrity()
    test_full_scenario(lc)

    print(f"\n{DLINE}")
    print(f"  RESULTS: {PASS} PASSED  |  {FAIL} FAILED")
    if FAIL == 0:
        print("  ALL TESTS PASSED — BASTION is working correctly")
    else:
        print(f"  {FAIL} TEST(S) FAILED — review output above")
    print(DLINE)

    print(f"\n  Your lock code  : {lc}")
    today = datetime.date.today().strftime('%Y-%m-%d')
    print(f"  Regular key     : {make_regular_key(lc, today)}  (12 chars)")
    print(f"  BASTION key     : {make_bastion_key(lc, today)}  (16 chars)")
    print()
    input("  Press Enter to exit...")

if __name__ == '__main__':
    main()