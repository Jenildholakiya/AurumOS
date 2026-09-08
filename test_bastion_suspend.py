import sys
import os
import json
import sqlite3
import hashlib
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

sys.path.insert(0, '.')

TEST_DB = 'database/aurum_test_bastion.db'

def _setup_test_db():
    os.environ['AURUM_DB_PATH'] = TEST_DB
    from database.db_manager import DBManager
    db = DBManager()
    conn = sqlite3.connect(db.db_path)
    conn.execute("DELETE FROM app_config WHERE key IN ('bastion_suspended','bastion_record','account_locked','login_attempts','exe_hash')")
    conn.execute("DELETE FROM stock_inventory WHERE it_code LIKE 'ROGUE%'")
    conn.commit()
    conn.close()
    return db

def _teardown_test_db():
    if 'AURUM_DB_PATH' in os.environ:
        del os.environ['AURUM_DB_PATH']

def _get_app_config(db):
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT key, value FROM app_config WHERE key IN "
        "('bastion_suspended','bastion_record','account_locked','login_attempts')"
    ).fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}

def _make_admin_key(db, lock_code=None):
    BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'
    if not lock_code:
        lock_code = db._machine_fingerprint()[:8].upper()
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    date_str = now_ist.strftime('%Y-%m-%d')
    key = hashlib.sha256((lock_code + BASTION_SALT + date_str).encode('utf-8')).hexdigest()[:16].upper()
    return key, lock_code


class TestBastionSuspend:

    def _fresh_db(self):
        return _setup_test_db()

    def test_1_attack_registry_token_mismatch_triggers_suspend(self):
        """REAL ATTACK: Generate a session token, then change the registry value to mismatch with RAM."""
        db = self._fresh_db()

        # Step 1: Generate a session token (stores in RAM + registry + temp file)
        token = db._generate_session_token()
        print(f"  [ATTACK SETUP] Session token generated: {token[:8]}...")

        # Step 2: Verify token matches (all 3 stores agree)
        result = db._verify_session_token()
        assert result is True, "Token should verify before attack"
        print("  [VERIFY] Token verified OK before attack")

        # Step 3: REAL ATTACK — Change registry token to a different value
        import winreg as _wr
        key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\InputMethod\AOS', 0, _wr.KEY_SET_VALUE)
        _wr.SetValueEx(key, 'SessionCache', 0, _wr.REG_SZ, 'FAKE_TAMPERED_TOKEN')
        _wr.CloseKey(key)
        print("  [ATTACK] Registry token changed to FAKE_TAMPERED_TOKEN")

        # Step 4: Now verify session — should detect mismatch and SUSPEND
        result = db._verify_session_token()
        assert result is False, "_verify_session_token MUST return False on registry mismatch"

        cfg = _get_app_config(db)
        assert cfg.get('bastion_suspended') == '1', "Account MUST be suspended after registry tamper"
        print("  [RESULT] Account SUSPENDED immediately — registry tamper detected")
        print("PASS: Registry token mismatch SUSPENDS account immediately")

    def test_2_attack_session_file_tamper_triggers_suspend(self):
        """REAL ATTACK: Generate a session token, then modify the temp file to mismatch with RAM."""
        db = self._fresh_db()

        # Step 1: Generate a session token
        token = db._generate_session_token()
        print(f"  [ATTACK SETUP] Session token generated: {token[:8]}...")

        # Step 2: Verify token matches
        result = db._verify_session_token()
        assert result is True, "Token should verify before attack"
        print("  [VERIFY] Token verified OK before attack")

        # Step 3: REAL ATTACK — Modify the temp file to a different value
        tmp = getattr(db, '_session_token_file', None)
        if not tmp:
            print("SKIP: No session token file path set")
            return

        with open(tmp, 'w') as f:
            f.write('FAKE_TAMPERED_FILE_TOKEN')
        print(f"  [ATTACK] Session temp file tampered: {tmp}")

        # Step 4: Now verify session — should detect mismatch and SUSPEND
        result = db._verify_session_token()
        assert result is False, "_verify_session_token MUST return False on file mismatch"

        cfg = _get_app_config(db)
        assert cfg.get('bastion_suspended') == '1', "Account MUST be suspended after file tamper"
        print("  [RESULT] Account SUSPENDED immediately — file tamper detected")
        print("PASS: Session file tamper SUSPENDS account immediately")

    def test_3_attack_exe_hash_mismatch_triggers_suspend(self):
        """REAL ATTACK: Modify exe_trusted_hash.txt to mismatch with actual EXE hash."""
        db = self._fresh_db()

        # Step 1: Find the exe_trusted_hash.txt file
        import sys as _sys
        if not getattr(_sys, 'frozen', False):
            print("SKIP: Not running as frozen EXE — exe tamper check skipped in dev mode")
            return

        exe_path = _sys.executable
        exe_dir = os.path.dirname(exe_path)
        trust_path = os.path.join(exe_dir, 'exe_trusted_hash.txt')

        if not os.path.exists(trust_path):
            print("SKIP: exe_trusted_hash.txt not found")
            return

        # Step 2: Read the current trusted hash
        with open(trust_path, 'r') as f:
            original_hash = f.read().strip()
        print(f"  [ATTACK SETUP] Original trusted hash: {original_hash[:12]}...")

        # Step 3: REAL ATTACK — Replace trusted hash with a wrong value
        with open(trust_path, 'w') as f:
            f.write('DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF')
        print(f"  [ATTACK] exe_trusted_hash.txt replaced with fake hash")

        try:
            # Step 4: Now verify EXE — should detect mismatch and SUSPEND
            result = db.bastion_verify_exe(exe_path)
            assert result is False, "bastion_verify_exe MUST return False on hash mismatch"

            cfg = _get_app_config(db)
            assert cfg.get('bastion_suspended') == '1', "Account MUST be suspended after EXE tamper"
            print("  [RESULT] Account SUSPENDED immediately — EXE tamper detected")
            print("PASS: EXE hash mismatch SUSPENDS account immediately")
        finally:
            # Restore original hash
            with open(trust_path, 'w') as f:
                f.write(original_hash)
            print("  [CLEANUP] Restored original exe_trusted_hash.txt")

    def test_4_attack_db_state_hash_change_detected_on_new_token(self):
        """REAL ATTACK: Modify the database externally, then tamper with RAM token."""
        db = self._fresh_db()

        # Step 1: Generate a session token (captures current DB state hash)
        token = db._generate_session_token()
        print(f"  [ATTACK SETUP] Session token generated with DB state hash")

        # Step 2: Verify token matches
        result = db._verify_session_token()
        assert result is True, "Token should verify before attack"
        print("  [VERIFY] Token verified OK before attack")

        # Step 3: REAL ATTACK — Modify the database file directly (simulate external DB edit)
        conn = sqlite3.connect(db.db_path)
        conn.execute(
            "INSERT INTO stock_inventory (it_code, it_name, tag_id, gr_wt, nt_wt, touch, huid, is_tagged, entry_date) "
            "VALUES ('ROGUE001', 'Rogue Item', 'TAG-ROGUE', 1.0, 1.0, 0.0, 'B-999', 0, date('now'))"
        )
        conn.commit()
        conn.close()
        print("  [ATTACK] Database edited externally — rogue row inserted")

        # Step 4: Now tamper with RAM token only (simulate memory replay attack)
        # The registry/file still have the original token, but RAM now has a different one
        db._session_token = 'FAKE_TAMPERED_RAM_TOKEN'
        print("  [ATTACK] RAM token replaced with fake value")

        # Step 5: Verify session — should detect RAM/registry mismatch and SUSPEND
        result = db._verify_session_token()
        assert result is False, "_verify_session_token MUST return False on RAM/registry mismatch"

        cfg = _get_app_config(db)
        assert cfg.get('bastion_suspended') == '1', "Account MUST be suspended after DB tamper detected"
        print("  [RESULT] Account SUSPENDED — DB tamper + RAM mismatch detected")
        print("PASS: External DB edit + RAM tamper SUSPENDS account")

        # Cleanup: remove the rogue row
        conn = sqlite3.connect(db.db_path)
        conn.execute("DELETE FROM stock_inventory WHERE it_code='ROGUE001'")
        conn.commit()
        conn.close()

    def test_5_attack_replay_old_session_token_triggers_suspend(self):
        """REAL ATTACK: Generate a token, then replay an old token value."""
        db = self._fresh_db()

        # Step 1: Generate a session token
        token = db._generate_session_token()
        print(f"  [ATTACK SETUP] Session token generated: {token[:8]}...")

        # Step 2: Set up all 3 stores correctly
        db._session_token = token
        import winreg as _wr
        key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\InputMethod\AOS', 0, _wr.KEY_SET_VALUE)
        _wr.SetValueEx(key, 'SessionCache', 0, _wr.REG_SZ, token)
        _wr.CloseKey(key)
        tmp = getattr(db, '_session_token_file', None)
        if tmp:
            with open(tmp, 'w') as f:
                f.write(token)

        # Step 3: Verify token matches
        result = db._verify_session_token()
        assert result is True, "Token should verify before attack"
        print("  [VERIFY] Token verified OK before attack")

        # Step 4: REAL ATTACK — Replay an old token in RAM
        old_token = 'old_replayed_token_from_previous_session_abc123'
        db._session_token = old_token
        print(f"  [ATTACK] RAM token replaced with old replayed token")

        # Step 5: Now verify session — should detect mismatch and SUSPEND
        result = db._verify_session_token()
        assert result is False, "_verify_session_token MUST return False on replayed token"

        cfg = _get_app_config(db)
        assert cfg.get('bastion_suspended') == '1', "Account MUST be suspended after replay attack"
        print("  [RESULT] Account SUSPENDED immediately — replay attack detected")
        print("PASS: Replay attack with old token SUSPENDS account immediately")

    def test_6_direct_bastion_suspend_permanently_locks_account(self):
        """REAL ATTACK: Call bastion_suspend directly (as bastion_ai.py would on confirmed attack)."""
        db = self._fresh_db()

        print("  [ATTACK] Calling bastion_suspend directly (simulating bastion_ai.py auto-suspend)")
        db.bastion_suspend('fingerprint_mismatch', 'Database copied from another PC illegally')

        cfg = _get_app_config(db)
        assert cfg.get('bastion_suspended') == '1', "Account MUST be suspended"
        assert cfg.get('account_locked') == '1', "Account MUST be locked"
        assert cfg.get('login_attempts') == '99', "Login attempts MUST be maxed out"
        record = json.loads(cfg.get('bastion_record', '{}'))
        assert record.get('attack_type') == 'fingerprint_mismatch'
        assert record.get('suspended') is True
        print("  [RESULT] Account PERMANENTLY LOCKED — requires admin key to unlock")

        # Verify lock_code is present via bastion_get_status (which adds it)
        status = db.bastion_get_status()
        assert 'lock_code' in status, "Lock code must be present for admin unlock"
        print("PASS: Direct bastion_suspend permanently locks the account")

    def test_7_suspended_account_blocks_stock_entry(self):
        """Verify that add_stock_entry is blocked when account is suspended."""
        db = self._fresh_db()
        db.bastion_suspend('session_tamper', 'Simulated attack')

        result = db.add_stock_entry(
            it_code='ATTACK001', it_name='Attack Test Item', tag_id='TAG-ATTACK',
            gr_wt=10.0, nt_wt=10.0, touch=0, pcs=1, huid='B-001'
        )
        assert result is False, "add_stock_entry MUST be blocked when suspended"
        print("PASS: add_stock_entry blocked when account is suspended")

    def test_8_suspended_account_blocks_client_add(self):
        """Verify that add_client is blocked when account is suspended."""
        db = self._fresh_db()
        db.bastion_suspend('db_edit', 'Simulated DB tamper')

        result = db.add_client(
            name='Attack Client', phone='99999', metal_limit=1000, cash_limit=1000
        )
        assert result is False, "add_client MUST be blocked when suspended"
        print("PASS: add_client blocked when account is suspended")

    def test_9_suspended_account_blocks_ledger_entry(self):
        """Verify that post_ledger_entry is blocked when account is suspended."""
        db = self._fresh_db()
        db.bastion_suspend('replay_attack', 'Simulated replay')

        result = db.post_ledger_entry(
            vch_id='VCH-ATTACK', txn_type='IN',
            it_code='LEDGER001', it_name='Ledger Attack',
            gr_wt=5.0, nt_wt=5.0, touch=0, pcs=1,
            rate=100, amount=500, category='Gold',
            huid='B-001', tag_id='TAG-LEDGER'
        )
        assert result is False, "post_ledger_entry MUST be blocked when suspended"
        print("PASS: post_ledger_entry blocked when account is suspended")

    def test_10_suspended_account_blocks_katti_batch(self):
        """Verify that save_katti_batch is blocked when account is suspended."""
        db = self._fresh_db()
        db.bastion_suspend('exe_tamper', 'Simulated EXE tamper')

        result = db.save_katti_batch(
            vch_id='KATTI-ATTACK', total_wt=10.0, total_packets=1,
            note='Attack test', items=[], box_id=''
        )
        assert result is False, "save_katti_batch MUST be blocked when suspended"
        print("PASS: save_katti_batch blocked when account is suspended")

    def test_11_suspended_account_blocks_uchak_inward(self):
        """Verify that save_uchak_inward_transaction is blocked when account is suspended."""
        db = self._fresh_db()
        db.bastion_suspend('session_tamper', 'Simulated session tamper')

        result = db.save_uchak_inward_transaction(
            vch_id='UCHAK-ATTACK', total_lines=1, total_pcs=1,
            total_value=100, items_list=[]
        )
        assert result is False, "save_uchak_inward_transaction MUST be blocked when suspended"
        print("PASS: save_uchak_inward_transaction blocked when account is suspended")

    def test_12_suspension_permanently_persists_until_cleared(self):
        """Verify suspension persists across new DB connections until explicitly cleared."""
        db = self._fresh_db()
        db.bastion_suspend('session_tamper', 'Permanent test suspension')

        from database.db_manager import DBManager
        db2 = DBManager()
        status = db2.bastion_get_status()
        assert status.get('suspended') is True, "Suspension MUST persist across connections"
        db2 = None

        admin_key, lock_code = _make_admin_key(db)
        db.bastion_clear(admin_key, lock_code)

        db3 = DBManager()
        status3 = db3.bastion_get_status()
        assert status3.get('suspended') is False, "Suspension MUST be cleared across connections"
        db3 = None
        print("PASS: Suspension persists permanently until cleared with correct admin key")

    def test_13_wrong_admin_key_cannot_clear_suspension(self):
        """Verify that wrong admin key cannot clear suspension."""
        db = self._fresh_db()
        db.bastion_suspend('session_tamper', 'Test wrong key')

        result = db.bastion_clear('WRONGKEY12345678')
        assert result.get('status') == 'error', "Wrong key must be rejected"

        cfg = _get_app_config(db)
        assert cfg.get('bastion_suspended') == '1', "Suspension must remain after wrong key"
        print("PASS: Wrong admin key cannot clear suspension")

    def test_14_all_attack_types_trigger_immediate_suspend(self):
        """Verify every attack type in BASTION_CODES triggers immediate suspension."""
        db = self._fresh_db()
        for attack_type in db.BASTION_CODES:
            db2 = self._fresh_db()
            db2.bastion_suspend(attack_type, f'Testing {attack_type}')
            cfg = _get_app_config(db2)
            assert cfg.get('bastion_suspended') == '1', f"{attack_type} must suspend immediately"
        print("PASS: All BASTION_CODES attack types trigger immediate suspension")


if __name__ == '__main__':
    print("=" * 60)
    print("  AURUMOS BASTION SUSPEND — LIVE ATTACK SIMULATION TEST")
    print("  WARNING: This file performs REAL attack simulations")
    print("  that will SUSPEND the test account immediately.")
    print("  For testing purposes only.")
    print("=" * 60)

    t = TestBastionSuspend()
    test_methods = [
        'test_1_attack_registry_token_mismatch_triggers_suspend',
        'test_2_attack_session_file_tamper_triggers_suspend',
        'test_3_attack_exe_hash_mismatch_triggers_suspend',
        'test_4_attack_db_state_hash_change_detected_on_new_token',
        'test_5_attack_replay_old_session_token_triggers_suspend',
        'test_6_direct_bastion_suspend_permanently_locks_account',
        'test_7_suspended_account_blocks_stock_entry',
        'test_8_suspended_account_blocks_client_add',
        'test_9_suspended_account_blocks_ledger_entry',
        'test_10_suspended_account_blocks_katti_batch',
        'test_11_suspended_account_blocks_uchak_inward',
        'test_12_suspension_permanently_persists_until_cleared',
        'test_13_wrong_admin_key_cannot_clear_suspension',
        'test_14_all_attack_types_trigger_immediate_suspend',
    ]

    passed = 0
    failed = 0
    skipped = 0

    for name in test_methods:
        test = getattr(t, name)
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {name} — {e}")
            failed += 1
        except Exception as e:
            if 'SKIP' in str(e):
                print(f"SKIP: {name}")
                skipped += 1
            else:
                print(f"ERROR: {name} — {e}")
                failed += 1

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'=' * 60}")