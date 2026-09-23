# -*- coding: utf-8 -*-
import sqlite3
import os
import random
import json
import sys
import traceback
from datetime import date, datetime



# Safe to embed in every distributed EXE -- this key can ONLY mint a
# per-shop secret for self-registration, nothing else. Set to match
# AURUM_PROVISION_KEY on the Vercel dashboard. Rotating it just means
# rebuilding the EXE and updating one Vercel env var.
_AUTO_PROVISION_KEY = '2351370ae9fa41af69a603046b015b2cfc048fb30ee357c9647a18ea838c43cf'


def _dblog(msg):
    """Safe logger — writes to file log and console with UTF-8, never crashes."""
    try:
        import logging as _lg
        _lg.getLogger('aurumos.db').info(str(msg))
    except Exception:
        pass
    try:
        # Safe console print — encode to ascii replacing non-ascii
        safe = str(msg).encode('ascii', errors='replace').decode('ascii')
        print(safe, flush=True)
    except Exception:
        pass


def _dberr(msg):
    """Safe error logger."""
    try:
        import logging as _lg
        _lg.getLogger('aurumos.db').error(str(msg))
    except Exception:
        pass
    try:
        safe = str(msg).encode('ascii', errors='replace').decode('ascii')
        print(f"[DB_ERR] {safe}", flush=True)
    except Exception:
        pass


class DBManager:
    def __init__(self):
        import sys as _sys
        # Priority: AURUM_DB_PATH env var (used by switch_year) → db_path.txt → default
        env_path = os.environ.get('AURUM_DB_PATH', '').strip()
        if env_path and os.path.exists(os.path.dirname(env_path) or '.'):
            self.db_path = env_path
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.db_dir = os.path.dirname(env_path)
        else:
            if getattr(_sys, 'frozen', False):
                app_dir = os.path.dirname(_sys.executable)
            else:
                app_dir = os.path.abspath('.')
            self.db_dir = os.path.join(app_dir, 'database')
            self.db_path = os.path.join(self.db_dir, 'aurum_local.db')

        try:
            os.makedirs(self.db_dir, exist_ok=True)
        except Exception as e:
            _dblog(f'[DB] Cannot create database dir: {e}')

        _dblog(f'[DB] Path: {self.db_path}')
        print(f"[DB] Dir exists: {os.path.exists(self.db_dir)}")
        print(f"[DB] Dir writable: {os.access(self.db_dir, os.W_OK)}")
        self.initialize_tables()
        try:
            # Before anything asks. An install that already has a
            # fingerprint keeps it, whatever the hardware readers say today.
            with self._get_connection() as _c:
                _row = _c.execute(
                    "SELECT value FROM app_config WHERE key='machine_fingerprint'"
                ).fetchone()
            if _row and str(_row[0] or '').strip():
                DBManager._pin_fingerprint(_row[0])
        except Exception:
            pass
        try:
            self.ensure_retail_columns()
            self.ensure_crm_tables()
            self.ensure_hallmark_tables()
            self.ensure_karigar_tables()
            self.ensure_held_bills_table()
            self.ensure_finance_tables()
        except Exception:
            pass
        try:
            self.ensure_notifications_table()
        except Exception:
            pass
        try:
            self.ensure_urgent_table()
        except Exception:
            pass
        try:
            self.repair_retail_fine()
        except Exception:
            pass
        try:
            self.apply_migrations()
        except Exception:
            pass

    def apply_migrations(self, base_dir=None):
        """Run migrations/*.sql once each, in filename order.

        The channel schema fixes travel OTA: a release lists
        migrations/NNNN_what.sql in version.json, the updater downloads it
        next to the app, and this runs it exactly once (recorded in
        app_config, so re-runs and restarts are no-ops). A failing file is
        logged and skipped — it never blocks startup or later files.
        Returns how many files were newly applied.
        """
        import glob as _glob
        import hashlib as _hl
        applied = 0
        try:
            if base_dir:
                root = str(base_dir)
            else:
                root = os.path.dirname(os.path.abspath(self.db_dir))
            files = sorted(_glob.glob(os.path.join(root, 'migrations', '*.sql')))
            if not files:
                return 0
            with self._get_connection() as conn:
                for path in files:
                    name = os.path.basename(path)
                    try:
                        sql = open(path, 'r', encoding='utf-8').read()
                    except Exception as _re:
                        _dberr(f"[MIGRATE] cannot read {name}: {_re}")
                        continue
                    if not sql.strip():
                        continue
                    digest = _hl.sha256(sql.encode('utf-8')).hexdigest()
                    try:
                        row = conn.execute(
                            "SELECT value FROM app_config WHERE key=?",
                            (f'migration_applied:{name}',)).fetchone()
                    except Exception:
                        row = None
                    if row and row['value'] == digest:
                        continue
                    try:
                        conn.executescript(sql)
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                            (f'migration_applied:{name}', digest))
                        conn.commit()
                        applied += 1
                        _dblog(f"[MIGRATE] applied {name}")
                    except Exception as _ae:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        _dberr(f"[MIGRATE] {name} failed (skipped): {_ae}")
        except Exception as e:
            _dberr(f"[MIGRATE] {e}")
        return applied

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=20,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL mode -- allows reads while writing, critical for EXE multi-access
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def initialize_tables(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                ac_info = cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='admin_creds'"
                ).fetchone()
                if ac_info and 'CHECK (id = 1)' in (ac_info['sql'] or ''):
                    cursor.executescript("""
                        ALTER TABLE admin_creds RENAME TO admin_creds_old;
                        CREATE TABLE admin_creds (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password TEXT);
                        INSERT INTO admin_creds (username, password) SELECT username, password FROM admin_creds_old;
                        DROP TABLE admin_creds_old;
                    """)

                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS admin_creds (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password TEXT);
                    CREATE TABLE IF NOT EXISTS business_profile (
                        id INTEGER PRIMARY KEY CHECK (id=1), biz_name TEXT, phone TEXT, address TEXT, gstin TEXT);
                    CREATE TABLE IF NOT EXISTS categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS touch_groups (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value REAL NOT NULL, wastage REAL DEFAULT 0.00);
                    CREATE TABLE IF NOT EXISTS product_master (
                        code TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, touch TEXT, wastage REAL DEFAULT 0.00);
                    CREATE TABLE IF NOT EXISTS stock_inventory (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        it_code       TEXT,
                        it_name       TEXT,
                        tag_id        TEXT UNIQUE,
                        pkg_wt        REAL DEFAULT 0.000,
                        para_stone_wt REAL DEFAULT 0.000,
                        size          TEXT,
                        design        TEXT,
                        pcs           INTEGER,
                        gr_wt         REAL,
                        ls_wt         REAL,
                        nt_wt         REAL,
                        ghat_wt       REAL,
                        touch         REAL DEFAULT 0.00,
                        wastage       REAL DEFAULT 0.00,
                        huid          TEXT,
                        vch_reference TEXT,
                        is_tagged     INTEGER DEFAULT 0,
                        entry_date    DATE     DEFAULT (date('now')),
                        timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE IF NOT EXISTS logs (
                        ts TEXT,
                        event_type TEXT,
                        severity TEXT,
                        score INTEGER,
                        detail TEXT,
                        action_taken TEXT
                    );
                    CREATE TABLE IF NOT EXISTS sales_history (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        vch_id         TEXT UNIQUE,
                        customer       TEXT,
                        mobile         TEXT DEFAULT '',
                        status         TEXT DEFAULT 'CREDIT',
                        ledger_fine    REAL, collected_fine REAL, fine_995 REAL,
                        fine_dhal      REAL, remaining_fine REAL, gold_rate REAL,
                        total_amount   REAL, items TEXT,
                        date           DATE     DEFAULT (date('now')),
                        time_stamp     TEXT     DEFAULT (time('now')));
                    CREATE TABLE IF NOT EXISTS katti_vouchers (
                        vch_id        TEXT PRIMARY KEY,
                        total_weight  REAL, total_packets INTEGER, total_pcs INTEGER,
                        note          TEXT, touch REAL DEFAULT 0.00, box_id TEXT DEFAULT NULL,
                        date          DATE     DEFAULT (date('now')),
                        timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE IF NOT EXISTS katti_voucher_items (
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        vch_id  TEXT,
                        it_code TEXT DEFAULT '',
                        it_name TEXT,
                        nt_wt   REAL,
                        touch   REAL,
                        huid    TEXT,
                        pcs     INTEGER DEFAULT 1);
                    CREATE TABLE IF NOT EXISTS clients_master (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, phone TEXT,
                        metal_limit REAL DEFAULT 0.000, cash_limit REAL DEFAULT 0.00,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE IF NOT EXISTS credit_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, client_name TEXT,
                        date DATE DEFAULT (date('now')), vch_reference TEXT, description TEXT,
                        metal_dr REAL DEFAULT 0, metal_cr REAL DEFAULT 0,
                        cash_dr  REAL DEFAULT 0, cash_cr  REAL DEFAULT 0, gold_rate REAL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS uchak_inward_vouchers (
                        vch_id TEXT PRIMARY KEY, total_lines INTEGER DEFAULT 0,
                        total_pcs INTEGER DEFAULT 0, total_value REAL DEFAULT 0.00,
                        date DATE DEFAULT (date('now')), timestamp DATETIME DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE IF NOT EXISTS uchak_inward_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, vch_id TEXT,
                        it_code TEXT, it_name TEXT, pcs INTEGER DEFAULT 1, price REAL DEFAULT 0.00);
                    CREATE TABLE IF NOT EXISTS mac_lock (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        fingerprint TEXT DEFAULT '',
                        locked_at TEXT DEFAULT '',
                        hostname TEXT DEFAULT '');
                """)

                bp_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(business_profile)").fetchall()]
                if 'owner_name' not in bp_cols:
                    cursor.execute("ALTER TABLE business_profile ADD COLUMN owner_name TEXT DEFAULT NULL")

                # ── is_owner: replaces the fragile "row with id==1 is the
                # owner" convention. Once LAN sync merges staff rows from
                # another PC, local autoincrement ids are no longer a
                # reliable way to identify the owner -- a synced row could
                # land at id=1 on a fresh DB. is_owner is an explicit,
                # permanent flag that travels WITH the row during sync
                # (it's a real column, just like username/password), so
                # the original owner stays the owner on every PC forever,
                # regardless of what local id their row ends up with.
                ac_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(admin_creds)").fetchall()]
                if 'is_owner' not in ac_cols:
                    cursor.execute("ALTER TABLE admin_creds ADD COLUMN is_owner INTEGER DEFAULT 0")
                    # One-time migration for existing installs: whoever is
                    # currently id=1 (the old implicit-owner convention)
                    # gets explicitly flagged now, so nothing changes for
                    # anyone already using the app.
                    cursor.execute(
                        "UPDATE admin_creds SET is_owner=1 WHERE id = (SELECT MIN(id) FROM admin_creds)"
                    )
                    _dblog("[MIGRATION] admin_creds.is_owner added, existing id=1 row flagged as owner")

                # ── permissions: stores staff module assignments
                # as a JSON array (e.g. ["billing","inventory"]).
                # New column defaults to '[]' for existing rows.
                if 'permissions' not in ac_cols:
                    cursor.execute("ALTER TABLE admin_creds ADD COLUMN permissions TEXT DEFAULT '[]'")
                    _dblog("[MIGRATION] admin_creds.permissions added")

                kv_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(katti_vouchers)").fetchall()]
                for col, defn in [('total_packets', 'INTEGER DEFAULT 0'), ('total_pcs', 'INTEGER DEFAULT 0'),
                                  ('touch', 'REAL DEFAULT 0.00'), ('box_id', 'TEXT DEFAULT NULL')]:
                    if col not in kv_cols:
                        cursor.execute(f"ALTER TABLE katti_vouchers ADD COLUMN {col} {defn}")

                kvi_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(katti_voucher_items)").fetchall()]
                if 'it_code' not in kvi_cols:
                    cursor.execute("ALTER TABLE katti_voucher_items ADD COLUMN it_code TEXT DEFAULT ''")

                si_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(stock_inventory)").fetchall()]
                for col, defn in [('vch_reference', 'TEXT'), ('entry_date', "DATE DEFAULT (date('now'))"),
                                  ('wastage', 'REAL DEFAULT 0.00')]:
                    if col not in si_cols:
                        cursor.execute(f"ALTER TABLE stock_inventory ADD COLUMN {col} {defn}")

                sh_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(sales_history)").fetchall()]
                if 'mobile' not in sh_cols:
                    cursor.execute("ALTER TABLE sales_history ADD COLUMN mobile TEXT DEFAULT ''")
                if 'status' not in sh_cols:
                    cursor.execute("ALTER TABLE sales_history ADD COLUMN status TEXT DEFAULT 'CREDIT'")
                # Discount columns -- added in v2
                for col, defn in [
                    ('discount_type', "TEXT DEFAULT 'none'"),
                    ('discount_touch', 'REAL DEFAULT 0.0'),
                    ('discount_fine', 'REAL DEFAULT 0.0'),
                    ('discount_amount', 'REAL DEFAULT 0.0'),
                ]:
                    if col not in sh_cols:
                        cursor.execute(f"ALTER TABLE sales_history ADD COLUMN {col} {defn}")

                # App config -- setup status, business profile
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS login_log (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        username   TEXT    NOT NULL DEFAULT 'owner',
                        role       TEXT    NOT NULL DEFAULT 'admin',
                        login_time TEXT    NOT NULL DEFAULT (datetime('now')),
                        ip         TEXT    DEFAULT ''
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts         TEXT    NOT NULL,
                        username   TEXT    NOT NULL DEFAULT 'system',
                        action     TEXT    NOT NULL,
                        detail     TEXT    DEFAULT '',
                        category   TEXT    DEFAULT 'general'
                    )
                """)

                # ── APP CONFIG ─────────────────────────────────────────────
                # PERMANENT FIX: this key/value table was referenced
                # throughout the file (setup status, Bastion AI state,
                # lock/unlock keys, EXE hash, financial year, etc.) but was
                # NEVER actually created anywhere -- every read/write just
                # silently assumed it already existed. Worked on existing
                # installs only because the table happened to get created
                # at some earlier point in this app's history; a genuinely
                # fresh install would hit "no such table: app_config" on
                # the very first config read/write. Creating it explicitly
                # here, once and for all.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key   TEXT PRIMARY KEY,
                        value TEXT DEFAULT ''
                    )
                """)

                # ── BASTION AI TABLES ───────────────────────────────────────
                # Referenced throughout bastion_ai.py but never created --
                # every record_event/queue_alert/learn call was silently
                # failing on "no such table". Fixing permanently here.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bastion_events (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts           TEXT,
                        event_type   TEXT,
                        severity     TEXT,
                        score        INTEGER DEFAULT 0,
                        detail       TEXT DEFAULT '',
                        actor        TEXT DEFAULT 'system',
                        action_taken TEXT DEFAULT '',
                        auto_healed  INTEGER DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bastion_alerts (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        subject   TEXT,
                        body      TEXT,
                        html_body TEXT DEFAULT '',
                        sent      INTEGER DEFAULT 0,
                        sent_at   TEXT DEFAULT '',
                        created_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                # Migration: older DBs may already have bastion_alerts
                # without html_body (CREATE TABLE IF NOT EXISTS skips
                # existing tables, so the column never got added).
                try:
                    ba_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(bastion_alerts)").fetchall()]
                    if 'html_body' not in ba_cols:
                        cursor.execute("ALTER TABLE bastion_alerts ADD COLUMN html_body TEXT DEFAULT ''")
                except Exception as _bae:
                    _dblog(f"[MIGRATION] bastion_alerts.html_body: {_bae}")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bastion_learning (
                        key        TEXT PRIMARY KEY,
                        value      TEXT,
                        updated_at TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bastion_escalation (
                        attack_type TEXT PRIMARY KEY,
                        level       INTEGER DEFAULT 1,
                        last_ts     TEXT,
                        updated_at  TEXT
                    )
                """)

                # ── LAN SYNC SCHEMA ──────────────────────────────────────────
                # Permanent per-PC identity for sync (separate from the
                # hardware security fingerprint -- this one is purely for
                # telling rows apart across two independent local databases).
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS device_registry (
                        id          INTEGER PRIMARY KEY CHECK (id=1),
                        device_id   TEXT    NOT NULL,
                        device_name TEXT    DEFAULT '',
                        created_at  TEXT    DEFAULT (datetime('now'))
                    )
                """)
                # Tracks the last sync_version seen FROM the other device,
                # per table, so each sync only pulls what's new.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_state (
                        peer_device_id TEXT NOT NULL,
                        table_name     TEXT NOT NULL,
                        last_version   INTEGER DEFAULT 0,
                        last_synced_at TEXT DEFAULT (datetime('now')),
                        PRIMARY KEY (peer_device_id, table_name)
                    )
                """)
                # Anomalies found after a merge (e.g. same stock sold twice
                # independently on both PCs while offline). Never blocks
                # anything -- purely a log for manual review.
                # Maps a voucher number that had to be renumbered on arrival to the
                # number it was given, so this voucher's line items land on it
                # instead of on a voucher that was never theirs.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_rekey_map (
                        table_name     TEXT NOT NULL,
                        peer_device_id TEXT NOT NULL,
                        old_value      TEXT NOT NULL,
                        new_value      TEXT NOT NULL,
                        created_at     TEXT DEFAULT (datetime('now')),
                        PRIMARY KEY (table_name, peer_device_id, old_value)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_conflicts (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        detected_at  TEXT    DEFAULT (datetime('now')),
                        conflict_type TEXT,
                        description  TEXT,
                        ref_a        TEXT,
                        ref_b        TEXT,
                        resolved     INTEGER DEFAULT 0
                    )
                """)

                # Add device_id + sync_version to every syncable table.
                # sync_version is a local logical counter (NOT wall-clock) --
                # bumped on every insert/update to that row on this device.
                SYNC_TABLES = [
                    'sales_history', 'stock_inventory', 'katti_vouchers',
                    'katti_voucher_items', 'clients_master', 'credit_ledger',
                    'uchak_inward_vouchers', 'uchak_inward_items', 'admin_creds',
                ]
                for tbl in SYNC_TABLES:
                    try:
                        cols = [r['name'] for r in cursor.execute(f"PRAGMA table_info({tbl})").fetchall()]
                        if 'device_id' not in cols:
                            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN device_id TEXT DEFAULT ''")
                        if 'sync_version' not in cols:
                            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN sync_version INTEGER DEFAULT 0")
                        if 'local_id' not in cols:
                            # local_id = this row's original autoincrement id on the
                            # device that created it. Needed because once merged
                            # onto the other PC, the autoincrement id will differ.
                            cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN local_id INTEGER DEFAULT NULL")
                    except Exception as _se:
                        _dblog(f"[SYNC SCHEMA] {tbl}: {_se}")

            return True
        except Exception as e:
            print(f"? [DB INIT ERROR] {e}")
            return False

    # ══════════════════════════════════════════════════════════════
    # LAN SYNC -- DEVICE IDENTITY
    # ══════════════════════════════════════════════════════════════

    # def is_feature_enabled(self, feature):
    #     """Plan-based feature gate. Checks app_config 'plan_features'
    #     (comma list, e.g. 'lan_sync,reports'). Empty/missing = disabled
    #     by default -- safer than accidentally enabling paid features."""
    #     try:
    #         with self._get_connection() as conn:
    #             row = conn.execute("SELECT value FROM app_config WHERE key='plan_features'").fetchone()
    #         enabled = (row['value'] or '').split(',') if row else []
    #         return feature.strip() in [f.strip() for f in enabled]
    #     except Exception:
    #         return False

    def set_plan_features(self, features_csv):
        """Called by license/setup flow, e.g. set_plan_features('lan_sync,reports')."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('plan_features',?)",
                    (str(features_csv or ''),)
                )
                conn.commit()
            return True
        except Exception:
            return False

    def get_or_create_device_id(self, device_name=''):
        """
        Permanent, random ID for THIS PC's database -- generated once,
        stored forever. Used only to tell rows apart during sync; has
        nothing to do with the hardware fingerprint / license system.
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT device_id, device_name FROM device_registry WHERE id=1").fetchone()
                if row and row['device_id']:
                    return row['device_id']
                import uuid as _uuid
                new_id = 'DEV-' + _uuid.uuid4().hex[:12].upper()
                conn.execute(
                    "INSERT OR REPLACE INTO device_registry (id, device_id, device_name) VALUES (1, ?, ?)",
                    (new_id, device_name or '')
                )
                conn.commit()
                _dblog(f"[SYNC] New device_id generated: {new_id}")
                return new_id
        except Exception as e:
            _dberr(f"[SYNC] get_or_create_device_id: {e}")
            return 'DEV-UNKNOWN'

    def is_feature_enabled(self, feature_name):
        """
        Updated plan-based feature gate.
        Defaults to ENABLED (True) if not set.
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM app_config WHERE key=?",
                    (f'feature_{feature_name}',)
                ).fetchone()

            # If the key is missing (None), return True (Fail-Open)
            if row is None:
                return True

            # Only disable if explicitly set to '0'
            return str(row['value']) == '1'

        except Exception as e:
            _dberr(f"[FEATURE] check {feature_name} failed: {e}")
            # Default to True so the app remains functional if DB has an error
            return True

    def set_feature_enabled(self, feature_name, enabled):
        """Admin/license tool calls this to turn a plan feature on/off."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                    (f'feature_{feature_name}', '1' if enabled else '0')
                )
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[FEATURE] set {feature_name}: {e}")
            return False

    def get_or_create_shop_id(self):
        """
        Every PC belonging to the SAME shop must share this exact value --
        it's how sync tells "another PC in my shop" apart from "a totally
        different shop's PC that happens to be on the same WiFi router".
        Generated once on the FIRST PC of a shop; every additional PC in
        that shop must call set_shop_id() with the same value during setup
        instead of getting a fresh random one (see set_shop_id below).
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT value FROM app_config WHERE key='shop_id'").fetchone()
                if row and row['value']:
                    return row['value']
                import uuid as _uuid
                new_id = 'SHOP-' + _uuid.uuid4().hex[:8].upper()
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('shop_id',?)", (new_id,)
                )
                conn.commit()
                _dblog(f"[SYNC] New shop_id generated: {new_id}")
                return new_id
        except Exception as e:
            _dberr(f"[SYNC] get_or_create_shop_id: {e}")
            return 'SHOP-UNKNOWN'

    def set_shop_id(self, shop_id):
        """
        Called once, manually, when setting up the 2nd/3rd/Nth PC for a
        shop that already has a shop_id from its first PC. The owner
        reads the shop_id off PC #1 (shown in settings) and types it into
        every additional PC during setup. After this, all PCs broadcast
        the SAME shop_id and discover each other automatically.
        """
        try:
            sid = str(shop_id or '').strip().upper()
            if not sid.startswith('SHOP-') or len(sid) < 8:
                return {'status': 'error', 'message': 'Invalid shop ID format (expected SHOP-XXXXXXXX)'}
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('shop_id',?)", (sid,)
                )
                conn.commit()
            _dblog(f"[SYNC] shop_id manually set: {sid}")
            return {'status': 'success', 'shop_id': sid}
        except Exception as e:
            _dberr(f"[SYNC] set_shop_id: {e}")
            return {'status': 'error', 'message': str(e)}

    def set_health_config(self, health_url, admin_secret, client_id):
        """
        One-time setup: store the health dashboard connection details
        permanently in this PC's own DB, instead of requiring OS
        environment variables to be set every single launch. This is
        what makes onboarding a new shop scale -- run once per shop
        (or per PC), never touch it again.
        """
        try:
            with self._get_connection() as conn:
                for k, v in [
                    ('health_url', str(health_url or '').strip()),
                    ('health_secret', str(admin_secret or '').strip()),
                    ('health_client_id', str(client_id or '').strip()),
                ]:
                    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)", (k, v))
                conn.commit()
            _dblog(f"[HEALTH] config saved: client_id={client_id}")
            return {'status': 'success'}
        except Exception as e:
            _dberr(f"[HEALTH] set_health_config: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_health_config(self):
        """Reads the persisted health-reporting config, falling back to
        OS environment variables if nothing was saved (back-compat)."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM app_config WHERE key IN "
                    "('health_url','health_secret','health_client_id')"
                ).fetchall()
            cfg = {r['key']: r['value'] for r in rows}
            return {
                'url': cfg.get('health_url') or os.environ.get('AURUM_HEALTH_URL', ''),
                'secret': cfg.get('health_secret') or os.environ.get('AURUM_ADMIN_SECRET', ''),
                'client_id': cfg.get('health_client_id') or os.environ.get('AURUM_CLIENT_ID', ''),
            }
        except Exception as e:
            _dberr(f"[HEALTH] get_health_config: {e}")
            return {'url': '', 'secret': '', 'client_id': ''}

    def auto_provision_health_key(self, health_base_url):
        """
        Fully automatic key generation sending both ID and Business Name.
        """
        try:
            existing = self.get_health_config()
            # Fetch business name from your business_profile table
            biz_name = self.get_config('business_name', 'Unnamed Shop')

            if existing.get('url') and existing.get('secret') and existing.get('client_id'):
                return {'status': 'already_configured', 'client_id': existing['client_id']}

            client_id = self.get_or_create_shop_id()
            events_url = health_base_url.rstrip('/') + '/api/bastion/events'

            import urllib.request, urllib.error, json as _json

            payload = self.get_provisioning_payload()

            req = urllib.request.Request(
                events_url,
                data=_json.dumps(payload).encode('utf-8'),
                method='POST',
                headers={
                    'Content-Type': 'application/json',
                    'x-admin-secret': _AUTO_PROVISION_KEY
                },
            )

            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_data = resp.read().decode('utf-8')
                    result = _json.loads(resp_data)

                    if result.get('ok'):
                        shop_secret = result.get('shop_secret', 'DEFAULT_SECRET')
                        self.set_health_config(events_url, shop_secret, client_id)
                        _dblog(f"[HEALTH] Provisioned {biz_name} ({client_id})")
                        return {'status': 'success', 'client_id': client_id}
                    else:
                        error_msg = result.get('error', 'Unknown')
                        _dberr(f"[HEALTH] Rejected: {error_msg}")
                        return {'status': 'error', 'message': error_msg}

            except urllib.error.HTTPError as e:
                error_body = e.read().decode('utf-8', errors='ignore')
                _dberr(f"[HEALTH] HTTP {e.code}: {error_body}")
                return {'status': 'error', 'message': f"HTTP {e.code}: {error_body}"}
            except Exception as e:
                _dberr(f"[HEALTH] Request failed: {str(e)}")
                return {'status': 'error', 'message': str(e)}

        except Exception as e:
            _dberr(f"[HEALTH] Exception: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    # ══════════════════════════════════════════════════════════════
    # LAN SYNC -- ENGINE
    # ══════════════════════════════════════════════════════════════

    SYNC_TABLES = [
        'sales_history', 'stock_inventory', 'katti_vouchers',
        'katti_voucher_items', 'clients_master', 'credit_ledger',
        'uchak_inward_vouchers', 'uchak_inward_items', 'admin_creds',
    ]

    # The human-facing unique key each synced table is numbered by. A clash on
    # one of these means two PCs invented the same number while apart -- not a
    # duplicate of the same real event.
    SYNC_NATURAL_KEY = {
        'sales_history':         'vch_id',
        'katti_vouchers':        'vch_id',
        'uchak_inward_vouchers': 'vch_id',
        'stock_inventory':       'tag_id',
        'clients_master':        'name',
    }

    # Rows that point back at a parent voucher. If a parent gets re-keyed on
    # arrival, its children must follow or the voucher arrives with no lines.
    SYNC_CHILD_REF = {
        'katti_voucher_items':  ('katti_vouchers',        'vch_id'),
        'uchak_inward_items':   ('uchak_inward_vouchers', 'vch_id'),
        'credit_ledger':        ('sales_history',         'vch_reference'),
    }

    # Clashes here are the SAME real-world entity entered on two PCs (one
    # customer, one tag). Keep the local copy and log it; do not re-key.
    SYNC_MERGE_ON_CLASH = {'clients_master'}

    @staticmethod
    def _sync_rekey_value(original, peer_device_id):
        """Build a collision-free version of a voucher number that still shows
        plainly which PC it came from, e.g. VCH-001 -> VCH-001~A31F."""
        tail = (str(peer_device_id or 'PEER').replace('-', '')[-4:] or 'PEER').upper()
        return f"{original}~{tail}"

    def _sync_record_rekey(self, conn, table_name, peer_device_id, old_value, new_value):
        conn.execute(
            "INSERT OR REPLACE INTO sync_rekey_map "
            "(table_name, peer_device_id, old_value, new_value) VALUES (?,?,?,?)",
            (table_name, peer_device_id, str(old_value), str(new_value))
        )

    def _sync_lookup_rekey(self, conn, table_name, peer_device_id, old_value):
        try:
            r = conn.execute(
                "SELECT new_value FROM sync_rekey_map "
                "WHERE table_name=? AND peer_device_id=? AND old_value=?",
                (table_name, peer_device_id, str(old_value))
            ).fetchone()
            return r['new_value'] if r else None
        except Exception:
            return None

    def _sync_log_conflict(self, conn, ctype, description, ref_a='', ref_b=''):
        """Record a merge anomaly once. Never blocks the merge."""
        try:
            already = conn.execute(
                "SELECT id FROM sync_conflicts WHERE conflict_type=? AND description=? AND resolved=0",
                (ctype, description)
            ).fetchone()
            if not already:
                conn.execute(
                    "INSERT INTO sync_conflicts (conflict_type, description, ref_a, ref_b) "
                    "VALUES (?,?,?,?)", (ctype, description, str(ref_a), str(ref_b))
                )
        except Exception as e:
            _dberr(f"[SYNC] could not log conflict: {e}")

    def _sync_resolve_clash(self, conn, tbl, row, cols, values,
                            nk_col, peer_device_id, clash):
        """A row from another PC hit a UNIQUE/PK constraint.

        The old code used INSERT OR IGNORE here, which threw the row away
        silently while still logging it as received -- a bill could vanish
        with no trace. Nothing is ever dropped without a conflict record now.
        Returns True if the row was stored under a new key.
        """
        original = row.get(nk_col) if nk_col else None

        if tbl in self.SYNC_MERGE_ON_CLASH:
            self._sync_log_conflict(
                conn, 'duplicate_record',
                f"{tbl} '{original}' was created on two PCs. Kept the copy already on this PC.",
                str(original), peer_device_id)
            return False

        if not nk_col or original is None:
            self._sync_log_conflict(
                conn, 'merge_rejected',
                f"A {tbl} row from another PC could not be merged: {clash}",
                '', peer_device_id)
            return False

        new_key = self._sync_rekey_value(original, peer_device_id)
        values = list(values)
        values[cols.index(nk_col)] = new_key
        try:
            conn.execute(
                f"INSERT INTO {tbl} ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
                values)
        except Exception as e:
            self._sync_log_conflict(
                conn, 'merge_rejected',
                f"A {tbl} row from another PC could not be merged even after "
                f"renumbering it to '{new_key}': {e}",
                str(original), peer_device_id)
            return False

        self._sync_record_rekey(conn, tbl, peer_device_id, original, new_key)
        self._sync_log_conflict(
            conn, 'duplicate_voucher_id',
            f"{tbl} '{original}' was created on two PCs at the same time. The copy "
            f"from the other PC was saved as '{new_key}' so nothing was lost. "
            f"Renumber it when convenient.",
            str(original), str(new_key))
        _dblog(f"[SYNC] {tbl} '{original}' clashed -- stored as '{new_key}' instead of being dropped")
        return True

    # Add this method to your DBManager class in db_manager.py
    def get_provisioning_payload(self):
        return {
            'client_id': self.get_or_create_shop_id(),
            'business_name': self.get_config('business_name', 'Unnamed Shop'),
            'events': [],
            'summary': 'Periodic Sync'
        }

    def stamp_row_for_sync(self, table_name, row_id, pk_col='id'):
        """
        Stamps a new row for synchronization.
        Includes robust fallback for rowid lookups on TEXT primary key tables.
        """
        try:
            if table_name not in self.SYNC_TABLES:
                return False

            dev_id = self.get_or_create_device_id()

            with self._get_connection() as conn:
                # 1. Get the next version for this device
                nxt = conn.execute(
                    f"SELECT COALESCE(MAX(sync_version),0)+1 as n FROM {table_name} WHERE device_id=?",
                    (dev_id,)
                ).fetchone()
                next_version = int(nxt['n'] or 1)

                # 2. Safely retrieve the rowid for local_id mapping
                # We explicitly cast row_id to string to match TEXT PKs if necessary
                rowid_row = conn.execute(
                    f"SELECT rowid FROM {table_name} WHERE {pk_col}=?", (row_id,)
                ).fetchone()

                # Fallback: if rowid is missing, use a hash of the row_id as local_id
                local_id_val = rowid_row[0] if rowid_row else abs(hash(str(row_id)))

                # 3. Apply the sync stamps
                conn.execute(
                    f"UPDATE {table_name} SET device_id=?, sync_version=?, local_id=? WHERE {pk_col}=?",
                    (dev_id, next_version, local_id_val, row_id)
                )
                conn.commit()

                _dblog(f"[SYNC] Stamped {table_name} [{row_id}] as v{next_version}")
            return True

        except Exception as e:
            _dberr(f"[SYNC] stamp_row_for_sync {table_name} failed for ID {row_id}: {e}")
            return False

    def get_logs_last_7_days(self):
        """
        Retrieves logs from the last 7 days from the local database.
        """
        import datetime
        try:
            seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()

            # Replace this query with your actual DB query logic
            # Example for a simple list-based log:
            all_logs = self.load_all_logs()
            recent_logs = [log for log in all_logs if log.get('ts', '') >= seven_days_ago]

            return recent_logs
        except Exception as e:
            _dberr(f"[DB] Error fetching logs: {str(e)}")
            return []

    def load_all_logs(self):
        """
        Fetches all event logs from the local database.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT * FROM logs ORDER BY ts DESC")
                # Convert rows to dictionary format
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            _dberr(f"[DB] load_all_logs failed: {e}")
            return []

    def get_changes_since(self, peer_device_id, since_versions):
        """
        Called when responding to a sync request FROM another device.
        since_versions: {table_name: last_version_that_peer_already_has_from_ME}
        Returns: {table_name: [rows...]} -- only rows ORIGINATING on this
        device (device_id = my own id) that the peer doesn't have yet.
        Rows the peer itself created are never sent back to it.
        """
        try:
            my_id = self.get_or_create_device_id()
            out = {}
            with self._get_connection() as conn:
                for tbl in self.SYNC_TABLES:
                    last_v = int(since_versions.get(tbl, 0) or 0)
                    rows = conn.execute(
                        f"SELECT * FROM {tbl} WHERE device_id=? AND sync_version>? ORDER BY sync_version ASC LIMIT 500",
                        (my_id, last_v)
                    ).fetchall()
                    if rows:
                        out[tbl] = [dict(r) for r in rows]
            return {'status': 'success', 'device_id': my_id, 'changes': out}
        except Exception as e:
            _dberr(f"[SYNC] get_changes_since: {e}")
            return {'status': 'error', 'message': str(e), 'changes': {}}

    def _describe_synced_row(self, table_name, row):
        """
        Turns a raw row dict into a short, human-readable description for
        the sync log -- e.g. "Staff account 'cashier1' added" or
        "Bill BILL-0042 for Ramesh Kumar, Rs 12,500". Keeps logs readable
        instead of dumping raw column data.
        """
        try:
            if table_name == 'admin_creds':
                role = 'OWNER' if int(row.get('is_owner') or 0) == 1 else 'staff'
                return f"{role} login '{row.get('username', '?')}'"
            if table_name == 'sales_history':
                amt = row.get('total_amount') or 0
                return f"Bill {row.get('vch_id', '?')} for {row.get('customer', '?')}, Rs {amt}"
            if table_name == 'stock_inventory':
                return (f"Stock item '{row.get('it_code', '?')}' ({row.get('it_name', '?')}) "
                        f"tag={row.get('tag_id', '-')} pcs={row.get('pcs', 0)}")
            if table_name in ('katti_vouchers',):
                return f"Katti voucher {row.get('vch_id', '?')}, {row.get('total_weight', 0)}g"
            if table_name == 'katti_voucher_items':
                return f"Katti item '{row.get('it_name', '?')}' for voucher {row.get('vch_id', '?')}"
            if table_name == 'clients_master':
                return f"Client '{row.get('name', '?')}' (phone {row.get('phone', '-')})"
            if table_name == 'credit_ledger':
                return f"Ledger entry for '{row.get('client_name', '?')}' ref {row.get('vch_reference', '-')}"
            if table_name in ('uchak_inward_vouchers',):
                return f"Uchak inward voucher {row.get('vch_id', '?')}"
            if table_name == 'uchak_inward_items':
                return f"Uchak item '{row.get('it_name', '?')}' x{row.get('pcs', 0)}"
            return str(row)
        except Exception:
            return str(row)

    def apply_incoming_rows(self, peer_device_id, changes):
        """
        Merge rows that originated on the OTHER device into this DB.
        Identity for dedup = (device_id, local_id) -- never the local
        autoincrement id, since that differs per machine. Existing tables
        already have a few real UNIQUE constraints (tag_id, vch_id) which
        we respect via INSERT OR IGNORE / matching update.
        """
        applied = {}
        failed = {}
        try:
            with self._get_connection() as conn:
                for tbl, rows in (changes or {}).items():
                    if tbl not in self.SYNC_TABLES or not rows:
                        continue
                    count = 0
                    for row in rows:
                        try:
                            # Skip if we already have this exact peer row.
                            # rowid, not id: katti_vouchers and
                            # uchak_inward_vouchers are keyed by vch_id and
                            # have no id column, so asking for one threw and
                            # sent every voucher of those two kinds down the
                            # skip path -- they never synced at all, while
                            # their line items did and attached themselves to
                            # whichever voucher already held that number here.
                            existing = conn.execute(
                                f"SELECT rowid FROM {tbl} WHERE device_id=? AND local_id=?",
                                (peer_device_id, row.get('local_id'))
                            ).fetchone()
                            if existing:
                                # Already have this exact peer row: settled,
                                # count it so the sender advances past it.
                                count += 1
                                continue

                            # admin_creds has no SQL UNIQUE on username -- check
                            # for a same-username row created independently on
                            # a different device (two offline PCs both adding
                            # the same staff username before ever syncing).
                            skip_row = False
                            force_is_owner_zero = False
                            if tbl == 'admin_creds':
                                uname = str(row.get('username') or '').strip()
                                incoming_is_owner = int(row.get('is_owner') or 0) == 1
                                dupe = conn.execute(
                                    "SELECT id, password, device_id, is_owner FROM admin_creds "
                                    "WHERE LOWER(TRIM(username))=LOWER(TRIM(?))", (uname,)
                                ).fetchone()

                                if incoming_is_owner:
                                    local_owner = conn.execute(
                                        "SELECT id, username FROM admin_creds WHERE is_owner=1 LIMIT 1"
                                    ).fetchone()
                                    if local_owner is None:
                                        # No owner here yet -- accept this one as
                                        # THIS PC's owner too (same person, same
                                        # shop, logging in from either PC).
                                        pass
                                    elif local_owner['username'].strip().lower() == uname.lower():
                                        # Same owner we already know about
                                        # (e.g. password was changed on the peer) --
                                        # let it through as a normal update via
                                        # INSERT OR IGNORE below; if truly new
                                        # row, dupe check below still applies.
                                        pass
                                    else:
                                        # A DIFFERENT owner exists locally already.
                                        # Two independently-created owners is a
                                        # real conflict -- never silently let one
                                        # win. Flag it, keep the local owner,
                                        # import the incoming person as staff
                                        # instead so at least their login still
                                        # works going forward.
                                        desc = (f"Two different owner accounts were created independently: "
                                                f"'{local_owner['username']}' (this PC) and '{uname}' (other PC). "
                                                f"Kept '{local_owner['username']}' as owner here; "
                                                f"'{uname}' was added as a staff login instead. Review and decide "
                                                f"which one should be the real owner.")
                                        already = conn.execute(
                                            "SELECT id FROM sync_conflicts WHERE conflict_type='owner_clash' "
                                            "AND description=? AND resolved=0", (desc,)
                                        ).fetchone()
                                        if not already:
                                            conn.execute(
                                                "INSERT INTO sync_conflicts (conflict_type, description, ref_a, ref_b) "
                                                "VALUES ('owner_clash', ?, ?, ?)",
                                                (desc, local_owner['username'], uname)
                                            )
                                        force_is_owner_zero = True

                                if dupe and not incoming_is_owner:
                                    if dupe['password'] != row.get('password'):
                                        desc = (f"Staff username '{uname}' was created independently on "
                                                f"two PCs with different passwords. Local copy kept; "
                                                f"incoming copy from the other PC was skipped.")
                                        already = conn.execute(
                                            "SELECT id FROM sync_conflicts WHERE conflict_type='staff_username_clash' "
                                            "AND description=? AND resolved=0", (desc,)
                                        ).fetchone()
                                        if not already:
                                            conn.execute(
                                                "INSERT INTO sync_conflicts (conflict_type, description, ref_a, ref_b) "
                                                "VALUES ('staff_username_clash', ?, ?, ?)",
                                                (desc, uname, peer_device_id)
                                            )
                                    skip_row = True  # never duplicate or overwrite an existing staff username

                            if skip_row:
                                # Deterministic refusal (username clash) with
                                # a conflict record written: settled, the
                                # sender advances past it.
                                count += 1
                                continue

                            # If this row's parent voucher had to be renumbered
                            # when it arrived, point the row at the number the
                            # parent actually got.
                            child_ref = self.SYNC_CHILD_REF.get(tbl)
                            if child_ref:
                                parent_tbl, ref_col = child_ref
                                if row.get(ref_col):
                                    moved = self._sync_lookup_rekey(
                                        conn, parent_tbl, peer_device_id, row[ref_col])
                                    if moved:
                                        row = dict(row)
                                        row[ref_col] = moved

                            cols = [c for c in row.keys() if c != 'id']
                            placeholders = ','.join(['?'] * len(cols))
                            colnames = ','.join(cols)
                            values = [row[c] for c in cols]
                            # Force device_id to the PEER's id (not overwritten by us)
                            if 'device_id' in cols:
                                values[cols.index('device_id')] = peer_device_id
                            if tbl == 'admin_creds' and force_is_owner_zero and 'is_owner' in cols:
                                values[cols.index('is_owner')] = 0

                            # A plain INSERT, so a clash raises instead of being
                            # thrown away. _sync_resolve_clash decides whether the
                            # row is a genuine duplicate or a number two PCs both
                            # invented, and stores it either way.
                            try:
                                conn.execute(
                                    f"INSERT INTO {tbl} ({colnames}) VALUES ({placeholders})",
                                    values
                                )
                            except sqlite3.IntegrityError as _clash:
                                if not self._sync_resolve_clash(
                                        conn, tbl, row, cols, values,
                                        self.SYNC_NATURAL_KEY.get(tbl),
                                        peer_device_id, _clash):
                                    # Decided duplicate / unmergeable, with a
                                    # conflict record written: settled, the
                                    # sender advances past it.
                                    count += 1
                                    continue
                            count += 1

                            # ── DETAILED TRANSFER LOG ──────────────────────
                            # Plain-English description of exactly what just
                            # moved from the peer into this DB, per row.
                            desc_str = self._describe_synced_row(tbl, row)
                            _dblog(f"[SYNC] RECEIVED from {peer_device_id} -> {tbl}: {desc_str}")
                        except Exception as _re:
                            # Unexpected failure (bad column, corrupt value…).
                            # Counted separately from deterministic skips
                            # (dedup, username clashes, resolved duplicates),
                            # which the sender may treat as settled. A failed
                            # row is retried on the next exchange instead of
                            # being silently marked as sent.
                            failed[tbl] = failed.get(tbl, 0) + 1
                            _dblog(f"[SYNC] apply row FAILED {tbl} (will retry): {_re}")
                    if count:
                        applied[tbl] = count

                # Remember how far we've pulled from this peer
                for tbl, rows in (changes or {}).items():
                    if not rows:
                        continue
                    max_v = max(int(r.get('sync_version') or 0) for r in rows)
                    conn.execute(
                        "INSERT OR REPLACE INTO sync_state (peer_device_id, table_name, last_version, last_synced_at) "
                        "VALUES (?,?,?,datetime('now'))",
                        (peer_device_id, tbl, max_v)
                    )
                conn.commit()
            if applied:
                total = sum(applied.values())
                breakdown = ', '.join(f"{v} {k}" for k, v in applied.items())
                _dblog(f"[SYNC] MERGE COMPLETE from {peer_device_id}: {total} row(s) total ({breakdown})")
            else:
                _dblog(f"[SYNC] Sync with {peer_device_id} complete -- nothing new to merge")
            if failed:
                _dberr(f"[SYNC] {sum(failed.values())} row(s) FAILED (will retry): " +
                       ', '.join(f"{v} {k}" for k, v in failed.items()))
            return {'status': 'success', 'applied': applied, 'failed': failed}
        except Exception as e:
            _dberr(f"[SYNC] apply_incoming_rows: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_my_sync_versions(self, peer_device_id):
        """What versions have I already pulled from this peer, per table?
        Sent to the peer so it knows what's new to send me."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT table_name, last_version FROM sync_state WHERE peer_device_id=?",
                    (peer_device_id,)
                ).fetchall()
                return {r['table_name']: r['last_version'] for r in rows}
        except Exception as e:
            _dberr(f"[SYNC] get_my_sync_versions: {e}")
            return {}

    def detect_stock_conflicts(self):
        """
        Run after every merge. Checks whether total weight sold per touch
        group (across BOTH devices' sales now in this DB) ever exceeded
        what was actually in stock at the time. Does not block or fix
        anything -- just logs a row to sync_conflicts for manual review.
        Lightweight heuristic: total sold for a touch vs total ever
        received (opening + katti inward) for that touch. If sold > received,
        something was double-counted (most likely a double-sale of the
        same physical stock from two offline devices).
        """
        try:
            import json as _json
            with self._get_connection() as conn:
                received = conn.execute("""
                    SELECT ROUND(touch,2) as touch, COALESCE(SUM(gr_wt),0) as wt
                    FROM stock_inventory
                    WHERE touch IS NOT NULL AND touch > 0
                    GROUP BY ROUND(touch,2)
                """).fetchall()
                received_map = {round(float(r['touch']), 2): float(r['wt']) for r in received}

                sales = conn.execute(
                    "SELECT vch_id, device_id, items FROM sales_history WHERE items IS NOT NULL"
                ).fetchall()
                sold_map = {}
                sold_refs = {}
                for s in sales:
                    try:
                        items = _json.loads(s['items'] or '[]')
                    except Exception:
                        continue
                    for it in items:
                        wt = float(it.get('weight') or it.get('gr_wt') or 0)
                        tv = float(it.get('touch') or 0)
                        if not tv:
                            try:
                                p = float(it.get('it_code') or it.get('code') or 0)
                                if 1 <= p <= 100:
                                    tv = p
                            except Exception:
                                pass
                        if tv > 0 and wt > 0:
                            key = round(tv, 2)
                            sold_map[key] = sold_map.get(key, 0) + wt
                            sold_refs.setdefault(key, []).append(s['vch_id'])

                new_conflicts = 0
                for touch, sold_wt in sold_map.items():
                    avail = received_map.get(touch, 0)
                    if sold_wt > avail + 0.05:  # small float tolerance
                        refs = sold_refs.get(touch, [])
                        desc = (f"Touch {touch}%: sold {round(sold_wt, 3)}g across bills "
                                f"but only {round(avail, 3)}g was ever recorded as stock. "
                                f"Possible double-sale from offline sync merge.")
                        already = conn.execute(
                            "SELECT id FROM sync_conflicts WHERE conflict_type='stock_oversold' "
                            "AND description=? AND resolved=0",
                            (desc,)
                        ).fetchone()
                        if not already:
                            conn.execute(
                                "INSERT INTO sync_conflicts (conflict_type, description, ref_a, ref_b) "
                                "VALUES ('stock_oversold', ?, ?, ?)",
                                (desc, refs[0] if refs else '', ','.join(refs[1:]) if len(refs) > 1 else '')
                            )
                            new_conflicts += 1
                conn.commit()
            if new_conflicts:
                _dblog(f"[SYNC] {new_conflicts} new stock conflict(s) flagged")
            return {'status': 'success', 'new_conflicts': new_conflicts}
        except Exception as e:
            _dberr(f"[SYNC] detect_stock_conflicts: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_sync_conflicts(self, include_resolved=False):
        try:
            with self._get_connection() as conn:
                q = "SELECT * FROM sync_conflicts"
                if not include_resolved:
                    q += " WHERE resolved=0"
                q += " ORDER BY id DESC"
                rows = conn.execute(q).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            _dberr(f"[SYNC] get_sync_conflicts: {e}")
            return []

    def resolve_sync_conflict(self, conflict_id):
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE sync_conflicts SET resolved=1 WHERE id=?", (int(conflict_id),))
                conn.commit()
            return True
        except Exception:
            return False

    def generate_unique_tag_id(self):
        while True:
            new_id = "".join([str(random.randint(0, 9)) for _ in range(10)])
            if self.get_scalar("SELECT COUNT(*) FROM stock_inventory WHERE tag_id=?", (new_id,)) == 0:
                return new_id

    def _is_weight_stock_row(self, tag_id):
        """Returns True if this row is a weight-based stock row (not a physical tagged piece)."""
        if not tag_id: return True
        tag_str = str(tag_id).strip()
        return tag_str in ('N/A', '', 'null') or tag_str.startswith('KATTI-')

    def execute_query(self, query, params=()):
        try:
            with self._get_connection() as conn:
                conn.execute(query, params);
                conn.commit()
            return True
        except:
            return False

    def fetch_one(self, query, params=()):
        try:
            with self._get_connection() as conn:
                res = conn.execute(query, params).fetchone()
                return dict(res) if res else None
        except:
            return None

    def fetch_all(self, query, params=()):
        try:
            with self._get_connection() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in rows]
        except:
            return []

    def get_scalar(self, query, params=()):
        try:
            with self._get_connection() as conn:
                res = conn.execute(query, params).fetchone()
                return res[0] if res else 0
        except:
            return 0

    def is_setup_complete(self):
        return self.get_scalar("SELECT COUNT(*) FROM admin_creds") > 0

    def _hash_pw(self, pw):
        """SHA-256 hash password. Handles both plain and already-hashed input."""
        import hashlib as _hl
        s = str(pw).strip()
        # Already a SHA-256 hex digest (64 chars) -- return as-is
        if len(s) == 64 and all(c in '0123456789abcdef' for c in s.lower()):
            return s
        return _hl.sha256(s.encode('utf-8')).hexdigest()

    def authenticate_user(self, username, password):
        try:
            pw_hash = self._hash_pw(password)
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT id, username, COALESCE(is_owner,0) as is_owner FROM admin_creds "
                    "WHERE LOWER(TRIM(username))=LOWER(TRIM(?)) "
                    "  AND (TRIM(password)=? OR TRIM(password)=?)",
                    (str(username).strip(), pw_hash, str(password).strip())
                ).fetchone()
                if row:
                    return {"authenticated": True, "role": "admin" if row["is_owner"] == 1 else "staff",
                            "username": row["username"]}
                return {"authenticated": False, "role": "visitor"}
        except Exception as e:
            _dblog('[DB AUTH ERROR] {e}')
            return {"authenticated": False, "role": "visitor"}

    def authenticate_user_by_password(self, password):
        """Fallback: check password against ANY account in admin_creds."""
        try:
            pw_hash = self._hash_pw(password)
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT id, username, COALESCE(is_owner,0) as is_owner FROM admin_creds "
                    "WHERE TRIM(password)=? OR TRIM(password)=? LIMIT 1",
                    (pw_hash, str(password).strip())
                ).fetchone()
                if row:
                    _dblog(f"[DB AUTH] Matched username={row['username']!r}")
                    return {"authenticated": True, "role": "admin" if row["is_owner"] == 1 else "staff",
                            "username": row["username"]}
                return {"authenticated": False, "role": "visitor"}
        except Exception as e:
            _dblog(f'[DB AUTH FALLBACK ERROR] {e}')
            return {"authenticated": False, "role": "visitor"}

    def complete_initial_setup(self, biz_name, username, password, owner_name=None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO business_profile (id,biz_name,owner_name) VALUES (1,?,?)",
                    (biz_name, owner_name)
                )
                exists = conn.execute(
                    "SELECT COUNT(*) FROM admin_creds WHERE LOWER(username)=LOWER(?)", (username,)
                ).fetchone()[0]
                if exists == 0:
                    cur = conn.execute("INSERT INTO admin_creds (username,password,is_owner) VALUES (?,?,1)",
                                       (username, password))
                    owner_row_id = cur.lastrowid
                else:
                    conn.execute("UPDATE admin_creds SET password=?, is_owner=1 WHERE LOWER(username)=LOWER(?)",
                                 (password, username))
                    owner_row_id = conn.execute(
                        "SELECT id FROM admin_creds WHERE LOWER(username)=LOWER(?)", (username,)
                    ).fetchone()[0]
                conn.commit()
            # Owner account DOES sync (per explicit decision: owner can log
            # in on either PC). Stamped like any other syncable row -- the
            # is_owner=1 flag travels WITH it, but apply_incoming_rows has
            # a separate, stricter rule for this: an incoming owner row is
            # only ever accepted as a SECOND login method for the SAME
            # person if no conflicting owner already exists locally with a
            # different username -- see apply_incoming_rows for the guard.
            try:
                self.stamp_row_for_sync('admin_creds', owner_row_id)
            except Exception as _ose:
                _dblog(f"[SYNC] stamp skip (owner admin_creds): {_ose}")
            return True
        except Exception as e:
            print(f"? [SETUP ERROR] {e}");
            return False

    def get_all_staff(self):
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, username, COALESCE(is_owner,0) as is_owner, COALESCE(permissions,'[]') as permissions FROM admin_creds ORDER BY id ASC"
                ).fetchall()
                return [{
                    "id": r["id"],
                    "username": r["username"],
                    "role": "admin" if r["is_owner"] == 1 else "staff",
                    "permissions": json.loads(r["permissions"]) if isinstance(r["permissions"], str) else (r["permissions"] or [])
                } for r in rows]
        except Exception as e:
            _dblog(f"[STAFF] get_all_staff error: {e}")
            return []

    def add_staff_user(self, username, password, permissions=None):
        try:
            import hashlib as _hl
            import json as _json
            u = str(username).strip()
            p = str(password).strip()
            if not u or not p:
                return False, "Username and password are required."
            if len(p) < 4:
                return False, "Password must be at least 4 characters."
            hashed = _hl.sha256(p.encode('utf-8')).hexdigest()
            perms_json = _json.dumps(permissions or [])
            with self._get_connection() as conn:
                exists = conn.execute(
                    "SELECT COUNT(*) FROM admin_creds WHERE LOWER(TRIM(username))=LOWER(TRIM(?))", (u,)
                ).fetchone()[0]
                if exists > 0:
                    return False, f"Username '{u}' is already taken."
                cur = conn.execute(
                    "INSERT INTO admin_creds (username, password, permissions) VALUES (?,?,?)",
                    (u, hashed, perms_json)
                )
                conn.commit()
                new_id = cur.lastrowid
            try:
                self.stamp_row_for_sync('admin_creds', new_id)
            except Exception as _ase:
                _dblog(f"[SYNC] stamp skip (admin_creds): {_ase}")
            _dblog(f"[STAFF] Added staff: {u}")
            return True, f"Staff '{u}' registered successfully."
        except Exception as e:
            return False, f"Database Error: {str(e)}"

    def update_staff_permissions(self, staff_id, permissions):
        try:
            import json as _json
            sid = int(staff_id)
            perms_json = _json.dumps(permissions or [])
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE admin_creds SET permissions=? WHERE id=?",
                    (perms_json, sid)
                )
                conn.commit()
            _dblog(f"[STAFF] Permissions updated for id={sid}")
            return True, f"Permissions updated for staff id={sid}."
        except Exception as e:
            return False, f"Database Error: {str(e)}"

    def is_touch_valid(self, touch_value):
        try:
            val_float = float(touch_value)
            return self.get_scalar(
                "SELECT COUNT(*) FROM touch_groups WHERE value=? OR name=?",
                (val_float, str(touch_value).strip())
            ) > 0
        except ValueError:
            return self.get_scalar(
                "SELECT COUNT(*) FROM touch_groups WHERE UPPER(TRIM(name))=UPPER(TRIM(?))",
                (str(touch_value).strip(),)
            ) > 0
        except:
            return False

    def add_category(self, code, name):
        return self.execute_query("INSERT INTO categories (code,name) VALUES (?,?)", (code, name))

    def get_all_categories(self):
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM categories").fetchall()]

    def add_touch_group(self, name, value, wastage):
        return self.execute_query(
            "INSERT INTO touch_groups (name,value,wastage) VALUES (?,?,?)",
            (name, float(value or 0), float(wastage or 0))
        )

    def get_all_touch_groups(self):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("SELECT * FROM touch_groups").fetchall()]
        except:
            return []

    def add_product_master(self, code, name, category, touch, wastage,
                           making_charge=0, making_type='percent', sale_price=0, hsn=''):
        """Save a product. The four retail arguments are optional so every
        existing caller keeps working unchanged."""
        return self.execute_query(
            """INSERT OR REPLACE INTO product_master
                   (code,name,category,touch,wastage,making_charge,making_type,sale_price,hsn)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (code, name, category, touch, float(wastage or 0),
             float(making_charge or 0), str(making_type or 'percent'),
             float(sale_price or 0), str(hsn or ''))
        )

    def get_all_products(self):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(
                    """SELECT code,name,category,touch,wastage,
                              COALESCE(making_charge,0) AS making_charge,
                              COALESCE(making_type,'percent') AS making_type,
                              COALESCE(sale_price,0) AS sale_price,
                              COALESCE(hsn,'') AS hsn
                       FROM product_master"""
                ).fetchall()]
        except:
            return []

    def get_weight_stock_it_codes(self):
        """Returns only WEIGHT-based IT codes (gr_wt > 0).
        Excludes uchak/piece stock (gr_wt=0, pcs>0 rows).
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT DISTINCT it_code, it_name, touch, gr_wt, pcs
                    FROM stock_inventory
                    WHERE it_code IS NOT NULL
                      AND TRIM(it_code) != ''
                      AND gr_wt > 0
                      AND (
                          tag_id IS NULL OR tag_id = '' OR
                          tag_id = 'N/A' OR tag_id LIKE 'KATTI-%'
                      )
                    ORDER BY it_code ASC
                """).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"? [WEIGHT STOCK IT CODES ERROR] {e}");
            return []

    def get_pos_stock(self):
        """Return live inventory rows used by retail billing."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT id, TRIM(it_code) AS it_code, it_name, tag_id, pcs,
                           gr_wt, nt_wt, touch, huid
                    FROM stock_inventory
                    WHERE TRIM(COALESCE(it_code, '')) != ''
                      AND (COALESCE(gr_wt, 0) > 0 OR COALESCE(pcs, 0) > 0)
                    ORDER BY it_code, id
                """).fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            _dberr(f"[POS STOCK ERROR] {e}")
            return []

    def validate_retail_items(self, items):
        """Validate item identity, touch, and available stock before a retail sale."""
        try:
            errors = []
            requirements = {}
            with self._get_connection() as conn:
                for item in items if isinstance(items, list) else []:
                    code = str(item.get('code') or item.get('it_code') or '').strip()
                    touch = float(item.get('touch') or 0)
                    required = float(item.get('weight') or item.get('gr_wt') or 0) * max(int(item.get('qty') or 1), 1)
                    if not code:
                        errors.append("IT code is required.")
                        continue
                    if touch <= 0:
                        errors.append(f"Touch is required for IT code {code}.")
                        continue
                    requirements[(code, round(touch, 2))] = requirements.get((code, round(touch, 2)), 0.0) + required
                for (code, touch_key), required in requirements.items():
                    touch = float(touch_key)
                    rows = conn.execute("""
                        SELECT gr_wt, touch FROM stock_inventory
                        WHERE TRIM(it_code)=? AND COALESCE(gr_wt, 0) > 0
                    """, (code,)).fetchall()
                    if not rows:
                        errors.append(f"IT code {code} is not available in inventory.")
                        continue
                    touches = [float(row['touch'] or 0) for row in rows if float(row['touch'] or 0) > 0]
                    if not any(abs(stock_touch - touch) <= 0.01 for stock_touch in touches):
                        errors.append(f"Touch {touch:.2f}% is not available for IT code {code}.")
                        continue
                    available = sum(float(row['gr_wt'] or 0) for row in rows
                                    if abs(float(row['touch'] or 0) - touch) <= 0.01)
                    if available + 0.001 < required:
                        errors.append(
                            f"Stock over for IT code {code}: available {available:.3f}g, required {required:.3f}g."
                        )
            return {"valid": not errors, "errors": errors}
        except Exception as e:
            _dberr(f"[POS STOCK VALIDATION ERROR] {e}")
            return {"valid": False, "errors": [f"Unable to validate inventory: {e}"]}

    def delete_master_entry(self, data_type, entry_id):
        table_map = {
            'category': ('categories', 'id'),
            'touch': ('touch_groups', 'id'),
            'product': ('product_master', 'code'),
            'stock': ('stock_inventory', 'id'),
            'client': ('clients_master', 'id'),
            'client_ledger': ('credit_ledger', 'id'),
            'admin_creds': ('admin_creds', 'username')
        }
        try:
            table, col = table_map[data_type]
            return self.execute_query(f"DELETE FROM {table} WHERE {col}=?", (entry_id,))
        except:
            return False

    def add_stock_entry(self, **kwargs):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        try:
            import time as _t
            tag_id = str(kwargs.get('tag_id') or '').strip()

            # Always generate a unique tag_id if blank/invalid/duplicate
            BAD = {"N/A", "undefined", "---", "-", "", "null", "none"}
            if tag_id.upper() in {b.upper() for b in BAD}:
                tag_id = ''

            if tag_id:
                # Check if tag_id already exists -- if so, generate new one
                exists = self.get_scalar(
                    "SELECT COUNT(*) FROM stock_inventory WHERE tag_id=?", (tag_id,))
                if exists:
                    _dblog("[DB] tag_id '{tag_id}' already exists -- generating new unique ID")
                    tag_id = ''

            if not tag_id:
                # Generate a guaranteed-unique tag_id
                tag_id = self.generate_unique_tag_id()

            with self._get_connection() as conn:
                sql = "INSERT OR REPLACE INTO stock_inventory" if tag_id.startswith(
                    'OPENING-') else "INSERT INTO stock_inventory"
                conn.execute(sql + """
                        (it_code,it_name,tag_id,pkg_wt,para_stone_wt,size,design,
                         pcs,gr_wt,ls_wt,nt_wt,ghat_wt,touch,wastage,huid,is_tagged,entry_date,
                         category,karat,making_type,making_charge,sale_price,stone_value)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,date('now'),?,?,?,?,?,?)""",
                             (str(kwargs.get('it_code', '')).strip(),
                              str(kwargs.get('it_name', '')),
                              str(tag_id),
                              float(kwargs.get('pkg_wt') or 0.0),
                              float(kwargs.get('para_stone_wt') or 0.0),
                              str(kwargs.get('size') or '-'),
                              str(kwargs.get('design') or '-'),
                              int(kwargs.get('pcs') or 1),
                              float(kwargs.get('gr_wt') or 0.0),
                              float(kwargs.get('ls_wt') or 0.0),
                              float(kwargs.get('nt_wt') or 0.0),
                              float(kwargs.get('ghat_wt') or 0.0),
                              float(kwargs.get('touch') or 0.0),
                              float(kwargs.get('wastage') or 0.0),
                              str(kwargs.get('huid') or '-'),
                              # Retail pricing. Absent for a wholesale entry,
                              # which stores the same harmless defaults.
                              str(kwargs.get('category') or ''),
                              str(kwargs.get('karat') or ''),
                              str(kwargs.get('making_type') or 'percent'),
                              float(kwargs.get('making_charge') or 0.0),
                              float(kwargs.get('sale_price') or 0.0),
                              float(kwargs.get('stone_value') or 0.0))
                             )
                conn.commit()
                new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            try:
                self.stamp_row_for_sync('stock_inventory', new_id)
            except Exception as _sse:
                _dblog(f"[SYNC] stamp skip (stock_inventory): {_sse}")
            print(f"[DB] Stock entry saved: {kwargs.get('it_code')} tag={tag_id}")
            return True
        except Exception as e:
            print(f"? [DB STOCK ENTRY ERROR] {e}")
            return False

    def delete_stock_entry(self, entry_id):
        """Delete an opening stock entry by ID."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM stock_inventory WHERE id=? AND tag_id LIKE 'OPENING-%'",
                    (int(entry_id),)
                )
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[OPENING STOCK] delete: {e}")
            return False

    def update_stock_entry(self, entry_id, data):
        """Update one stock row.

        Column names are checked against the table before being put into the
        statement. They arrive as dictionary keys from the screens and used to
        be interpolated straight into SQL, so a key that was mistyped took the
        whole update down with a syntax error, and a key that was not a column
        at all had no business being there.
        """
        try:
            if not isinstance(data, dict) or not data:
                return False
            with self._get_connection() as conn:
                allowed = {r[1] for r in conn.execute(
                    "PRAGMA table_info(stock_inventory)").fetchall()}
                fields = {k: v for k, v in data.items() if k in allowed and k != 'id'}
                skipped = [k for k in data if k not in fields and k != 'id']
                if skipped:
                    _dblog(f"[DB] update_stock_entry ignored unknown column(s): {skipped}")
                if not fields:
                    return False
                cols = ", ".join(f"{k}=?" for k in fields)
                conn.execute(f"UPDATE stock_inventory SET {cols} WHERE id=?",
                             tuple(list(fields.values()) + [entry_id]))
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[DB] update_stock_entry: {e}")
            return False

    def mark_as_tagged(self, item_id):
        return self.execute_query("UPDATE stock_inventory SET is_tagged=1 WHERE id=?", (item_id,))

    def add_client(self, name, phone, metal_limit, cash_limit):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        ok = self.execute_query(
            "INSERT INTO clients_master (name,phone,metal_limit,cash_limit) VALUES (?,?,?,?)",
            (name, phone, float(metal_limit or 0), float(cash_limit or 0))
        )
        if ok:
            try:
                self.stamp_row_for_sync('clients_master', name, pk_col='name')
            except Exception as _cse:
                _dblog(f"[SYNC] stamp skip (clients_master): {_cse}")
        return ok

    def get_all_clients(self):
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM clients_master ORDER BY name ASC").fetchall()]

    def delete_client(self, client_id):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT name FROM clients_master WHERE id=?", (int(client_id),)).fetchone()
                if not row:
                    return False
                conn.execute("DELETE FROM clients_master WHERE id=?", (int(client_id),))
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[DB DELETE CLIENT ERROR] {e}")
            return False

    def update_client_limits(self, data):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE clients_master SET metal_limit=?,cash_limit=? WHERE id=?",
                    (float(data.get('metal_limit', 0)), float(data.get('cash_limit', 0)), int(data.get('id')))
                )
                conn.commit()
            return True
        except:
            return False

    def post_ledger_entry(self, **kwargs):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        return self.execute_query(
            """INSERT INTO credit_ledger
                   (client_name,vch_reference,description,metal_dr,metal_cr,cash_dr,cash_cr,gold_rate)
               VALUES (?,?,?,?,?,?,?,?)""",
            (kwargs.get('client_name'), kwargs.get('vch_id'), kwargs.get('desc'),
             float(kwargs.get('metal_dr') or 0), float(kwargs.get('metal_cr') or 0),
             float(kwargs.get('cash_dr') or 0), float(kwargs.get('cash_cr') or 0),
             float(kwargs.get('gold_rate') or 0))
        )

    def get_client_statement(self, client_name):
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM credit_ledger WHERE client_name=? ORDER BY id ASC", (client_name,)
            ).fetchall()]

    def get_global_market_summary(self):
        try:
            with self._get_connection() as conn:
                res = conn.execute(
                    "SELECT SUM(metal_dr-metal_cr) as mb, SUM(cash_dr-cash_cr) as cb FROM credit_ledger"
                ).fetchone()
                return {"metal": round(res['mb'] or 0, 3), "cash": round(res['cb'] or 0, 2)}
        except:
            return {"metal": 0, "cash": 0}

    def get_next_vch_id(self):
        try:
            with self._get_connection() as conn:
                res = conn.execute("SELECT MAX(CAST(vch_id AS INTEGER)) FROM katti_vouchers").fetchone()
                return str(int(res[0]) + 1).zfill(4) if (res and res[0] is not None) else "0001"
        except:
            return "0001"

    def save_katti_batch(self, vch_id, total_wt, total_packets, note="", items=None, box_id=None):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        items = items or []
        try:
            safe_vch_id = str(vch_id).strip().zfill(4)
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                cursor = conn.cursor()

                touch_values = [float(i.get('touch', 0)) if isinstance(i, dict) else 0.0 for i in items]
                valid_touches = [t for t in touch_values if t > 0]
                avg_touch_val = sum(valid_touches) / len(valid_touches) if valid_touches else 0.0

                resolved_box_id = box_id
                if not resolved_box_id:
                    for item in items:
                        if isinstance(item, dict):
                            c = str(item.get('box') or '').strip()
                            if c and c not in ('', '-', 'None', 'N/A'):
                                resolved_box_id = c;
                                break

                cursor.execute(
                    """INSERT OR REPLACE INTO katti_vouchers
                           (vch_id,total_weight,total_packets,total_pcs,note,touch,box_id,date)
                       VALUES (?,?,?,?,?,?,?,date('now'))""",
                    (safe_vch_id, float(total_wt or 0), int(total_packets or 0),
                     int(total_packets or 0), str(note).strip(), avg_touch_val, resolved_box_id)
                )

                cursor.execute("DELETE FROM katti_voucher_items WHERE vch_id=?", (safe_vch_id,))

                # Delete old stock rows for this voucher before re-inserting
                cursor.execute(
                    "DELETE FROM stock_inventory WHERE vch_reference=? AND is_tagged=0",
                    (safe_vch_id,)
                )

                stock_tags = []
                for item in items:
                    if isinstance(item, dict):
                        item_code = str(item.get('it_code') or '').strip()
                        item_name = str(item.get('name', '') or item.get('it_name', '')).strip()
                        item_touch = float(item.get('touch') or 0.0)
                        item_wt = float(item.get('weight') or item.get('nt_wt') or 0.0)
                        item_pcs = int(item.get('packets') or item.get('pcs') or 1)
                        raw_box = str(item.get('box') or resolved_box_id or '').strip()
                        item_box = raw_box if raw_box not in ('', '-', 'None', 'N/A') else "B-001"
                    else:
                        item_code = '';
                        item_name = str(item).strip()
                        item_touch = 0.0;
                        item_wt = 0.0;
                        item_pcs = 1
                        item_box = resolved_box_id or "B-001"

                    cursor.execute(
                        "INSERT INTO katti_voucher_items (vch_id,it_code,it_name,nt_wt,touch,huid,pcs) VALUES (?,?,?,?,?,?,?)",
                        (safe_vch_id, item_code, item_name, item_wt, item_touch, item_box, item_pcs)
                    )

                    if item_code and item_wt > 0:
                        # Each voucher gets its own stock row — no merging with other batches
                        unique_tag = f"KATTI-{safe_vch_id}-{item_code}"
                        cursor.execute(
                            """INSERT OR REPLACE INTO stock_inventory
                                   (it_code,it_name,tag_id,pcs,gr_wt,ls_wt,nt_wt,
                                    touch,wastage,is_tagged,vch_reference,huid,entry_date)
                               VALUES (?,?,?,0,?,0,?,?,0,0,?,?,date('now'))""",
                            (item_code, item_name, unique_tag, item_wt, item_wt,
                             item_touch, safe_vch_id, item_box)
                        )
                        print(f"[KATTI] Stock inserted: {item_code} tag={unique_tag} wt={item_wt}g")
                        stock_tags.append(unique_tag)

                conn.commit()
                # Stamp for sync AFTER commit. Doing it inside the open write
                # transaction opened a 2nd connection that blocked on the WAL
                # write-lock (busy_timeout 30s) once PER ITEM — that was the
                # cause of the very slow katti saves.
                try:
                    self.stamp_row_for_sync('katti_vouchers', safe_vch_id, pk_col='vch_id')
                    for _tag in stock_tags:
                        _r = conn.execute("SELECT id FROM stock_inventory WHERE tag_id=?",
                                          (_tag,)).fetchone()
                        if _r:
                            self.stamp_row_for_sync('stock_inventory', _r[0])
                except Exception as _kve:
                    _dblog(f"[SYNC] stamp skip (katti): {_kve}")
                return True
        except Exception as e:
            print(f"? [KATTI SAVE ERROR] {e}");
            return False

    def get_all_katti_vouchers(self):
        """Return all katti vouchers ordered newest first."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT vch_id, date, total_weight, total_packets, note
                    FROM katti_vouchers
                    ORDER BY id DESC
                """).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"? [KATTI LIST] {e}");
            return []

    def delete_katti_voucher(self, vch_id):
        """Delete a katti voucher and all its stock rows."""
        try:
            safe = str(vch_id).strip().zfill(4)
            with self._get_connection() as conn:
                conn.execute("DELETE FROM katti_voucher_items WHERE vch_id=?", (safe,))
                conn.execute("DELETE FROM katti_vouchers WHERE vch_id=?", (safe,))
                conn.execute("DELETE FROM stock_inventory WHERE vch_reference=?", (safe,))
                conn.execute("DELETE FROM stock_inventory WHERE tag_id LIKE ?", (f'KATTI-{safe}-%',))
                conn.commit()
            return {"status": "success"}
        except Exception as e:
            print(f"? [KATTI DELETE] {e}")
            return {"status": "error", "message": str(e)}

    def update_katti_voucher(self, vch_id, note, items):
        """Update note and items of an existing katti voucher."""
        try:
            safe = str(vch_id).strip().zfill(4)
            with self._get_connection() as conn:
                total_w = sum(float(i.get('weight', 0)) for i in items)
                total_p = sum(int(i.get('packets', 1)) for i in items)
                conn.execute(
                    "UPDATE katti_vouchers SET note=?, total_weight=?, total_packets=? WHERE vch_id=?",
                    (note, total_w, total_p, safe)
                )
                conn.execute("DELETE FROM katti_voucher_items WHERE vch_id=?", (safe,))
                conn.execute("DELETE FROM stock_inventory WHERE vch_reference=?", (safe,))
                conn.execute("DELETE FROM stock_inventory WHERE tag_id LIKE ?", (f'KATTI-{safe}-%',))
                for item in items:
                    touch = float(item.get('touch', 0))
                    wt = float(item.get('weight', 0))
                    pcs = int(item.get('packets', 1))
                    icode = f"KATTI-{touch}"
                    iname = item.get('name', icode)
                    box = item.get('box', 'B-001')
                    tag = f"KATTI-{safe}-{icode}"
                    conn.execute("""
                        INSERT INTO katti_voucher_items
                            (vch_id, it_code, it_name, nt_wt, touch, huid, pcs)
                        VALUES (?,?,?,?,?,?,?)
                    """, (safe, icode, iname, wt, touch, box, pcs))
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_inventory
                            (it_code, it_name, tag_id, pcs, gr_wt, ls_wt, nt_wt,
                             touch, wastage, huid, is_tagged, vch_reference)
                        VALUES (?,?,?,?,?,0,?,?,0,?,0,?)
                    """, (icode, iname, tag, pcs, wt, wt, touch, box, safe))
                conn.commit()
            return {"status": "success"}
        except Exception as e:
            print(f"? [KATTI UPDATE] {e}")
            return {"status": "error", "message": str(e)}

    def get_katti_voucher_details(self, vch_id):
        try:
            with self._get_connection() as conn:
                vch = conn.execute("SELECT * FROM katti_vouchers WHERE vch_id=?", (vch_id,)).fetchone()
                if not vch: return None
                items = conn.execute(
                    "SELECT it_code,it_name,nt_wt,touch,huid,pcs FROM katti_voucher_items WHERE vch_id=?", (vch_id,)
                ).fetchall()
                if not items:
                    items = conn.execute(
                        "SELECT it_code,it_name,nt_wt,touch,huid,pcs FROM stock_inventory WHERE vch_reference=?",
                        (vch_id,)
                    ).fetchall()
                return {"voucher": dict(vch), "items": [dict(i) for i in items]}
        except Exception as e:
            print(f"? History Exception: {e}");
            return None

    def get_last_uchak_inward_vch_id(self):
        try:
            with self._get_connection() as conn:
                res = conn.execute(
                    "SELECT vch_id FROM uchak_inward_vouchers ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                if res and res['vch_id']:
                    return f"UCHK-IN-{(int(res['vch_id'].split('-')[-1]) + 1):03d}"
                return "UCHK-IN-001"
        except:
            return "UCHK-IN-001"

    def save_uchak_inward_transaction(self, vch_id, total_lines, total_pcs, total_value, items_list):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        try:
            safe_vch_id = str(vch_id).strip()
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                cursor = conn.cursor()

                old_items = cursor.execute(
                    "SELECT it_code,pcs FROM uchak_inward_items WHERE vch_id=?", (safe_vch_id,)
                ).fetchall()
                for old in old_items:
                    old_code = str(old['it_code']).strip();
                    old_pcs = int(old['pcs'] or 0)
                    if old_pcs <= 0: continue
                    row = cursor.execute(
                        """SELECT id,pcs FROM stock_inventory WHERE TRIM(it_code)=?
                           AND (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                           LIMIT 1""", (old_code,)
                    ).fetchone()
                    if row:
                        rem = (row['pcs'] or 0) - old_pcs
                        if rem <= 0:
                            cursor.execute("DELETE FROM stock_inventory WHERE id=?", (row['id'],))
                        else:
                            cursor.execute("UPDATE stock_inventory SET pcs=? WHERE id=?", (rem, row['id']))

                cursor.execute(
                    """INSERT OR REPLACE INTO uchak_inward_vouchers
                           (vch_id,total_lines,total_pcs,total_value,date)
                       VALUES (?,?,?,?,date('now'))""",
                    (safe_vch_id, int(total_lines), int(total_pcs), float(total_value))
                )
                cursor.execute("DELETE FROM uchak_inward_items WHERE vch_id=?", (safe_vch_id,))

                for item in items_list:
                    code = str(item.get('it_code', '')).strip()
                    name = str(item.get('it_name', '')).strip()
                    pcs = int(item.get('pcs') or 1)
                    price = float(item.get('price') or 0.0)
                    cursor.execute(
                        "INSERT INTO uchak_inward_items (vch_id,it_code,it_name,pcs,price) VALUES (?,?,?,?,?)",
                        (safe_vch_id, code, name, pcs, price)
                    )
                    existing = cursor.execute(
                        """SELECT id,pcs FROM stock_inventory WHERE TRIM(it_code)=?
                           AND (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                           LIMIT 1""", (code,)
                    ).fetchone()
                    if existing:
                        cursor.execute(
                            "UPDATE stock_inventory SET pcs=?,it_name=?,design=? WHERE id=?",
                            ((existing['pcs'] or 0) + pcs, name, str(price), existing['id'])
                        )
                    else:
                        cursor.execute(
                            """INSERT INTO stock_inventory
                                   (it_code,it_name,pcs,design,gr_wt,ls_wt,nt_wt,
                                    touch,wastage,tag_id,is_tagged,entry_date)
                               VALUES (?,?,?,?,0,0,0,0,0,'N/A',0,date('now'))""",
                            (code, name, pcs, str(price))
                        )
                conn.commit()
                try:
                    self.stamp_row_for_sync('uchak_inward_vouchers', safe_vch_id, pk_col='vch_id')
                except Exception as _use:
                    _dblog(f"[SYNC] stamp skip (uchak_inward_vouchers): {_use}")
                return True
        except Exception as e:
            print(f"? [DB UCHAK INWARD SAVE ERROR] {e}");
            return False

    def get_uchak_inward_voucher_details(self, vch_id):
        try:
            safe = str(vch_id).strip()
            with self._get_connection() as conn:
                vch = conn.execute("SELECT * FROM uchak_inward_vouchers WHERE vch_id=?", (safe,)).fetchone()
                if not vch: return None
                items = conn.execute(
                    "SELECT it_code,it_name,pcs,price AS design FROM uchak_inward_items WHERE vch_id=?", (safe,)
                ).fetchall()
                return {"voucher": dict(vch), "items": [dict(i) for i in items]}
        except:
            return None

    def add_uchak_stock_entry_raw(self, it_code, it_name, pcs, price):
        try:
            with self._get_connection() as conn:
                existing = conn.execute(
                    """SELECT id,pcs FROM stock_inventory WHERE TRIM(it_code)=?
                       AND (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                       LIMIT 1""", (it_code,)
                ).fetchone()
                if existing:
                    conn.execute("UPDATE stock_inventory SET pcs=?,it_name=?,design=? WHERE id=?",
                                 ((existing['pcs'] or 0) + pcs, it_name, str(price), existing['id']))
                else:
                    conn.execute(
                        """INSERT INTO stock_inventory
                               (it_code,it_name,pcs,design,gr_wt,ls_wt,nt_wt,
                                touch,wastage,tag_id,is_tagged,entry_date)
                           VALUES (?,?,?,?,0,0,0,0,0,'N/A',0,date('now'))""",
                        (it_code, it_name, pcs, str(price))
                    )
                conn.commit()
            return True
        except:
            return False

    # --- STOCK DEDUCTION -----------------------------------------------------
    def check_weight_stock_available(self, items_json: str) -> dict:
        """
        BEFORE committing a bill, check if every weight-based (katti) item
        has sufficient stock. Returns:
          { status: 'ok' }               — all good, proceed
          { status: 'insufficient',
            items: [ {touch, required, available, shortage} ] }
        Never allows negative stock.
        """
        import json as _json
        try:
            parsed = _json.loads(items_json) if isinstance(items_json, str) else items_json
            if not isinstance(parsed, list):
                return {'status': 'ok'}

            # Group required weight by touch value
            required: dict[float, float] = {}
            for item in parsed:
                tag_id = str(item.get('tag_id') or '').strip()
                it_code = str(item.get('it_code') or item.get('code') or '').strip()
                weight = float(item.get('weight') or item.get('gr_wt') or 0.0)

                # Only check weight-based (non-physical-tag) items
                is_real_tag = (
                        tag_id and
                        tag_id not in ('N/A', '-', '', 'undefined', 'null') and
                        not tag_id.startswith('KATTI-') and
                        len(tag_id) >= 8
                )
                if is_real_tag or weight <= 0:
                    continue

                # Resolve touch value
                touch_val = None
                if it_code:
                    try:
                        parsed_t = float(it_code)
                        if 1.0 <= parsed_t <= 100.0:
                            touch_val = round(parsed_t, 4)
                    except ValueError:
                        pass

                if touch_val is None:
                    continue

                required[touch_val] = round(required.get(touch_val, 0.0) + weight, 3)

            if not required:
                return {'status': 'ok'}

            # Check availability for each touch
            insufficient = []
            with self._get_connection() as conn:
                for touch_val, req_wt in required.items():
                    row = conn.execute(
                        """SELECT COALESCE(SUM(gr_wt), 0) as total
                           FROM stock_inventory
                           WHERE ABS(touch - ?) < 0.01
                             AND (tag_id IS NULL OR tag_id=''
                                  OR tag_id='N/A'
                                  OR tag_id LIKE 'KATTI-%')
                             AND tag_id NOT LIKE 'OPENING-%'
                             AND gr_wt > 0""",
                        (touch_val,)
                    ).fetchone()
                    available = round(float(row['total'] or 0), 3)
                    if available < req_wt - 0.001:  # 0.001g tolerance
                        insufficient.append({
                            'touch': touch_val,
                            'required': req_wt,
                            'available': available,
                            'shortage': round(req_wt - available, 3)
                        })

            if insufficient:
                return {'status': 'insufficient', 'items': insufficient}
            return {'status': 'ok'}

        except Exception as e:
            print(f'[STOCK CHECK] Error: {e}')
            return {'status': 'ok'}  # fail-open only on unexpected error

    def deduct_stock_after_sale(self, items_json):
        """
        ? PERMANENT FIX:
        - No vch_reference filter (katti rows have vch_reference set)
        - tag_id filter includes 'N/A' and 'KATTI-%'
        - is_real_tag excludes KATTI- prefixed tags
        """
        try:
            parsed_items = json.loads(items_json)
            if not isinstance(parsed_items, list):
                return False

            with self._get_connection() as conn:
                cursor = conn.cursor()

                for item in parsed_items:
                    tag_id = str(item.get('tag_id') or '').strip()
                    it_code = str(item.get('it_code') or item.get('code') or '').strip()
                    gross_wt = float(item.get('weight') or item.get('gr_wt') or 0.0)
                    sold_pcs = int(item.get('pcs') or 1)

                    # -- Case 1: Physical tag (10-digit, NOT KATTI- prefix) ----
                    is_real_tag = (
                            tag_id and
                            tag_id not in ('N/A', '-', '', 'undefined', 'null') and
                            not tag_id.startswith('KATTI-') and
                            len(tag_id) >= 8
                    )

                    if is_real_tag:
                        cursor.execute(
                            "DELETE FROM stock_inventory WHERE LOWER(TRIM(tag_id))=LOWER(?)", (tag_id,)
                        )
                        print(f"? [DEDUCT] Tagged deleted: tag_id={tag_id}")

                    # -- Case 2: Weight-based (katti / opening stock) ----------
                    # Logic:
                    #   Step 1 - Match it_code against real stock, use that row's touch
                    #   Step 2 - Only if nothing matched, read it_code AS a touch value
                    #   Step 3 - FIFO multi-row deduction across batches
                    #
                    # Step 1 used to come second. A number between 1 and 100 was
                    # read as a purity before anything checked whether it was a
                    # product code, so selling an item whose code happened to be
                    # "75" deducted from 75-touch bulk gold and left the item
                    # itself sitting in stock. Real stock wins now; reading the
                    # code as a purity is the fallback, which is what a
                    # wholesale weight line relies on and still gets.
                    elif gross_wt > 0:

                        touch_val = None
                        use_touch = False

                        # Step 1: a real stock row with this code decides the touch.
                        if it_code:
                            ref = cursor.execute(
                                """SELECT id, gr_wt, touch FROM stock_inventory
                                   WHERE TRIM(it_code)=?
                                     AND (tag_id IS NULL OR tag_id=''
                                          OR tag_id='N/A'
                                          OR tag_id LIKE 'KATTI-%')
                                     AND tag_id NOT LIKE 'OPENING-%'
                                   ORDER BY id ASC LIMIT 1""",
                                (it_code,)
                            ).fetchone()
                            if not ref:
                                ref = cursor.execute(
                                    """SELECT id, gr_wt, touch FROM stock_inventory
                                       WHERE LOWER(TRIM(it_code))=LOWER(?)
                                         AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A'
                                              OR tag_id LIKE 'KATTI-%')
                                         AND tag_id NOT LIKE 'OPENING-%'
                                       ORDER BY id ASC LIMIT 1""",
                                    (it_code,)
                                ).fetchone()
                            if not ref:
                                # Last look, this time without ruling out tagged
                                # rows. A tagged piece normally leaves by its tag
                                # and never reaches here, but a line that arrives
                                # with the tag missing would otherwise fall
                                # through and be read as a purity.
                                ref = cursor.execute(
                                    """SELECT id, gr_wt, touch FROM stock_inventory
                                       WHERE TRIM(it_code)=?
                                         AND tag_id NOT LIKE 'OPENING-%'
                                       ORDER BY id ASC LIMIT 1""",
                                    (it_code,)
                                ).fetchone()
                            if ref and ref['touch']:
                                # Found a stock row -- use its touch for deduction
                                touch_val = float(ref['touch'])
                                use_touch = True
                                print(f"[DEDUCT] it_code={it_code} -> touch={touch_val}")

                        # Step 2: nothing in stock under that code, so fall back
                        # to reading it as a purity (a wholesale weight line).
                        if not use_touch and it_code:
                            try:
                                parsed = float(it_code)
                                if 1.0 <= parsed <= 100.0:
                                    touch_val = parsed
                                    use_touch = True
                                    print(f"[DEDUCT] it_code={it_code} read as touch={touch_val}")
                            except ValueError:
                                pass

                        # FIFO touch-based deduction across multiple rows
                        if use_touch and touch_val is not None:
                            # Fetch ALL rows matching this touch, oldest first
                            rows = cursor.execute(
                                """SELECT id, gr_wt, nt_wt, it_code FROM stock_inventory
                                   WHERE touch=?
                                     AND (tag_id IS NULL OR tag_id=''
                                          OR tag_id='N/A'
                                          OR tag_id LIKE 'KATTI-%')
                                     AND tag_id NOT LIKE 'OPENING-%'
                                     AND gr_wt > 0
                                   ORDER BY id ASC""",
                                (touch_val,)
                            ).fetchall()

                            remaining_to_deduct = round(gross_wt, 3)
                            deducted_total = 0.0

                            for row in rows:
                                if remaining_to_deduct <= 0.001:
                                    break
                                row_wt = round(float(row['gr_wt'] or 0), 3)
                                if row_wt <= remaining_to_deduct + 0.001:
                                    # This row is fully consumed
                                    cursor.execute(
                                        "DELETE FROM stock_inventory WHERE id=?",
                                        (row['id'],)
                                    )
                                    deducted_total += row_wt
                                    remaining_to_deduct = round(remaining_to_deduct - row_wt, 3)
                                    print(f"[DEDUCT] touch={touch_val} row={row['id']} "
                                          f"it={row['it_code']} EXHAUSTED ({row_wt}g)")
                                else:
                                    # Partially deduct from this row
                                    orig_gr_wt = round(float(row['gr_wt'] or 0), 3)
                                    orig_nt_wt = round(float(row['nt_wt'] or 0), 3)
                                    new_wt = round(orig_gr_wt - remaining_to_deduct, 3)
                                    ratio = new_wt / orig_gr_wt if orig_gr_wt > 0 else 0
                                    new_nt_wt = round(orig_nt_wt * ratio, 3)
                                    cursor.execute(
                                        "UPDATE stock_inventory SET gr_wt=?, nt_wt=? WHERE id=?",
                                        (new_wt, new_nt_wt, row['id'])
                                    )
                                    print(f"[DEDUCT] touch={touch_val} row={row['id']} "
                                          f"it={row['it_code']} "
                                          f"{orig_gr_wt}g -> {new_wt}g "
                                          f"(deducted {remaining_to_deduct}g)")
                                    deducted_total += remaining_to_deduct
                                    remaining_to_deduct = 0.0

                            if deducted_total < gross_wt - 0.001:
                                print(f"[!] [DEDUCT] touch={touch_val}: "
                                      f"needed {gross_wt}g but only {deducted_total}g available")
                            else:
                                print(f"[DEDUCT] touch={touch_val}: "
                                      f"deducted {deducted_total}g total OK")

                        else:
                            # Could not resolve touch -- log and skip
                            print(f"[!] [DEDUCT] Cannot resolve touch for it_code={it_code!r} "
                                  f"wt={gross_wt}g -- stock NOT deducted")

                    # -- Case 3: Piece-based (uchak) ---------------------------
                    # Uchak items have: name, rate, pcs, amount fields
                    # it_code is stored in item['name'] for uchak bills
                    elif 'amount' in item or 'price' in item:
                        # Try it_code first, then 'name' field (uchak billing stores code in 'name')
                        piece_code = str(
                            item.get('it_code') or
                            item.get('code') or
                            item.get('name') or ''
                        ).strip()
                        sold_qty = int(item.get('pcs') or item.get('qty') or 1)

                        if piece_code:
                            # Try exact it_code match first
                            row = cursor.execute(
                                """SELECT id, pcs FROM stock_inventory
                                   WHERE TRIM(it_code)=? AND pcs>0
                                     AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A')
                                   LIMIT 1""",
                                (piece_code,)
                            ).fetchone()

                            # Fallback: match by it_name if it_code not found
                            if not row:
                                row = cursor.execute(
                                    """SELECT id, pcs FROM stock_inventory
                                       WHERE TRIM(it_name)=? AND pcs>0
                                         AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A')
                                       LIMIT 1""",
                                    (piece_code,)
                                ).fetchone()

                            if row:
                                rem = (row['pcs'] or 0) - sold_qty
                                if rem <= 0:
                                    cursor.execute("DELETE FROM stock_inventory WHERE id=?", (row['id'],))
                                    print(f"? [DEDUCT] Uchak exhausted & deleted: {piece_code} -{sold_qty}pcs")
                                else:
                                    cursor.execute(
                                        "UPDATE stock_inventory SET pcs=? WHERE id=?",
                                        (rem, row['id'])
                                    )
                                    print(
                                        f"? [DEDUCT] Uchak pcs reduced: {piece_code} -{sold_qty}pcs -> {rem}pcs remaining")
                            else:
                                print(f"[!]?  [DEDUCT] Uchak stock not found for code: {piece_code}")

                conn.commit()
                return True
        except Exception as e:
            print(f"? [DB STOCK DEDUCTION ERROR] {e}")
            return False

    def _retail_fine_from_items(self, items_json):
        """Single source of truth: retail (R-) fine in grams, derived from items.
        collected_fine must NEVER hold rupees. Returns 0.0 on bad input."""
        try:
            parsed = json.loads(items_json or '[]')
        except (TypeError, ValueError):
            return 0.0
        total = 0.0
        if isinstance(parsed, list):
            for it in parsed:
                if not isinstance(it, dict):
                    continue
                try:
                    if it.get('fine') not in (None, ''):
                        total += float(it.get('fine') or 0)
                    else:
                        total += float(it.get('weight') or it.get('gr_wt') or 0) * float(it.get('touch') or 0) / 100.0
                except (TypeError, ValueError):
                    continue
        return round(total, 3)

    def repair_retail_fine(self):
        """One-time self-healing: fix legacy R- rows where Rs was stored in
        collected_fine (equals total_amount, or absurd gram value). Idempotent."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT vch_id, total_amount, collected_fine, items FROM sales_history "
                    "WHERE UPPER(TRIM(vch_id)) LIKE 'R-%'"
                ).fetchall()
                fixed = 0
                for r in rows:
                    try:
                        amt = float(r['total_amount'] or 0)
                        coll = float(r['collected_fine'] or 0)
                    except (TypeError, ValueError):
                        continue
                    looks_like_rs = (
                        (amt > 500 and abs(coll - amt) < 0.01) or  # exact Rs copy
                        coll > 500  # no retail fine is ever >500g per bill
                    )
                    if looks_like_rs:
                        true_fine = self._retail_fine_from_items(r['items'])
                        conn.execute(
                            "UPDATE sales_history SET collected_fine=? WHERE vch_id=?",
                            (true_fine, r['vch_id']))
                        fixed += 1
                if fixed:
                    conn.commit()
                    _dblog(f"[REPAIR] retail fine fixed on {fixed} R- row(s)")
        except Exception as e:
            _dberr(f"[REPAIR] retail fine: {e}")

    def record_sale(self, vch_id, customer, mobile, status, l_fine, coll, f995, dhal, rem, rate, amt, items_json,
                    disc_type='none', disc_touch=0.0, disc_fine=0.0, disc_amount=0.0,
                    pay_mode='cash', og_value=0.0, og_wt=0.0,
                    og_desc='', og_touch=0.0, og_rate=0.0):
        try:
            safe_vch_id = str(vch_id).strip()
            safe_mobile = str(mobile or '').strip()
            safe_pay = str(pay_mode or 'cash').strip().lower()
            if safe_pay not in ('cash', 'upi', 'card'):
                safe_pay = 'cash'
            self.ensure_retail_columns()
            self.ensure_crm_tables()
            _dblog(f"[SALE] Recording sale vch_id={safe_vch_id} customer={customer}")
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                try:
                    parsed = json.loads(items_json)
                    is_uchak = (
                        not safe_vch_id.upper().startswith('R-') and
                        any(('amount' in i or 'price' in i) for i in parsed)
                    ) if isinstance(parsed, list) else False
                except:
                    is_uchak = False
                resolved = (
                    'UCHAK_UNPAID' if (status == 'CREDIT' and is_uchak) else
                    'UCHAK_PAID' if (status == 'PAID' and is_uchak) else status
                )
                # PERMANENT GUARD: R- collected_fine is grams only, never Rs.
                # Derive from items; ignore any Rs-like caller value.
                if safe_vch_id.upper().startswith('R-'):
                    coll = self._retail_fine_from_items(items_json)
                conn.execute(
                    """INSERT OR REPLACE INTO sales_history
                           (vch_id,customer,mobile,status,ledger_fine,collected_fine,fine_995,fine_dhal,
                            remaining_fine,gold_rate,total_amount,items,date,time_stamp,
                            discount_type,discount_touch,discount_fine,discount_amount,
                            payment_mode,old_gold_value,old_gold_wt,
                            old_gold_desc,old_gold_touch,old_gold_rate)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,date('now'),time('now'),?,?,?,?,
                                ?,?,?,?,?,?)""",
                    (safe_vch_id, customer, safe_mobile, resolved,
                     0.0 if is_uchak else float(l_fine or 0),
                     0.0 if is_uchak else float(coll or 0),
                     0.0 if is_uchak else float(f995 or 0),
                     0.0 if is_uchak else float(dhal or 0),
                     0.0 if is_uchak else float(rem or 0),
                     0.0 if is_uchak else float(rate or 0),
                     float(amt or 0), items_json,
                     str(disc_type or 'none'),
                     float(disc_touch or 0.0),
                     float(disc_fine or 0.0),
                     float(disc_amount or 0.0),
                     safe_pay,
                     0.0 if is_uchak else float(og_value or 0),
                     0.0 if is_uchak else float(og_wt or 0),
                     '' if is_uchak else str(og_desc or ''),
                     0.0 if is_uchak else float(og_touch or 0),
                     0.0 if is_uchak else float(og_rate or 0))
                )
                _dblog(f"[SALE] INSERT OR REPLACE succeeded for vch_id={safe_vch_id}")
                # NOTE: stock_inventory modifications are handled exclusively by
                # deduct_stock_after_sale() called after this function in generate_bill().
                # Previously record_sale() also deleted KATTI/B- rows here, causing
                # DOUBLE stock deduction (once here, once in deduct_stock_after_sale).
                conn.commit()
                _dblog(f"[SALE] Committed sale vch_id={safe_vch_id}")
                sale_row_id = conn.execute("SELECT id FROM sales_history WHERE vch_id=?", (safe_vch_id,)).fetchone()
                if sale_row_id is None:
                    _dberr(f"[SALE] CRITICAL: sale row not found after INSERT for vch_id={safe_vch_id}")
                    return False
                _dblog(f"[SALE] Sale row id={sale_row_id[0]} for vch_id={safe_vch_id}")
            try:
                self.stamp_row_for_sync('sales_history', sale_row_id[0])
            except Exception as _sse:
                _dblog(f"[SYNC] stamp skip (sales_history): {_sse}")
            return True
        except Exception as e:
            _dblog(f"[DB RECORD SALE ERROR] {e}")
            return False

    def get_bill_details(self, vch_id):
        """Fetch full bill from DB for bill_print.html"""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    """SELECT vch_id, customer, mobile, status, date,
                              ledger_fine, collected_fine, fine_995, fine_dhal,
                              remaining_fine, gold_rate, total_amount, items,
                              discount_type, discount_touch, discount_fine, discount_amount
                         FROM sales_history WHERE vch_id=? LIMIT 1""",
                    (str(vch_id).strip(),)
                ).fetchone()
                if not row:
                    return {'status': 'error', 'message': f'Voucher {vch_id} not found'}
                try:
                    items = json.loads(row['items'] or '[]')
                except:
                    items = []
                st = row['status'] or ''
                is_uchak = 'UCHAK' in st.upper()
                is_credit = 'CREDIT' in st.upper() or 'UDHAR' in st.upper()
                return {
                    'status': 'success',
                    'voucher': {
                        'vch_id': row['vch_id'],
                        'customer': row['customer'],
                        'mobile': row['mobile'] or '',
                        'date': row['date'],
                        'status': st,
                        'ledger_fine': float(row['ledger_fine'] or 0),
                        'collected_fine': float(row['collected_fine'] or 0),
                        'fine_995': float(row['fine_995'] or 0),
                        'fine_dhal': float(row['fine_dhal'] or 0),
                        'remaining_fine': float(row['remaining_fine'] or 0),
                        'gold_rate': float(row['gold_rate'] or 0),
                        'total_amount': float(row['total_amount'] or 0),
                        'discount_type': row['discount_type'] or 'none',
                        'discount_touch': float(row['discount_touch'] or 0),
                        'discount_fine': float(row['discount_fine'] or 0),
                        'discount_amount': float(row['discount_amount'] or 0),
                    },
                    'items': items,
                    'is_uchak': is_uchak,
                    'is_credit': is_credit,
                }
        except Exception as e:
            print(f'get_bill_details error: {e}')
            return {'status': 'error', 'message': str(e)}

    def fetch_history(self):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT date, vch_id AS voucher_id, customer AS client_name, status,
                           COALESCE(ledger_fine,0.0)    AS ledger_fine,
                           COALESCE(collected_fine,0.0) AS collected,
                           COALESCE(remaining_fine,0.0) AS amount_due,
                           ROUND(COALESCE(total_amount,0.0),2) AS total_cash_amount
                    FROM sales_history ORDER BY id DESC
                """).fetchall()]
        except:
            return []

    def get_old_gold_register(self, limit=1000):
        """Every bill that took old gold in part-exchange, newest first."""
        try:
            with self._get_connection() as conn:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(sales_history)").fetchall()]
                if 'old_gold_value' not in cols or 'old_gold_wt' not in cols:
                    return []
                has_desc = 'old_gold_desc' in cols
                has_touch = 'old_gold_touch' in cols
                has_rate = 'old_gold_rate' in cols
                try:
                    lim = max(1, min(int(limit or 1000), 5000))
                except (TypeError, ValueError):
                    lim = 1000
                return [dict(r) for r in conn.execute(f"""
                    SELECT date, time_stamp, vch_id, customer, mobile,
                           COALESCE(old_gold_wt,0.0)    AS old_gold_wt,
                           COALESCE(old_gold_value,0.0) AS old_gold_value,
                           {'COALESCE(old_gold_desc,\'\') AS old_gold_desc,' if has_desc else "'' AS old_gold_desc,"}
                           {'COALESCE(old_gold_touch,0.0) AS old_gold_touch,' if has_touch else "0.0 AS old_gold_touch,"}
                           {'COALESCE(old_gold_rate,0.0) AS old_gold_rate' if has_rate else "0.0 AS old_gold_rate"},
                           COALESCE(total_amount,0.0)   AS total_amount
                    FROM sales_history
                    WHERE COALESCE(old_gold_value,0) > 0
                       OR COALESCE(old_gold_wt,0) > 0
                    ORDER BY id DESC LIMIT ?
                """, (lim,)).fetchall()]
        except Exception as e:
            _dberr(f"[OLDGOLD] register: {e}")
            return []

    def get_customer_purchases(self, customer_name, mobile=''):
        """Return every bill recorded for one customer, newest first.
        UNIT CONTRACT: collected_fine is ALWAYS grams. Cash totals live in
        total_amount only. Rs must never enter collected_fine."""
        try:
            self.repair_retail_fine()
            safe_name = str(customer_name or '').strip()
            safe_mobile = ''.join(ch for ch in str(mobile or '') if ch.isalnum())
            if not safe_name and not safe_mobile:
                return []
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT vch_id, customer, mobile, status, date, time_stamp,
                           COALESCE(ledger_fine, 0.0) AS ledger_fine,
                           COALESCE(collected_fine, 0.0) AS collected_fine,
                           COALESCE(remaining_fine, 0.0) AS remaining_fine,
                           COALESCE(gold_rate, 0.0) AS gold_rate,
                           COALESCE(total_amount, 0.0) AS total_amount,
                           COALESCE(items, '[]') AS items,
                           COALESCE(discount_amount, 0.0) AS discount_amount
                    FROM sales_history
                    ORDER BY id DESC
                """).fetchall()
                def norm(value):
                    return ' '.join(str(value or '').replace('\u00a0', ' ').split()).casefold()
                wanted_name = norm(safe_name)
                result = []
                for row in rows:
                    item = dict(row)
                    same_name = wanted_name and norm(item.get('customer')) == wanted_name
                    row_mobile = ''.join(ch for ch in str(item.get('mobile') or '') if ch.isalnum())
                    same_mobile = safe_mobile and row_mobile and row_mobile == safe_mobile
                    if same_name or same_mobile:
                        is_retail = str(item.get('vch_id') or '').upper().startswith('R-')
                        if is_retail:
                            item['status'] = 'PAID'
                            # Grams from items; Rs stays in total_amount only.
                            item['collected_fine'] = self._retail_fine_from_items(item.get('items'))
                            item['remaining_fine'] = 0.0
                            try:
                                parsed_items = json.loads(item.get('items') or '[]')
                            except (TypeError, ValueError):
                                parsed_items = []
                            if not item.get('gold_rate') and isinstance(parsed_items, list):
                                rates = []
                                for bill_item in parsed_items:
                                    if isinstance(bill_item, dict):
                                        raw_rate = bill_item.get('rate') or bill_item.get('gold_rate')
                                        try:
                                            if raw_rate is not None:
                                                rates.append(float(str(raw_rate).replace(',', '')))
                                        except (TypeError, ValueError):
                                            continue
                                if rates:
                                    item['gold_rate'] = rates[0]
                        result.append(item)
                return result
        except Exception as e:
            _dberr(f"[CUSTOMER PURCHASES] {e}")
            return []

    def get_voucher_history_list(self, limit=1000):
        """
        Flat voucher list for ui/voucher_history.html -- one row per sales
        bill, with its item lines rolled up into the grid columns
        (touch group, tag ids, pcs, gross wt, net fine).
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM sales_history ORDER BY id DESC LIMIT ?",
                    (int(limit or 1000),)
                ).fetchall()

            out = []
            for r in rows:
                row = dict(r)
                try:
                    items = json.loads(row.get('items') or '[]')
                except:
                    items = []
                if not isinstance(items, list):
                    items = []

                touches, tag_ids, norm = [], [], []
                pcs, gross_wt, net_fine = 0, 0.0, 0.0

                for it in items:
                    if not isinstance(it, dict):
                        continue
                    tv   = float(it.get('touch') or 0)
                    wt   = float(it.get('weight') or it.get('gr_wt') or it.get('gross_wt') or 0)
                    para = float(it.get('para') or 0)
                    less = float(it.get('less') or 0)
                    ipcs = int(it.get('pcs') or 1)
                    nt_wt = wt - para - less
                    # Billing stores the already-computed fine; recompute only
                    # for legacy rows that predate it.
                    if it.get('fine') not in (None, ''):
                        fine = float(it.get('fine') or 0)
                    else:
                        fine = nt_wt * (tv + float(it.get('wastage') or 0)) / 100.0

                    tag = str(it.get('tag_id') or '').strip()
                    if tag and tag not in ('N/A', '---', 'undefined', '-') and tag not in tag_ids:
                        tag_ids.append(tag)
                    if tv > 0:
                        lbl = ('%.2f' % tv).rstrip('0').rstrip('.')
                        if lbl not in touches:
                            touches.append(lbl)

                    pcs      += ipcs
                    gross_wt += wt
                    net_fine += fine

                    d = dict(it)
                    d.update({
                        'tag_id': tag, 'touch': tv, 'pcs': ipcs,
                        'gross_wt': round(wt, 3), 'net_wt': round(nt_wt, 3),
                        'net_fine': round(fine, 3),
                        'it_name': (it.get('it_name') or it.get('name')
                                    or it.get('code') or it.get('it_code') or ''),
                    })
                    norm.append(d)

                dtype = str(row.get('discount_type') or 'none')
                dval  = (float(row.get('discount_amount') or 0) if dtype == 'amount'
                         else float(row.get('discount_fine') or 0))

                out.append({
                    'id':             row.get('id'),
                    'vch_id':         row.get('vch_id') or '',
                    'date':           row.get('date') or '',
                    'customer':       row.get('customer') or '',
                    'mobile':         row.get('mobile') or '',
                    'status':         row.get('status') or '',
                    'fgl':            float(row.get('ledger_fine') or 0),
                    'f995':           float(row.get('fine_995') or 0),
                    'dhal':           float(row.get('fine_dhal') or 0),
                    'collected_fine': float(row.get('collected_fine') or 0),
                    'remaining_fine': float(row.get('remaining_fine') or 0),
                    'gold_rate':      float(row.get('gold_rate') or 0),
                    'total_amount':   float(row.get('total_amount') or 0),
                    'touches':        touches,
                    'tag_ids':        tag_ids,
                    'pcs':            pcs,
                    'gross_wt':       round(gross_wt, 3),
                    'net_fine':       round(net_fine, 3),
                    'discount_type':  dtype,
                    'discount_value': round(dval, 3),
                    'discount_fine':  float(row.get('discount_fine') or 0),
                    'discount_amount': float(row.get('discount_amount') or 0),
                    'items':          norm,
                })
            return out
        except Exception as e:
            print(f"? [VOUCHER HISTORY LIST ERROR] {e}")
            return []

    def get_touch_details(self, touch_val):
        """Resolve a numeric touch (e.g. 91.6) to its touch_groups row."""
        try:
            tv = float(str(touch_val).strip())
        except:
            return None
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    """SELECT name, value, wastage FROM touch_groups
                        WHERE ROUND(value,2)=ROUND(?,2) LIMIT 1""",
                    (tv,)
                ).fetchone()
            if not row:
                return None
            return {'name': row['name'] or '', 'value': float(row['value'] or 0),
                    'wastage': float(row['wastage'] or 0)}
        except Exception as e:
            print(f"? [TOUCH DETAILS ERROR] {e}")
            return None

    def check_uchak_stock_available(self, items):
        """
        Pre-flight piece-stock check for uchak bills. Mirrors the lookup in
        deduct_stock_on_sale() exactly (same it_code -> it_name fallback and
        same LIMIT 1 row) so this check can never pass where the deduction
        would then fail.
        Returns a list of shortfalls: [{it_code, available, requested}].
        """
        try:
            if isinstance(items, str):
                try:
                    items = json.loads(items or '[]')
                except:
                    return []
            if not isinstance(items, list):
                return []

            # Same code can appear on several lines -- total the request.
            wanted = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                code = str(it.get('it_code') or it.get('code') or it.get('name') or '').strip()
                if not code:
                    continue
                wanted[code] = wanted.get(code, 0) + int(it.get('pcs') or it.get('qty') or 1)

            if not wanted:
                return []

            short = []
            with self._get_connection() as conn:
                for code, req in wanted.items():
                    row = conn.execute(
                        """SELECT pcs FROM stock_inventory
                            WHERE TRIM(it_code)=? AND pcs>0
                              AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A')
                            LIMIT 1""",
                        (code,)
                    ).fetchone()
                    if not row:
                        row = conn.execute(
                            """SELECT pcs FROM stock_inventory
                                WHERE TRIM(it_name)=? AND pcs>0
                                  AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A')
                                LIMIT 1""",
                            (code,)
                        ).fetchone()
                    avail = int(row['pcs'] or 0) if row else 0
                    if avail < req:
                        short.append({'it_code': code, 'available': avail, 'requested': req})
            return short
        except Exception as e:
            print(f"? [UCHAK STOCK CHECK ERROR] {e}")
            return []

    # --- DASHBOARD STATS -----------------------------------------------------
    def get_dashboard_stats(self):
        """
        ? FIXED: weight stock now reads from stock_inventory directly
        (tag_id IN 'N/A', '', NULL, 'KATTI-%') so reduced weights show correctly.
        """
        try:
            import json as _json
            with self._get_connection() as conn:
                # Tagged pieces (physical tags, 10-digit)
                showroom = conn.execute("""
                    SELECT COUNT(*) as p, SUM(nt_wt) as w FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                """).fetchone()

                # Uchak piece stock
                uchak = conn.execute("""
                    SELECT SUM(pcs) as p FROM stock_inventory
                    WHERE (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                      AND pcs > 0
                      AND (gr_wt IS NULL OR gr_wt = 0)
                """).fetchone()

                # Opening + Katti inward stock (raw from stock_inventory)
                weight_stock = conn.execute("""
                    SELECT SUM(gr_wt) as w, COUNT(*) as p FROM stock_inventory
                    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND tag_id NOT LIKE 'OPENING-%'
                      AND gr_wt > 0
                """).fetchone()
                katti_gross = float(weight_stock['w'] or 0)

                # Subtract weight sold via sales_history (katti/weight items only)
                sales = conn.execute(
                    "SELECT items FROM sales_history"
                ).fetchall()
                sold_wt = 0.0
                for sale in sales:
                    try:
                        items = _json.loads(sale['items'] or '[]')
                    except:
                        continue
                    for item in items:
                        tag_id = str(item.get('tag_id') or '').strip()
                        # Weight item if: no tag, N/A, KATTI- prefix, B-NNN bin format, or not a real SKU tag
                        is_weight = (
                                not tag_id
                                or tag_id in ('', 'N/A', '---', 'undefined', '-')
                                or tag_id.startswith('KATTI-')
                                or tag_id.startswith('B-')
                                or tag_id.startswith('b-')
                                or (len(tag_id) < 8 and not any(c.isalpha() and c.isupper() for c in tag_id[2:]))
                        )
                        if is_weight:
                            sold_wt += float(item.get('weight') or item.get('gr_wt') or 0)

                live_katti = max(0.0, katti_gross - sold_wt)

                # Katti voucher packets count
                katti = conn.execute(
                    "SELECT SUM(total_packets) as p FROM katti_vouchers"
                ).fetchone()

                profile = conn.execute("SELECT owner_name FROM business_profile WHERE id=1").fetchone()
                owner = profile['owner_name'] if (profile and profile['owner_name']) else None

                total_w = (showroom['w'] or 0) + live_katti

                return {
                    "net": round(total_w, 3),
                    "pcs": showroom['p'] or 0,
                    "uchak_pcs": uchak['p'] or 0,
                    "packets": katti['p'] or 0,
                    "katti_net": round(live_katti, 3),
                    "owner_name": owner
                }
        except Exception as e:
            print(f"? [DASHBOARD STATS ERROR] {e}")
            return {"net": 0, "pcs": 0, "uchak_pcs": 0, "packets": 0, "katti_net": 0, "owner_name": None}

    def fetch_stock_ledger_by_date(self, target_date=None):
        if not target_date: target_date = datetime.now().strftime('%Y-%m-%d')
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id,it_code,it_name,pcs,touch,size,design,
                           gr_wt,para_stone_wt,ls_wt,nt_wt,ghat_wt,huid,entry_date,tag_id,
                           wastage,
                           COALESCE(category,'')      AS category,
                           COALESCE(karat,'')         AS karat,
                           COALESCE(making_type,'')   AS making_type,
                           COALESCE(making_charge,0)  AS making_charge,
                           COALESCE(sale_price,0)     AS sale_price,
                           COALESCE(stone_value,0)    AS stone_value,
                           COALESCE(photo,'')         AS photo,
                           CASE WHEN is_tagged=1 THEN 'TAGGED' ELSE 'PENDING' END as live_status
                    FROM stock_inventory
                    WHERE entry_date=?
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND tag_id NOT LIKE 'OPENING-%'
                    ORDER BY id DESC
                """, (target_date,)).fetchall()]
        except:
            return []

    def get_available_ledger_dates(self):
        from datetime import datetime as _dt
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT DISTINCT entry_date FROM stock_inventory
                    WHERE tag_id NOT IN ('N/A','') AND tag_id NOT LIKE 'KATTI-%' AND tag_id NOT LIKE 'OPENING-%'
                    ORDER BY entry_date ASC
                """).fetchall()
                return [r['entry_date'] for r in rows] if rows else [_dt.now().strftime('%Y-%m-%d')]
        except Exception:
            from datetime import datetime as _dt2
            return [_dt2.now().strftime('%Y-%m-%d')]

    def get_product_by_tag(self, tag_id):
        try:
            with self._get_connection() as conn:
                res = conn.execute("""
                    SELECT it_code,it_name,tag_id,pcs,gr_wt,pkg_wt,
                           para_stone_wt,ls_wt,touch,wastage,huid,is_tagged
                    FROM stock_inventory WHERE TRIM(tag_id)=TRIM(?) OR TRIM(it_code)=TRIM(?)
                """, (str(tag_id).strip(), str(tag_id).strip())).fetchone()
                if res:
                    r = dict(res)
                    return {k: r.get(k, 0.0) if k.endswith('_wt') or k in ('touch', 'wastage', 'pcs') else r.get(k) for
                            k in r}
                return None
        except:
            return None

    def get_uchak_price_by_code(self, it_code):
        """
        Look up Uchak opening stock by IT Code for billing auto-fill.
        Uchak opening rows are tag_id LIKE 'OPENING-UCH-%' with price
        stored in the 'design' column (text) -- see opening_stock.html.
        Returns {found, it_code, it_name, price, pcs} or {found:false}.
        """
        try:
            code = str(it_code).strip().upper()
            if not code:
                return {'found': False}
            with self._get_connection() as conn:
                res = conn.execute("""
                    SELECT it_code, it_name, design, pcs
                    FROM stock_inventory
                    WHERE tag_id LIKE 'OPENING-UCH-%'
                      AND UPPER(TRIM(it_code)) = ?
                    ORDER BY id DESC LIMIT 1
                """, (code,)).fetchone()
                if not res:
                    return {'found': False, 'it_code': code}
                r = dict(res)
                try:
                    price = float(r.get('design') or 0)
                except (TypeError, ValueError):
                    price = 0.0
                return {
                    'found': True,
                    'it_code': r.get('it_code') or code,
                    'it_name': r.get('it_name') or code,
                    'price': price,
                    'pcs': int(r.get('pcs') or 0),
                }
        except Exception as e:
            _dberr(f"[UCHAK PRICE LOOKUP] {e}")
            return {'found': False, 'it_code': it_code}

    # --- ANALYTICS -----------------------------------------------------------
    def get_inventory_analytics(self):
        """
        ? FIXED: bins now read from stock_inventory (live, reduced weights)
        not from katti_vouchers (which has original weights).
        """
        try:
            with self._get_connection() as conn:

                # ? Bin heatmap -- read LIVE weight from stock_inventory
                bins_si = conn.execute("""
                    SELECT UPPER(TRIM(huid)) as bin_id,
                           SUM(gr_wt) as weight,
                           json_group_array(json_object(
                               'it_name', it_name,
                               'nt_wt',   gr_wt,
                               'it_code', it_code
                           )) as items_json
                    FROM stock_inventory
                    WHERE huid IS NOT NULL
                      AND TRIM(huid) NOT IN ('','-','None','N/A')
                      AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND gr_wt > 0
                    GROUP BY UPPER(TRIM(huid))
                """).fetchall()

                # Tagged piece bins
                bins_tagged = conn.execute("""
                    SELECT UPPER(TRIM(huid)) as bin_id,
                           SUM(nt_wt) as weight,
                           json_group_array(json_object(
                               'it_name', it_name,
                               'nt_wt',   nt_wt,
                               'it_code', it_code
                           )) as items_json
                    FROM stock_inventory
                    WHERE huid IS NOT NULL
                      AND TRIM(huid) NOT IN ('','-','None','N/A')
                      AND tag_id NOT IN ('N/A','') AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                    GROUP BY UPPER(TRIM(huid))
                """).fetchall()

                def norm(raw):
                    c = str(raw).replace(' ', '').replace('-', '').upper()
                    return f"B-{c[1:].zfill(3)}" if c.startswith('B') and len(c) > 1 else raw

                bin_map = {};
                items_map = {}
                for b in list(bins_si) + list(bins_tagged):
                    k = norm(b['bin_id'])
                    bin_map[k] = bin_map.get(k, 0.0) + (b['weight'] or 0.0)
                    try:
                        parsed = json.loads(b['items_json'])
                        if k not in items_map: items_map[k] = []
                        items_map[k].extend(parsed)
                    except:
                        pass

                bins = [{"bin_id": k, "weight": round(v, 3), "items": items_map.get(k, [])}
                        for k, v in sorted(bin_map.items())]

                # Category distribution -- weight based (live from stock_inventory)
                k_data = [dict(r) for r in conn.execute("""
                    SELECT it_name as cat_group, gr_wt as total_weight
                    FROM stock_inventory
                    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND gr_wt > 0
                    ORDER BY it_name ASC
                """).fetchall()]

                # Tagged piece distribution
                p_data = [dict(r) for r in conn.execute("""
                    SELECT UPPER(TRIM(it_name)) as cat_group, SUM(pcs) as total_pcs
                    FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                    GROUP BY UPPER(TRIM(it_name))
                """).fetchall()]

                u_data = [dict(r) for r in conn.execute("""
                    SELECT ui.vch_id as cat_group, SUM(ui.pcs) as total_pcs
                    FROM uchak_inward_items ui
                    JOIN uchak_inward_vouchers uv ON ui.vch_id=uv.vch_id
                    WHERE ui.pcs>0 GROUP BY ui.vch_id ORDER BY uv.timestamp ASC
                """).fetchall()]

                return {"status": "success", "bins": bins, "katti_weight": k_data,
                        "manual_pcs": p_data, "uchak_pcs_chart": u_data}
        except Exception as e:
            print(f"? Analytics Error: {e}")
            return {"status": "error", "bins": [], "katti_weight": [], "manual_pcs": [], "uchak_pcs_chart": []}

    def get_stocksync_snapshot(self):
        import json as _j
        try:
            with self._get_connection() as conn:

                # Opening stock rows (OPENING- tagged), by purity or by category
                opening = conn.execute("""
                    SELECT ROUND(touch,2) as touch,
                           COALESCE(category,'') as category,
                           COALESCE(SUM(gr_wt),0) as wt
                    FROM stock_inventory
                    WHERE tag_id LIKE 'OPENING-%'
                      AND ((touch IS NOT NULL AND touch > 0)
                           OR COALESCE(category,'') != '')
                    GROUP BY ROUND(touch,2), COALESCE(category,'')
                """).fetchall()

                # All live stock per touch (includes opening + katti + tagged).
                # Rows with no purity are grouped by category instead: a retail
                # shop counts rings and chains, not 91.6% and 75%, and dropping
                # them here is why its stock take came back empty.
                live = conn.execute("""
                    SELECT ROUND(touch,2) as touch,
                           COALESCE(category,'') as category,
                           COALESCE(SUM(gr_wt),0) as wt,
                           COALESCE(COUNT(*),0)   as cnt
                    FROM stock_inventory
                    WHERE (touch IS NOT NULL AND touch > 0)
                       OR COALESCE(category,'') != ''
                    GROUP BY ROUND(touch,2), COALESCE(category,'')
                    ORDER BY touch, category
                """).fetchall()

                # Katti inward
                katti = conn.execute('''
                    SELECT ROUND(touch,2) as touch,
                           COALESCE(SUM(nt_wt),0) as wt
                    FROM katti_voucher_items
                    WHERE touch IS NOT NULL AND touch > 0
                    GROUP BY ROUND(touch,2)
                ''').fetchall()

                # Sales outward from JSON
                out = {}
                for row in conn.execute(
                        "SELECT items FROM sales_history "
                        "WHERE items IS NOT NULL AND items!='' AND items!='[]'"
                ).fetchall():
                    try:
                        for item in _j.loads(row['items'] or '[]'):
                            tv = None
                            try:
                                p = float(str(item.get('it_code') or ''))
                                if 1 <= p <= 100: tv = round(p, 2)
                            except:
                                pass
                            if not tv:
                                try:
                                    tv = round(float(item.get('touch') or 0), 2)
                                except:
                                    pass
                            if not tv or tv <= 0: continue
                            wt = float(item.get('weight') or item.get('gr_wt') or 0)
                            if wt > 0:
                                out[tv] = round(out.get(tv, 0) + wt, 3)
                    except:
                        pass

                # Rows are keyed by purity where there is one and by category
                # otherwise, so a retail shop that records no touch still gets
                # a line per category instead of everything collapsing into a
                # single 0.0 group it cannot count against.
                def _key(r):
                    t = round(float(r['touch'] or 0), 2)
                    if t > 0:
                        return t
                    cat = str((r['category'] if 'category' in r.keys() else '') or '').strip()
                    return cat or 0.0

                open_wt = {}
                for r in opening:
                    open_wt[_key(r)] = open_wt.get(_key(r), 0) + float(r['wt'])
                live_wt, live_cnt, cat_of = {}, {}, {}
                for r in live:
                    k = _key(r)
                    live_wt[k] = live_wt.get(k, 0) + float(r['wt'])
                    live_cnt[k] = live_cnt.get(k, 0) + int(r['cnt'])
                    cat_of[k] = str((r['category'] if 'category' in r.keys() else '') or '').strip()
                kat_wt = {round(float(r['touch']), 2): float(r['wt']) for r in katti}

                # Include ALL groups — even those with 0 opening after stock med
                all_tv = sorted(set(list(open_wt) + list(live_wt) + list(kat_wt) + list(out)),
                                key=lambda k: (isinstance(k, str), k))
                result = []
                for tv in all_tv:
                    ow_val = round(open_wt.get(tv, 0), 3)  # opening stock weight
                    lw = round(live_wt.get(tv, 0), 3)  # total live stock
                    kw = round(kat_wt.get(tv, 0), 3)  # katti inward
                    ow = round(out.get(tv, 0), 3)  # sales outward
                    cnt = live_cnt.get(tv, 0)
                    _dblog(f"[STOCKSYNC] touch={tv} opening={ow_val} live={lw} katti={kw} sold={ow}")
                    result.append({
                        'touch': tv if not isinstance(tv, str) else 0.0,
                        'category': cat_of.get(tv, '') if isinstance(tv, str) else '',
                        'wt_opening': ow_val,
                        'wt_inward': kw,
                        'wt_outward': ow,
                        'wt_book_close': lw,
                        'wt_live': lw,
                        'pc_book_close': cnt,
                        'pc_live': cnt,
                        'pc_opening': 0,
                        'pc_inward': 0,
                        'pc_outward': 0,
                        'opening': ow_val,
                        'inward': kw,
                        'outward': ow,
                        'book_close': lw,
                        'live_stock': lw,
                    })
                _dblog(f"[STOCKSYNC] Snapshot: {len(result)} touch groups")
                return {'status': 'success', 'data': result}
        except Exception as e:
            _dberr(f"[STOCKSYNC] error: {e}")
            return {'status': 'error', 'message': str(e), 'data': []}

    def get_touch_stock_report(self):
        """
        Touch-based stock report showing:
        - Opening stock per touch
        - Total sold per touch (from sales_history items)
        - Remaining stock per touch (live from stock_inventory)
        This is the key report for touch-based deduction system.
        """
        try:
            with self._get_connection() as conn:
                # Opening stock split by mode:
                # WEIGHT mode = KATTI-% it_code rows (bulk weight entries)
                # PIECE mode  = non-KATTI it_code rows (tagged individual pieces)
                opening = conn.execute("""
                    SELECT touch,
                           COALESCE(SUM(nt_wt), 0)  as opening_wt,
                           COALESCE(SUM(gr_wt), 0)  as current_wt,
                           COUNT(*)                  as entries,
                           -- weight-only rows (KATTI bulk entries)
                           COALESCE(SUM(CASE WHEN it_code LIKE 'KATTI-%'
                                            THEN nt_wt ELSE 0 END), 0) as wt_opening,
                           COALESCE(SUM(CASE WHEN it_code LIKE 'KATTI-%'
                                            THEN gr_wt ELSE 0 END), 0) as wt_remaining,
                           COUNT(CASE WHEN it_code LIKE 'KATTI-%' THEN 1 END) as wt_entries,
                           -- piece-based rows (tagged individual items)
                           COALESCE(SUM(CASE WHEN it_code NOT LIKE 'KATTI-%'
                                             AND it_code IS NOT NULL AND TRIM(it_code) != ''
                                            THEN nt_wt ELSE 0 END), 0) as pc_opening,
                           COALESCE(SUM(CASE WHEN it_code NOT LIKE 'KATTI-%'
                                             AND it_code IS NOT NULL AND TRIM(it_code) != ''
                                            THEN gr_wt ELSE 0 END), 0) as pc_remaining,
                           COALESCE(SUM(CASE WHEN it_code NOT LIKE 'KATTI-%'
                                             AND it_code IS NOT NULL AND TRIM(it_code) != ''
                                            THEN pcs ELSE 0 END), 0) as pc_entries
                    FROM stock_inventory
                    WHERE touch IS NOT NULL AND touch > 0
                      AND (nt_wt > 0 OR gr_wt > 0)
                    GROUP BY touch
                    ORDER BY touch
                """).fetchall()
                _dblog(
                    f"[STOCK_RPT] opening rows: {len(opening)}, data: {[(r['touch'], r['wt_opening'], r['pc_opening']) for r in opening]}")

                # remaining is now computed inline in the opening query above
                # (wt_remaining = KATTI live, pc_remaining = piece live)

                # Sold per touch from sales history items
                # items_json contains it_code which may be a touch number
                sold_rows = conn.execute("""
                    SELECT items_json FROM sales_history
                    WHERE items_json IS NOT NULL AND items_json != ''
                """).fetchall()

            import json as _j
            sold_by_touch = {}
            for row in sold_rows:
                try:
                    items = _j.loads(row['items_json'])
                    for item in (items if isinstance(items, list) else []):
                        code = str(item.get('it_code') or item.get('code') or '').strip()
                        wt = float(item.get('weight') or item.get('gr_wt') or 0)
                        t = float(item.get('touch') or 0)
                        # Also detect if code itself is the touch
                        try:
                            cn = float(code)
                            if 1 <= cn <= 100:
                                t = cn
                        except (ValueError, TypeError):
                            pass
                        if t > 0 and wt > 0:
                            sold_by_touch[t] = sold_by_touch.get(t, 0) + wt
                except Exception:
                    pass

            # Build unified report
            # Weight-mode maps (KATTI bulk entries)
            wt_opening_map = {float(r['touch']): float(r['wt_opening']) for r in opening}
            wt_remaining_map = {float(r['touch']): float(r['wt_remaining']) for r in opening}
            wt_entries_map = {float(r['touch']): int(r['wt_entries']) for r in opening}
            # Piece-mode maps (tagged individual items)
            pc_opening_map = {float(r['touch']): float(r['pc_opening']) for r in opening}
            pc_remaining_map = {float(r['touch']): float(r['pc_remaining']) for r in opening}
            pc_entries_map = {float(r['touch']): int(r['pc_entries']) for r in opening}
            # Combined (used for all_touches)
            opening_map = {float(r['touch']): float(r['opening_wt']) for r in opening}
            remaining_map = {float(r['touch']): float(r['wt_remaining']) + float(r['pc_remaining']) for r in opening}

            all_touches = sorted(set(
                list(opening_map.keys()) +
                list(remaining_map.keys()) +
                list(sold_by_touch.keys())
            ))

            result = []
            for touch in all_touches:
                op = opening_map.get(touch, 0.0)
                rem = remaining_map.get(touch, 0.0)
                sol = round(sold_by_touch.get(touch, 0.0), 3)
                wt_op = wt_opening_map.get(touch, 0.0)
                wt_rem = wt_remaining_map.get(touch, 0.0)
                wt_ent = wt_entries_map.get(touch, 0)
                pc_op = pc_opening_map.get(touch, 0.0)
                pc_rem = pc_remaining_map.get(touch, 0.0)
                pc_ent = pc_entries_map.get(touch, 0)
                result.append({
                    'touch': touch,
                    # Combined (legacy)
                    'opening_wt': round(op, 3),
                    'sold_wt': sol,
                    'remaining_wt': round(rem, 3),
                    'entries': wt_ent + pc_ent,
                    # Weight-mode (KATTI bulk)
                    'wt_opening': round(wt_op, 3),
                    'wt_remaining': round(wt_rem, 3),
                    'wt_entries': wt_ent,
                    # Piece-mode (tagged items)
                    'pc_opening': round(pc_op, 3),
                    'pc_remaining': round(pc_rem, 3),
                    'pc_entries': pc_ent,
                })

            return result
        except Exception as e:
            print(f"[TOUCH REPORT ERROR] {e}")
            return []

    # ── SETUP / CONFIG ────────────────────────────────────────────────────────

    # ── SETUP STATE ────────────────────────────────────────────────────────────

    # ── Fingerprint cache — computed ONCE per process ─────────────────────────
    _FP_CACHE = None
    _FP_PINNED = None

    @staticmethod
    def _machine_fingerprint() -> str:
        """The value a licence lock code is cut from. It must not move.

        It used to be a hash of five ids, four of them read with `wmic`.
        Windows removed `wmic`, every one of those four came back empty, and
        the fingerprint changed on machines that had not been touched. A
        lock code that changes is a customer locked out of software they
        paid for.

        Two changes. The ids now come from readers that still exist. And
        once an install has a fingerprint recorded, that recorded one is
        what gets returned, for ever, because a licence lock has to be a
        fact about an install rather than a fresh opinion each morning.
        """
        if DBManager._FP_CACHE is not None:
            return DBManager._FP_CACHE
        if DBManager._FP_PINNED:
            DBManager._FP_CACHE = DBManager._FP_PINNED
            _dblog(f"[DNA] using the recorded fingerprint {DBManager._FP_PINNED[:8]}..")
            return DBManager._FP_CACHE

        import hashlib as _hl

        probe = DBManager._id_probe()
        parts = [str(probe.get(k) or '') for k in
                 ('mac', 'csproduct', 'volume', 'bios', 'baseboard')]
        raw = '|'.join(parts)
        fp = _hl.sha256(raw.encode('utf-8')).hexdigest()[:24]

        DBManager._FP_CACHE = fp
        answered = [k for k in ('mac', 'csproduct', 'volume', 'bios', 'baseboard')
                    if probe.get(k)]
        _dblog(f"[DNA] computed from {', '.join(answered) or 'nothing'} -> {fp[:8]}..")
        return fp

    @classmethod
    def _pin_fingerprint(cls, value):
        """Adopt the fingerprint an install already has, so the lock code it
        was activated with keeps working."""
        v = str(value or '').strip()
        if not v:
            return
        cls._FP_PINNED = v
        cls._FP_CACHE = v

    _ID_CACHE = None

    @classmethod
    def _id_probe(cls):
        """What this machine's readers say about it, one value per source.

        None of this goes through `wmic`. Microsoft removed it, and the day
        it went the old fingerprint changed on machines that had not moved,
        which is what sent shops back to the setup wizard with their books
        deleted. MachineGuid comes straight out of the registry with no
        process spawned at all, so there is nothing left to retire.
        """
        if cls._ID_CACHE is not None:
            return cls._ID_CACHE

        out = {}

        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SOFTWARE\Microsoft\Cryptography', 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                out['machineguid'] = str(winreg.QueryValueEx(k, 'MachineGuid')[0])
        except Exception as e:
            _dblog(f"[ID] MachineGuid unreadable: {e}")

        # CIM is what replaced wmic. One call for all of it, so a slow
        # machine pays for one process rather than four.
        try:
            import subprocess as _sp, json as _js
            script = (
                "$ErrorActionPreference='SilentlyContinue';"
                "$o=[ordered]@{};"
                "$o.csproduct=(Get-CimInstance Win32_ComputerSystemProduct).UUID;"
                "$o.bios=(Get-CimInstance Win32_BIOS).SerialNumber;"
                "$o.baseboard=(Get-CimInstance Win32_BaseBoard).SerialNumber;"
                "$o.volume=(Get-CimInstance Win32_LogicalDisk "
                "-Filter \"DeviceID='C:'\").VolumeSerialNumber;"
                "$o|ConvertTo-Json -Compress")
            raw = _sp.check_output(
                ['powershell', '-NoProfile', '-NonInteractive',
                 '-ExecutionPolicy', 'Bypass', '-Command', script],
                timeout=20, stderr=_sp.DEVNULL,
                creationflags=0x08000000).decode('utf-8', 'ignore').strip()
            if raw:
                for k2, v2 in (_js.loads(raw) or {}).items():
                    if v2:
                        out[k2] = str(v2)
        except Exception as e:
            _dblog(f"[ID] CIM unreadable: {e}")

        # Last resort only. uuid.getnode() invents a random number when it
        # cannot find a network card, so it must never outrank a real id.
        try:
            import uuid as _uuid
            node = _uuid.getnode()
            if not (node >> 40) & 1:          # the random-fallback marker
                out['mac'] = str(node)
        except Exception:
            pass

        cls._ID_CACHE = out
        _dblog('[ID] readers answering: '
               + (', '.join(sorted(k for k, v in out.items() if v)) or 'none'))
        return out

    def machine_identity(self):
        """This machine's recorded identity, e.g. 'machineguid:1a2b...'."""
        from core import machine_id as _mid
        return _mid.identity(self._id_probe())[0]

    def _wipe_business_data(self):
        """
        Called when DB is detected on a new/different PC.
        Wipes ALL business data but keeps app structure intact.
        After wipe: DB is fresh — new PC must go through setup again.
        Also deletes the setup marker file and db_path.txt so the new PC
        gets a completely clean start with no traces of the old machine.
        """
        _dblog("[WIPE] Fingerprint mismatch — wiping business data for new PC")
        try:
            with self._get_connection() as conn:
                # ── Wipe all transaction & stock tables ──
                tables = [
                    'admin_creds',
                    'stock_inventory',
                    'katti_vouchers',
                    'katti_voucher_items',
                    'sales_history',
                    'credit_ledger',
                    'uchak_inward_vouchers',
                    'uchak_inward_items',
                    'clients_master',
                    'audit_log',
                    'login_log',
                    'mac_lock',
                    'device_registry',
                    'sync_state',
                    'sync_conflicts',
                    'bastion_events',
                    'bastion_alerts',
                    'bastion_learning',
                ]
                for t in tables:
                    try:
                        conn.execute(f"DELETE FROM {t}")
                        _dblog(f"[WIPE] cleared {t}")
                    except Exception:
                        pass  # table may not exist — skip

                # ── Reset app_config: clear setup, fingerprint, owner ──
                conn.execute("""
                    UPDATE app_config
                    SET value = '0'
                    WHERE key = 'setup_done'
                """)
                conn.execute("""
                    UPDATE app_config
                    SET value = ''
                    WHERE key = 'machine_fingerprint'
                """)
                # Clear business_profile sensitive data
                try:
                    conn.execute("""
                        UPDATE business_profile
                        SET owner_name='', business_name='', city='',
                            owner_phone='', license_key=''
                        WHERE id=1
                    """)
                except Exception:
                    pass

                # Clear mac_lock so new PC can claim this DB
                try:
                    conn.execute("DELETE FROM mac_lock")
                except Exception:
                    pass
                conn.commit()
                _dblog("[WIPE] DB tables cleared")
        except Exception as e:
            _dberr(f"[WIPE] DB wipe error: {e}")

        # ── Delete setup marker file (fingerprint-locked to old PC) ──
        try:
            marker = self._setup_marker_path()
            if os.path.isfile(marker):
                os.remove(marker)
                _dblog(f"[WIPE] Deleted marker file: {marker}")
        except Exception as e:
            _dberr(f"[WIPE] Marker delete error: {e}")

        # ── Delete .license_key (machine-bound XOR, useless on new PC) ──
        try:
            key_file = os.path.join(self.db_dir, '.license_key')
            if os.path.isfile(key_file):
                os.remove(key_file)
                _dblog(f"[WIPE] Deleted .license_key: {key_file}")
        except Exception as e:
            _dberr(f"[WIPE] .license_key delete error: {e}")

        # ── Delete .lockout_state (developer's login lockouts) ──
        try:
            lockout = os.path.join(self.db_dir, '.lockout_state')
            if os.path.isfile(lockout):
                os.remove(lockout)
                _dblog(f"[WIPE] Deleted .lockout_state: {lockout}")
        except Exception as e:
            _dberr(f"[WIPE] .lockout_state delete error: {e}")

        # ── Delete db_path.txt so new PC can choose fresh DB location ──
        try:
            if getattr(sys, 'frozen', False):
                _exe_dir = os.path.dirname(sys.executable)
            else:
                _exe_dir = os.path.abspath('.')
            pref_file = os.path.join(_exe_dir, 'db_path.txt')
            if os.path.isfile(pref_file):
                os.remove(pref_file)
                _dblog(f"[WIPE] Deleted db_path.txt: {pref_file}")
        except Exception as e:
            _dberr(f"[WIPE] db_path.txt delete error: {e}")

        _dblog("[WIPE] complete — DB is fresh for new PC setup")

    def _setup_marker_path(self):
        """Path to the permanent setup-complete marker file (backup to DB flag)."""
        return os.path.join(self.db_dir, '.setup_complete')

    def _write_setup_marker(self):
        """Write marker file with current machine fingerprint baked in.
        If copied to another PC the fingerprint won't match and the marker is ignored."""
        try:
            fp = self._machine_fingerprint()
            marker = self._setup_marker_path()
            with open(marker, 'w') as f:
                f.write(fp)
            _dblog(f"[SETUP] Permanent marker written (fp={fp[:8]}...): {marker}")
        except Exception as e:
            _dberr(f"[SETUP] Failed to write marker: {e}")

    def _setup_marker_valid(self):
        """Check if marker file exists AND was written for THIS machine.
        Returns True only if the fingerprint inside the marker matches current hardware.
        If someone copies the database folder to another PC the fingerprints won't match."""
        try:
            marker = self._setup_marker_path()
            if not os.path.isfile(marker):
                return False
            with open(marker, 'r') as f:
                stored_fp = f.read().strip()
            if not stored_fp:
                return False
            current_fp = self._machine_fingerprint()
            return stored_fp == current_fp
        except Exception:
            return False

    def is_setup_done(self) -> bool:
        """Has this shop already been set up here.

        The old answer was a hash of five hardware ids that had to match
        exactly, and four of them came from `wmic`. When Windows removed
        `wmic` the hash changed on machines that had not moved, and the code
        read that as "this database belongs to another PC", wiped the stock,
        sales, customers and ledger, and showed the setup wizard.

        So: a reader that cannot answer is not evidence. If the source that
        recorded this machine has gone quiet, and somebody has an account
        here, the shop opens at its login screen. Nothing in this method
        deletes anything any more.
        """
        from core import machine_id as _mid
        try:
            probe = self._id_probe()
            with self._get_connection() as conn:
                rows = {
                    r['key']: r['value']
                    for r in conn.execute(
                        "SELECT key, value FROM app_config WHERE key IN "
                        "('setup_done','machine_fingerprint','machine_identity')"
                    ).fetchall()
                }
                accounts = conn.execute(
                    "SELECT COUNT(*) FROM admin_creds").fetchone()[0]

            stored = (rows.get('machine_identity')
                      or rows.get('machine_fingerprint') or '')
            verdict = _mid.compare(stored, probe)
            call = _mid.decide({'has_accounts': bool(accounts),
                                'setup_flag': rows.get('setup_done') == '1',
                                'verdict': verdict})

            _dblog(f"[SETUP] accounts={accounts} flag={rows.get('setup_done')!r} "
                   f"verdict={verdict} -> {call['screen']} ({call['reason']})")

            if call['screen'] == _mid.FOREIGN:
                # The folder really does look copied from another machine.
                # It still does not get the wizard: the wizard would write a
                # new shop over the one already in here. The flag is left
                # for the licence check, which is where a per-machine rule
                # belongs. Nothing is deleted either way.
                self._foreign = True
                _dblog('[SETUP] this database was set up on another machine; '
                       'opening the login and leaving the data alone')
                return True

            if call['screen'] == _mid.SETUP:
                return False

            # Recognised, or recognised enough. Take the chance to record a
            # better identity than whatever is stored, so the next launch
            # has a source it can actually re-check.
            if call.get('adopt') and _mid.should_restamp(stored, probe):
                self._stamp_identity()
            self._write_setup_marker()
            return True

        except Exception as e:
            _dberr(f"[SETUP] is_setup_done error: {e}")
            # An unreadable database is a reason to say so, not a reason to
            # walk somebody through a wizard that would write over a shop.
            try:
                with self._get_connection() as conn:
                    return conn.execute(
                        "SELECT COUNT(*) FROM admin_creds").fetchone()[0] > 0
            except Exception:
                return False

    def _stamp_identity(self):
        """Record which machine this is, and which reader said so."""
        from core import machine_id as _mid
        ident = _mid.identity(self._id_probe())[0]
        if not ident:
            _dblog('[SETUP] no reader answered, so no identity recorded')
            return
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) "
                    "VALUES('machine_identity',?)", (ident,))
                conn.commit()
            _dblog(f"[SETUP] identity recorded: {ident}")
        except Exception as e:
            _dberr(f"[SETUP] could not record identity: {e}")

    def mark_setup_done(self) -> None:
        """Write machine fingerprint to DB after successful setup."""
        try:
            fp = self._machine_fingerprint()
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('machine_fingerprint',?)",
                    (fp,)
                )
                # Also write to mac_lock table inside DB
                import socket as _sock
                hostname = ''
                try:
                    hostname = _sock.gethostname()
                except Exception:
                    pass
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mac_lock(
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        fingerprint TEXT DEFAULT '',
                        locked_at TEXT DEFAULT '',
                        hostname TEXT DEFAULT '')
                """)
                conn.execute("""
                    INSERT OR REPLACE INTO mac_lock(id, fingerprint, locked_at, hostname)
                    VALUES(1, ?, datetime('now'), ?)
                """, (fp, hostname))
                conn.commit()
            _dblog(f"[SETUP] machine_fingerprint stored: {fp[:8]}...")
            # The fingerprint above still feeds licence lock codes. This is
            # the one the setup decision reads, and it names the reader it
            # came from so a later launch can re-check that same reader.
            self._stamp_identity()
            self._write_setup_marker()
        except Exception as e:
            _dberr(f"[SETUP] mark_setup_done error: {e}")

    def save_setup(self, business_name, owner_name, owner_phone, city, pin, license_key='', address=''):
        """Save first-time setup data. Returns True on success, False on failure."""
        import hashlib as _hl, sqlite3 as _sq, datetime as _dt
        conn = None
        try:
            pin_str = str(pin or '').strip()
            pin_hash = _hl.sha256(pin_str.encode('utf-8')).hexdigest() if pin_str else ''
            now_str = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            lk = str(license_key or '').strip().upper()

            _dblog(f"[SETUP] Starting save: biz={business_name!r}")

            conn = _sq.connect(self.db_path, timeout=30, check_same_thread=False)
            conn.row_factory = _sq.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")

            # Write app_config
            pairs = [
                ('setup_done', '1'),
                ('business_name', str(business_name or '').strip()),
                ('owner_name', str(owner_name or '').strip()),
                ('owner_phone', str(owner_phone or '').strip()),
                ('city', str(city or '').strip()),
                ('address', str(address or '').strip()),
                ('owner_pin', pin_hash),
                ('setup_date', now_str),
                ('license_key', lk),
            ]
            for k, v in pairs:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                    (k, v)
                )
                _dblog(f"[SETUP] config set: {k}")

            # Write admin_creds
            conn.execute(
                "INSERT OR REPLACE INTO admin_creds(id,username,password,is_owner) VALUES(1,'owner',?,1)",
                (pin_hash,)
            )
            _dblog("[SETUP] admin_creds written")

            # One-time repair: if this PC already ran the old save_setup
            # (before is_owner was added here), id=1 exists but is_owner=0,
            # silently demoting the real owner to "staff" on every login.
            conn.execute("UPDATE admin_creds SET is_owner=1 WHERE id=1")

            conn.commit()
            _dblog("[SETUP] DB committed OK")

            # Store machine fingerprint — identifies THIS machine
            # If DB is copied to another PC, fingerprint won't match
            conn.execute(
                "INSERT OR REPLACE INTO app_config(key,value) VALUES('machine_fingerprint',?)",
                (self._machine_fingerprint(),)
            )
            conn.commit()
            _dblog(f"[SETUP] fingerprint stored: {self._machine_fingerprint()[:8]}...")

            # The line above is the licence lock code. This one is what the
            # setup check reads, and it names the reader it came from so a
            # later launch re-checks that same reader instead of hashing
            # whatever happens to answer that morning.
            self._stamp_identity()

            # Write permanent marker file — belt-and-suspenders so setup
            # NEVER shows again even if DB has future issues
            self._write_setup_marker()

        except Exception as e:
            _dberr(f"[SETUP] DB error: {e}")
            _dberr(traceback.format_exc())
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

        _dblog("[SETUP] Complete — returning True")
        return True

    def get_config(self, key, default=''):
        """Get a single config value."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM app_config WHERE key=?", (key,)
                ).fetchone()
                return row['value'] if row else default
        except Exception:
            return default

    def get_business_name(self, default='AurumOS'):
        """The shop's name, from wherever this install happens to keep it.

        Setup writes it to app_config, but business_profile.biz_name is filled
        by an older path and by some restores. Checking only the first means a
        shop can end up showing the generic product name, and that name is what
        a staff member reads to confirm they reached the right computer while
        joining, so a wrong answer there is worse than cosmetic.
        """
        name = (self.get_config('business_name', '') or '').strip()
        if name:
            return name
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT biz_name FROM business_profile WHERE id=1").fetchone()
            if row and (row['biz_name'] or '').strip():
                return row['biz_name'].strip()
        except Exception:
            pass
        return default

    def set_config(self, key, value):
        """Insert or update a single config value."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)",
                    (str(key), str(value)))
                conn.commit()
                return True
        except Exception as e:
            _dberr(f"[CONFIG] set {key}: {e}")
            return False

    # -- Bastion feature flags (local kill-switches) -------------------
    # bastion_ai.py reads these on every decision; the health dashboard
    # (or the owner, via a future settings toggle) writes them. Missing
    # means ON — a fresh install must never boot dark.

    _BASTION_FLAGS = (
        'db_watchdog_enabled',
        'session_guard_enabled',
        'auto_healer_enabled',
        'pattern_learner_enabled',
        'alert_sender_enabled',
    )

    def get_global_settings(self):
        """Every Bastion feature flag as {key: bool}."""
        out = {}
        for key in self._BASTION_FLAGS:
            try:
                raw = str(self.get_config(key, '1') or '1').strip().lower()
                out[key] = raw not in ('0', 'false', 'no', 'off', '')
            except Exception:
                out[key] = True
        return out

    def set_global_setting(self, key, enabled):
        """Flip one Bastion feature flag. Returns True on success."""
        if str(key or '') not in self._BASTION_FLAGS:
            return False
        return bool(self.set_config(str(key), '1' if enabled else '0'))

    def ensure_retail_columns(self):
        """Permanent schema migration: payment split + old-gold per bill.
        Safe to run on every startup (ALTER only if column missing)."""
        try:
            with self._get_connection() as conn:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(sales_history)").fetchall()]
                for col, ddl in (
                    ('payment_mode', "ALTER TABLE sales_history ADD COLUMN payment_mode TEXT DEFAULT 'cash'"),
                    ('old_gold_value', "ALTER TABLE sales_history ADD COLUMN old_gold_value REAL DEFAULT 0"),
                    ('old_gold_wt', "ALTER TABLE sales_history ADD COLUMN old_gold_wt REAL DEFAULT 0"),
                    ('old_gold_desc', "ALTER TABLE sales_history ADD COLUMN old_gold_desc TEXT DEFAULT ''"),
                    ('old_gold_touch', "ALTER TABLE sales_history ADD COLUMN old_gold_touch REAL DEFAULT 0"),
                    ('old_gold_rate', "ALTER TABLE sales_history ADD COLUMN old_gold_rate REAL DEFAULT 0"),
                ):
                    if col not in cols:
                        conn.execute(ddl)
                        _dblog(f"[MIGRATE] sales_history.{col} added")
                conn.commit()
        except Exception as e:
            _dberr(f"[MIGRATE] retail columns: {e}")
        self.ensure_retail_pricing_columns()

    # -- CRM ---------------------------------------------------------------
    # Storage only. Every rule about dates, dues, points and savings plans
    # lives in core/crm.py so it can be checked without a database.

    def crm_customers(self):
        """Every customer, with what they are worth and when they last came.

        The spend figures are read from the sale history rather than kept as a
        running total on the customer, so they cannot drift out of step with
        the bills they are made of.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT c.*,
                           COALESCE(s.bill_count, 0)     AS bill_count,
                           COALESCE(s.lifetime_value, 0) AS lifetime_value,
                           s.last_purchase               AS last_purchase,
                           COALESCE(l.points, 0)         AS loyalty_points
                    FROM clients_master c
                    LEFT JOIN (
                        SELECT TRIM(customer) AS cust,
                               COUNT(*) AS bill_count,
                               SUM(COALESCE(total_amount,0)) AS lifetime_value,
                               MAX(date) AS last_purchase
                        FROM sales_history
                        WHERE TRIM(COALESCE(customer,'')) != ''
                        GROUP BY TRIM(customer)
                    ) s ON TRIM(c.name) = s.cust
                    LEFT JOIN (
                        SELECT TRIM(customer) AS cust,
                               SUM(COALESCE(earned,0)) - SUM(COALESCE(used,0)) AS points
                        FROM loyalty_ledger GROUP BY TRIM(customer)
                    ) l ON TRIM(c.name) = l.cust
                    ORDER BY c.name
                """).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            _dberr(f"[CRM] customers: {e}")
            return []

    def crm_save_customer(self, data):
        """Create or update one customer, keyed by name.

        clients_master has a UNIQUE name and the screens have no stable id to
        hold on to, so the name is the key. Only real columns are written.
        """
        try:
            name = str((data or {}).get('name') or '').strip()
            if not name:
                return {'status': 'error', 'message': 'A customer needs a name.'}
            with self._get_connection() as conn:
                allowed = {r[1] for r in conn.execute(
                    "PRAGMA table_info(clients_master)").fetchall()} - {'id', 'created_at'}
                fields = {k: v for k, v in (data or {}).items() if k in allowed}
                fields['name'] = name
                found = conn.execute(
                    "SELECT id FROM clients_master WHERE TRIM(name)=?", (name,)).fetchone()
                if found:
                    rest = [k for k in fields if k != 'name']
                    if rest:
                        sets = ", ".join(f"{k}=?" for k in rest)
                        conn.execute(f"UPDATE clients_master SET {sets} WHERE id=?",
                                     tuple([fields[k] for k in rest] + [found['id']]))
                    row_id = found['id']
                else:
                    cols = ", ".join(fields)
                    marks = ", ".join("?" for _ in fields)
                    conn.execute(f"INSERT INTO clients_master ({cols}) VALUES ({marks})",
                                 tuple(fields.values()))
                    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.commit()
            return {'status': 'success', 'id': row_id, 'name': name}
        except Exception as e:
            _dberr(f"[CRM] save customer: {e}")
            return {'status': 'error', 'message': str(e)}

    def crm_dues(self):
        """Outstanding cash per customer, each entry with the date it arose,
        so the ageing buckets are built from real movements."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT TRIM(client_name) AS customer, date,
                           (COALESCE(cash_dr,0) - COALESCE(cash_cr,0)) AS amount
                    FROM credit_ledger
                    WHERE TRIM(COALESCE(client_name,'')) != ''
                    ORDER BY date
                """).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            _dberr(f"[CRM] dues: {e}")
            return []

    def crm_add_note(self, customer, note, kind='note', created_by=''):
        try:
            if not str(customer or '').strip() or not str(note or '').strip():
                return False
            return self.execute_query(
                "INSERT INTO customer_notes (customer,note,kind,created_by) VALUES (?,?,?,?)",
                (str(customer).strip(), str(note).strip(),
                 str(kind or 'note'), str(created_by or '')))
        except Exception as e:
            _dberr(f"[CRM] add note: {e}")
            return False

    def crm_notes(self, customer):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT * FROM customer_notes WHERE TRIM(customer)=? "
                    "ORDER BY id DESC LIMIT 200",
                    (str(customer or '').strip(),)).fetchall()]
        except Exception:
            return []

    def crm_add_followup(self, customer, task, due_date='', owner=''):
        try:
            if not str(customer or '').strip() or not str(task or '').strip():
                return False
            return self.execute_query(
                "INSERT INTO customer_followups (customer,task,due_date,owner) VALUES (?,?,?,?)",
                (str(customer).strip(), str(task).strip(),
                 str(due_date or '') or None, str(owner or '')))
        except Exception as e:
            _dberr(f"[CRM] add followup: {e}")
            return False

    def crm_followups(self, status='open'):
        try:
            with self._get_connection() as conn:
                order = " ORDER BY COALESCE(due_date,'9999-12-31'), id"
                if status and status != 'all':
                    rows = conn.execute(
                        "SELECT * FROM customer_followups WHERE status=?" + order,
                        (status,)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM customer_followups" + order).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def crm_close_followup(self, followup_id):
        return self.execute_query(
            "UPDATE customer_followups SET status='done', closed_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (followup_id,))

    def crm_add_loyalty(self, customer, earned=0, used=0, vch_id='', note=''):
        try:
            if not str(customer or '').strip():
                return False
            return self.execute_query(
                "INSERT INTO loyalty_ledger (customer,vch_id,earned,used,note) "
                "VALUES (?,?,?,?,?)",
                (str(customer).strip(), str(vch_id or ''), int(earned or 0),
                 int(used or 0), str(note or '')))
        except Exception as e:
            _dberr(f"[CRM] loyalty: {e}")
            return False

    def crm_loyalty_balance(self, customer):
        try:
            v = self.get_scalar(
                "SELECT COALESCE(SUM(earned),0) - COALESCE(SUM(used),0) "
                "FROM loyalty_ledger WHERE TRIM(customer)=?",
                (str(customer or '').strip(),))
            return int(v or 0)
        except Exception:
            return 0

    # -- savings schemes ---------------------------------------------------

    def crm_schemes(self, active_only=True):
        try:
            with self._get_connection() as conn:
                sql = "SELECT * FROM savings_schemes"
                if active_only:
                    sql += " WHERE active=1"
                return [dict(r) for r in conn.execute(sql + " ORDER BY name").fetchall()]
        except Exception:
            return []

    def crm_save_scheme(self, data):
        try:
            d = data or {}
            name = str(d.get('name') or '').strip()
            months = int(d.get('months') or 0)
            amount = float(d.get('monthly_amount') or 0)
            if not name or months <= 0 or amount <= 0:
                return {'status': 'error',
                        'message': 'A plan needs a name, a number of months and an amount.'}
            sid = d.get('id')
            with self._get_connection() as conn:
                if sid:
                    conn.execute(
                        "UPDATE savings_schemes SET name=?,months=?,monthly_amount=?,"
                        "bonus_type=?,bonus_value=?,active=? WHERE id=?",
                        (name, months, amount, str(d.get('bonus_type') or 'month'),
                         float(d.get('bonus_value') or 0), int(d.get('active', 1)), sid))
                else:
                    conn.execute(
                        "INSERT INTO savings_schemes "
                        "(name,months,monthly_amount,bonus_type,bonus_value,active) "
                        "VALUES (?,?,?,?,?,?)",
                        (name, months, amount, str(d.get('bonus_type') or 'month'),
                         float(d.get('bonus_value') or 0), int(d.get('active', 1))))
                    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.commit()
            return {'status': 'success', 'id': sid}
        except Exception as e:
            _dberr(f"[CRM] save scheme: {e}")
            return {'status': 'error', 'message': str(e)}

    def crm_enrol(self, scheme_id, customer, start_date=''):
        try:
            cust = str(customer or '').strip()
            if not cust or not scheme_id:
                return {'status': 'error', 'message': 'Pick a customer and a plan.'}
            start = str(start_date or '').strip() or date.today().isoformat()
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO scheme_enrolments (scheme_id,customer,start_date) "
                    "VALUES (?,?,?)", (int(scheme_id), cust, start))
                eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.commit()
            return {'status': 'success', 'id': eid}
        except Exception as e:
            _dberr(f"[CRM] enrol: {e}")
            return {'status': 'error', 'message': str(e)}

    def crm_enrolments(self, customer=''):
        """Enrolments joined to their plan, each with the payments received,
        which is what core.crm.scheme_status needs to say where one stands."""
        try:
            with self._get_connection() as conn:
                sql = ("SELECT e.id, e.customer, e.start_date, e.status, e.scheme_id, "
                       "       s.name AS scheme_name, s.months, s.monthly_amount, "
                       "       s.bonus_type, s.bonus_value "
                       "FROM scheme_enrolments e "
                       "JOIN savings_schemes s ON s.id = e.scheme_id")
                args = ()
                if str(customer or '').strip():
                    sql += " WHERE TRIM(e.customer)=?"
                    args = (str(customer).strip(),)
                sql += " ORDER BY e.id DESC"
                out = []
                for r in conn.execute(sql, args).fetchall():
                    row = dict(r)
                    row['payments'] = [dict(p) for p in conn.execute(
                        "SELECT amount,paid_date,mode FROM scheme_payments "
                        "WHERE enrolment_id=? ORDER BY paid_date",
                        (row['id'],)).fetchall()]
                    out.append(row)
                return out
        except Exception as e:
            _dberr(f"[CRM] enrolments: {e}")
            return []

    def crm_add_payment(self, enrolment_id, amount, paid_date='', mode='cash', note=''):
        try:
            amt = float(amount or 0)
            if not enrolment_id or amt <= 0:
                return {'status': 'error', 'message': 'An instalment needs an amount.'}
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO scheme_payments (enrolment_id,amount,paid_date,mode,note) "
                    "VALUES (?,?,COALESCE(NULLIF(?,''),date('now')),?,?)",
                    (int(enrolment_id), amt, str(paid_date or ''),
                     str(mode or 'cash'), str(note or '')))
                conn.commit()
            return {'status': 'success'}
        except Exception as e:
            _dberr(f"[CRM] payment: {e}")
            return {'status': 'error', 'message': str(e)}

    # -- orders and repairs ------------------------------------------------

    def crm_save_order(self, data):
        try:
            d = data or {}
            cust = str(d.get('customer') or '').strip()
            desc = str(d.get('description') or '').strip()
            if not cust or not desc:
                return {'status': 'error',
                        'message': 'An order needs a customer and a description.'}
            status = str(d.get('status') or 'open')
            with self._get_connection() as conn:
                if d.get('id'):
                    conn.execute(
                        "UPDATE customer_orders SET customer=?,kind=?,description=?,"
                        "promised_date=?,advance=?,karigar=?,status=?,"
                        "closed_at=CASE WHEN ?='done' THEN CURRENT_TIMESTAMP ELSE closed_at END "
                        "WHERE id=?",
                        (cust, str(d.get('kind') or 'order'), desc,
                         str(d.get('promised_date') or '') or None,
                         float(d.get('advance') or 0), str(d.get('karigar') or ''),
                         status, status, d['id']))
                    oid = d['id']
                else:
                    conn.execute(
                        "INSERT INTO customer_orders "
                        "(customer,kind,description,promised_date,advance,karigar,status) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (cust, str(d.get('kind') or 'order'), desc,
                         str(d.get('promised_date') or '') or None,
                         float(d.get('advance') or 0), str(d.get('karigar') or ''), status))
                    oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.commit()
            return {'status': 'success', 'id': oid}
        except Exception as e:
            _dberr(f"[CRM] save order: {e}")
            return {'status': 'error', 'message': str(e)}

    def wa_log_send(self, customer, kind, phone='', body=''):
        """Record that a message went out, so it does not go out again.

        A birthday wish should reach someone once a year, not once per time
        the screen is opened.
        """
        try:
            if not str(customer or '').strip():
                return False
            return self.execute_query(
                "INSERT INTO whatsapp_log (customer,phone,kind,body) VALUES (?,?,?,?)",
                (str(customer).strip(), str(phone or ''), str(kind or ''),
                 str(body or '')[:1000]))
        except Exception as e:
            _dberr(f"[WA] log: {e}")
            return False

    def wa_sent_since(self, kind, days=300):
        """Names this kind has already gone to recently.

        The default window is under a year on purpose: a birthday wish sent
        last year should not stop this year's.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT TRIM(customer) AS c FROM whatsapp_log "
                    "WHERE kind=? AND date(sent_on) >= date('now', ?)",
                    (str(kind or ''), '-%d days' % int(days))).fetchall()
                return {r['c'] for r in rows if r['c']}
        except Exception as e:
            _dberr(f"[WA] sent_since: {e}")
            return set()

    def wa_history(self, customer='', limit=200):
        try:
            with self._get_connection() as conn:
                if str(customer or '').strip():
                    rows = conn.execute(
                        "SELECT * FROM whatsapp_log WHERE TRIM(customer)=? "
                        "ORDER BY id DESC LIMIT ?",
                        (str(customer).strip(), int(limit))).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM whatsapp_log ORDER BY id DESC LIMIT ?",
                        (int(limit),)).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def wa_set_opt_out(self, customer, opted_out=True):
        """Stop messaging this customer, or start again."""
        try:
            return self.execute_query(
                "UPDATE clients_master SET wa_opt_out=? WHERE TRIM(name)=?",
                ('1' if opted_out else '0', str(customer or '').strip()))
        except Exception as e:
            _dberr(f"[WA] opt out: {e}")
            return False

    def crm_close_order(self, order_id):
        """Mark an order done without touching anything else on it.

        Reusing the save path for this meant sending a whole row back just to
        change one field, and any value the caller left out would overwrite
        what was stored.
        """
        return self.execute_query(
            "UPDATE customer_orders SET status='done', closed_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (order_id,))

    def crm_orders(self, status='open'):
        try:
            with self._get_connection() as conn:
                order = " ORDER BY COALESCE(promised_date,'9999-12-31'), id"
                if status and status != 'all':
                    rows = conn.execute(
                        "SELECT * FROM customer_orders WHERE status=?" + order,
                        (status,)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM customer_orders" + order).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def ensure_crm_tables(self):
        """Everything the CRM needs, added in place on an existing shop.

        clients_master held a name, a phone and two credit limits. That is
        enough to bill someone and not much else: no way to know when their
        birthday is, whether they are on a savings plan, what was promised to
        them, or why they stopped coming in.

        Safe on every startup. Columns are added only where missing and every
        table is IF NOT EXISTS.
        """
        try:
            with self._get_connection() as conn:
                have = [r[1] for r in conn.execute(
                    "PRAGMA table_info(clients_master)").fetchall()]
                for col, ddl in (
                    ('dob',          "ALTER TABLE clients_master ADD COLUMN dob DATE"),
                    ('anniversary',  "ALTER TABLE clients_master ADD COLUMN anniversary DATE"),
                    ('email',        "ALTER TABLE clients_master ADD COLUMN email TEXT"),
                    ('whatsapp',     "ALTER TABLE clients_master ADD COLUMN whatsapp TEXT"),
                    ('address',      "ALTER TABLE clients_master ADD COLUMN address TEXT"),
                    ('city',         "ALTER TABLE clients_master ADD COLUMN city TEXT"),
                    ('pan',          "ALTER TABLE clients_master ADD COLUMN pan TEXT"),
                    ('gstin',        "ALTER TABLE clients_master ADD COLUMN gstin TEXT"),
                    # Set by hand when the shop disagrees with the inferred one.
                    ('segment',      "ALTER TABLE clients_master ADD COLUMN segment TEXT"),
                    ('referred_by',  "ALTER TABLE clients_master ADD COLUMN referred_by TEXT"),
                    # Jewellery is bought by families, so several names are
                    # often one relationship.
                    ('household',    "ALTER TABLE clients_master ADD COLUMN household TEXT"),
                    ('notes',        "ALTER TABLE clients_master ADD COLUMN notes TEXT"),
                    # A customer who asks not to be messaged is kept out of
                    # every route, including the bulk ones where nobody is
                    # reading the list.
                    ('wa_opt_out',   "ALTER TABLE clients_master ADD COLUMN wa_opt_out TEXT DEFAULT '0'"),
                ):
                    if col not in have:
                        conn.execute(ddl)
                        _dblog(f"[MIGRATE] clients_master.{col} added")

                conn.executescript("""
                CREATE TABLE IF NOT EXISTS customer_notes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer    TEXT NOT NULL,
                    note        TEXT NOT NULL,
                    kind        TEXT DEFAULT 'note',
                    created_by  TEXT DEFAULT '',
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS idx_cust_notes ON customer_notes(customer);

                CREATE TABLE IF NOT EXISTS customer_followups (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer    TEXT NOT NULL,
                    task        TEXT NOT NULL,
                    due_date    DATE,
                    owner       TEXT DEFAULT '',
                    status      TEXT DEFAULT 'open',
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    closed_at   DATETIME);
                CREATE INDEX IF NOT EXISTS idx_followup_due ON customer_followups(status, due_date);

                CREATE TABLE IF NOT EXISTS loyalty_ledger (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer    TEXT NOT NULL,
                    vch_id      TEXT DEFAULT '',
                    earned      INTEGER DEFAULT 0,
                    used        INTEGER DEFAULT 0,
                    note        TEXT DEFAULT '',
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS idx_loyalty_cust ON loyalty_ledger(customer);

                CREATE TABLE IF NOT EXISTS savings_schemes (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT NOT NULL,
                    months         INTEGER NOT NULL,
                    monthly_amount REAL NOT NULL,
                    bonus_type     TEXT DEFAULT 'month',
                    bonus_value    REAL DEFAULT 1,
                    active         INTEGER DEFAULT 1,
                    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP);

                CREATE TABLE IF NOT EXISTS scheme_enrolments (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    scheme_id   INTEGER NOT NULL,
                    customer    TEXT NOT NULL,
                    start_date  DATE NOT NULL,
                    status      TEXT DEFAULT 'active',
                    closed_at   DATETIME,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS idx_enrol_cust ON scheme_enrolments(customer);

                CREATE TABLE IF NOT EXISTS scheme_payments (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    enrolment_id INTEGER NOT NULL,
                    amount       REAL NOT NULL,
                    paid_date    DATE DEFAULT (date('now')),
                    mode         TEXT DEFAULT 'cash',
                    note         TEXT DEFAULT '',
                    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS idx_pay_enrol ON scheme_payments(enrolment_id);

                CREATE TABLE IF NOT EXISTS whatsapp_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer    TEXT NOT NULL,
                    phone       TEXT DEFAULT '',
                    kind        TEXT NOT NULL,
                    body        TEXT DEFAULT '',
                    sent_on     DATE DEFAULT (date('now')),
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS idx_wa_cust ON whatsapp_log(customer, kind, sent_on);

                CREATE TABLE IF NOT EXISTS customer_orders (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer       TEXT NOT NULL,
                    kind           TEXT DEFAULT 'order',
                    description    TEXT NOT NULL,
                    promised_date  DATE,
                    advance        REAL DEFAULT 0,
                    karigar        TEXT DEFAULT '',
                    status         TEXT DEFAULT 'open',
                    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                    closed_at      DATETIME);
                CREATE INDEX IF NOT EXISTS idx_orders_status ON customer_orders(status, promised_date);
                """)
                conn.commit()
        except Exception as e:
            _dberr(f"[MIGRATE] crm tables: {e}")

    def set_item_photo(self, tag_id, filename):
        """Record which picture belongs to a piece."""
        try:
            return self.execute_query(
                "UPDATE stock_inventory SET photo=? WHERE LOWER(TRIM(tag_id))=LOWER(?)",
                (str(filename or ''), str(tag_id or '').strip()))
        except Exception as e:
            _dberr(f"[PHOTO] set: {e}")
            return False

    def items_with_photos(self, limit=200):
        """Tagged stock that has a picture, newest first."""
        try:
            sql = (
                "SELECT id, tag_id, it_code, it_name, gr_wt, nt_wt, "
                "       COALESCE(category,'') AS category, "
                "       COALESCE(karat,'')    AS karat, "
                "       COALESCE(sale_price,0) AS sale_price, "
                "       COALESCE(photo,'')     AS photo "
                "FROM stock_inventory "
                "WHERE COALESCE(photo,'') != '' "
                "ORDER BY id DESC LIMIT ?")
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(sql, (int(limit),)).fetchall()]
        except Exception:
            return []

    def get_item_by_tag(self, tag_id):
        """One tagged piece, by its tag, with everything the till needs.

        Without this the POS had no way to learn what a scanned piece costs,
        so rate and making came out of browser storage: one pair of numbers
        for the whole shop. Here the piece carries its own, falling back to
        its product master and then to nothing, which the till reads as
        "ask the salesperson".
        """
        try:
            tag = str(tag_id or '').strip()
            if not tag:
                return None
            with self._get_connection() as conn:
                row = conn.execute(
                    """SELECT s.*,
                              COALESCE(NULLIF(s.making_charge,0), p.making_charge, 0) AS eff_making_charge,
                              COALESCE(NULLIF(s.making_type,''),  p.making_type, 'percent') AS eff_making_type,
                              COALESCE(NULLIF(s.sale_price,0),    p.sale_price, 0) AS eff_sale_price,
                              COALESCE(NULLIF(s.category,''),     p.category, '') AS eff_category,
                              COALESCE(p.hsn,'') AS hsn
                       FROM stock_inventory s
                       LEFT JOIN product_master p ON TRIM(p.code) = TRIM(s.it_code)
                       WHERE LOWER(TRIM(s.tag_id)) = LOWER(?)
                       LIMIT 1""",
                    (tag,)).fetchone()
                return dict(row) if row else None
        except Exception as e:
            _dberr(f"[TAG LOOKUP] {tag_id}: {e}")
            return None

    def get_sold_tags(self, from_date='', to_date='', search='', group=''):
        """Pieces that have been sold, for the Sold Tags screen.

        The screen has called this since it was written and nothing ever
        defined it, so the page could only ever render "Failed to load".
        Rows are read out of the sale history, which is where the item lines
        actually live once stock has been deducted.
        """
        try:
            where, args = ["1=1"], []
            if from_date:
                where.append("date(sh.date) >= date(?)"); args.append(str(from_date))
            if to_date:
                where.append("date(sh.date) <= date(?)"); args.append(str(to_date))
            sql = f"""SELECT sh.vch_id, sh.customer, sh.date, sh.status,
                             sh.total_amount, sh.mobile, sh.items
                      FROM sales_history sh
                      WHERE {' AND '.join(where)}
                      ORDER BY sh.date DESC, sh.id DESC
                      LIMIT 2000"""
            out = []
            needle = str(search or '').strip().lower()
            grp = str(group or '').strip().lower()
            with self._get_connection() as conn:
                have = [r[1] for r in conn.execute("PRAGMA table_info(sales_history)").fetchall()]
                if 'items' not in have:
                    return []
                for bill in conn.execute(sql, tuple(args)).fetchall():
                    try:
                        items = json.loads(bill['items'] or '[]')
                    except Exception:
                        items = []
                    if not isinstance(items, list):
                        items = []
                    for it in items:
                        row = {
                            'vch_id':   bill['vch_id'],
                            'customer': bill['customer'],
                            'date':     bill['date'],
                            'status':   bill['status'],
                            'mobile':   bill['mobile'],
                            'tag_id':   str(it.get('tag_id') or '').strip(),
                            'it_code':  str(it.get('code') or it.get('it_code') or '').strip(),
                            'it_name':  str(it.get('it_name') or it.get('name') or '').strip(),
                            'huid':     str(it.get('huid') or '').strip(),
                            'touch':    float(it.get('touch') or 0),
                            'gr_wt':    float(it.get('weight') or it.get('gr_wt') or 0),
                            'less':     float(it.get('ls_wt') or 0),
                            'para':     float(it.get('para_stone_wt') or 0),
                            'fine':     float(it.get('fine') or 0),
                            'making':   float(it.get('making') or 0),
                            'amount':   float(it.get('amount') or 0),
                            'category': str(it.get('category') or '').strip(),
                        }
                        if needle and needle not in ' '.join(
                                str(row[k]).lower() for k in
                                ('tag_id', 'it_code', 'it_name', 'huid', 'customer', 'vch_id')):
                            continue
                        # The filter is a touch band in wholesale ("91.x") and a
                        # category in retail. One control, read whichever way the
                        # value looks.
                        if grp and grp not in ('', 'all'):
                            # A band reads "91.x" on the screen while its option
                            # value is the bare "91", so accept either rather
                            # than silently filtering every row away.
                            head = grp[:-2] if grp.endswith('.x') else grp
                            if head.isdigit():
                                if not str(int(row['touch'] or 0)).startswith(head):
                                    continue
                            elif row['category'].lower() != grp:
                                continue
                        out.append(row)
            return out
        except Exception as e:
            _dberr(f"[SOLD TAGS] {e}")
            return []

    def ensure_retail_pricing_columns(self):
        """What a retail shop needs to price a piece, which the schema had no
        room for.

        Stock was modelled the wholesale way: weight and purity, settled in
        grams. A retail shop sells a finished piece for money, so it has to be
        able to say what this piece costs to make and what it sells for. With
        nowhere to put that, the till fell back to one rate and one making
        charge held in browser storage and applied to every line, which is why
        a shop selling rings at 12% and chains at 8% retyped it every time.

        Safe on every startup: ALTER only where the column is missing.
        """
        try:
            with self._get_connection() as conn:
                def add(table, columns):
                    have = [r[1] for r in conn.execute(
                        f"PRAGMA table_info({table})").fetchall()]
                    for col, ddl in columns:
                        if col not in have:
                            conn.execute(ddl)
                            _dblog(f"[MIGRATE] {table}.{col} added")

                add('stock_inventory', (
                    ('category',      "ALTER TABLE stock_inventory ADD COLUMN category TEXT"),
                    ('karat',         "ALTER TABLE stock_inventory ADD COLUMN karat TEXT"),
                    # percent of metal value | rupees per gram | a flat amount
                    ('making_type',   "ALTER TABLE stock_inventory ADD COLUMN making_type TEXT DEFAULT 'percent'"),
                    ('making_charge', "ALTER TABLE stock_inventory ADD COLUMN making_charge REAL DEFAULT 0"),
                    # 0 means "work it out from today's rate" rather than "free"
                    ('sale_price',    "ALTER TABLE stock_inventory ADD COLUMN sale_price REAL DEFAULT 0"),
                    ('stone_value',   "ALTER TABLE stock_inventory ADD COLUMN stone_value REAL DEFAULT 0"),
                    # Filename only. The picture itself lives beside the
                    # database rather than in it, so the file stays a normal
                    # file a shopkeeper can open, and the database stays small.
                    ('photo',         "ALTER TABLE stock_inventory ADD COLUMN photo TEXT"),
                ))
                add('product_master', (
                    ('making_type',   "ALTER TABLE product_master ADD COLUMN making_type TEXT DEFAULT 'percent'"),
                    ('making_charge', "ALTER TABLE product_master ADD COLUMN making_charge REAL DEFAULT 0"),
                    ('sale_price',    "ALTER TABLE product_master ADD COLUMN sale_price REAL DEFAULT 0"),
                    ('hsn',           "ALTER TABLE product_master ADD COLUMN hsn TEXT"),
                ))
                conn.commit()
        except Exception as e:
            _dberr(f"[MIGRATE] retail pricing columns: {e}")

    # ── NOTIFICATION CENTER ──────────────────────────────────────────────
    # Owner inbox: staff-bill alerts (local) + AurumOS admin messages (remote).
    # Table is local-only and never synced.

    def ensure_notifications_table(self):
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS notifications (
                        id     INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                        type   TEXT DEFAULT 'info',
                        title  TEXT DEFAULT '',
                        body   TEXT DEFAULT '',
                        page   TEXT DEFAULT '',
                        source TEXT DEFAULT 'local',
                        ext_id TEXT DEFAULT '',
                        read   INTEGER DEFAULT 0)
                """)
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_ext "
                             "ON notifications(source, ext_id)")
                conn.commit()
        except Exception as e:
            _dberr(f"[NOTIF] table: {e}")

    def push_notification(self, ntype, title, body, page='', source='local', ext_id=''):
        """Insert one inbox notification. Admin messages dedupe by ext_id."""
        try:
            self.ensure_notifications_table()
            with self._get_connection() as conn:
                if source == 'admin' and ext_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO notifications(type,title,body,page,source,ext_id) "
                        "VALUES(?,?,?,?,?,?)",
                        (ntype, title, body, page, source, ext_id))
                else:
                    conn.execute(
                        "INSERT INTO notifications(type,title,body,page,source) VALUES(?,?,?,?,?)",
                        (ntype, title, body, page, source))
                conn.commit()
                return True
        except Exception as e:
            _dberr(f"[NOTIF] push: {e}")
            return False

    def get_notifications(self, limit=20):
        try:
            self.ensure_notifications_table()
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT id, ts, type, title, body, page, source, read "
                    "FROM notifications ORDER BY id DESC LIMIT ?",
                    (int(limit or 20),)).fetchall()]
        except Exception as e:
            _dberr(f"[NOTIF] list: {e}")
            return []

    def mark_notification_read(self, nid):
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE notifications SET read=1 WHERE id=?", (int(nid),))
                conn.commit()
                return True
        except Exception:
            return False

    def mark_all_notifications_read(self):
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE notifications SET read=1 WHERE read=0")
                conn.commit()
                return True
        except Exception:
            return False

    # ── URGENT MESSAGES (AurumOS admin → this terminal) ──────────────────
    # Local-only inbox. Server keeps ~30 recent; we keep last 100.

    def ensure_urgent_table(self):
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS urgent_messages (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        ext_id      TEXT UNIQUE,
                        title       TEXT DEFAULT '',
                        body        TEXT DEFAULT '',
                        priority    TEXT DEFAULT 'info',
                        sent_at     TEXT DEFAULT '',
                        target_mode TEXT DEFAULT '',
                        read        INTEGER DEFAULT 0,
                        created_ts  TEXT NOT NULL DEFAULT (datetime('now','localtime')))
                """)
                # Tombstones: user-deleted ids are never re-added by catch-up.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS urgent_hidden (
                        ext_id TEXT PRIMARY KEY,
                        ts     TEXT NOT NULL DEFAULT (datetime('now','localtime')))
                """)
                conn.commit()
        except Exception as e:
            _dberr(f"[URGENT] table: {e}")

    def is_urgent_hidden(self, ext_id):
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM urgent_hidden WHERE ext_id=?",
                    (str(ext_id),)).fetchone()
                return row is not None
        except Exception:
            return False

    @staticmethod
    def urgent_applies(msg, my_key):
        """True if a history message targets THIS terminal."""
        try:
            if str(msg.get('target_mode') or '').lower() == 'all':
                return True
            keys = msg.get('target_keys') or []
            if isinstance(keys, str):
                try:
                    import json as _j
                    keys = _j.loads(keys)
                except Exception:
                    keys = [keys]
            mine = str(my_key or '').strip().upper()
            return any(str(k or '').strip().upper() == mine for k in (keys or []))
        except Exception:
            return False

    def store_urgent_message(self, msg):
        """Dedupe by server id. Returns True only when newly inserted."""
        try:
            ext = str(msg.get('id') or '').strip()
            if not ext:
                return False
            if self.is_urgent_hidden(ext):
                return False
            pri = str(msg.get('priority') or 'info').lower()
            if pri not in ('info', 'warning', 'critical'):
                pri = 'info'
            self.ensure_urgent_table()
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO urgent_messages"
                    "(ext_id,title,body,priority,sent_at,target_mode) "
                    "VALUES(?,?,?,?,?,?)",
                    (ext, str(msg.get('title') or '')[:120],
                     str(msg.get('body') or '')[:1000], pri,
                     str(msg.get('sent_at') or msg.get('created_at') or ''),
                     str(msg.get('target_mode') or '')))
                conn.commit()
                is_new = cur.rowcount > 0
            if is_new:
                self.prune_urgent_messages(keep=100)
            return is_new
        except Exception as e:
            _dberr(f"[URGENT] store: {e}")
            return False

    def prune_urgent_messages(self, keep=100):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM urgent_messages WHERE id NOT IN "
                    "(SELECT id FROM urgent_messages ORDER BY id DESC LIMIT ?)",
                    (int(keep),))
                conn.commit()
        except Exception:
            pass

    def get_urgent_messages(self, limit=100):
        try:
            self.ensure_urgent_table()
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT ext_id AS id, title, body, priority, sent_at, "
                    "target_mode, read FROM urgent_messages "
                    "ORDER BY id DESC LIMIT ?", (int(limit or 100),)).fetchall()]
        except Exception as e:
            _dberr(f"[URGENT] list: {e}")
            return []

    def urgent_unread_count(self):
        try:
            self.ensure_urgent_table()
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM urgent_messages WHERE read=0").fetchone()
                return int(row['c'] or 0) if row else 0
        except Exception:
            return 0

    def mark_urgent_read(self, ext_id):
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE urgent_messages SET read=1 WHERE ext_id=?",
                             (str(ext_id),))
                conn.commit()
                return True
        except Exception:
            return False

    def mark_all_urgent_read(self):
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE urgent_messages SET read=1 WHERE read=0")
                conn.commit()
                return True
        except Exception:
            return False

    def delete_urgent_message(self, ext_id):
        """Permanently remove one message (used for test cleanup).
        Records a tombstone so catch-up never brings it back."""
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO urgent_hidden(ext_id) VALUES(?)",
                             (str(ext_id),))
                conn.execute("DELETE FROM urgent_messages WHERE ext_id=?",
                             (str(ext_id),))
                conn.commit()
                return True
        except Exception:
            return False

    # ── AUDIT LOG ──────────────────────────────────────────────────────────────

    def add_audit_log(self, action: str, detail: str = '',
                      username: str = 'system', category: str = 'general'):
        """Write one audit log entry. Auto-creates table if missing."""
        try:
            from datetime import datetime as _dt
            ts = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
            with self._get_connection() as conn:
                # Auto-create table — safe for old DBs
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts         TEXT    NOT NULL,
                        username   TEXT    NOT NULL DEFAULT 'system',
                        action     TEXT    NOT NULL,
                        detail     TEXT    DEFAULT '',
                        category   TEXT    DEFAULT 'general',
                        ip         TEXT    DEFAULT '')
                """)
                conn.execute(
                    "INSERT INTO audit_log(ts,username,action,detail,category)"
                    " VALUES(?,?,?,?,?)",
                    (ts, str(username), str(action), str(detail), str(category))
                )
                conn.commit()
        except Exception as e:
            print(f"[AUDIT] Log error: {e}")

    def get_audit_logs(self, limit: int = 20) -> list:
        """Return the most recent audit log entries."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT ts, username, action, detail, category"
                    " FROM audit_log ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[AUDIT] Read error: {e}")
            return []

    def get_stagnant_report(self, threshold=90):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT *, CAST((julianday('now')-julianday(timestamp)) AS INTEGER) as days_old
                    FROM stock_inventory
                    WHERE (julianday('now')-julianday(timestamp)) >= ?
                    ORDER BY days_old DESC
                """, (threshold,)).fetchall()]
        except:
            return []

    def get_velocity_products(self):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT it_name, COUNT(*) as qty, SUM(nt_wt) as weight
                    FROM stock_inventory GROUP BY it_name ORDER BY qty DESC LIMIT 5
                """).fetchall()]
        except:
            return []

    def get_inventory_stats(self):
        """Alias used by inventory.html -- returns live reduced stock values."""
        try:
            with self._get_connection() as conn:
                # Tagged pieces
                showroom = conn.execute("""
                    SELECT COUNT(*) as p, COALESCE(SUM(nt_wt),0) as w FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                """).fetchone()

                # Uchak piece stock -- exclude weight-based rows (gr_wt > 0)
                uchak = conn.execute("""
                    SELECT COALESCE(SUM(pcs),0) as p FROM stock_inventory
                    WHERE (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                      AND pcs > 0
                      AND (gr_wt IS NULL OR gr_wt = 0)
                """).fetchone()

                # ? LIVE weight from stock_inventory (reduced after sales)
                weight_stock = conn.execute("""
                    SELECT COALESCE(SUM(gr_wt),0) as w FROM stock_inventory
                    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND tag_id NOT LIKE 'OPENING-%'
                      AND gr_wt > 0
                """).fetchone()

                # Untagged pieces with weight (added via stock ledger, not yet tagged)
                untagged_wt = conn.execute("""
                    SELECT COALESCE(SUM(gr_wt),0) as w FROM stock_inventory
                    WHERE is_tagged = 0
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','-','undefined','null')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND tag_id NOT LIKE 'OPENING-%'
                      AND gr_wt > 0
                """).fetchone()

                # Katti packets (voucher count -- does not reduce)
                katti = conn.execute(
                    "SELECT COALESCE(SUM(total_packets),0) as p FROM katti_vouchers"
                ).fetchone()

                # Untagged physical items (real tag_id, not yet printed/tagged)
                # Include ALL untagged items regardless of gr_wt
                untagged = conn.execute("""
                    SELECT COUNT(*) as p FROM stock_inventory
                    WHERE is_tagged = 0
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','-','undefined','null')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND tag_id NOT LIKE 'OPENING-%'
                """).fetchone()

                profile = conn.execute("SELECT owner_name FROM business_profile WHERE id=1").fetchone()
                owner = profile['owner_name'] if (profile and profile['owner_name']) else None

                # Net weight = tagged showroom + weight stock only (untagged NOT included)
                total_w = (showroom['w'] or 0) + (weight_stock['w'] or 0)

                return {
                    "net": round(total_w, 3),
                    "pcs": showroom['p'] or 0,
                    "uchak_pcs": uchak['p'] or 0,
                    "packets": katti['p'] or 0,
                    "untagged_items": untagged['p'] or 0,
                    "katti_net": round(weight_stock['w'] or 0, 3),
                    "opening_wt": 0,
                    "owner_name": owner
                }
        except Exception as e:
            print(f"? [INVENTORY STATS ERROR] {e}")
            return {"net": 0, "pcs": 0, "uchak_pcs": 0, "packets": 0, "untagged_items": 0, "katti_net": 0,
                    "owner_name": None}

    def get_analytics_payload(self):
        """
        Alias used by inventory.html.
        ? FIXED: bins and katti_weight read from stock_inventory (live reduced values).
        """
        try:
            with self._get_connection() as conn:

                # ? Bin heatmap -- LIVE weight from stock_inventory
                bins_weight = conn.execute("""
                    SELECT UPPER(TRIM(huid)) as bin_id,
                           SUM(nt_wt) as weight,
                           json_group_array(json_object(
                               'it_name', it_name,
                               'nt_wt',   nt_wt,
                               'it_code', it_code
                           )) as items_json
                    FROM stock_inventory
                    WHERE huid IS NOT NULL
                      AND TRIM(huid) NOT IN ('','-','None','N/A')
                      AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND nt_wt > 0
                    GROUP BY UPPER(TRIM(huid))
                """).fetchall()

                # Tagged piece bins
                bins_tagged = conn.execute("""
                    SELECT UPPER(TRIM(huid)) as bin_id,
                           SUM(nt_wt) as weight,
                           json_group_array(json_object(
                               'it_name', it_name,
                               'nt_wt',   nt_wt,
                               'it_code', it_code
                           )) as items_json
                    FROM stock_inventory
                    WHERE huid IS NOT NULL
                      AND TRIM(huid) NOT IN ('','-','None','N/A')
                      AND tag_id NOT IN ('N/A','')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                    GROUP BY UPPER(TRIM(huid))
                """).fetchall()

                def norm(raw):
                    c = str(raw).replace(' ', '').replace('-', '').upper()
                    return f"B-{c[1:].zfill(3)}" if c.startswith('B') and len(c) > 1 else raw

                bin_map = {};
                items_map = {}
                for b in list(bins_weight) + list(bins_tagged):
                    k = norm(b['bin_id'])
                    bin_map[k] = round(bin_map.get(k, 0.0) + (b['weight'] or 0.0), 3)
                    try:
                        parsed = json.loads(b['items_json'])
                        if k not in items_map: items_map[k] = []
                        items_map[k].extend(parsed)
                    except:
                        pass

                bins = [{"bin_id": k, "weight": v, "items": items_map.get(k, [])}
                        for k, v in sorted(bin_map.items())]

                # ? Category distribution -- LIVE weight from stock_inventory
                k_data = [dict(r) for r in conn.execute("""
                    SELECT it_name as cat_group,
                           gr_wt   as total_weight
                    FROM stock_inventory
                    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND gr_wt > 0
                    ORDER BY it_name ASC
                """).fetchall()]

                # Tagged piece distribution
                p_data = [dict(r) for r in conn.execute("""
                    SELECT UPPER(TRIM(it_name)) as cat_group, SUM(pcs) as total_pcs
                    FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                    GROUP BY UPPER(TRIM(it_name))
                """).fetchall()]

                u_data = [dict(r) for r in conn.execute("""
                    SELECT ui.vch_id as cat_group, SUM(ui.pcs) as total_pcs
                    FROM uchak_inward_items ui
                    JOIN uchak_inward_vouchers uv ON ui.vch_id=uv.vch_id
                    WHERE ui.pcs>0
                    GROUP BY ui.vch_id ORDER BY uv.timestamp ASC
                """).fetchall()]

                return {
                    "status": "success",
                    "bins": bins,
                    "katti_weight": k_data,
                    "manual_pcs": p_data,
                    "uchak_pcs_chart": u_data
                }
        except Exception as e:
            print(f"? [ANALYTICS PAYLOAD ERROR] {e}")
            return {"status": "error", "bins": [], "katti_weight": [], "manual_pcs": [], "uchak_pcs_chart": []}

    def get_items_by_bin(self, bin_id):
        """Returns all stock items belonging to a specific storage bin."""
        try:
            def norm(raw):
                c = str(raw).replace(' ', '').replace('-', '').upper()
                return f"B-{c[1:].zfill(3)}" if c.startswith('B') and len(c) > 1 else raw

            bin_id = norm(bin_id)
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT id, it_code, it_name, tag_id, pcs, touch, gr_wt, nt_wt,
                           para_stone_wt, ls_wt, huid, entry_date,
                           COALESCE(category,'')      AS category,
                           COALESCE(karat,'')         AS karat,
                           COALESCE(making_type,'')   AS making_type,
                           COALESCE(making_charge,0)  AS making_charge,
                           COALESCE(sale_price,0)     AS sale_price,
                           COALESCE(stone_value,0)    AS stone_value,
                           COALESCE(photo,'')         AS photo,
                           CASE WHEN is_tagged=1 THEN 'TAGGED' ELSE 'PENDING' END as live_status
                    FROM stock_inventory
                    WHERE UPPER(TRIM(huid)) = ?
                      AND nt_wt > 0
                    ORDER BY id DESC
                """, (bin_id,)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[GET ITEMS BY BIN ERROR] {e}")
            return []

    def get_stock_ledger(self):
        """Returns tagged stock items for the inventory table -- excludes opening stock."""
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id, it_code, it_name, tag_id, pcs, touch, gr_wt, nt_wt,
                           para_stone_wt, ls_wt, huid, entry_date,
                           COALESCE(category,'')      AS category,
                           COALESCE(karat,'')         AS karat,
                           COALESCE(making_type,'')   AS making_type,
                           COALESCE(making_charge,0)  AS making_charge,
                           COALESCE(sale_price,0)     AS sale_price,
                           COALESCE(stone_value,0)    AS stone_value,
                           COALESCE(photo,'')         AS photo,
                           CASE WHEN is_tagged=1 THEN 'TAGGED' ELSE 'PENDING' END as live_status
                    FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND tag_id NOT LIKE 'OPENING-%'
                    ORDER BY id DESC
                """).fetchall()]
        except Exception as e:
            print(f"? [STOCK LEDGER ERROR] {e}");
            return []

    def get_opening_stock(self):
        """
        Returns ONLY rows explicitly tagged as OPENING stock.
        These are entries made via opening_stock.html with tag_id = 'OPENING-xxx'.

        PERMANENT RULE -- one source of truth, no overlap:
        +-----------------+----------------------------------------------+
        | OPENING column  | stock_inventory WHERE tag_id LIKE 'OPENING-%'|
        | INWARD column   | katti_voucher_items (via katti terminal)     |
        |                 | + stock_inventory NULL/N/A rows (stock ledger)|
        | OUTWARD column  | sales_history                                |
        +-----------------+----------------------------------------------+
        Rows with tag_id NULL/N/A go into INWARD (not opening).
        Rows with tag_id KATTI- go into INWARD via katti_voucher_items.
        Only 'OPENING-' prefix rows are true opening carry-forward stock.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT id, it_code, it_name, tag_id,
                           gr_wt, ls_wt, nt_wt, touch, wastage,
                           pcs, entry_date, design
                    FROM stock_inventory
                    WHERE tag_id LIKE 'OPENING-%'
                    ORDER BY id DESC
                """).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    # Uchak rows store price in the 'design' column as text -- expose as 'price' too
                    try:
                        d['price'] = float(d.get('design') or 0)
                    except (TypeError, ValueError):
                        d['price'] = 0.0
                    result.append(d)
                return result
        except Exception as e:
            _dberr(f"[OPENING STOCK] get: {e}");
            return []

    def get_untagged_items(self):
        """Returns all untagged physical stock items (is_tagged=0, real tag_id exists)."""
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id, it_code, it_name, tag_id, gr_wt, nt_wt, touch, huid, entry_date
                    FROM stock_inventory
                    WHERE is_tagged = 0
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                    ORDER BY id DESC
                """).fetchall()]
        except Exception as e:
            print(f"? [UNTAGGED ITEMS ERROR] {e}");
            return []

    def get_touch_ledger_details(self, touch_value, mode='weight', from_date='', to_date=''):
        """
        Returns IN and OUT transactions for a given touch value (or ALL).
        Pass touch_value='ALL' for master table aggregation.
        Pass a specific float (e.g. 76.0) for drilldown.
        """
        try:
            all_touch = (str(touch_value).strip().upper() == 'ALL')
            if not all_touch:
                touch_val = float(touch_value)

            with self._get_connection() as conn:
                results = []

                # -- OPENING ------------------------------------------------
                # Weight mode → KATTI-% rows (bulk weight, no piece tag)
                # Pcs mode    → tagged items (Ring, Chain etc, is_tagged=1)
                # Opening stock = OPENING-% rows only (entered via opening_stock.html)
                # These are permanent carry-forward values — never change
                opn_sql = """
                    SELECT gr_wt, nt_wt, ls_wt, touch, huid, pcs,
                           it_code, it_name, tag_id,
                           entry_date AS vch_dt
                    FROM stock_inventory
                    WHERE tag_id LIKE 'OPENING-%'
                      AND touch IS NOT NULL AND touch > 0
                      AND gr_wt > 0
                """

                # OPENING is PERMANENT — never subtract sales from it
                # Sales reduce INWARD stock, not the opening balance
                opn_rows = conn.execute(opn_sql).fetchall()
                opn_by_touch = {}
                for r in opn_rows:
                    r = dict(r)
                    tv = round(float(r.get('touch') or 0), 2)
                    if not all_touch and round(tv, 2) != round(touch_val, 2):
                        continue
                    opn_by_touch.setdefault(tv, {'wt': 0.0, 'nt': 0.0, 'pcs': 0, 'r': r})
                    opn_by_touch[tv]['wt'] += float(r.get('gr_wt') or 0)
                    opn_by_touch[tv]['nt'] += float(r.get('nt_wt') or r.get('gr_wt') or 0)
                    opn_by_touch[tv]['pcs'] += int(r.get('pcs') or 0)

                for tv, opn in opn_by_touch.items():
                    r = opn['r']
                    results.append({
                        'txn_type': 'OPENING',
                        'touch_val': tv,
                        'book_name': 'Opening Stock',
                        'vch_no': r.get('tag_id', ''),
                        'vch_dt': r.get('vch_dt', ''),
                        'ac_name': 'Opening Balance',
                        'as_type': 'Opening A/c',
                        'sign': 'Dr',
                        'tag_no': r.get('tag_id', ''),
                        'huid': r.get('huid') or '--',
                        'it_code': r.get('it_code', ''),
                        'gr_name': 'Opening',
                        'primary_val': '',
                        'it_name': r.get('it_name', ''),
                        'variety': 'Opening',
                        'carat': '',
                        'pcs': opn['pcs'],
                        'gr_wt': round(opn['wt'], 3),
                        'ls_wt': float(r.get('ls_wt') or 0),
                        'in_net_wt': round(opn['nt'], 3),
                        'out_net_wt': 0.0,
                        'bal_wt': round(opn['nt'], 3),
                    })

                # -- INWARD: weight mode ------------------------------------
                # Source 1: katti_voucher_items (katti terminal entries)
                # Source 2: stock_inventory NULL/N/A rows (stock ledger entries)
                # OPENING- rows handled above -- never included here
                if mode == 'weight':
                    # Source 1 -- katti_voucher_items
                    inward = conn.execute("""
                        SELECT kvi.it_code, kvi.it_name, kvi.nt_wt AS gr_wt,
                               kvi.touch, kvi.huid, kvi.pcs,
                               kv.vch_id, kv.date AS vch_dt
                        FROM katti_voucher_items kvi
                        JOIN katti_vouchers kv ON kvi.vch_id = kv.vch_id
                    """).fetchall()

                    for r in inward:
                        r = dict(r)
                        tv = float(r.get('touch') or 0)
                        if not all_touch and round(tv, 2) != round(touch_val, 2): continue
                        vdt = r.get('vch_dt', '')
                        if from_date and vdt < from_date: continue
                        if to_date and vdt > to_date:   continue
                        wt = float(r.get('gr_wt') or 0)
                        results.append({
                            'txn_type': 'IN', 'touch_val': tv,
                            'book_name': 'Katti Inward', 'vch_no': r.get('vch_id', ''),
                            'vch_dt': vdt, 'ac_name': 'Stock Inward', 'as_type': 'Katti A/c',
                            'sign': 'Dr', 'tag_no': 'N/A', 'huid': r.get('huid') or '--',
                            'it_code': r.get('it_code', ''), 'gr_name': 'Weight Gold',
                            'primary_val': r.get('vch_id', ''), 'it_name': r.get('it_name', ''),
                            'variety': 'Katti',
                            'carat': '22K' if tv >= 91 else ('18K' if tv >= 75 else 'Katti'),
                            'pcs': int(r.get('pcs') or 1), 'gr_wt': wt, 'ls_wt': 0.0,
                            'in_net_wt': wt, 'out_net_wt': 0.0, 'bal_wt': wt,
                        })

                    # Source 2 -- stock_inventory NULL/N/A rows (stock ledger weight entries)
                    # These are weight-based entries NOT from katti terminal
                    # EXCLUDE OPENING- (those go to Opening column)
                    # EXCLUDE KATTI- (those are duplicates of katti_voucher_items above)
                    weight_stock = conn.execute("""
                        SELECT id, it_code, it_name, tag_id,
                               gr_wt, ls_wt, nt_wt, touch, huid, pcs,
                               entry_date AS vch_dt
                        FROM stock_inventory
                        WHERE (
                            tag_id IS NULL OR
                            tag_id = '' OR
                            tag_id = 'N/A' OR
                            tag_id = '---' OR
                            tag_id = '-'
                        )
                        AND tag_id NOT LIKE 'KATTI-%'
                        AND tag_id NOT LIKE 'OPENING-%'
                        AND gr_wt > 0
                    """).fetchall()

                    for r in weight_stock:
                        r = dict(r)
                        tv = float(r.get('touch') or 0)
                        if not all_touch and round(tv, 2) != round(touch_val, 2): continue
                        vdt = r.get('vch_dt', '')
                        if from_date and vdt < from_date: continue
                        if to_date and vdt > to_date:   continue
                        wt = float(r.get('gr_wt') or 0)
                        nt = float(r.get('nt_wt') or wt)
                        results.append({
                            'txn_type': 'IN', 'touch_val': tv,
                            'book_name': 'Stock Ledger', 'vch_no': str(r.get('id', '')),
                            'vch_dt': vdt, 'ac_name': 'Stock Inward', 'as_type': 'Weight Stock',
                            'sign': 'Dr', 'tag_no': r.get('tag_id') or 'N/A',
                            'huid': r.get('huid') or '--',
                            'it_code': r.get('it_code', ''), 'gr_name': 'Weight Gold',
                            'primary_val': '', 'it_name': r.get('it_name', ''),
                            'variety': 'Weight',
                            'carat': '22K' if tv >= 91 else ('18K' if tv >= 75 else 'Weight'),
                            'pcs': int(r.get('pcs') or 1), 'gr_wt': wt, 'ls_wt': float(r.get('ls_wt') or 0),
                            'in_net_wt': nt, 'out_net_wt': 0.0, 'bal_wt': nt,
                        })

                # -- OPENING: pcs mode --------------------------------------
                if mode == 'pcs':
                    opn_pcs_rows = conn.execute("""
                        SELECT id, it_code, it_name, tag_id,
                               gr_wt, ls_wt, nt_wt, touch, huid, pcs,
                               entry_date AS vch_dt
                        FROM stock_inventory
                        WHERE touch IS NOT NULL AND touch > 0
                          AND pcs > 0
                          AND (tag_id LIKE 'OPENING-%' OR tag_id LIKE 'KATTI-%')
                    """).fetchall()

                    for r in opn_pcs_rows:
                        r = dict(r)
                        tv = float(r.get('touch') or 0)
                        pcs = int(r.get('pcs') or 0)
                        if not all_touch and round(tv, 2) != round(touch_val, 2): continue
                        if pcs <= 0: continue  # extra safety check
                        wt = float(r.get('gr_wt') or 0)
                        nt = float(r.get('nt_wt') or wt)
                        results.append({
                            'txn_type': 'OPENING', 'touch_val': tv,
                            'book_name': 'Opening Stock',
                            'vch_no': r.get('tag_id', ''), 'vch_dt': r.get('vch_dt', ''),
                            'ac_name': 'Opening Balance', 'as_type': 'Opening A/c',
                            'sign': 'Dr', 'tag_no': r.get('tag_id', ''),
                            'huid': r.get('huid') or '--',
                            'it_code': r.get('it_code', ''), 'gr_name': 'Ornaments',
                            'primary_val': '', 'it_name': r.get('it_name', ''),
                            'variety': 'Opening', 'carat': '',
                            'pcs': pcs,
                            'gr_wt': wt, 'ls_wt': float(r.get('ls_wt') or 0),
                            'in_net_wt': nt, 'out_net_wt': 0.0, 'bal_wt': nt,
                        })

                if mode == 'pcs':
                    # ? FIX: PCS Inward = current stock + sold items (reconstructed from sales_history)
                    # Sold items are DELETED from stock_inventory after sale, so we rebuild from sales

                    # Step 1 -- Current remaining stock (not yet sold)
                    # EXCLUDE: KATTI- (counted in katti_voucher_items as Inward)
                    # EXCLUDE: OPENING- (counted in get_opening_stock as Opening)
                    # EXCLUDE: NULL/N/A/--- (weight-based, not PCS goods)
                    current_stock = conn.execute("""
                        SELECT it_code, it_name, tag_id, gr_wt, nt_wt, ls_wt,
                               touch, huid, pcs, entry_date AS vch_dt, vch_reference
                        FROM stock_inventory
                        WHERE tag_id IS NOT NULL
                          AND tag_id NOT IN ('N/A','','---','-')
                          AND tag_id NOT LIKE 'KATTI-%'
                          AND tag_id NOT LIKE 'OPENING-%'
                    """).fetchall()

                    for r in current_stock:
                        r = dict(r)
                        tv = float(r.get('touch') or 0)
                        if not all_touch and round(tv, 2) != round(touch_val, 2): continue
                        vdt = r.get('vch_dt', '')
                        if from_date and vdt < from_date: continue
                        if to_date and vdt > to_date:   continue
                        nt = float(r.get('nt_wt') or 0)
                        results.append({
                            'txn_type': 'IN', 'touch_val': tv,
                            'book_name': 'Stock Entry', 'vch_no': r.get('tag_id', ''),
                            'vch_dt': vdt, 'ac_name': 'Stock Inward', 'as_type': 'Stock A/c',
                            'sign': 'Dr', 'tag_no': r.get('tag_id', ''), 'huid': r.get('huid') or '--',
                            'it_code': r.get('it_code', ''), 'gr_name': 'Ornaments',
                            'primary_val': r.get('vch_reference', ''), 'it_name': r.get('it_name', ''),
                            'variety': 'Standard',
                            'carat': '22K' if tv >= 91 else ('18K' if tv >= 75 else 'Other'),
                            'pcs': int(r.get('pcs') or 1), 'gr_wt': float(r.get('gr_wt') or 0),
                            'ls_wt': float(r.get('ls_wt') or 0),
                            'in_net_wt': nt, 'out_net_wt': 0.0, 'bal_wt': nt,
                        })

                    # Step 2 -- Already sold tagged pieces (deleted from stock after sale)
                    # Reconstruct from sales_history items where tag_id is a real physical tag
                    sold_sales = conn.execute(
                        "SELECT vch_id, customer, date, items FROM sales_history ORDER BY id ASC"
                    ).fetchall()

                    seen_tags = set()  # avoid duplicate inward entries
                    for sale in sold_sales:
                        svdt = sale['date'] or ''
                        try:
                            sitems = json.loads(sale['items'] or '[]')
                        except:
                            continue
                        for item in sitems:
                            tag_id = str(item.get('tag_id') or '').strip()
                            # Only real physical tags (not weight/katti)
                            if not tag_id or tag_id in ('', 'N/A') or tag_id.startswith('KATTI-') or len(tag_id) < 8:
                                continue
                            if tag_id in seen_tags: continue
                            seen_tags.add(tag_id)

                            tv = float(item.get('touch') or 0)
                            if not all_touch and round(tv, 2) != round(touch_val, 2): continue
                            if from_date and svdt < from_date: continue
                            if to_date and svdt > to_date:   continue

                            wt = float(item.get('weight') or item.get('gr_wt') or 0)
                            ls = float(item.get('less') or item.get('ls_wt') or 0)
                            results.append({
                                'txn_type': 'IN', 'touch_val': tv,
                                'book_name': 'Stock Entry (Sold)', 'vch_no': tag_id,
                                'vch_dt': svdt, 'ac_name': 'Stock Inward', 'as_type': 'Stock A/c',
                                'sign': 'Dr', 'tag_no': tag_id, 'huid': item.get('huid') or '--',
                                'it_code': item.get('it_code') or item.get('code') or '--',
                                'gr_name': 'Ornaments',
                                'primary_val': sale['vch_id'],
                                'it_name': item.get('it_name') or item.get('code') or '--',
                                'variety': 'Standard',
                                'carat': '22K' if tv >= 91 else ('18K' if tv >= 75 else 'Other'),
                                'pcs': 1, 'gr_wt': wt, 'ls_wt': ls,
                                'in_net_wt': wt, 'out_net_wt': 0.0, 'bal_wt': wt,
                            })

                # -- OUTWARD: sales_history ----------------------------------
                sales = conn.execute(
                    "SELECT vch_id, customer, date, items FROM sales_history ORDER BY id ASC"
                ).fetchall()

                for sale in sales:
                    vdt = sale['date'] or ''
                    if from_date and vdt < from_date: continue
                    if to_date and vdt > to_date:   continue
                    try:
                        items = json.loads(sale['items'] or '[]')
                    except:
                        continue

                    for item in items:
                        tv = float(item.get('touch') or 0)
                        if not all_touch and round(tv, 2) != round(touch_val, 2): continue

                        tag_id = str(item.get('tag_id') or '').strip()
                        # Weight item if: no tag, N/A, KATTI- prefix, B-NNN bin format, or not a real SKU tag
                        is_weight = (
                                not tag_id
                                or tag_id in ('', 'N/A', '---', 'undefined', '-')
                                or tag_id.startswith('KATTI-')
                                or tag_id.startswith('B-')
                                or tag_id.startswith('b-')
                                or (len(tag_id) < 8 and not any(c.isalpha() and c.isupper() for c in tag_id[2:]))
                        )
                        if mode == 'weight' and not is_weight: continue
                        if mode == 'pcs' and is_weight:     continue

                        wt = float(item.get('weight') or item.get('gr_wt') or 0)
                        results.append({
                            'txn_type': 'OUT', 'touch_val': tv,
                            'book_name': 'Sales Bill', 'vch_no': sale['vch_id'],
                            'vch_dt': vdt, 'ac_name': sale['customer'], 'as_type': 'Sales A/c',
                            'sign': 'Cr', 'tag_no': tag_id or 'N/A', 'huid': item.get('huid') or '--',
                            'it_code': item.get('it_code') or item.get('code') or '--',
                            'gr_name': 'Weight Gold' if is_weight else 'Ornaments',
                            'primary_val': sale['vch_id'],
                            'it_name': item.get('it_name') or item.get('code') or '--',
                            'variety': 'Bulk' if is_weight else 'Standard',
                            'carat': '22K' if tv >= 91 else ('18K' if tv >= 75 else 'Katti'),
                            'pcs': int(item.get('pcs') or 1), 'gr_wt': wt,
                            'ls_wt': float(item.get('less') or 0),
                            'in_net_wt': 0.0, 'out_net_wt': wt, 'bal_wt': wt,
                        })

                return results

        except Exception as e:
            print(f"? [TOUCH LEDGER DETAILS ERROR] {e}");
            return []

    def get_low_stock_items(self, threshold=10.0):
        """
        Returns weight-based IT codes where 0 < gr_wt <= threshold.
        Items at exactly 0 are handled by get_out_of_stock_items.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT it_code, it_name,
                           COALESCE(SUM(gr_wt), 0) as remaining
                    FROM stock_inventory
                    WHERE it_code IS NOT NULL
                      AND TRIM(it_code) != ''
                      AND gr_wt > 0
                      AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A'
                           OR tag_id LIKE 'KATTI-%')
                    GROUP BY it_code
                    HAVING remaining > 0 AND remaining <= ?
                    ORDER BY remaining ASC
                """, (float(threshold),)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"? [LOW STOCK ERROR] {e}");
            return []

    def get_low_stock_uchak_items(self, threshold=5):
        """
        Returns uchak IT codes where total pcs <= threshold.
        These are piece-based items from uchak_inward_items.
        """
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT it_code, it_name,
                           COALESCE(SUM(pcs), 0) as remaining_pcs
                    FROM uchak_inward_items
                    WHERE it_code IS NOT NULL AND TRIM(it_code) != ''
                    GROUP BY it_code
                    HAVING remaining_pcs > 0 AND remaining_pcs <= ?
                    ORDER BY remaining_pcs ASC
                """, (int(threshold),)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[UCHAK LOW STOCK ERROR] {e}");
            return []

    def get_out_of_stock_items(self):
        """
        Returns ALL IT codes that have ever had stock (via any entry method)
        but now have gr_wt = 0 in stock_inventory.
        Checks both stock_inventory current rows AND sales_history for ever-sold items.
        """
        try:
            with self._get_connection() as conn:
                # Step 1: all IT codes ever sold (from sales history items JSON)
                sold_rows = conn.execute(
                    "SELECT items FROM sales_history WHERE items IS NOT NULL"
                ).fetchall()
                ever_sold = set()
                import json as _json
                for r in sold_rows:
                    try:
                        for it in _json.loads(r['items'] or '[]'):
                            code = str(it.get('it_code') or it.get('code') or '').strip()
                            if code:
                                ever_sold.add(code)
                    except:
                        pass

                # Step 2: all IT codes ever in katti vouchers
                katti_rows = conn.execute(
                    "SELECT DISTINCT it_code FROM katti_voucher_items WHERE it_code IS NOT NULL AND TRIM(it_code) != ''"
                ).fetchall()
                ever_katti = {r['it_code'] for r in katti_rows}

                # Step 3: all IT codes currently in stock_inventory (any amount)
                inv_rows = conn.execute(
                    "SELECT it_code, it_name, SUM(gr_wt) as total_wt FROM stock_inventory WHERE it_code IS NOT NULL AND TRIM(it_code) != '' GROUP BY it_code"
                ).fetchall()
                current_stock = {}
                for r in inv_rows:
                    current_stock[r['it_code']] = {
                        'it_name': r['it_name'] or '',
                        'gr_wt': float(r['total_wt'] or 0)
                    }

                # OUT OF STOCK = ever sold OR ever in katti, but now gr_wt <= 0.001
                all_known = ever_sold | ever_katti
                out_of_stock = []
                for code in sorted(all_known):
                    info = current_stock.get(code, {})
                    remaining = info.get('gr_wt', 0.0)
                    if remaining <= 0.001:
                        out_of_stock.append({
                            'it_code': code,
                            'it_name': info.get('it_name', ''),
                            'original_wt': 0.0,
                            'remaining': 0.0
                        })

                return out_of_stock
        except Exception as e:
            print(f"? [OUT OF STOCK ERROR] {e}");
            return []

    def get_lock_status(self):
        """
        Returns lock status + lock code.
        CRITICAL: lock_code is always from cached fingerprint.
        Also persists lock_code to DB so unlock keygen can use it.
        """
        try:
            with self._get_connection() as conn:
                rows = {r['key']: r['value'] for r in conn.execute(
                    "SELECT key,value FROM app_config WHERE key IN "
                    "('account_locked','locked_at','login_attempts','lock_code_cache')"
                ).fetchall()}

            locked = rows.get('account_locked', '0') == '1'
            attempts = int(rows.get('login_attempts', '0'))

            # Always use cached fingerprint — never re-run WMIC here
            lock_code = self._machine_fingerprint()[:8].upper()

            # Persist lock_code to DB so aurum_health.py can read it
            if locked and not rows.get('lock_code_cache'):
                try:
                    with self._get_connection() as conn2:
                        conn2.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) "
                            "VALUES('lock_code_cache',?)", (lock_code,)
                        )
                        conn2.commit()
                except Exception:
                    pass

            _dblog(f"[LOCK] status: locked={locked} attempts={attempts} lock_code={lock_code}")
            return {
                'locked': locked,
                'attempts': attempts,
                'lock_code': lock_code,
                'locked_at': rows.get('locked_at', '')
            }
        except Exception as e:
            _dberr(f"[LOCK] get_lock_status: {e}")
            return {'locked': False, 'attempts': 0, 'lock_code': '', 'locked_at': ''}

    def record_failed_attempt(self):
        """Increment login_attempts. Lock account permanently after 3 failures."""
        try:
            from datetime import datetime as _dt
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM app_config WHERE key='login_attempts'"
                ).fetchone()
                attempts = int(row['value']) + 1 if row else 1
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('login_attempts',?)",
                    (str(attempts),)
                )
                if attempts >= 3:
                    conn.execute(
                        "INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','1')"
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO app_config(key,value) VALUES('locked_at',?)",
                        (_dt.now().strftime('%Y-%m-%d %H:%M:%S'),)
                    )
                    _dblog(f"[LOCK] Account LOCKED after {attempts} attempts")
                conn.commit()
            return {'attempts': attempts, 'locked': attempts >= 3}
        except Exception as e:
            _dberr(f"[LOCK] record_failed_attempt: {e}")
            return {'attempts': 0, 'locked': False}

    # ── One-time unlock nonce helpers ──────────────────────────────
    def _nonce_path(self):
        """Path to the one-time-use nonce file shared with generate_unlock_keys.py."""
        return os.path.join(self.db_dir, '.unlock_nonce')

    def _get_unlock_nonce(self):
        """Read the current one-time-use nonce. Returns 0 if missing."""
        try:
            p = self._nonce_path()
            if os.path.isfile(p):
                with open(p, 'r') as f:
                    return int(f.read().strip())
        except (ValueError, IOError, OSError):
            pass
        return 0

    def _bump_unlock_nonce(self):
        """Increment the nonce after a successful one-time unlock."""
        try:
            n = self._get_unlock_nonce() + 1
            with open(self._nonce_path(), 'w') as f:
                f.write(str(n))
            _dblog(f'[NONCE] incremented to {n}')
        except Exception as e:
            _dberr(f'[NONCE] bump error: {e}')

    def _verify_nonce_key(self, lock_code, salt, date_str, entered, nonce):
        """Check one key candidate against the given nonce. Returns True on match."""
        expected = hashlib.sha256(
            (lock_code + salt + date_str + str(nonce)).encode('utf-8')
        ).hexdigest()[:len(entered)].upper()
        return entered == expected

    def verify_unlock_key(self, unlock_key, lock_code=None):
        import hashlib as _hl
        from datetime import datetime as _dt, timedelta as _td
        SALT = 'AurumOS@Jewel#2024$Prof'
        try:
            real_lc = self._machine_fingerprint()[:8].upper()
            entered = str(unlock_key or '').strip().upper()[:12]
            if len(entered) < 6:
                return {'status': 'error', 'message': 'Key too short'}

            # Build lock code variants
            lc_list = [real_lc]
            if lock_code:
                lc_list.append(str(lock_code).strip().upper()[:8])
            try:
                with self._get_connection() as _c:
                    _r = _c.execute(
                        "SELECT value FROM app_config WHERE key='lock_code_cache'"
                    ).fetchone()
                    if _r and _r['value']:
                        lc_list.append(str(_r['value']).strip().upper()[:8])
            except Exception:
                pass
            lc_list = list(dict.fromkeys(lc_list))

            # Build date variants — IST, UTC, local, ±3 days (matches keygen range)
            now_utc = _dt.utcnow()
            now_ist = now_utc + _td(hours=5, minutes=30)
            now_loc = _dt.now()
            dates = []
            for base in [now_ist, now_utc, now_loc]:
                for delta in range(-3, 4):
                    ds = (base + _td(days=delta)).strftime('%Y-%m-%d')
                    if ds not in dates:
                        dates.append(ds)

            # Try stored nonce + nonce=0 (keygen default)
            stored_nonce = self._get_unlock_nonce()
            nonces = list(dict.fromkeys([stored_nonce, 0]))

            _dblog(f'[LOCK] verify entered={entered[:4]}**** lc={lc_list} dates={dates[:7]} nonces={nonces}')

            # Try every combination
            for nonce in nonces:
              for lc in lc_list:
                for date_str in dates:
                    expected = _hl.sha256(
                        (lc + SALT + date_str + str(nonce)).encode('utf-8')
                    ).hexdigest()[:12].upper()
                    if entered == expected:
                        # One-time use: increment nonce so old key stops working
                        if nonce == stored_nonce:
                            self._bump_unlock_nonce()
                        try:
                            with self._get_connection() as conn:
                                for k, v in [
                                    ('account_locked', '0'),
                                    ('login_attempts', '0'),
                                    ('locked_at', ''),
                                    ('lock_code_cache', ''),
                                ]:
                                    conn.execute(
                                        'INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',
                                        (k, v)
                                    )
                                conn.commit()
                        except Exception as _e:
                            _dblog(f'[LOCK] clear error: {_e}')
                        _dblog(f'[LOCK] UNLOCKED lc={lc} date={date_str} nonce={nonce}')
                        return {'status': 'success', 'message': 'Account unlocked'}

            _dblog('[LOCK] No match — all combinations tried')
            return {'status': 'error', 'message': 'Invalid unlock key. Verify the code and try again.'}

        except Exception as e:
            _dberr(f'[LOCK] verify_unlock_key: {e}')
            return {'status': 'error', 'message': str(e)}

    # ══════════════════════════════════════════════════════════
    # TAG AUDIT MODULE
    # ══════════════════════════════════════════════════════════

    def tag_audit_init_tables(self):
        """Create tag audit tables if not exist."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tag_audit_sessions (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_ref TEXT UNIQUE,
                        started_at  TEXT DEFAULT (datetime('now')),
                        ended_at    TEXT,
                        started_by  TEXT,
                        touch_filter TEXT DEFAULT 'ALL',
                        status      TEXT DEFAULT 'active',
                        total_book  INTEGER DEFAULT 0,
                        total_scanned INTEGER DEFAULT 0,
                        total_found INTEGER DEFAULT 0,
                        total_missing INTEGER DEFAULT 0,
                        total_extra INTEGER DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tag_audit_scans (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  INTEGER,
                        tag_id      TEXT,
                        scanned_at  TEXT DEFAULT (datetime('now')),
                        status      TEXT,
                        it_name     TEXT,
                        touch       REAL,
                        gr_wt       REAL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tag_audit_absences (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  INTEGER,
                        tag_id      TEXT,
                        reason      TEXT,
                        marked_by   TEXT,
                        marked_at   TEXT DEFAULT (datetime('now'))
                    )
                """)
                conn.commit()
                _dblog("[TAG_AUDIT] Tables ready")
                return True
        except Exception as e:
            _dberr(f"[TAG_AUDIT] init_tables: {e}")
            return False

    def tag_audit_start(self, started_by='Admin', touch_filter='ALL'):
        """
        Start new tag audit session.

        PERMANENT LOGIC:
        - A fresh audit always starts with ALL booked pieces as MISSING.
          (An audit means physical re-verification — is_tagged alone
           does not prove the piece is still there right now.)
        - EXCEPTION: any tag_id that was marked FOUND in the most
          recently CLOSED audit session is carried forward as
          pre-verified FOUND, since it was physically confirmed last time.
        - Any tag_id added to stock AFTER that last closed audit has
          never been scanned in any audit -> starts as MISSING.
        - Any tag_id that was MISSING in the last closed audit stays
          MISSING (still unverified).
        """
        import datetime as _datetime
        try:
            self.tag_audit_init_tables()
            with self._get_connection() as conn:
                q = """
                    SELECT id, tag_id, it_code, it_name, touch, gr_wt, nt_wt, huid, pcs,
                           COALESCE(is_tagged,0) as is_tagged
                    FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id != ''
                      AND tag_id != 'N/A'
                      AND tag_id NOT LIKE 'OPENING-%'
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND touch IS NOT NULL AND touch > 0
                """
                params = []
                if touch_filter and touch_filter != 'ALL':
                    q += " AND ROUND(touch,2) = ?"
                    params.append(round(float(touch_filter), 2))
                q += " ORDER BY touch, tag_id"
                rows = conn.execute(q, params).fetchall()

                total_book = len(rows)
                ref = 'TA-' + _datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

                conn.execute("""
                    INSERT INTO tag_audit_sessions
                    (session_ref, started_by, touch_filter, status, total_book)
                    VALUES (?,?,?,?,?)
                """, (ref, started_by, touch_filter, 'active', total_book))
                conn.commit()

                session_id = conn.execute(
                    "SELECT id FROM tag_audit_sessions WHERE session_ref=?", (ref,)
                ).fetchone()['id']

                # Find the most recently CLOSED session (any touch filter)
                last_closed = conn.execute("""
                    SELECT id FROM tag_audit_sessions
                    WHERE status='closed' AND id != ?
                    ORDER BY ended_at DESC LIMIT 1
                """, (session_id,)).fetchone()

                carried_found = set()
                if last_closed:
                    prev_found = conn.execute("""
                        SELECT DISTINCT tag_id FROM tag_audit_scans
                        WHERE session_id=? AND status='found'
                    """, (last_closed['id'],)).fetchall()
                    carried_found = {str(r['tag_id']).strip().upper() for r in prev_found}
                    _dblog(f"[TAG_AUDIT] Carrying forward {len(carried_found)} "
                           f"found tags from session {last_closed['id']}")

                # Build snapshot + carry forward only previously-FOUND tags
                snapshot = []
                touches = {}
                auto_found = 0
                for r in rows:
                    tv = round(float(r['touch'] or 0), 2)
                    tid = str(r['tag_id']).strip().upper()

                    snapshot.append({
                        'tag_id': tid,
                        'it_name': r['it_name'] or r['it_code'] or 'Unknown',
                        'touch': tv,
                        'gr_wt': float(r['gr_wt'] or 0),
                        'nt_wt': float(r['nt_wt'] or 0),
                        'huid': r['huid'] or '',
                        'is_tagged': int(r['is_tagged'] or 0),
                    })
                    if tv not in touches:
                        touches[tv] = 0
                    touches[tv] += 1

                    # Carry forward ONLY if this exact tag was Found last audit
                    if tid in carried_found:
                        conn.execute("""
                            INSERT INTO tag_audit_scans
                            (session_id, tag_id, status, it_name, touch, gr_wt)
                            VALUES (?,?,?,?,?,?)
                        """, (
                            session_id, tid, 'found',
                            r['it_name'] or r['it_code'] or 'Unknown',
                            tv, float(r['gr_wt'] or 0)
                        ))
                        auto_found += 1

                conn.commit()

                _dblog(f"[TAG_AUDIT] Session {ref} started — {total_book} tags, "
                       f"{auto_found} carried forward as found, filter={touch_filter}")
                return {
                    'status': 'success',
                    'session_id': session_id,
                    'session_ref': ref,
                    'total_book': total_book,
                    'snapshot': snapshot,
                    'touches': {str(k): v for k, v in sorted(touches.items())},
                    'auto_found': auto_found,
                }
        except Exception as e:
            _dberr(f"[TAG_AUDIT] start: {e}")
            return {'status': 'error', 'message': str(e)}

    def tag_audit_start_snapshot(self, session_id, touch_filter='ALL'):
        """
        Get snapshot for an existing session — used when resuming.
        Read-only — does NOT create a new session.
        Includes is_tagged so the UI can reflect already-tagged pieces.
        """
        try:
            q = """
                SELECT tag_id, it_code, it_name, touch, gr_wt, nt_wt, huid,
                       COALESCE(is_tagged,0) as is_tagged
                FROM stock_inventory
                WHERE tag_id IS NOT NULL AND tag_id != ''
                  AND tag_id != 'N/A'
                  AND tag_id NOT LIKE 'OPENING-%'
                  AND tag_id NOT LIKE 'KATTI-%'
                  AND touch IS NOT NULL AND touch > 0
            """
            params = []
            if touch_filter and touch_filter != 'ALL':
                q += " AND ROUND(touch,2) = ?"
                params.append(round(float(touch_filter), 2))
            q += " ORDER BY touch, tag_id"
            with self._get_connection() as conn:
                rows = conn.execute(q, params).fetchall()
            snapshot = []
            for r in rows:
                snapshot.append({
                    'tag_id': str(r['tag_id']).strip().upper(),
                    'it_name': r['it_name'] or r['it_code'] or 'Unknown',
                    'touch': round(float(r['touch'] or 0), 2),
                    'gr_wt': float(r['gr_wt'] or 0),
                    'nt_wt': float(r['nt_wt'] or 0),
                    'huid': r['huid'] or '',
                    'is_tagged': int(r['is_tagged'] or 0),
                })
            return {'status': 'success', 'snapshot': snapshot}
        except Exception as e:
            _dberr(f"[TAG_AUDIT] start_snapshot: {e}")
            return {'status': 'error', 'message': str(e), 'snapshot': []}

    def tag_audit_scan(self, session_id, tag_id, book_tags=None):
        """
        Process a single scan.
        book_tags param kept for API compat but ignored — DB lookup used instead.
        """
        tag_id = str(tag_id or '').strip().upper()
        if not tag_id:
            return {'status': 'error', 'message': 'Empty tag'}
        try:
            with self._get_connection() as conn:
                # Check already scanned
                existing = conn.execute(
                    "SELECT id, status FROM tag_audit_scans WHERE session_id=? AND tag_id=?",
                    (session_id, tag_id)
                ).fetchone()
                if existing:
                    return {'status': 'duplicate', 'tag_id': tag_id,
                            'message': f'{tag_id} already scanned'}

                # Get session info to know touch filter
                sess = conn.execute(
                    "SELECT touch_filter FROM tag_audit_sessions WHERE id=?",
                    (session_id,)
                ).fetchone()
                tf = sess['touch_filter'] if sess else 'ALL'

                # Check if tag is in book (stock_inventory as a real tagged piece)
                q = """SELECT tag_id, it_name, touch, gr_wt, nt_wt, huid
                       FROM stock_inventory
                       WHERE tag_id=?
                         AND tag_id NOT LIKE 'OPENING-%'
                         AND tag_id NOT LIKE 'KATTI-%'"""
                params = [tag_id]
                if tf and tf != 'ALL':
                    q += " AND ROUND(touch,2)=?"
                    params.append(round(float(tf), 2))

                book_row = conn.execute(q, params).fetchone()

                if book_row:
                    scan_status = 'found'
                    tag_info = {
                        'it_name': book_row['it_name'] or '',
                        'touch': float(book_row['touch'] or 0),
                        'gr_wt': float(book_row['gr_wt'] or 0),
                        'nt_wt': float(book_row['nt_wt'] or 0),
                        'huid': book_row['huid'] or '',
                    }
                else:
                    # Not in book — check if in inventory at all
                    any_row = conn.execute(
                        "SELECT it_name, touch, gr_wt FROM stock_inventory WHERE tag_id=?",
                        (tag_id,)
                    ).fetchone()
                    scan_status = 'extra'
                    tag_info = {
                        'it_name': any_row['it_name'] if any_row else 'Unknown',
                        'touch': float(any_row['touch'] or 0) if any_row else 0,
                        'gr_wt': float(any_row['gr_wt'] or 0) if any_row else 0,
                    } if any_row else {}

                conn.execute("""
                    INSERT INTO tag_audit_scans
                    (session_id, tag_id, status, it_name, touch, gr_wt)
                    VALUES (?,?,?,?,?,?)
                """, (
                    session_id, tag_id, scan_status,
                    tag_info.get('it_name', ''),
                    tag_info.get('touch', 0),
                    tag_info.get('gr_wt', 0),
                ))
                conn.commit()

                return {
                    'status': scan_status,
                    'tag_id': tag_id,
                    'tag_info': tag_info,
                    'message': f'{tag_id} — {scan_status.upper()}',
                }
        except Exception as e:
            _dberr(f"[TAG_AUDIT] scan: {e}")
            return {'status': 'error', 'message': str(e)}

    def tag_audit_get_status(self, session_id):
        """Get current audit progress."""
        try:
            with self._get_connection() as conn:
                sess = conn.execute(
                    "SELECT * FROM tag_audit_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if not sess:
                    return {'status': 'error', 'message': 'Session not found'}

                scans = conn.execute(
                    "SELECT * FROM tag_audit_scans WHERE session_id=? ORDER BY scanned_at DESC",
                    (session_id,)
                ).fetchall()
                absences = conn.execute(
                    "SELECT tag_id FROM tag_audit_absences WHERE session_id=?",
                    (session_id,)
                ).fetchall()
                absent_ids = {r['tag_id'] for r in absences}

                found_ids = {s['tag_id'] for s in scans if s['status'] == 'found'}
                extra_ids = {s['tag_id'] for s in scans if s['status'] == 'extra'}

                return {
                    'status': 'success',
                    'session_id': session_id,
                    'session_ref': sess['session_ref'],
                    'touch_filter': sess['touch_filter'] if sess['touch_filter'] else 'ALL',
                    'total_book': sess['total_book'],
                    'total_scanned': len(scans),
                    'total_found': len(found_ids),
                    'total_extra': len(extra_ids),
                    'absent_ids': list(absent_ids),
                    'scans': [dict(s) for s in scans],
                }
        except Exception as e:
            _dberr(f"[TAG_AUDIT] get_status: {e}")
            return {'status': 'error', 'message': str(e)}

    def tag_audit_mark_absent(self, session_id, tag_id, reason, marked_by='Admin'):
        """Mark a missing tag as absent (repair/loan etc)."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tag_audit_absences
                    (session_id, tag_id, reason, marked_by)
                    VALUES (?,?,?,?)
                """, (session_id, tag_id, reason, marked_by))
                conn.commit()
                return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def tag_audit_remove_absence(self, session_id, tag_id):
        """Remove absent mark — bring back to missing list."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM tag_audit_absences WHERE session_id=? AND tag_id=?",
                    (session_id, tag_id)
                )
                conn.commit()
                return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def tag_audit_remove_scan(self, session_id, tag_id):
        """Remove a scan (undo accidental scan)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM tag_audit_scans WHERE session_id=? AND tag_id=?",
                    (session_id, tag_id)
                )
                conn.commit()
                return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def tag_audit_close(self, session_id, closed_by='Admin'):
        """Close audit session and save final counts."""
        import datetime as _datetime
        try:
            with self._get_connection() as conn:
                scans = conn.execute(
                    "SELECT status FROM tag_audit_scans WHERE session_id=?",
                    (session_id,)
                ).fetchall()
                found = sum(1 for s in scans if s['status'] == 'found')
                extra = sum(1 for s in scans if s['status'] == 'extra')
                sess = conn.execute(
                    "SELECT total_book FROM tag_audit_sessions WHERE id=?",
                    (session_id,)
                ).fetchone()
                missing = (sess['total_book'] if sess else 0) - found
                conn.execute("""
                    UPDATE tag_audit_sessions SET
                        status='closed', ended_at=?,
                        total_scanned=?, total_found=?,
                        total_missing=?, total_extra=?
                    WHERE id=?
                """, (
                    _datetime.datetime.now().isoformat(),
                    len(scans), found, max(0, missing), extra, session_id
                ))
                conn.commit()
                return {'status': 'success', 'found': found, 'extra': extra, 'missing': max(0, missing)}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def tag_audit_get_sessions(self, limit=20):
        """Get recent audit sessions for history view."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT * FROM tag_audit_sessions
                    ORDER BY started_at DESC LIMIT ?
                """, (limit,)).fetchall()
                return {'status': 'success', 'sessions': [dict(r) for r in rows]}
        except Exception as e:
            return {'status': 'error', 'message': str(e), 'sessions': []}

    def tag_audit_get_available_touches(self):
        """Get all touches that have tagged pieces."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT ROUND(touch,2) as touch, COUNT(*) as pcs,
                           COALESCE(SUM(gr_wt),0) as total_wt
                    FROM stock_inventory
                    WHERE tag_id IS NOT NULL AND tag_id != ''
                      AND tag_id != 'N/A'
                      AND tag_id NOT LIKE 'OPENING-%'
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND touch > 0
                    GROUP BY ROUND(touch,2)
                    ORDER BY touch DESC
                """).fetchall()
                return {'status': 'success', 'touches': [dict(r) for r in rows]}
        except Exception as e:
            return {'status': 'error', 'message': str(e), 'touches': []}

    # ══════════════════════════════════════════════════════════════
    # YEAR-END BALANCE TRANSFER SYSTEM
    # ══════════════════════════════════════════════════════════════

    def tag_audit_delete_session(self, session_id):
        """Permanently delete an audit session and all its scans/absences."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM tag_audit_scans WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM tag_audit_absences WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM tag_audit_sessions WHERE id=?", (session_id,))
                conn.commit()
                return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}


    def get_year_list(self):
        """Return list of financial years — current + all archived."""
        import os as _os
        try:
            with self._get_connection() as conn:
                # Get current year from app_config
                row = conn.execute(
                    "SELECT value FROM app_config WHERE key='financial_year'"
                ).fetchone()
                if row and row['value']:
                    current_year = row['value']
                else:
                    current_year = self._guess_financial_year()
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) VALUES('financial_year',?)",
                            (current_year,)
                        )
                    except Exception:
                        pass

            # Get archived years
            base = _os.path.dirname(self.db_path)
            arch = _os.path.join(base, 'archives')
            years = []
            if _os.path.isdir(arch):
                for d in sorted(_os.listdir(arch), reverse=True):
                    db_file = _os.path.join(arch, d, 'aurum_local.db')
                    if _os.path.isdir(_os.path.join(arch, d)) and _os.path.exists(db_file):
                        years.append({
                            'year': d,
                            'path': db_file,
                            'size_mb': round(_os.path.getsize(db_file) / 1048576, 2),
                            'archived': True,
                        })
            return {
                'status': 'success',
                'current_year': current_year,
                'archived': years,
            }
        except Exception as e:
            _dberr(f"[YEAR] get_year_list: {e}")
            return {'status': 'error', 'message': str(e)}

    def _guess_financial_year(self):
        """Guess current financial year (April-March Indian FY)."""
        from datetime import datetime as _dt
        now = _dt.now()
        if now.month >= 4:
            return f"{now.year}-{str(now.year + 1)[2:]}"
        return f"{now.year - 1}-{str(now.year)[2:]}"

    def get_year_close_preview(self):
        """
        Preview what will be transferred/cleared in year-end close.
        Shows counts before user confirms.
        """
        try:
            with self._get_connection() as conn:
                # Current year
                from datetime import datetime as _dt
                row = conn.execute(
                    "SELECT value FROM app_config WHERE key='financial_year'"
                ).fetchone()
                if row and row['value']:
                    current_year = row['value']
                else:
                    current_year = self._guess_financial_year()
                    # Seed it so it persists
                    conn.execute(
                        "INSERT OR REPLACE INTO app_config(key,value) VALUES('financial_year',?)",
                        (current_year,)
                    )

                # CARRY-FORWARD: full closing balance (all stock per touch)
                # Matches EXACTLY what do_year_close will transfer
                # Tagged pieces + bulk opening + unsold katti = total closing weight
                carry_rows = conn.execute("""
                    SELECT COUNT(DISTINCT ROUND(touch,2)) as n,
                           COALESCE(SUM(gr_wt),0) as wt
                    FROM stock_inventory
                    WHERE touch IS NOT NULL AND touch > 0
                      AND gr_wt > 0
                """).fetchone()

                # Tagged pieces separately (for display)
                tagged_rows = conn.execute("""
                    SELECT COUNT(*) as n, COALESCE(SUM(gr_wt),0) as wt
                    FROM stock_inventory
                    WHERE is_tagged = 1
                      AND gr_wt > 0
                """).fetchone()

                # What will be cleared
                sales_count = conn.execute(
                    "SELECT COUNT(*) as n FROM sales_history"
                ).fetchone()
                katti_count = conn.execute(
                    "SELECT COUNT(*) as n FROM katti_vouchers"
                ).fetchone()
                client_count = conn.execute(
                    "SELECT COUNT(*) as n FROM clients_master"
                ).fetchone()

                stock_count = carry_rows

                # Suggest next year
                parts = current_year.split('-')
                try:
                    y1 = int(parts[0])
                    y2 = int('20' + parts[1]) if len(parts[1]) == 2 else int(parts[1])
                    next_year = f"{y1 + 1}-{str(y2 + 1)[2:]}"
                except Exception:
                    next_year = ''

                return {
                    'status': 'success',
                    'current_year': current_year,
                    'next_year': next_year,
                    'stock_rows': int(stock_count['n']),
                    'stock_wt': round(float(stock_count['wt']), 3),
                    'tagged_rows': int(tagged_rows['n']),
                    'tagged_wt': round(float(tagged_rows['wt']), 3),
                    'sales_count': int(sales_count['n']),
                    'katti_count': int(katti_count['n']),
                    'client_count': int(client_count['n']),
                }
        except Exception as e:
            _dberr(f"[YEAR] preview: {e}")
            return {'status': 'error', 'message': str(e)}

    def do_year_close(self, new_year, closing_note=''):
        """
        Year-end balance transfer:
        1. Archive current DB to archives/<current_year>/
        2. Carry forward live stock as new OPENING rows
        3. Wipe all transactional data
        4. Update financial year in app_config
        """
        import os as _os, shutil as _sh
        from datetime import datetime as _dt
        try:
            with self._get_connection() as conn:
                # Step 0: Get current year
                row = conn.execute(
                    "SELECT value FROM app_config WHERE key='financial_year'"
                ).fetchone()
                current_year = row['value'] if row else self._guess_financial_year()

                _dblog(f"[YEAR] Starting year close: {current_year} -> {new_year}")

                # ── Step 1: Archive current DB ────────────────────────
                base = _os.path.dirname(self.db_path)
                arch_dir = _os.path.join(base, 'archives', current_year)
                _os.makedirs(arch_dir, exist_ok=True)

                # WAL checkpoint before copy
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass

                arch_db = _os.path.join(arch_dir, 'aurum_local.db')
                _sh.copy2(self.db_path, arch_db)
                _dblog(f"[YEAR] Archived to {arch_db}")

                # Also save a metadata file
                import json as _json
                meta = {
                    'year': current_year,
                    'archived_at': _dt.now().isoformat(),
                    'closing_note': closing_note,
                    'new_year': new_year,
                }
                with open(_os.path.join(arch_dir, 'meta.json'), 'w') as mf:
                    _json.dump(meta, mf, indent=2)

                # ── Step 2: Read TAGGED live stock only for carry-forward ─
                # Exclude OPENING- rows (previous year opening)
                # Exclude KATTI- rows (bulk katti stock tracked separately)
                # Only carry forward real tagged pieces in stock
                # Carry forward ONLY real tagged pieces:
                # Exclude OPENING- (untagged bulk opening stock)
                # Exclude KATTI-  (katti/refining stock)
                # Exclude WT-     (weight-based system rows)
                # Only genuine barcode-tagged jewellery pieces transfer
                # ── Carry-forward: FULL closing balance per touch ─────────
                # Enterprise logic:
                #   New year opening = EVERYTHING in stock at year end
                #   = Tagged pieces + Remaining bulk + Unsold katti
                #   = SUM(gr_wt) per touch — no exclusions
                #
                # Example 91.6%:
                #   OPENING-WT (remaining bulk) = 100g
                #   KATTI-2025-001 (unsold)     =  45g
                #   TAG-001 Ring                =  5.32g
                #   TAG-002 Chain               =  8.10g
                #   ─────────────────────────────────────
                #   New year OPENING-WT         = 158.42g  ← full closing balance
                live_stock = conn.execute("""
                    SELECT ROUND(touch,2) as touch,
                           COALESCE(SUM(gr_wt),0) as total_wt,
                           COALESCE(SUM(nt_wt),0) as total_nt,
                           COUNT(*) as pcs
                    FROM stock_inventory
                    WHERE touch IS NOT NULL
                      AND touch > 0
                      AND gr_wt > 0
                    GROUP BY ROUND(touch,2)
                    HAVING COALESCE(SUM(gr_wt),0) > 0
                """).fetchall()
                _dblog(f"[YEAR] Carry-forward full closing balance: {len(live_stock)} touch groups")
                for _r in live_stock:
                    _dblog(f"[YEAR]   touch={_r['touch']}%  closing_wt={float(_r['total_wt']):.3f}g  rows={_r['pcs']}")

                # ── Step 3: Clear ALL transactional tables ────────────
                CLEAR = [
                    'sales_history',
                    'katti_vouchers', 'katti_voucher_items',
                    'credit_ledger',
                    'uchak_inward_vouchers', 'uchak_inward_items',
                    'clients_master',
                    'product_master',
                    'categories',
                    'touch_groups',
                    'stock_inventory',
                    'stock_med_sessions',
                    'tag_audit_sessions', 'tag_audit_scans', 'tag_audit_absences',
                ]
                for tbl in CLEAR:
                    try:
                        conn.execute(f"DELETE FROM {tbl}")
                        conn.execute(
                            f"DELETE FROM sqlite_sequence WHERE name='{tbl}'"
                        )
                    except Exception as _te:
                        _dblog(f"[YEAR] Clear {tbl}: {_te}")

                # ── Step 4: Insert carry-forward OPENING rows ─────────
                today = _dt.now().strftime('%Y-%m-%d')
                inserted = 0
                # Query already has GROUP BY touch — use directly
                for row in live_stock:
                    tv = round(float(row['touch']), 2)
                    wt = round(float(row['total_wt']), 3)
                    nt = round(float(row['total_nt']), 3)
                    tid = f"OPENING-WT-{_dt.now().strftime('%Y%m%d')}-{str(int(tv * 10)).zfill(4)}"
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_inventory
                            (it_code, it_name, tag_id, pcs,
                             gr_wt, ls_wt, nt_wt,
                             touch, wastage, is_tagged, entry_date)
                        VALUES (?, ?, ?, 0, ?, 0, ?, ?, 0, 0, ?)
                    """, (
                        f'OPENING-{int(tv)}',
                        f'Opening Stock {tv}% - {new_year}',
                        tid, wt, nt, tv, today,
                    ))
                    inserted += 1

                # ── Step 5: Update financial year ─────────────────────
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('financial_year',?)",
                    (new_year,)
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('year_close_date',?)",
                    (_dt.now().isoformat(),)
                )

                # Audit log
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id       INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts       TEXT, username TEXT,
                        action   TEXT, detail TEXT,
                        category TEXT, ip TEXT DEFAULT '')
                """)
                conn.execute(
                    "INSERT INTO audit_log(ts,username,action,detail,category) VALUES(?,?,?,?,?)",
                    (_dt.now().isoformat(), 'system',
                     f'Year Close {current_year}',
                     f'Archived to {arch_db}. New year: {new_year}. '
                     f'Carried {inserted} touch groups.',
                     'year_close')
                )

                # WAL checkpoint
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass

                conn.commit()
                _dblog(f"[YEAR] Year close complete. {inserted} OPENING rows created.")

                return {
                    'status': 'success',
                    'current_year': current_year,
                    'new_year': new_year,
                    'archive_path': arch_db,
                    'opening_rows': inserted,
                    'message': f'Year {current_year} closed. {inserted} touch groups carried to {new_year}.',
                }
        except Exception as e:
            _dberr(f"[YEAR] do_year_close: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_archive_data(self, year, table, limit=500):
        """Read data from an archived year DB (read-only)."""
        import os as _os
        SAFE_TABLES = {
            'sales_history', 'katti_vouchers', 'katti_voucher_items',
            'stock_inventory', 'clients_master', 'credit_ledger',
            'audit_log', 'stock_med_sessions',
        }
        if table not in SAFE_TABLES:
            return {'status': 'error', 'message': f'Table {table} not allowed'}
        try:
            base = _os.path.dirname(self.db_path)
            arch_db = _os.path.join(base, 'archives', year, 'aurum_local.db')
            if not _os.path.exists(arch_db):
                return {'status': 'error', 'message': f'Archive for {year} not found'}
            import sqlite3 as _sq
            conn = _sq.connect(arch_db, check_same_thread=False)
            conn.row_factory = _sq.Row
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return {
                'status': 'success',
                'year': year,
                'table': table,
                'rows': [dict(r) for r in rows],
                'count': len(rows),
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def get_archive_summary(self, year):
        """Get summary stats from an archived year."""
        import os as _os
        try:
            base = _os.path.dirname(self.db_path)
            arch_db = _os.path.join(base, 'archives', year, 'aurum_local.db')
            if not _os.path.exists(arch_db):
                return {'status': 'error', 'message': 'Archive not found'}

            # Load meta
            meta_path = _os.path.join(base, 'archives', year, 'meta.json')
            meta = {}
            if _os.path.exists(meta_path):
                import json as _json
                with open(meta_path) as mf:
                    meta = _json.load(mf)

            import sqlite3 as _sq
            conn = _sq.connect(arch_db, check_same_thread=False)
            conn.row_factory = _sq.Row

            def safe_query(sql, default=0):
                try:
                    return conn.execute(sql).fetchone()[0] or default
                except:
                    return default

            summary = {
                'status': 'success',
                'year': year,
                'meta': meta,
                'total_sales': safe_query("SELECT COUNT(*) FROM sales_history"),
                'total_katti': safe_query("SELECT COUNT(*) FROM katti_vouchers"),
                'total_clients': safe_query("SELECT COUNT(*) FROM clients_master"),
                'closing_stock_wt': round(safe_query(
                    "SELECT COALESCE(SUM(gr_wt),0) FROM stock_inventory", 0.0
                ), 3),
                'closing_stock_rows': safe_query("SELECT COUNT(*) FROM stock_inventory"),
                'archive_path': arch_db,
                'size_mb': round(_os.path.getsize(arch_db) / 1048576, 2),
            }
            conn.close()
            return summary
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _mirror_data(self):
        """Backup DB to C:/ProgramData/AurumOS/aurum_backup.db. Called after stock med."""
        import os as _os, shutil as _sh
        try:
            secret_dir = r"C:\ProgramData\AurumOS"
            backup_path = _os.path.join(secret_dir, 'aurum_backup.db')
            _os.makedirs(secret_dir, exist_ok=True)
            try:
                with self._get_connection() as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            _sh.copy2(self.db_path, backup_path)
            _dblog(f"[BACKUP] Mirrored to {backup_path}")
            return True
        except Exception as e:
            _dberr(f"[BACKUP] Mirror failed: {e}")
            return False

    def do_stock_med(self, data):
        """
        Stock Med Sign-Off:
        1. Log session in stock_med_sessions
        2. Archive katti, sales, stock into audit_archive (kept forever)
        3. Write new OPENING- rows from physical count
        4. Reset period: clear all katti, sales, stock (non-opening)
        """
        import json as _json
        from datetime import datetime as _dt

        try:
            # Ensure tables exist — safe for old DBs that were created before this feature
            with self._get_connection() as _tc:
                _tc.execute("""
                    CREATE TABLE IF NOT EXISTS stock_med_sessions (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        med_date    TEXT,
                        period_from TEXT,
                        period_to   TEXT,
                        signed_by   TEXT,
                        notes       TEXT,
                        reason      TEXT,
                        status      TEXT    DEFAULT 'SIGNED',
                        created_at  TEXT    DEFAULT (datetime('now'))
                    )
                """)
                _tc.execute("""
                    CREATE TABLE IF NOT EXISTS audit_archive (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id   INTEGER,
                        table_name   TEXT,
                        record_json  TEXT,
                        archived_at  TEXT    DEFAULT (datetime('now'))
                    )
                """)
                _tc.commit()
            _dblog("[STOCK_MED] Tables verified OK")
            signed_by = str(data.get('signed_by') or '').strip()
            notes = str(data.get('notes') or '').strip()
            reason = str(data.get('reason') or 'normal').strip()
            period_from = str(data.get('period_from') or '').strip()
            period_to = str(data.get('period_to') or '').strip()
            new_opening = data.get('new_opening') or []

            if not signed_by:
                return {'status': 'error', 'message': 'Signed-by name is required'}
            if not new_opening:
                return {'status': 'error', 'message': 'Physical count data is missing'}

            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")

                # 1. Log session
                conn.execute("""
                    INSERT INTO stock_med_sessions
                        (med_date, period_from, period_to, signed_by, notes, reason, status)
                    VALUES (date('now'), ?, ?, ?, ?, ?, 'SIGNED')
                """, (period_from, period_to, signed_by, notes, reason))
                session_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                _dblog(f"[STOCK_MED] Session {session_id} created — signed by {signed_by}")

                # 2. Archive all records into audit_archive (never deleted)
                for tbl in ['katti_vouchers', 'katti_voucher_items', 'sales_history', 'credit_ledger']:
                    rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
                    for r in rows:
                        conn.execute(
                            "INSERT INTO audit_archive (session_id, table_name, record_json) VALUES (?,?,?)",
                            (session_id, tbl, _json.dumps(dict(r)))
                        )
                    _dblog(f"[STOCK_MED] Archived {len(rows)} rows from {tbl}")

                si_rows = conn.execute(
                    "SELECT * FROM stock_inventory WHERE tag_id NOT LIKE 'OPENING-%'"
                ).fetchall()
                for r in si_rows:
                    conn.execute(
                        "INSERT INTO audit_archive (session_id, table_name, record_json) VALUES (?,?,?)",
                        (session_id, 'stock_inventory', _json.dumps(dict(r)))
                    )
                _dblog(f"[STOCK_MED] Archived {len(si_rows)} stock rows")

                # 3. Clear ALL stock_inventory rows
                #    - OPENING- rows: replaced by new physical count
                #    - Katti/bulk rows: period is closed, start fresh
                #    - Tagged pieces: archived above, now clear
                conn.execute("DELETE FROM stock_inventory")
                _dblog("[STOCK_MED] stock_inventory cleared completely")

                # 4. Write new OPENING- rows from physical count
                _dblog(f"[STOCK_MED] new_opening received: {new_opening}")
                med_date = _dt.now().strftime('%Y-%m-%d')
                inserted = 0
                for entry in new_opening:
                    touch = float(entry.get('touch') or 0)
                    phys_wt = float(entry.get('physical_wt') or 0)
                    # A retail shop counts by category, not purity, so its rows
                    # arrive with no touch at all. They used to be dropped here,
                    # which meant a shop could complete a whole stock take and
                    # sign it off and have nothing written.
                    category = str(entry.get('category') or entry.get('label') or '').strip()
                    if touch <= 0 and not category:
                        continue
                    if phys_wt < 0:
                        phys_wt = 0
                    if touch > 0:
                        tag_id = (
                                'OPENING-WT-'
                                + _dt.now().strftime('%Y%m%d')
                                + '-'
                                + str(int(touch * 10)).zfill(4)
                        )
                    else:
                        slug = ''.join(ch for ch in category.upper()
                                       if ch.isalnum())[:12] or 'UNCAT'
                        tag_id = ('OPENING-CAT-' + _dt.now().strftime('%Y%m%d')
                                  + '-' + slug)
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_inventory
                            (it_code, it_name, tag_id, pcs,
                             gr_wt, ls_wt, nt_wt,
                             touch, wastage, is_tagged, entry_date)
                        VALUES (?, ?, ?, 0, ?, 0, ?, ?, 0, 0, ?)
                    """, (
                        ('OPENING-' + str(int(touch))) if touch > 0
                        else ('OPENING-' + (category.upper() or 'UNCAT')),
                        ('Opening Stock ' + str(touch) + '% - ' + med_date) if touch > 0
                        else ('Opening Stock ' + category + ' - ' + med_date),
                        tag_id, phys_wt, phys_wt, touch, med_date
                    ))
                    inserted += 1
                    _dblog(f"[STOCK_MED] New opening: touch={touch}% wt={phys_wt}g tag={tag_id}")
                _dblog(f"[STOCK_MED] {inserted} new OPENING- rows written")

                # 5. Full DB reset — start completely fresh
                #
                # NEVER DELETE (permanent data):
                #   admin_creds        — login password
                #   app_config         — settings, hardware, session keys
                #   business_profile   — shop name, GSTIN, address
                #   audit_archive      — historical records (legal requirement)
                #   stock_med_sessions — audit history
                #   bastion_events     — security log
                #   bastion_learning   — AI thresholds
                #   bastion_alerts     — alert queue
                #   audit_log          — system audit trail
                #   mac_lock           — hardware whitelist
                #   login_log          — login history
                #
                # CLEAR (transactional — resets with new period):
                CLEAR_TABLES = [
                    'katti_vouchers',  # katti inward vouchers
                    'katti_voucher_items',  # katti voucher line items
                    'sales_history',  # all bills
                    'credit_ledger',  # credit/debit ledger
                    'uchak_inward_vouchers',  # uchak vouchers
                    'uchak_inward_items',  # uchak items
                    'clients_master',  # client list (fresh start)
                    'product_master',  # product catalog (fresh start)
                    'categories',  # categories (fresh start)
                    'touch_groups',  # touch groups (fresh start)
                ]

                cleared = []
                for tbl in CLEAR_TABLES:
                    try:
                        conn.execute(f"DELETE FROM {tbl}")
                        conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{tbl}'")
                        cleared.append(tbl)
                    except Exception as _te:
                        _dblog(f"[STOCK_MED] Clear {tbl}: {_te}")

                # Reset auto-increment sequences for cleared tables
                try:
                    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ({})".format(
                        ','.join("'" + t + "'" for t in cleared)
                    ))
                except Exception:
                    pass

                # stock_inventory already cleared + new OPENING rows written above
                _dblog(f"[STOCK_MED] Cleared {len(cleared)} tables: {cleared}")

                # WAL checkpoint before commit
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass

                conn.commit()
                _dblog(f"[STOCK_MED] Complete — session {session_id} — DB fresh start done")

            self._mirror_data()
            return {'status': 'success', 'session_id': session_id}

        except Exception as e:
            _dberr(f"[STOCK_MED] Error: {e}")
            import traceback as _tb
            _dberr(_tb.format_exc())
            return {'status': 'error', 'message': str(e)}

    # ── SESSION TOKEN — Layer 10 Security ─────────────────────────────────────
    def _db_state_hash(self):
        """Hash of current DB row counts — detects DB swap mid-session."""
        try:
            with self._get_connection() as conn:
                counts = []
                for tbl in ['sales_history', 'stock_inventory', 'katti_vouchers', 'admin_creds']:
                    try:
                        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                        counts.append(f"{tbl}:{n}")
                    except Exception:
                        counts.append(f"{tbl}:0")
            import hashlib as _hl
            return _hl.sha256('|'.join(counts).encode()).hexdigest()[:16]
        except Exception:
            return 'no-db'

    def _generate_session_token(self):
        """
        Called ONCE at startup. Generates a time-bound cryptographic token
        tied to this exact session — hardware + date + time window + DB state.
        Stored in 3 places: RAM, registry, temp file.
        Valid for this startup only — never reusable.
        """
        import hashlib as _hl, time as _t, tempfile as _tmp, os as _os

        SESSION_SALT = 'AurumOS@Session@Jenil#9x7z@2026'
        REG_KEY = r'SOFTWARE\Microsoft\InputMethod\AOS'

        dna = self._machine_fingerprint()
        today = __import__('datetime').date.today().isoformat()
        ts = str(int(_t.time() // 300))  # 5-minute rolling window
        dbhash = self._db_state_hash()

        raw = '|'.join([dna, today, ts, dbhash, SESSION_SALT])
        token = _hl.sha256(raw.encode('utf-8')).hexdigest()

        # Store 1: RAM
        self._session_token = token

        # Store 2: Windows registry (disguised as input method cache)
        try:
            import winreg as _wr
            key = _wr.CreateKey(_wr.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\InputMethod\AOS')
            _wr.SetValueEx(key, 'SessionCache', 0, _wr.REG_SZ, token)
            _wr.CloseKey(key)
        except Exception:
            pass

        # Store 3: Random temp file
        try:
            tmp = _tmp.mktemp(prefix='.~', suffix='.tmp',
                              dir=_os.environ.get('TEMP', _os.getcwd()))
            with open(tmp, 'w') as f:
                f.write(token)
            self._session_token_file = tmp
        except Exception:
            self._session_token_file = None

        _dblog(f"[SESSION] Token generated: {token[:8]}...")
        return token

    def _bastion_block(self):
        """Real-time guard -- call at the START of every sensitive write.
        Returns True if suspended (caller must abort the write)."""
        try:
            return bool(self.bastion_get_status().get('suspended'))
        except Exception:
            return False

    def _verify_session_token(self):
        """
        Called before every sensitive DB write.
        All 3 stores (RAM, registry, file) must match.
        One mismatch = operation BLOCKED.
        """
        ram_token = getattr(self, '_session_token', None)
        if not ram_token:
            _dberr("[SESSION] No token in RAM — BLOCKED")
            return False

        # Check registry
        try:
            import winreg as _wr
            key = _wr.OpenKey(_wr.HKEY_CURRENT_USER,
                              r'SOFTWARE\Microsoft\InputMethod\AOS')
            reg_token, _ = _wr.QueryValueEx(key, 'SessionCache')
            _wr.CloseKey(key)
            if reg_token != ram_token:
                _dberr("[SESSION] Registry mismatch — BLOCKED")
                self.bastion_suspend('session_tamper', 'Registry token mismatch — possible memory replay attack')
                return False
        except Exception:
            _dberr("[SESSION] Registry missing — BLOCKED")
            self.bastion_suspend('session_tamper', 'Registry token missing — possible file system attack')
            return False

        # Check temp file
        try:
            tf = getattr(self, '_session_token_file', None)
            if not tf:
                raise FileNotFoundError("no file path")
            with open(tf, 'r') as f:
                file_token = f.read().strip()
            if file_token != ram_token:
                _dberr("[SESSION] File token mismatch — BLOCKED")
                self.bastion_suspend('db_edit', 'Session file token mismatch — database edited mid-session')
                return False
        except Exception:
            _dberr("[SESSION] Temp file missing — BLOCKED")
            self.bastion_suspend('session_tamper', 'Session temp file missing — possible session hijack')
            return False

        return True

    def _cleanup_session(self):
        """Called on app exit — wipe all session traces from registry and disk."""
        self._session_token = None

        # Delete registry key
        try:
            import winreg as _wr
            key = _wr.OpenKey(_wr.HKEY_CURRENT_USER,
                              r'SOFTWARE\Microsoft\InputMethod\AOS',
                              0, _wr.KEY_SET_VALUE)
            _wr.DeleteValue(key, 'SessionCache')
            _wr.CloseKey(key)
        except Exception:
            pass

        # Delete temp file
        try:
            import os as _os
            tf = getattr(self, '_session_token_file', None)
            if tf and _os.path.exists(tf):
                _os.remove(tf)
        except Exception:
            pass

        _dblog("[SESSION] Cleaned up — all traces removed")

    # ══════════════════════════════════════════════════════════════════════
    # BASTION SECURITY SYSTEM — Permanent Threat Detection & Account Suspend
    # ══════════════════════════════════════════════════════════════════════

    # Attack type codes shown in suspension message
    BASTION_CODES = {
        'session_tamper': ('Session Token Tampering', 'A mid-session database edit or memory tamper was detected.'),
        'db_edit': ('Database Tampering Detected',
                    'The database was modified outside of AurumOS while the app was running.'),
        'fingerprint_mismatch': ('Hardware Identity Mismatch',
                                 'This database was copied from another PC and opened here illegally.'),
        'exe_tamper': ('EXE File Tampered', 'The AurumOS executable has been modified. Contact AurumOS Admin.'),
        'replay_attack': ('Replay Attack Detected', 'An old session token was injected. This is a hacking attempt.'),
    }

    def bastion_clear(self, admin_key, lock_code=None):
            """
            Tries ALL lock_code sources x ALL formulas x ALL dates.
            Guarantees a match regardless of which tool generated the key.

            Lock code sources: cache, passed param, machine fingerprint.
            Formulas: BASTION/REGULAR salt, with/without nonce, IST/UTC/local.
            """
            import hashlib as _hl
            import json as _json
            from datetime import datetime as _dt, timedelta as _td

            BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'
            REGULAR_SALT = 'AurumOS@Jewel#2024$Prof'

            try:
                entered = str(admin_key).strip().upper()
                nonce = self._get_unlock_nonce()

                # Collect ALL lock_code candidates
                lc_candidates = []
                try:
                    with self._get_connection() as conn:
                        row = conn.execute(
                            "SELECT value FROM app_config WHERE key='lock_code_cache'"
                        ).fetchone()
                        if row and row[0]:
                            lc_candidates.append(row[0].strip().upper())
                except Exception:
                    pass
                if lock_code:
                    lc_candidates.append(str(lock_code).strip().upper())
                fp = self._machine_fingerprint()[:8].upper()
                if fp not in lc_candidates:
                    lc_candidates.append(fp)
                if not lc_candidates:
                    lc_candidates.append('UNKNOWN')

                _dblog(f"[BASTION] lc_candidates={lc_candidates} nonce={nonce} entered={entered[:4]}****")

                # Date candidates (IST, UTC, local, +/-3 days)
                now_ist = _dt.utcnow() + _td(hours=5, minutes=30)
                now_utc = _dt.utcnow()
                now_loc = _dt.now()
                all_dates = []
                for base in [now_ist, now_utc, now_loc]:
                    for delta in range(-3, 4):
                        ds = (base + _td(days=delta)).strftime('%Y-%m-%d')
                        if ds not in all_dates:
                            all_dates.append(ds)

                # Exhaustive brute-force
                matched_date = None
                expected = ''
                used_salt = ''
                used_lc = ''

                for lc in lc_candidates:
                    if matched_date:
                        break
                    for date_str in all_dates:
                        # A) BASTION + no nonce
                        c = _hl.sha256(
                            (lc + BASTION_SALT + date_str).encode('utf-8')
                        ).hexdigest()[:16].upper()
                        if entered == c:
                            matched_date, expected, used_salt, used_lc = date_str, c, 'BASTION', lc
                            break
                        # B) BASTION + nonce
                        c = _hl.sha256(
                            (lc + BASTION_SALT + date_str + str(nonce)).encode('utf-8')
                        ).hexdigest()[:16].upper()
                        if entered == c:
                            matched_date, expected, used_salt, used_lc = date_str, c, 'BASTION+nonce', lc
                            break
                        # C) REGULAR + no nonce (12-char)
                        if len(entered) == 12:
                            c = _hl.sha256(
                                (lc + REGULAR_SALT + date_str).encode('utf-8')
                            ).hexdigest()[:12].upper()
                            if entered == c:
                                matched_date, expected, used_salt, used_lc = date_str, c, 'REGULAR', lc
                                break
                            # D) REGULAR + nonce (12-char)
                            c = _hl.sha256(
                                (lc + REGULAR_SALT + date_str + str(nonce)).encode('utf-8')
                            ).hexdigest()[:12].upper()
                            if entered == c:
                                matched_date, expected, used_salt, used_lc = date_str, c, 'REGULAR+nonce', lc
                                break

                _dblog(
                    f"[BASTION] lc_used={used_lc} nonce={nonce} dates_tried={len(all_dates)} "
                    f"entered={entered[:4]}**** matched={matched_date} salt={used_salt} "
                    f"expected={expected[:4]}****")

                if matched_date:
                    self._bump_unlock_nonce()
                    with self._get_connection() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) "
                            "VALUES('bastion_suspended','0')"
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) "
                            "VALUES('bastion_record','')"
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) "
                            "VALUES('bastion_suspend_reason','')"
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO app_config(key,value) "
                            "VALUES('lock_code_cache','')"
                        )
                        conn.commit()
                    _dblog(f'[BASTION] Suspension cleared with {used_salt} salt on {matched_date} lc={used_lc} nonce={nonce}')
                    return {'status': 'success', 'message': 'BASTION suspension cleared'}

                _dblog('[BASTION] Admin key did not match ALL combinations')
                return {'status': 'error', 'message': 'Invalid admin key'}

            except Exception as e:
                _dberr(f"[BASTION] clear error: {e}")
                return {'status': 'error', 'message': str(e)}

    def bastion_suspend(self, attack_type='unknown', detail=''):
        """
        BASTION: Permanently suspend account with full attack detail.
        Writes to DB so suspension survives restart.
        Triggers popup via window callback if available.
        """
        from datetime import datetime as _dt
        import json as _json

        try:
            ts = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
            code_name = attack_type
            title, reason = self.BASTION_CODES.get(
                attack_type,
                ('Security Violation', str(detail) or 'Unknown attack detected.')
            )

            # Lock code at suspension time — MUST match for unlock key gen
            lock_code = self._machine_fingerprint()[:8].upper()

            record = {
                'suspended': True,
                'attack_type': code_name,
                'title': title,
                'reason': reason,
                'detail': str(detail),
                'timestamp': ts,
                'lock_code': lock_code,
            }

            with self._get_connection() as conn:
                # Write suspension record
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','1')"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_record',?)",
                    (_json.dumps(record),)
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','1')"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('login_attempts','99')"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('lock_code_cache',?)",
                    (lock_code,)
                )
                conn.commit()

            _dberr(f"[BASTION] SUSPENDED — attack={attack_type} detail={detail} lock_code={lock_code}")

            # Write to audit log
            self.add_audit_log(
                f"BASTION SUSPEND: {title}",
                f"{reason} | {detail} | at {ts}",
                'BASTION',
                'security'
            )

        except Exception as e:
            _dberr(f"[BASTION] suspend error: {e}")

    def bastion_get_status(self):
        """
        Returns suspension status and attack record.
        Called by login screen on every load.
        """
        import json as _json
        try:
            with self._get_connection() as conn:
                rows = {r['key']: r['value'] for r in conn.execute(
                    "SELECT key,value FROM app_config WHERE key IN "
                    "('bastion_suspended','bastion_record')"
                ).fetchall()}

            suspended = rows.get('bastion_suspended', '0') == '1'
            if not suspended:
                return {'suspended': False}

            record = {}
            try:
                record = _json.loads(rows.get('bastion_record', '{}'))
            except Exception:
                pass

            # Use lock_code from suspension record (set at suspend time)
            lock_code = record.get('lock_code') or self._machine_fingerprint()[:8].upper()
            record['lock_code'] = lock_code
            record['suspended'] = True
            return record

        except Exception as e:
            _dberr(f"[BASTION] get_status error: {e}")
            return {'suspended': False}

    def bastion_verify_exe(self, exe_path=''):
        """
        Check if EXE has been tampered, by comparing its hash against the
        TRUSTED hash that build.py writes at build time into
        'exe_trusted_hash.txt' (sitting next to AurumOS.exe in dist\\).

        PERMANENT FIX: this used to self-capture "whatever ran first on
        this PC" as the trusted baseline -- but every fresh PyInstaller
        build produces a different hash even with zero source changes
        (embedded timestamps etc.), so every legitimate rebuild looked
        identical to tampering and falsely suspended the account.

        Now the BUILD decides what's trusted, not the first run. A fresh,
        untouched build always matches its own trusted_hash.txt and
        passes. Genuine tampering -- someone editing the EXE bytes AFTER
        it was built and shipped -- still changes the EXE's hash while
        trusted_hash.txt (a separate, untouched file) stays the same,
        so real tampering is still caught correctly.
        """
        import hashlib as _hl, os as _os, sys as _sys
        try:
            if not getattr(_sys, 'frozen', False):
                return True  # dev mode — skip

            if not exe_path:
                exe_path = _sys.executable

            if not _os.path.exists(exe_path):
                return True

            # Trusted hash lives next to the EXE, written by build.py
            exe_dir = _os.path.dirname(exe_path)
            trust_path = _os.path.join(exe_dir, 'exe_trusted_hash.txt')

            if not _os.path.exists(trust_path):
                # No trusted-hash file shipped with this build (e.g. an
                # older build made before this fix, or it was deleted).
                # Fail OPEN rather than suspend on missing metadata --
                # we only ever suspend on a confirmed MISMATCH, never on
                # "we don't have anything to compare against."
                _dblog("[BASTION] No exe_trusted_hash.txt found next to EXE -- skipping tamper check")
                return True

            with open(trust_path, 'r') as f:
                trusted_hash = f.read().strip()

            if not trusted_hash:
                _dblog("[BASTION] exe_trusted_hash.txt is empty -- skipping tamper check")
                return True

            # Compute current hash
            h = _hl.sha256()
            with open(exe_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk: break
                    h.update(chunk)
            current_hash = h.hexdigest()

            if trusted_hash != current_hash:
                _dberr(f"[BASTION] EXE TAMPERED! trusted={trusted_hash[:12]} current={current_hash[:12]}")
                self.bastion_suspend(
                    'exe_tamper',
                    f"Trusted={trusted_hash[:12]}... Current={current_hash[:12]}..."
                )
                return False

            _dblog(f"[BASTION] EXE integrity OK: {current_hash[:12]}...")
            return True

        except Exception as e:
            _dberr(f"[BASTION] exe_verify error: {e}")
            return True  # fail open on unexpected error

    # ══════════════════════════════════════════════════════════════════
    #  DEAD STOCK
    #  Everything still in stock, with the date it arrived. A sold piece is
    #  deducted from this table, so whatever is here has not been sold.
    # ══════════════════════════════════════════════════════════════════

    def dead_stock_rows(self, limit=5000):
        """Every row still in stock, with the columns the report needs.

        Columns added after the table was first written are selected through
        PRAGMA rather than assumed, because a shop upgrading from an old
        version has the table without them and the query has to survive that.
        """
        try:
            with self._get_connection() as conn:
                have = {r[1] for r in conn.execute(
                    "PRAGMA table_info(stock_inventory)").fetchall()}

                def col(name, default="''"):
                    return ("COALESCE(%s,%s)" % (name, default)) if name in have else default

                sql = (
                    "SELECT id, tag_id, it_code, it_name, "
                    "       COALESCE(gr_wt,0) AS gr_wt, COALESCE(nt_wt,0) AS nt_wt, "
                    "       COALESCE(touch,0) AS touch, COALESCE(pcs,1) AS pcs, "
                    "       COALESCE(is_tagged,0) AS is_tagged, "
                    "       COALESCE(huid,'') AS huid, entry_date, "
                    + col('category') + " AS category, "
                    + col('karat') + " AS karat, "
                    + col('sale_price', '0') + " AS sale_price, "
                    + col('stone_value', '0') + " AS stone_value, "
                    + col('photo') + " AS photo, "
                    + col('design') + " AS design "
                    "FROM stock_inventory "
                    "ORDER BY date(COALESCE(entry_date,'9999-12-31')) ASC "
                    "LIMIT ?")
                return [dict(r) for r in conn.execute(sql, (int(limit or 5000),)).fetchall()]
        except Exception as e:
            _dberr(f"[DEAD] rows: {e}")
            return []

    def latest_gold_rate(self):
        """The ten-gram rate the shop last billed at.

        Read from billing rather than a setting, exactly as the dashboard
        does, so the dead stock report and the dashboard can never put two
        different values on the same shelf.
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT gold_rate FROM sales_history "
                    "WHERE COALESCE(gold_rate,0)>0 ORDER BY id DESC LIMIT 1"
                ).fetchone()
            return float(row[0]) if row and row[0] else 0.0
        except Exception as e:
            _dberr(f"[DEAD] rate: {e}")
            return 0.0

    # ══════════════════════════════════════════════════════════════════
    #  HALLMARKING
    #  Stock cannot be sold until it comes back from the assaying centre
    #  with a number struck on it. These tables are the register of what
    #  went, when, and what came home.
    # ══════════════════════════════════════════════════════════════════

    def ensure_hallmark_tables(self):
        """Added in place on an existing shop. Safe on every startup."""
        try:
            with self._get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS hallmark_centres (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        name           TEXT NOT NULL,
                        code           TEXT DEFAULT '',
                        phone          TEXT DEFAULT '',
                        address        TEXT DEFAULT '',
                        per_piece      REAL DEFAULT 45.0,
                        min_charge     REAL DEFAULT 200.0,
                        turnaround_days INTEGER DEFAULT 7,
                        active         INTEGER DEFAULT 1,
                        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP);

                    CREATE TABLE IF NOT EXISTS hallmark_lots (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        lot_no        TEXT UNIQUE,
                        centre_id     INTEGER,
                        centre_name   TEXT DEFAULT '',
                        sent_date     DATE,
                        expected_date DATE,
                        received_date DATE,
                        status        TEXT DEFAULT 'out',
                        charge_base   REAL DEFAULT 0,
                        charge_gst    REAL DEFAULT 0,
                        charge_total  REAL DEFAULT 0,
                        note          TEXT DEFAULT '',
                        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP);

                    CREATE TABLE IF NOT EXISTS hallmark_lot_items (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        lot_id       INTEGER NOT NULL,
                        tag_id       TEXT,
                        it_name      TEXT DEFAULT '',
                        gr_wt        REAL DEFAULT 0,
                        nt_wt        REAL DEFAULT 0,
                        karat        TEXT DEFAULT '',
                        grade        TEXT DEFAULT '',
                        huid         TEXT DEFAULT '',
                        result       TEXT DEFAULT 'pending',
                        purity_found REAL DEFAULT 0,
                        note         TEXT DEFAULT '');

                    CREATE INDEX IF NOT EXISTS ix_hm_items_lot
                        ON hallmark_lot_items(lot_id);
                    CREATE INDEX IF NOT EXISTS ix_hm_items_tag
                        ON hallmark_lot_items(tag_id);
                """)
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[HM] tables: {e}")
            return False

    # ── centres ──────────────────────────────────────────────────────

    def hm_centres(self, active_only=False):
        try:
            with self._get_connection() as conn:
                sql = "SELECT * FROM hallmark_centres"
                if active_only:
                    sql += " WHERE active=1"
                sql += " ORDER BY active DESC, name"
                return [dict(r) for r in conn.execute(sql).fetchall()]
        except Exception as e:
            _dberr(f"[HM] centres: {e}")
            return []

    def hm_save_centre(self, data):
        """Insert or update one centre. Returns its id, or 0."""
        try:
            d = dict(data or {})
            cid = int(d.get('id') or 0)
            vals = (str(d.get('name') or '').strip(),
                    str(d.get('code') or '').strip(),
                    str(d.get('phone') or '').strip(),
                    str(d.get('address') or '').strip(),
                    float(d.get('per_piece') or 45.0),
                    float(d.get('min_charge') or 200.0),
                    int(d.get('turnaround_days') or 7),
                    0 if str(d.get('active', 1)) in ('0', 'false', 'False', '') else 1)
            with self._get_connection() as conn:
                if cid:
                    conn.execute(
                        "UPDATE hallmark_centres SET name=?,code=?,phone=?,"
                        "address=?,per_piece=?,min_charge=?,turnaround_days=?,"
                        "active=? WHERE id=?", vals + (cid,))
                else:
                    cur = conn.execute(
                        "INSERT INTO hallmark_centres(name,code,phone,address,"
                        "per_piece,min_charge,turnaround_days,active) "
                        "VALUES(?,?,?,?,?,?,?,?)", vals)
                    cid = int(cur.lastrowid)
                conn.commit()
            return cid
        except Exception as e:
            _dberr(f"[HM] save centre: {e}")
            return 0

    # ── stock waiting to go ──────────────────────────────────────────

    def hm_pending_stock(self, limit=500):
        """Tagged pieces with no number on them that are not already away.

        A piece sitting in an open lot must not be offered again, which is
        the whole reason this is a query rather than a column: cancelling a
        lot releases its pieces with nothing left over to clean up.
        """
        try:
            with self._get_connection() as conn:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(stock_inventory)").fetchall()]
                karat = "COALESCE(s.karat,'')" if 'karat' in cols else "''"
                cat = "COALESCE(s.category,'')" if 'category' in cols else "''"
                rows = conn.execute(
                    "SELECT s.tag_id, s.it_name, s.it_code, s.gr_wt, s.nt_wt, "
                    "       s.touch, " + karat + " AS karat, "
                    + cat + " AS category, s.entry_date "
                    "FROM stock_inventory s "
                    "WHERE COALESCE(s.tag_id,'') <> '' "
                    "  AND COALESCE(s.huid,'') = '' "
                    "  AND s.tag_id NOT IN ("
                    "      SELECT i.tag_id FROM hallmark_lot_items i "
                    "      JOIN hallmark_lots l ON l.id = i.lot_id "
                    "      WHERE l.status <> 'cancelled' "
                    "        AND COALESCE(l.received_date,'') = '') "
                    "ORDER BY s.entry_date, s.tag_id LIMIT ?",
                    (int(limit or 500),)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            _dberr(f"[HM] pending stock: {e}")
            return []

    # ── lots ─────────────────────────────────────────────────────────

    def hm_lot_numbers(self):
        try:
            with self._get_connection() as conn:
                return [r[0] for r in conn.execute(
                    "SELECT lot_no FROM hallmark_lots").fetchall()]
        except Exception as e:
            _dberr(f"[HM] lot numbers: {e}")
            return []

    def hm_lots(self, with_items=True, limit=200):
        try:
            with self._get_connection() as conn:
                lots = [dict(r) for r in conn.execute(
                    "SELECT * FROM hallmark_lots ORDER BY id DESC LIMIT ?",
                    (int(limit or 200),)).fetchall()]
                if with_items and lots:
                    ids = tuple(l['id'] for l in lots)
                    marks = '(' + ','.join('?' * len(ids)) + ')'
                    by_lot = {}
                    for r in conn.execute(
                            "SELECT * FROM hallmark_lot_items WHERE lot_id IN "
                            + marks + " ORDER BY id", ids).fetchall():
                        by_lot.setdefault(r['lot_id'], []).append(dict(r))
                    for l in lots:
                        l['items'] = by_lot.get(l['id'], [])
                return lots
        except Exception as e:
            _dberr(f"[HM] lots: {e}")
            return []

    def hm_create_lot(self, lot_no, centre, tag_ids, sent_date, expected_date,
                      charge, note=''):
        """Pack a lot. Each piece is copied in as it stands, so a later edit
        to the stock record cannot rewrite what was sent."""
        try:
            tags = [str(t).strip() for t in (tag_ids or []) if str(t).strip()]
            if not tags:
                return {'status': 'error', 'message': 'No pieces chosen.'}
            c = dict(centre or {})
            ch = dict(charge or {})
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO hallmark_lots(lot_no,centre_id,centre_name,"
                    "sent_date,expected_date,status,charge_base,charge_gst,"
                    "charge_total,note) VALUES(?,?,?,?,?,'out',?,?,?,?)",
                    (str(lot_no), int(c.get('id') or 0),
                     str(c.get('name') or ''), str(sent_date),
                     str(expected_date), float(ch.get('base') or 0),
                     float(ch.get('gst') or 0), float(ch.get('total') or 0),
                     str(note or '')))
                lot_id = int(cur.lastrowid)
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(stock_inventory)").fetchall()]
                karat = "COALESCE(karat,'')" if 'karat' in cols else "''"
                added = 0
                for tag in tags:
                    row = conn.execute(
                        "SELECT it_name, gr_wt, nt_wt, " + karat + " AS karat "
                        "FROM stock_inventory WHERE tag_id=?",
                        (tag,)).fetchone()
                    if not row:
                        continue
                    conn.execute(
                        "INSERT INTO hallmark_lot_items(lot_id,tag_id,it_name,"
                        "gr_wt,nt_wt,karat,result) "
                        "VALUES(?,?,?,?,?,?,'pending')",
                        (lot_id, tag, row['it_name'] or '',
                         float(row['gr_wt'] or 0), float(row['nt_wt'] or 0),
                         str(row['karat'] or '')))
                    added += 1
                conn.commit()
            return {'status': 'success', 'lot_id': lot_id, 'lot_no': lot_no,
                    'pieces': added}
        except Exception as e:
            _dberr(f"[HM] create lot: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_lot(self, lot_id):
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT * FROM hallmark_lots WHERE id=?",
                                   (int(lot_id),)).fetchone()
                if not row:
                    return None
                lot = dict(row)
                lot['items'] = [dict(r) for r in conn.execute(
                    "SELECT * FROM hallmark_lot_items WHERE lot_id=? "
                    "ORDER BY id", (int(lot_id),)).fetchall()]
                return lot
        except Exception as e:
            _dberr(f"[HM] lot: {e}")
            return None

    def hm_huids_in_use(self, exclude_lot=0):
        """Every number already recorded, so one cannot land twice."""
        out = set()
        try:
            with self._get_connection() as conn:
                for r in conn.execute(
                        "SELECT huid FROM stock_inventory "
                        "WHERE COALESCE(huid,'') <> ''").fetchall():
                    out.add(str(r[0]).strip().upper())
                for r in conn.execute(
                        "SELECT huid FROM hallmark_lot_items "
                        "WHERE COALESCE(huid,'') <> '' AND lot_id <> ?",
                        (int(exclude_lot or 0),)).fetchall():
                    out.add(str(r[0]).strip().upper())
        except Exception as e:
            _dberr(f"[HM] huids in use: {e}")
        return out

    def hm_receive(self, lot_id, rows, received_date):
        """Book a lot back in.

        A passed piece gets its number written onto the stock record, which
        is what makes it saleable. A failed piece gets nothing and stays
        unsaleable, where the panel can still see it.
        """
        try:
            marked = failed = 0
            still = 0
            with self._get_connection() as conn:
                for r in (rows or []):
                    tag = str(r.get('tag_id') or '').strip()
                    res = str(r.get('result') or 'pending').lower()
                    huid = str(r.get('huid') or '').strip().upper()
                    if res == 'fail':
                        huid = ''
                    conn.execute(
                        "UPDATE hallmark_lot_items SET huid=?,result=?,"
                        "purity_found=?,note=? WHERE lot_id=? AND tag_id=?",
                        (huid, res, float(r.get('purity_found') or 0),
                         str(r.get('note') or ''), int(lot_id), tag))
                    if res == 'pass' and huid:
                        conn.execute(
                            "UPDATE stock_inventory SET huid=? WHERE tag_id=?",
                            (huid, tag))
                        marked += 1
                    elif res == 'fail':
                        failed += 1
                still = conn.execute(
                    "SELECT COUNT(*) FROM hallmark_lot_items WHERE lot_id=? "
                    "AND LOWER(COALESCE(result,'pending'))='pending'",
                    (int(lot_id),)).fetchone()[0]
                if not still:
                    conn.execute(
                        "UPDATE hallmark_lots SET received_date=?,"
                        "status='received' WHERE id=?",
                        (str(received_date), int(lot_id)))
                else:
                    conn.execute(
                        "UPDATE hallmark_lots SET status='part' WHERE id=?",
                        (int(lot_id),))
                conn.commit()
            return {'status': 'success', 'marked': marked, 'failed': failed,
                    'still_out': int(still)}
        except Exception as e:
            _dberr(f"[HM] receive: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_cancel_lot(self, lot_id):
        """A lot that never went. The pieces are released; nothing is
        deleted, so the register still shows it was packed."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE hallmark_lots SET status='cancelled' WHERE id=? "
                    "AND COALESCE(received_date,'') = ''", (int(lot_id),))
                conn.commit()
            return {'status': 'success'}
        except Exception as e:
            _dberr(f"[HM] cancel: {e}")
            return {'status': 'error', 'message': str(e)}

    def hm_find(self, term):
        """Look a piece up by its number or its tag, for a BIS question."""
        try:
            t = '%' + str(term or '').strip().upper() + '%'
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT i.*, l.lot_no, l.centre_name, l.sent_date, "
                    "       l.received_date "
                    "FROM hallmark_lot_items i "
                    "JOIN hallmark_lots l ON l.id = i.lot_id "
                    "WHERE UPPER(COALESCE(i.huid,'')) LIKE ? "
                    "   OR UPPER(COALESCE(i.tag_id,'')) LIKE ? "
                    "ORDER BY i.id DESC LIMIT 50", (t, t)).fetchall()]
        except Exception as e:
            _dberr(f"[HM] find: {e}")
            return []

    # ── KARIGAR (job-work register) ───────────────────────────────────
    # UI: karigar.html (dashboard), karigar_master.html (register),
    # karigar_order.html (issue/receive), karigar_details.html (statement).
    # Safe on every startup; adds tables/columns in place on old shops.

    def ensure_karigar_tables(self):
        try:
            with self._get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS karigars (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        name           TEXT NOT NULL,
                        phone          TEXT DEFAULT '',
                        specialty      TEXT DEFAULT '',
                        address        TEXT DEFAULT '',
                        ghat_norm      REAL DEFAULT 0.5,
                        ghat_allowance REAL DEFAULT 1.0,
                        active         INTEGER DEFAULT 1,
                        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP);

                    CREATE TABLE IF NOT EXISTS karigar_orders (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        karigar_id     INTEGER NOT NULL,
                        order_no       TEXT DEFAULT '',
                        issue_date     TEXT DEFAULT '',
                        due_date       TEXT DEFAULT '',
                        given_fine_wt  REAL DEFAULT 0,
                        given_touch    REAL DEFAULT 100,
                        status         TEXT DEFAULT 'issued',
                        returned_fine  REAL DEFAULT 0,
                        loss           REAL DEFAULT 0,
                        allowance      REAL DEFAULT 0,
                        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP);

                    CREATE TABLE IF NOT EXISTS karigar_order_items (
                        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id           INTEGER NOT NULL,
                        item_name          TEXT DEFAULT '',
                        fine_wt            REAL DEFAULT 0,
                        touch              REAL DEFAULT 0,
                        expected_return_wt REAL DEFAULT 0,
                        received           INTEGER DEFAULT 0,
                        recv_gross_wt      REAL DEFAULT 0,
                        recv_touch         REAL DEFAULT 0);

                    CREATE INDEX IF NOT EXISTS ix_karigar_orders_kid
                        ON karigar_orders(karigar_id);
                    CREATE INDEX IF NOT EXISTS ix_karigar_items_oid
                        ON karigar_order_items(order_id);
                """)
                # Columns added after first release — old shops lack them.
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(karigars)").fetchall()]
                if 'ghat_norm' not in cols:
                    conn.execute(
                        "ALTER TABLE karigars ADD COLUMN ghat_norm REAL DEFAULT 0.5")
                if 'ghat_allowance' not in cols:
                    conn.execute(
                        "ALTER TABLE karigars ADD COLUMN ghat_allowance REAL DEFAULT 1.0")
                if 'active' not in cols:
                    conn.execute(
                        "ALTER TABLE karigars ADD COLUMN active INTEGER DEFAULT 1")
                ocols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(karigar_orders)").fetchall()]
                for _c, _d in (('returned_fine', 'ALTER TABLE karigar_orders ADD COLUMN returned_fine REAL DEFAULT 0'),
                               ('loss', 'ALTER TABLE karigar_orders ADD COLUMN loss REAL DEFAULT 0'),
                               ('allowance', 'ALTER TABLE karigar_orders ADD COLUMN allowance REAL DEFAULT 0')):
                    if _c not in ocols:
                        conn.execute(_d)
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[KARIGAR] tables: {e}")
            return False

    def _karigar_metrics(self, conn, kid):
        """Totals + health for one karigar, from its orders."""
        try:
            orders = conn.execute(
                "SELECT * FROM karigar_orders WHERE karigar_id=?",
                (int(kid),)).fetchall()
            given = sum(float(o['given_fine_wt'] or 0) for o in orders)
            holding = sum(float(o['given_fine_wt'] or 0) for o in orders
                          if str(o['status'] or '') != 'completed')
            ongoing = sum(1 for o in orders
                          if str(o['status'] or '') != 'completed')
            completed = sum(1 for o in orders
                            if str(o['status'] or '') == 'completed')
            loss = sum(float(o['loss'] or 0) for o in orders
                       if str(o['status'] or '') == 'completed')
            allow_row = conn.execute(
                "SELECT ghat_allowance FROM karigars WHERE id=?",
                (int(kid),)).fetchone()
            allow = float((allow_row['ghat_allowance']
                           if allow_row else 0) or 0)
            over = max(0.0, round(loss - allow, 3)) if allow > 0 else 0.0
            # Overdue = any ongoing order past its due date.
            from datetime import date as _d
            today = _d.today().isoformat()
            overdue = any(str(o['due_date'] or '') != ''
                          and str(o['due_date']) < today
                          and str(o['status'] or '') != 'completed'
                          for o in orders)
            oldest = 0
            for o in orders:
                if str(o['status'] or '') == 'completed':
                    continue
                try:
                    _iss = str(o['issue_date'] or '')[:10]
                    if len(_iss) == 10:
                        _y, _m, _dd = map(int, _iss.split('-'))
                        _delta = (_d.today() - _d(_y, _m, _dd)).days
                        oldest = max(oldest, max(0, _delta))
                except Exception:
                    pass
            if overdue:
                health = 'RED'
            elif over > 0:
                health = 'AMBER'
            else:
                health = 'GREEN'
            return dict(metal_given=round(given, 3),
                        metal_holding=round(holding, 3),
                        ghat_wt=round(loss, 3),
                        over_allowed=round(over, 3),
                        ongoing=ongoing, completed=completed,
                        oldest_job_days=oldest, health=health)
        except Exception:
            return dict(metal_given=0, metal_holding=0, ghat_wt=0,
                        over_allowed=0, ongoing=0, completed=0,
                        oldest_job_days=0, health='GREEN')

    def karigar_all(self):
        """Every karigar with dashboard metrics. Returns a list of dicts."""
        try:
            self.ensure_karigar_tables()
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM karigars ORDER BY active DESC, name"
                ).fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    d['ghat_norm'] = float(d.get('ghat_norm') or 0.5)
                    d['ghat_allowance'] = float(d.get('ghat_allowance') or 0)
                    d.update(self._karigar_metrics(conn, d['id']))
                    out.append(d)
                return out
        except Exception as e:
            _dberr(f"[KARIGAR] all: {e}")
            return []

    def karigar_add(self, name, phone='', specialty='', address='',
                    ghat_norm=0.5, allowance=1.0):
        try:
            name = str(name or '').strip()
            if not name:
                return {'status': 'error', 'message': 'Karigar name is required.'}
            try:
                ghat_norm = float(ghat_norm or 0.5)
            except (TypeError, ValueError):
                ghat_norm = 0.5
            try:
                allowance = float(allowance if allowance not in (None, '') else 1.0)
            except (TypeError, ValueError):
                allowance = 1.0
            self.ensure_karigar_tables()
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO karigars(name,phone,specialty,address,"
                    "ghat_norm,ghat_allowance,active) VALUES(?,?,?,?,?,?,1)",
                    (name, str(phone or '').strip(),
                     str(specialty or '').strip(),
                     str(address or '').strip(), ghat_norm, allowance))
                conn.commit()
                return {'status': 'success', 'id': int(cur.lastrowid),
                        'message': f'{name} registered.'}
        except Exception as e:
            _dberr(f"[KARIGAR] add: {e}")
            return {'status': 'error', 'message': str(e)}

    def karigar_update(self, kid, name, phone='', specialty='', address='',
                       ghat_norm=0.5, allowance=1.0):
        try:
            name = str(name or '').strip()
            if not name:
                return {'status': 'error', 'message': 'Karigar name is required.'}
            try:
                ghat_norm = float(ghat_norm or 0.5)
            except (TypeError, ValueError):
                ghat_norm = 0.5
            try:
                allowance = float(allowance if allowance not in (None, '') else 1.0)
            except (TypeError, ValueError):
                allowance = 1.0
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE karigars SET name=?,phone=?,specialty=?,address=?,"
                    "ghat_norm=?,ghat_allowance=? WHERE id=?",
                    (name, str(phone or '').strip(),
                     str(specialty or '').strip(),
                     str(address or '').strip(),
                     ghat_norm, allowance, int(kid)))
                conn.commit()
            return {'status': 'success', 'message': 'Karigar updated.'}
        except Exception as e:
            _dberr(f"[KARIGAR] update: {e}")
            return {'status': 'error', 'message': str(e)}

    def karigar_delete(self, kid):
        """Refused while orders exist — history must survive the register."""
        try:
            with self._get_connection() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM karigar_orders WHERE karigar_id=?",
                    (int(kid),)).fetchone()['c']
                if int(n or 0) > 0:
                    return {'status': 'error',
                            'message': 'Karigar has job orders — cannot remove. Mark inactive instead.'}
                conn.execute("DELETE FROM karigars WHERE id=?", (int(kid),))
                conn.commit()
            return {'status': 'success', 'message': 'Karigar removed.'}
        except Exception as e:
            _dberr(f"[KARIGAR] delete: {e}")
            return {'status': 'error', 'message': str(e)}

    def karigar_orders(self, karigar_id=None):
        """All orders (issue screen) or one karigar's (detail screen)."""
        try:
            self.ensure_karigar_tables()
            with self._get_connection() as conn:
                if karigar_id:
                    rows = conn.execute(
                        "SELECT o.*, k.name AS karigar_name, "
                        "       COALESCE(k.ghat_allowance,0) AS k_allow "
                        "FROM karigar_orders o "
                        "LEFT JOIN karigars k ON k.id=o.karigar_id "
                        "WHERE o.karigar_id=? ORDER BY o.id DESC",
                        (int(karigar_id),)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT o.*, k.name AS karigar_name, "
                        "       COALESCE(k.ghat_allowance,0) AS k_allow "
                        "FROM karigar_orders o "
                        "LEFT JOIN karigars k ON k.id=o.karigar_id "
                        "ORDER BY o.id DESC").fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    items = [dict(i) for i in conn.execute(
                        "SELECT * FROM karigar_order_items WHERE order_id=? "
                        "ORDER BY id", (d['id'],)).fetchall()]
                    d['items'] = items
                    d['allowance'] = float(d.get('allowance') or d.get('k_allow') or 0)
                    d.pop('k_allow', None)
                    out.append(d)
                return out
        except Exception as e:
            _dberr(f"[KARIGAR] orders: {e}")
            return []

    def karigar_create_order(self, karigar_id, order_no='', issue_date='',
                             due_date='', given_fine_wt=0, given_touch=100,
                             items=None):
        try:
            karigar_id = int(karigar_id or 0)
            if not karigar_id:
                return {'status': 'error', 'message': 'Karigar is required.'}
            items = list(items or [])
            if not items:
                return {'status': 'error', 'message': 'Add at least one item.'}
            try:
                given = float(given_fine_wt or 0)
            except (TypeError, ValueError):
                given = 0
            if not given > 0:
                return {'status': 'error', 'message': 'Gold given must be above zero.'}
            try:
                gtouch = float(given_touch or 100)
            except (TypeError, ValueError):
                gtouch = 100
            alloc = 0.0
            clean = []
            for it in items:
                d = dict(it or {})
                nm = str(d.get('name') or d.get('item_name') or '').strip() or 'Item'
                try:
                    fw = float(d.get('fine_wt') or 0)
                except (TypeError, ValueError):
                    fw = 0
                try:
                    tc = float(d.get('touch') or 0)
                except (TypeError, ValueError):
                    tc = 0
                if fw <= 0:
                    continue
                exp = round(fw / (tc / 100), 3) if tc > 0 else 0.0
                alloc += fw
                clean.append((nm, round(fw, 3), tc, exp))
            if not clean:
                return {'status': 'error', 'message': 'Add at least one item with weight.'}
            if abs(round(alloc, 3) - round(given, 3)) > 0.001:
                return {'status': 'error',
                        'message': f'Fine allocated ({alloc:.3f} g) must equal gold given ({given:.3f} g).'}
            from datetime import date as _d
            self.ensure_karigar_tables()
            with self._get_connection() as conn:
                k = conn.execute(
                    "SELECT ghat_allowance FROM karigars WHERE id=?",
                    (karigar_id,)).fetchone()
                if not k:
                    return {'status': 'error', 'message': 'Karigar not found.'}
                allow = float(k['ghat_allowance'] or 0)
                cur = conn.execute(
                    "INSERT INTO karigar_orders(karigar_id,order_no,issue_date,"
                    "due_date,given_fine_wt,given_touch,status,allowance) "
                    "VALUES(?,?,?,?,?,?,'issued',?)",
                    (karigar_id, str(order_no or '').strip(),
                     str(issue_date or _d.today().isoformat()),
                     str(due_date or ''),
                     round(given, 3), gtouch, allow))
                oid = int(cur.lastrowid)
                for nm, fw, tc, exp in clean:
                    conn.execute(
                        "INSERT INTO karigar_order_items(order_id,item_name,"
                        "fine_wt,touch,expected_return_wt) VALUES(?,?,?,?,?)",
                        (oid, nm, fw, tc, exp))
                conn.commit()
                return {'status': 'success', 'id': oid,
                        'message': f'Order {str(order_no or oid).strip()} issued.'}
        except Exception as e:
            _dberr(f"[KARIGAR] create order: {e}")
            return {'status': 'error', 'message': str(e)}

    def karigar_receive_order(self, order_id, received_items=None):
        try:
            oid = int(order_id or 0)
            if not oid:
                return {'status': 'error', 'message': 'Order is required.'}
            recv = list(received_items or [])
            if not recv:
                return {'status': 'error', 'message': 'No received weights given.'}
            with self._get_connection() as conn:
                o = conn.execute(
                    "SELECT * FROM karigar_orders WHERE id=?", (oid,)).fetchone()
                if not o:
                    return {'status': 'error', 'message': 'Order not found.'}
                if str(o['status'] or '') == 'completed':
                    return {'status': 'error', 'message': 'Order already completed.'}
                given = float(o['given_fine_wt'] or 0)
                allow = float(o['allowance'] or 0)
                returned = 0.0
                for ri in recv:
                    d = dict(ri or {})
                    try:
                        iid = int(d.get('item_id') or d.get('id') or 0)
                    except (TypeError, ValueError):
                        continue
                    try:
                        rg = float(d.get('recv_gross_wt') or d.get('actual_wt') or 0)
                    except (TypeError, ValueError):
                        rg = 0
                    try:
                        rt = float(d.get('recv_touch') or d.get('touch') or 0)
                    except (TypeError, ValueError):
                        rt = 0
                    if rg <= 0:
                        return {'status': 'error',
                                'message': 'Every item needs an actual weight above zero.'}
                    conn.execute(
                        "UPDATE karigar_order_items SET received=1,"
                        "recv_gross_wt=?,recv_touch=? WHERE id=? AND order_id=?",
                        (round(rg, 3), rt, iid, oid))
                    returned += rg * rt / 100.0
                returned = round(returned, 3)
                loss = round(max(0.0, given - returned), 3)
                conn.execute(
                    "UPDATE karigar_orders SET status='completed',"
                    "returned_fine=?,loss=? WHERE id=?",
                    (returned, loss, oid))
                conn.commit()
                over = round(max(0.0, loss - allow), 3)
                msg = f'Order received. Loss {loss:.3f} g.'
                if allow > 0 and over > 0:
                    msg += f' Over allowance by {over:.3f} g.'
                return {'status': 'success', 'message': msg,
                        'returned_fine': returned, 'loss': loss,
                        'over_allowance': over}
        except Exception as e:
            _dberr(f"[KARIGAR] receive: {e}")
            return {'status': 'error', 'message': str(e)}

    # ── HELD BILLS (POS hold / resume, survives navigation) ──────────
    # pos_billing.html keeps a live in-memory strip; this table is the
    # persistence behind it so a held bill is still there after the page
    # reloads or the app restarts.

    def ensure_held_bills_table(self):
        try:
            with self._get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS held_bills (
                        vch_id      TEXT PRIMARY KEY,
                        customer    TEXT DEFAULT '',
                        grand_total REAL DEFAULT 0,
                        items       TEXT DEFAULT '[]',
                        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP);
                """)
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[HELD] tables: {e}")
            return False

    def held_save(self, bill):
        try:
            import json as _j
            d = dict(bill or {})
            vch = str(d.get('vch_id') or '').strip()
            if not vch:
                return {'status': 'error', 'message': 'Bill id is required.'}
            items = d.get('items') or []
            self.ensure_held_bills_table()
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO held_bills(vch_id,customer,"
                    "grand_total,items) VALUES(?,?,?,?)",
                    (vch, str(d.get('customer') or 'Walk-in'),
                     float(d.get('grand_total') or 0),
                     _j.dumps(items, ensure_ascii=False)))
                conn.commit()
            return {'status': 'success', 'vch_id': vch}
        except Exception as e:
            _dberr(f"[HELD] save: {e}")
            return {'status': 'error', 'message': str(e)}

    def held_list(self):
        try:
            import json as _j
            self.ensure_held_bills_table()
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT vch_id,customer,grand_total,items,created_at "
                    "FROM held_bills ORDER BY created_at").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d['items'] = _j.loads(d.get('items') or '[]')
                except Exception:
                    d['items'] = []
                out.append({k: d[k] for k in
                            ('vch_id', 'customer', 'grand_total', 'items')})
            return {'status': 'success', 'bills': out}
        except Exception as e:
            _dberr(f"[HELD] list: {e}")
            return {'status': 'error', 'message': str(e), 'bills': []}

    def held_delete(self, vch_id):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM held_bills WHERE vch_id=?",
                             (str(vch_id or '').strip(),))
                conn.commit()
            return {'status': 'success'}
        except Exception as e:
            _dberr(f"[HELD] delete: {e}")
            return {'status': 'error', 'message': str(e)}

    # ── UCHAK ITEM LOOKUP (uchak_stock_entry.html autocomplete) ───────
    # The page blurs the code field and expects
    # {status:'success', it_name, rate}. Sources, in order: last inward
    # batch using that code (freshest price), opening-stock row (the same
    # source get_uchak_price_by_code reads), product master name.

    def get_uchak_item_details(self, it_code):
        try:
            code = str(it_code or '').strip().upper()
            if not code:
                return {'status': 'error', 'message': 'Code is required.'}
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT it_name, price FROM uchak_inward_items "
                    "WHERE UPPER(TRIM(it_code))=? ORDER BY id DESC LIMIT 1",
                    (code,)).fetchone()
                if row and (row['it_name'] or row['price']):
                    return {'status': 'success', 'it_code': code,
                            'it_name': row['it_name'] or code,
                            'rate': float(row['price'] or 0)}
            price = self.get_uchak_price_by_code(code)
            if price.get('found'):
                return {'status': 'success', 'it_code': code,
                        'it_name': price.get('it_name') or code,
                        'rate': float(price.get('price') or 0)}
            with self._get_connection() as conn:
                pm = conn.execute(
                    "SELECT name FROM product_master WHERE UPPER(TRIM(code))=?"
                    " LIMIT 1", (code,)).fetchone()
                if pm:
                    return {'status': 'success', 'it_code': code,
                            'it_name': pm['name'] or code, 'rate': 0}
            return {'status': 'error', 'message': f'{code} not found.'}
        except Exception as e:
            _dberr(f"[UCHAK LOOKUP] {e}")
            return {'status': 'error', 'message': str(e)}

    # ── FINANCE (double-entry cash/bank + chart of accounts) ─────────
    # Screens: cash_bank.html (receipts/payments/contra + daybook),
    # accounting.html (position + daybook + trial balance),
    # chart_of_accounts.html (heads register).
    # One heads table serves both registers: kind='' is a plain head,
    # kind CASH/BANK is a cash or bank account. Vouchers always balance
    # (sum dr == sum cr) or they are refused — that invariant is what
    # keeps the trial balance honest.

    VOUCHER_PREFIXES = {
        'CASH_RCPT': 'CR', 'CASH_PAY': 'CP',
        'BANK_RCPT': 'BR', 'BANK_PAY': 'BP',
        'CONTRA': 'CT', 'JV': 'JV', 'GST_JV': 'GJ',
    }

    def ensure_finance_tables(self):
        try:
            with self._get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS chart_accounts (
                        code            TEXT PRIMARY KEY,
                        name            TEXT NOT NULL,
                        type            TEXT DEFAULT 'ASSET',
                        acc_group       TEXT DEFAULT '',
                        balance_type    TEXT DEFAULT 'Dr',
                        opening_balance REAL DEFAULT 0,
                        is_gst          INTEGER DEFAULT 0,
                        gst_rate        REAL DEFAULT 0,
                        hsn             TEXT DEFAULT '',
                        kind            TEXT DEFAULT '',
                        bank_name       TEXT DEFAULT '',
                        account_no      TEXT DEFAULT '',
                        ifsc            TEXT DEFAULT '',
                        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP);

                    CREATE TABLE IF NOT EXISTS cash_vouchers (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        vch_no    TEXT UNIQUE,
                        type      TEXT DEFAULT '',
                        date      TEXT DEFAULT '',
                        narration TEXT DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP);

                    CREATE TABLE IF NOT EXISTS cash_voucher_lines (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        vch_no       TEXT NOT NULL,
                        account_code TEXT NOT NULL,
                        dr           REAL DEFAULT 0,
                        cr           REAL DEFAULT 0);

                    CREATE INDEX IF NOT EXISTS ix_cash_lines_vch
                        ON cash_voucher_lines(vch_no);
                    CREATE INDEX IF NOT EXISTS ix_cash_lines_acct
                        ON cash_voucher_lines(account_code);
                """)
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(chart_accounts)").fetchall()]
                for _c, _d in (
                        ('kind', "ALTER TABLE chart_accounts ADD COLUMN kind TEXT DEFAULT ''"),
                        ('bank_name', "ALTER TABLE chart_accounts ADD COLUMN bank_name TEXT DEFAULT ''"),
                        ('account_no', "ALTER TABLE chart_accounts ADD COLUMN account_no TEXT DEFAULT ''"),
                        ('ifsc', "ALTER TABLE chart_accounts ADD COLUMN ifsc TEXT DEFAULT ''"),
                        ('is_gst', "ALTER TABLE chart_accounts ADD COLUMN is_gst INTEGER DEFAULT 0"),
                        ('gst_rate', "ALTER TABLE chart_accounts ADD COLUMN gst_rate REAL DEFAULT 0"),
                        ('hsn', "ALTER TABLE chart_accounts ADD COLUMN hsn TEXT DEFAULT ''")):
                    if _c not in cols:
                        conn.execute(_d)
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[FINANCE] tables: {e}")
            return False

    @staticmethod
    def _finance_signed(opening, balance_type, dr, cr):
        try:
            sign = -1.0 if str(balance_type or 'Dr').strip().lower().startswith('cr') else 1.0
        except Exception:
            sign = 1.0
        try:
            return float(opening or 0) * sign + float(dr or 0) - float(cr or 0)
        except (TypeError, ValueError):
            return 0.0

    def _finance_balances(self, conn):
        """Closing signed balance per account code: opening ± posted."""
        bals = {}
        for r in conn.execute("SELECT * FROM chart_accounts").fetchall():
            d = dict(r)
            bals[d['code']] = {'account': d, 'dr': 0.0, 'cr': 0.0}
        for r in conn.execute(
                "SELECT account_code, COALESCE(SUM(dr),0) AS dr, "
                "COALESCE(SUM(cr),0) AS cr FROM cash_voucher_lines "
                "GROUP BY account_code").fetchall():
            if r['account_code'] in bals:
                bals[r['account_code']]['dr'] = float(r['dr'] or 0)
                bals[r['account_code']]['cr'] = float(r['cr'] or 0)
        out = {}
        for code, b in bals.items():
            a = b['account']
            signed = self._finance_signed(a.get('opening_balance'),
                                          a.get('balance_type'), b['dr'], b['cr'])
            out[code] = {'account': a, 'posted_dr': b['dr'],
                         'posted_cr': b['cr'], 'closing': round(signed, 2)}
        return out

    def finance_accounts(self, cash_bank_only=False):
        """Heads register; cash_bank_only limits to CASH/BANK accounts."""
        try:
            self.ensure_finance_tables()
            with self._get_connection() as conn:
                bals = self._finance_balances(conn)
                out = []
                for code in sorted(bals):
                    a = dict(bals[code]['account'])
                    kind = str(a.get('kind') or '').upper()
                    if cash_bank_only and kind not in ('CASH', 'BANK'):
                        continue
                    a['kind'] = kind
                    a['current_balance'] = bals[code]['closing']
                    out.append(a)
                return {'status': 'success', 'accounts': out}
        except Exception as e:
            _dberr(f"[FINANCE] accounts: {e}")
            return {'status': 'error', 'message': str(e), 'accounts': []}

    def finance_save_account(self, data):
        try:
            d = dict(data or {})
            code = str(d.get('code') or '').strip().upper()
            name = str(d.get('name') or '').strip()
            if not code or not name:
                return {'status': 'error',
                        'message': 'Code and Name are required.'}
            self.ensure_finance_tables()
            fields = {'name': name}
            for key in ('type', 'acc_group', 'balance_type', 'bank_name',
                        'account_no', 'ifsc', 'hsn', 'kind'):
                if d.get(key) not in (None, ''):
                    fields[key] = str(d.get(key)).strip()
                    if key == 'kind':
                        fields[key] = fields[key].upper()
            for key in ('opening_balance', 'gst_rate'):
                if d.get(key) not in (None, ''):
                    try:
                        fields[key] = float(d.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
            if 'is_gst' in d:
                try:
                    fields['is_gst'] = 1 if int(d.get('is_gst') or 0) else 0
                except (TypeError, ValueError):
                    fields['is_gst'] = 0
            with self._get_connection() as conn:
                exists = conn.execute(
                    "SELECT code FROM chart_accounts WHERE code=?",
                    (code,)).fetchone()
                if exists:
                    sets = ', '.join(f"{k}=?" for k in fields)
                    conn.execute(f"UPDATE chart_accounts SET {sets} WHERE code=?",
                                 list(fields.values()) + [code])
                else:
                    cols = ['code'] + list(fields.keys())
                    conn.execute(
                        f"INSERT INTO chart_accounts({','.join(cols)}) "
                        f"VALUES({','.join(['?'] * len(cols))})",
                        [code] + list(fields.values()))
                conn.commit()
            return {'status': 'success', 'code': code}
        except Exception as e:
            _dberr(f"[FINANCE] save: {e}")
            return {'status': 'error', 'message': str(e)}

    def finance_delete_account(self, code):
        """Refused while any voucher line points at the head."""
        try:
            code = str(code or '').strip().upper()
            if not code:
                return {'status': 'error', 'message': 'Code is required.'}
            with self._get_connection() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM cash_voucher_lines "
                    "WHERE UPPER(TRIM(account_code))=?", (code,)).fetchone()['c']
                if int(n or 0) > 0:
                    return {'status': 'error',
                            'message': f'{code} has posted entries and cannot be deleted.'}
                conn.execute("DELETE FROM chart_accounts WHERE code=?", (code,))
                conn.commit()
            return {'status': 'success', 'message': f'{code} deleted.'}
        except Exception as e:
            _dberr(f"[FINANCE] delete: {e}")
            return {'status': 'error', 'message': str(e)}

    def finance_delete_all(self):
        """Wipe every head with no posted entries; keep the rest."""
        try:
            self.ensure_finance_tables()
            with self._get_connection() as conn:
                used = {str(r[0] or '').strip().upper() for r in conn.execute(
                    "SELECT DISTINCT UPPER(TRIM(account_code)) "
                    "FROM cash_voucher_lines").fetchall()}
                codes = [r[0] for r in conn.execute(
                    "SELECT code FROM chart_accounts").fetchall()]
                deleted, blocked = 0, 0
                for code in codes:
                    if str(code or '').strip().upper() in used:
                        blocked += 1
                        continue
                    conn.execute("DELETE FROM chart_accounts WHERE code=?", (code,))
                    deleted += 1
                conn.commit()
            return {'status': 'success', 'deleted': deleted, 'blocked': blocked}
        except Exception as e:
            _dberr(f"[FINANCE] delete all: {e}")
            return {'status': 'error', 'message': str(e)}

    def finance_cash_position(self):
        """Cash total, bank total, combined — for the position cards."""
        try:
            res = self.finance_accounts(cash_bank_only=True)
            cash = bank = 0.0
            for a in res.get('accounts', []):
                if a.get('kind') == 'BANK':
                    bank += float(a.get('current_balance') or 0)
                else:
                    cash += float(a.get('current_balance') or 0)
            cash, bank = round(cash, 2), round(bank, 2)
            return {'status': 'success', 'cash': cash, 'bank': bank,
                    'total': round(cash + bank, 2)}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def finance_daybook(self, limit=500, day=None):
        """Today's vouchers newest-first with a one-line Dr/Cr summary."""
        try:
            from datetime import date as _d
            self.ensure_finance_tables()
            day = str(day or _d.today().isoformat())
            try:
                limit = max(1, min(int(limit or 500), 2000))
            except (TypeError, ValueError):
                limit = 500
            with self._get_connection() as conn:
                vchs = conn.execute(
                    "SELECT vch_no, type, date, narration FROM cash_vouchers "
                    "WHERE date=? ORDER BY id DESC LIMIT ?",
                    (day, limit)).fetchall()
                out = []
                for v in vchs:
                    lines = conn.execute(
                        "SELECT account_code, dr, cr FROM cash_voucher_lines "
                        "WHERE vch_no=? ORDER BY id", (v['vch_no'],)).fetchall()
                    summary = ', '.join(
                        f"{l['account_code']}:{float(l['dr'] or 0):.2f}/"
                        f"{float(l['cr'] or 0):.2f}" for l in lines)
                    out.append({'vch_no': v['vch_no'], 'type': v['type'],
                                'date': v['date'], 'narration': v['narration'],
                                'lines': summary})
            return {'status': 'success', 'entries': out}
        except Exception as e:
            _dberr(f"[FINANCE] daybook: {e}")
            return {'status': 'error', 'message': str(e), 'entries': []}

    def finance_trial_balance(self):
        """Every head with its Dr/Cr closing column and the balanced verdict."""
        try:
            self.ensure_finance_tables()
            with self._get_connection() as conn:
                bals = self._finance_balances(conn)
                rows, total_dr, total_cr = [], 0.0, 0.0
                for code in sorted(bals):
                    closing = bals[code]['closing']
                    if abs(closing) < 0.005 and not (
                            bals[code]['posted_dr'] or bals[code]['posted_cr']):
                        continue
                    # A Dr-normal head closes on Dr, Cr-normal on Cr,
                    # flipping side only when the balance goes negative.
                    # worth = balance measured on the normal side: positive
                    # stays home, negative crosses to the other column.
                    try:
                        normal_dr = not str(bals[code]['account'].get('balance_type')
                                            or 'Dr').strip().lower().startswith('cr')
                    except Exception:
                        normal_dr = True
                    worth = round(closing * (1.0 if normal_dr else -1.0), 2)
                    if worth >= 0:
                        dr, cr = (worth, 0.0) if normal_dr else (0.0, worth)
                    else:
                        dr, cr = (0.0, -worth) if normal_dr else (-worth, 0.0)
                    total_dr += dr
                    total_cr += cr
                    rows.append({'code': code,
                                 'name': bals[code]['account'].get('name') or code,
                                 'dr': dr, 'cr': cr})
            total_dr, total_cr = round(total_dr, 2), round(total_cr, 2)
            return {'status': 'success',
                    'trial': {'balanced': abs(total_dr - total_cr) < 0.01,
                              'rows': rows, 'total_dr': total_dr,
                              'total_cr': total_cr}}
        except Exception as e:
            _dberr(f"[FINANCE] trial: {e}")
            return {'status': 'error', 'message': str(e)}

    def finance_next_voucher_no(self, vch_type=''):
        try:
            self.ensure_finance_tables()
            prefix = self.VOUCHER_PREFIXES.get(
                str(vch_type or '').strip().upper(), 'V')
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT vch_no FROM cash_vouchers WHERE vch_no LIKE ? "
                    "ORDER BY id DESC LIMIT 50", (prefix + '-%',)).fetchone()
                best = 0
                for r in conn.execute(
                        "SELECT vch_no FROM cash_vouchers WHERE vch_no LIKE ?",
                        (prefix + '-%',)).fetchall():
                    try:
                        best = max(best, int(str(r[0]).rsplit('-', 1)[-1]))
                    except (TypeError, ValueError):
                        pass
            return {'status': 'success', 'vch_no': f"{prefix}-{best + 1:03d}"}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def finance_post_voucher(self, vch):
        """Post one balanced voucher. Returns its number."""
        try:
            d = dict(vch or {})
            vch_type = str(d.get('type') or '').strip().upper()
            if vch_type not in self.VOUCHER_PREFIXES:
                return {'status': 'error',
                        'message': f"Unknown voucher type '{vch_type}'."}
            lines = [x for x in (d.get('lines') or []) if isinstance(x, dict)]
            if len(lines) < 2:
                return {'status': 'error',
                        'message': 'A voucher needs at least two lines.'}
            clean = []
            for ln in lines:
                code = str(ln.get('account_code') or '').strip().upper()
                if not code:
                    return {'status': 'error',
                            'message': 'Every line needs an account.'}
                try:
                    dr = float(ln.get('dr') or 0)
                    cr = float(ln.get('cr') or 0)
                except (TypeError, ValueError):
                    return {'status': 'error',
                            'message': f'Bad amount on {code}.'}
                if dr < 0 or cr < 0 or (dr > 0 and cr > 0):
                    return {'status': 'error',
                            'message': f'Line {code}: use Dr or Cr, not both.'}
                clean.append((code, round(dr, 2), round(cr, 2)))
            total_dr = round(sum(x[1] for x in clean), 2)
            total_cr = round(sum(x[2] for x in clean), 2)
            if total_dr <= 0 or abs(total_dr - total_cr) > 0.01:
                return {'status': 'error',
                        'message': 'Voucher does not balance '
                                   f'(Dr {total_dr:.2f} vs Cr {total_cr:.2f}).'}
            from datetime import date as _d
            day = str(d.get('date') or _d.today().isoformat())[:10]
            narration = str(d.get('narration') or vch_type)
            self.ensure_finance_tables()
            with self._get_connection() as conn:
                codes = {r[0] for r in conn.execute(
                    "SELECT code FROM chart_accounts").fetchall()}
                missing = sorted({c for c, _, _ in clean} - codes)
                if missing:
                    return {'status': 'error',
                            'message': 'Unknown account: ' + ', '.join(missing)}
                vch_no = self.finance_next_voucher_no(vch_type).get('vch_no')
                # Number raced with another save: bump until free.
                for _ in range(25):
                    exists = conn.execute(
                        "SELECT id FROM cash_vouchers WHERE vch_no=?",
                        (vch_no,)).fetchone()
                    if not exists:
                        break
                    try:
                        stem, num = vch_no.rsplit('-', 1)
                        vch_no = f"{stem}-{int(num) + 1:03d}"
                    except (TypeError, ValueError):
                        vch_no = f"{vch_no}-2"
                conn.execute(
                    "INSERT INTO cash_vouchers(vch_no,type,date,narration) "
                    "VALUES(?,?,?,?)", (vch_no, vch_type, day, narration))
                for code, dr, cr in clean:
                    conn.execute(
                        "INSERT INTO cash_voucher_lines(vch_no,account_code,dr,cr) "
                        "VALUES(?,?,?,?)", (vch_no, code, dr, cr))
                conn.commit()
            return {'status': 'success', 'vch_no': vch_no,
                    'message': f'Voucher {vch_no} posted.'}
        except Exception as e:
            _dberr(f"[FINANCE] post: {e}")
            return {'status': 'error', 'message': str(e)}
