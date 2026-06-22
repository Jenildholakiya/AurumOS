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
                    )
                    CREATE TABLE IF NOT EXISTS sales_history (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        vch_id         TEXT UNIQUE,
                        customer       TEXT,
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

    def is_feature_enabled(self, feature):
        """Plan-based feature gate. Checks app_config 'plan_features'
        (comma list, e.g. 'lan_sync,reports'). Empty/missing = disabled
        by default -- safer than accidentally enabling paid features."""
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT value FROM app_config WHERE key='plan_features'").fetchone()
            enabled = (row['value'] or '').split(',') if row else []
            return feature.strip() in [f.strip() for f in enabled]
        except Exception:
            return False

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
        Call right after inserting a new row in a syncable table.
        Stamps it with this device's id, a fresh local sync_version,
        and local_id = its own rowid (so it keeps its identity even
        after being merged onto another PC with a different autoincrement id).

        pk_col: the column to match on. Most tables use 'id', but a few
        (katti_vouchers, uchak_inward_vouchers) use a TEXT PRIMARY KEY
        like vch_id instead -- pass that column name for those.
        local_id is always stored as SQLite's internal rowid converted
        to an integer string hash isn't needed here -- we just reuse
        sqlite's own rowid via "SELECT rowid" since every table (even
        ones with a TEXT PK) still has an implicit unique rowid.
        """
        try:
            if table_name not in self.SYNC_TABLES:
                return False
            dev_id = self.get_or_create_device_id()
            with self._get_connection() as conn:
                nxt = conn.execute(
                    f"SELECT COALESCE(MAX(sync_version),0)+1 as n FROM {table_name} WHERE device_id=?",
                    (dev_id,)
                ).fetchone()
                next_version = int(nxt['n'] or 1)
                # Use rowid (always present in SQLite, even for TEXT PK tables)
                # as local_id, and match the WHERE clause on whatever pk_col was given.
                rowid_row = conn.execute(
                    f"SELECT rowid FROM {table_name} WHERE {pk_col}=?", (row_id,)
                ).fetchone()
                local_id_val = rowid_row[0] if rowid_row else row_id
                conn.execute(
                    f"UPDATE {table_name} SET device_id=?, sync_version=?, local_id=? WHERE {pk_col}=?",
                    (dev_id, next_version, local_id_val, row_id)
                )
                conn.commit()
            return True
        except Exception as e:
            _dberr(f"[SYNC] stamp_row_for_sync {table_name}: {e}")
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
            cursor = self.conn.cursor()
            # Ensure 'logs' is the correct table name in your local DB
            cursor.execute("SELECT * FROM logs ORDER BY ts DESC")
            # Convert rows to dictionary format
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            _dberr(f"[DB] load_all_logs failed: {str(e)}")
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
        try:
            with self._get_connection() as conn:
                for tbl, rows in (changes or {}).items():
                    if tbl not in self.SYNC_TABLES or not rows:
                        continue
                    count = 0
                    for row in rows:
                        try:
                            # Skip if we already have this exact peer row
                            existing = conn.execute(
                                f"SELECT id FROM {tbl} WHERE device_id=? AND local_id=?",
                                (peer_device_id, row.get('local_id'))
                            ).fetchone()
                            if existing:
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
                                continue

                            cols = [c for c in row.keys() if c != 'id']
                            placeholders = ','.join(['?'] * len(cols))
                            colnames = ','.join(cols)
                            values = [row[c] for c in cols]
                            # Force device_id to the PEER's id (not overwritten by us)
                            if 'device_id' in cols:
                                values[cols.index('device_id')] = peer_device_id
                            if tbl == 'admin_creds' and force_is_owner_zero and 'is_owner' in cols:
                                values[cols.index('is_owner')] = 0

                            conn.execute(
                                f"INSERT OR IGNORE INTO {tbl} ({colnames}) VALUES ({placeholders})",
                                values
                            )
                            count += 1

                            # ── DETAILED TRANSFER LOG ──────────────────────
                            # Plain-English description of exactly what just
                            # moved from the peer into this DB, per row.
                            desc_str = self._describe_synced_row(tbl, row)
                            _dblog(f"[SYNC] RECEIVED from {peer_device_id} -> {tbl}: {desc_str}")
                        except Exception as _re:
                            _dblog(f"[SYNC] apply row skip {tbl}: {_re}")
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
            return {'status': 'success', 'applied': applied}
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
                    "SELECT id, username, COALESCE(is_owner,0) as is_owner FROM admin_creds ORDER BY id ASC"
                ).fetchall()
                return [{
                    "id": r["id"],
                    "username": r["username"],
                    "role": "admin" if r["is_owner"] == 1 else "staff"
                } for r in rows]
        except Exception as e:
            _dblog(f"[STAFF] get_all_staff error: {e}")
            return []

    def add_staff_user(self, username, password):
        try:
            import hashlib as _hl
            u = str(username).strip()
            p = str(password).strip()
            if not u or not p:
                return False, "Username and password are required."
            if len(p) < 4:
                return False, "Password must be at least 4 characters."
            hashed = _hl.sha256(p.encode('utf-8')).hexdigest()
            with self._get_connection() as conn:
                exists = conn.execute(
                    "SELECT COUNT(*) FROM admin_creds WHERE LOWER(TRIM(username))=LOWER(TRIM(?))", (u,)
                ).fetchone()[0]
                if exists > 0:
                    return False, f"Username '{u}' is already taken."
                cur = conn.execute(
                    "INSERT INTO admin_creds (username, password) VALUES (?,?)",
                    (u, hashed)
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

    def add_product_master(self, code, name, category, touch, wastage):
        return self.execute_query(
            "INSERT OR REPLACE INTO product_master (code,name,category,touch,wastage) VALUES (?,?,?,?,?)",
            (code, name, category, touch, float(wastage or 0))
        )

    def get_all_products(self):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT code,name,category,touch,wastage FROM product_master"
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
                         pcs,gr_wt,ls_wt,nt_wt,ghat_wt,touch,wastage,huid,is_tagged,entry_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,date('now'))""",
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
                              str(kwargs.get('huid') or '-'))
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
        try:
            cols = ", ".join([f"{k}=?" for k in data.keys()])
            values = list(data.values()) + [entry_id]
            return self.execute_query(f"UPDATE stock_inventory SET {cols} WHERE id=?", tuple(values))
        except:
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
        # Layer 10: verify session token before katti write
        if not self._verify_session_token():
            _dberr("[KATTI] Session token invalid — save BLOCKED")
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
                        try:
                            ks_id = cursor.execute("SELECT id FROM stock_inventory WHERE tag_id=?",
                                                   (unique_tag,)).fetchone()
                            if ks_id:
                                self.stamp_row_for_sync('stock_inventory', ks_id[0])
                        except Exception as _kse:
                            _dblog(f"[SYNC] stamp skip (katti stock): {_kse}")

                conn.commit()
                try:
                    self.stamp_row_for_sync('katti_vouchers', safe_vch_id, pk_col='vch_id')
                except Exception as _kve:
                    _dblog(f"[SYNC] stamp skip (katti_vouchers): {_kve}")
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
                            "DELETE FROM stock_inventory WHERE TRIM(tag_id)=?", (tag_id,)
                        )
                        print(f"? [DEDUCT] Tagged deleted: tag_id={tag_id}")

                    # -- Case 2: Weight-based (katti / opening stock) ----------
                    # Logic:
                    #   Step 1 - If it_code looks like a number -> treat as touch value
                    #   Step 2 - Else try exact it_code match in stock
                    #   Step 3 - Else try it_code as touch (float parse)
                    #   Step 4 - FIFO multi-row deduction across batches
                    elif gross_wt > 0:

                        touch_val = None
                        use_touch = False

                        # Step 1: Detect if it_code IS a touch value (pure number like 68, 91.6, 76)
                        if it_code:
                            try:
                                parsed = float(it_code)
                                # Valid touch range: 1 - 100
                                if 1.0 <= parsed <= 100.0:
                                    touch_val = parsed
                                    use_touch = True
                            except ValueError:
                                pass

                        # Step 2 & 3: If not touch, try it_code match first,
                        #             then check if that stock row has a touch we can use
                        if not use_touch and it_code:
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
                            if ref and ref['touch']:
                                # Found a stock row -- use its touch for deduction
                                touch_val = float(ref['touch'])
                                use_touch = True
                                print(f"[DEDUCT] it_code={it_code} -> touch={touch_val}")

                        # FIFO touch-based deduction across multiple rows
                        if use_touch and touch_val is not None:
                            # Fetch ALL rows matching this touch, oldest first
                            rows = cursor.execute(
                                """SELECT id, gr_wt, it_code FROM stock_inventory
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
                                    new_wt = round(row_wt - remaining_to_deduct, 3)
                                    cursor.execute(
                                        "UPDATE stock_inventory SET gr_wt=?, nt_wt=? WHERE id=?",
                                        (new_wt, new_wt, row['id'])
                                    )
                                    print(f"[DEDUCT] touch={touch_val} row={row['id']} "
                                          f"it={row['it_code']} "
                                          f"{row_wt}g -> {new_wt}g "
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

    def record_sale(self, vch_id, customer, status, l_fine, coll, f995, dhal, rem, rate, amt, items_json,
                    disc_type='none', disc_touch=0.0, disc_fine=0.0, disc_amount=0.0):
        if self._bastion_block():
            _dberr("[BASTION] Write blocked -- account suspended")
            return False
        # Session token check before sale write
        if not self._verify_session_token():
            _dberr("[SALE] Session token invalid — sale BLOCKED")
            return False
        try:
            safe_vch_id = str(vch_id).strip()
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                try:
                    parsed = json.loads(items_json)
                    is_uchak = any(('amount' in i or 'price' in i) for i in parsed) if isinstance(parsed,
                                                                                                  list) else False
                except:
                    is_uchak = False
                resolved = (
                    'UCHAK_UNPAID' if (status == 'CREDIT' and is_uchak) else
                    'UCHAK_PAID' if (status == 'PAID' and is_uchak) else status
                )
                conn.execute(
                    """INSERT OR REPLACE INTO sales_history
                           (vch_id,customer,status,ledger_fine,collected_fine,fine_995,fine_dhal,
                            remaining_fine,gold_rate,total_amount,items,date,time_stamp,
                            discount_type,discount_touch,discount_fine,discount_amount)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,date('now'),time('now'),?,?,?,?)""",
                    (safe_vch_id, customer, resolved,
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
                     float(disc_amount or 0.0))
                )
                # Remove katti/weight items from stock_inventory on sale
                # NEVER touch OPENING- rows — they are permanent reference stock
                try:
                    parsed_items = json.loads(items_json) if items_json else []
                except Exception:
                    parsed_items = []
                for it in parsed_items:
                    tid = str(it.get('tag_id') or '').strip()
                    if not tid:
                        continue
                    if tid.startswith('OPENING-'):
                        continue  # PERMANENT — never delete opening stock rows
                    if tid.startswith('KATTI-') or tid.startswith('B-'):
                        conn.execute(
                            "DELETE FROM stock_inventory WHERE TRIM(tag_id)=TRIM(?) AND tag_id NOT LIKE 'OPENING-%'",
                            (tid,)
                        )
                        _dblog(f"[SALE] Removed katti stock: {tid}")
                conn.commit()
                sale_row_id = conn.execute("SELECT id FROM sales_history WHERE vch_id=?", (safe_vch_id,)).fetchone()[0]
            try:
                self.stamp_row_for_sync('sales_history', sale_row_id)
            except Exception as _sse:
                _dblog(f"[SYNC] stamp skip (sales_history): {_sse}")
            return True
        except Exception as e:
            print(f"? [DB RECORD SALE ERROR] {e}");
            return False

    def get_bill_details(self, vch_id):
        """Fetch full bill from DB for bill_print.html"""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    """SELECT vch_id, customer, status, date,
                              ledger_fine, collected_fine, fine_995, fine_dhal,
                              remaining_fine, gold_rate, total_amount, items
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
                        'date': row['date'],
                        'status': st,
                        'ledger_fine': float(row['ledger_fine'] or 0),
                        'collected_fine': float(row['collected_fine'] or 0),
                        'fine_995': float(row['fine_995'] or 0),
                        'fine_dhal': float(row['fine_dhal'] or 0),
                        'remaining_fine': float(row['remaining_fine'] or 0),
                        'gold_rate': float(row['gold_rate'] or 0),
                        'total_amount': float(row['total_amount'] or 0),
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

                # Opening stock rows (OPENING- tagged) per touch
                opening = conn.execute("""
                    SELECT ROUND(touch,2) as touch,
                           COALESCE(SUM(gr_wt),0) as wt
                    FROM stock_inventory
                    WHERE tag_id LIKE 'OPENING-%'
                      AND touch IS NOT NULL AND touch > 0
                    GROUP BY ROUND(touch,2)
                """).fetchall()

                # All live stock per touch (includes opening + katti + tagged)
                live = conn.execute("""
                    SELECT ROUND(touch,2) as touch,
                           COALESCE(SUM(gr_wt),0) as wt,
                           COALESCE(COUNT(*),0)   as cnt
                    FROM stock_inventory
                    WHERE touch IS NOT NULL AND touch > 0
                    GROUP BY ROUND(touch,2)
                    ORDER BY touch
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

                open_wt = {round(float(r['touch']), 2): float(r['wt']) for r in opening}
                live_wt = {round(float(r['touch']), 2): float(r['wt']) for r in live}
                live_cnt = {round(float(r['touch']), 2): int(r['cnt']) for r in live}
                kat_wt = {round(float(r['touch']), 2): float(r['wt']) for r in katti}

                # Include ALL touches — even those with 0 opening after stock med
                all_tv = sorted(set(list(open_wt) + list(live_wt) + list(kat_wt) + list(out)))
                result = []
                for tv in all_tv:
                    ow_val = round(open_wt.get(tv, 0), 3)  # opening stock weight
                    lw = round(live_wt.get(tv, 0), 3)  # total live stock
                    kw = round(kat_wt.get(tv, 0), 3)  # katti inward
                    ow = round(out.get(tv, 0), 3)  # sales outward
                    cnt = live_cnt.get(tv, 0)
                    _dblog(f"[STOCKSYNC] touch={tv} opening={ow_val} live={lw} katti={kw} sold={ow}")
                    result.append({
                        'touch': tv,
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

    @staticmethod
    def _machine_fingerprint() -> str:
        """
        Hardware DNA — 5 hardware components hashed into one fingerprint.
        CACHED after first call so WMIC never runs twice.
        This is the core fix for unlock key mismatch — fingerprint
        must be identical at lock-time and unlock-time.
        """
        if DBManager._FP_CACHE is not None:
            return DBManager._FP_CACHE

        import uuid as _uuid, hashlib as _hl, subprocess as _sp

        def _wmic(query):
            try:
                out = _sp.check_output(
                    'wmic ' + query + ' get /value',
                    shell=True, timeout=5,
                    stderr=_sp.DEVNULL,
                    creationflags=0x08000000
                ).decode('ascii', errors='ignore')
                vals = [
                    l.split('=', 1)[1].strip()
                    for l in out.splitlines()
                    if '=' in l and l.split('=', 1)[1].strip()
                       and l.split('=', 1)[1].strip() not in
                       ('', 'None', 'To Be Filled By O.E.M.', 'Default string')
                ]
                return vals[0].strip() if vals else ''
            except Exception:
                return ''

        mac = str(_uuid.getnode())
        cpu_id = _wmic('cpu get ProcessorId')
        disk_id = _wmic('diskdrive get SerialNumber')
        bios_id = _wmic('bios get SerialNumber')
        board_id = _wmic('baseboard get SerialNumber')

        raw = '|'.join([mac, cpu_id, disk_id, bios_id, board_id])
        fp = _hl.sha256(raw.encode('utf-8')).hexdigest()[:24]

        DBManager._FP_CACHE = fp

        _dblog(
            f"[DNA] mac={mac[:6]}.. cpu={cpu_id[:6]}.. "
            f"disk={disk_id[:6]}.. bios={bios_id[:6]}.. "
            f"board={board_id[:6]}.. fp={fp[:8]}.."
        )
        return fp

    def _wipe_business_data(self):
        """
        Called when DB is detected on a new/different PC.
        Wipes ALL business data but keeps app structure intact.
        After wipe: DB is fresh — new PC must go through setup again.
        """
        _dblog("[WIPE] MAC mismatch — wiping business data for new PC")
        try:
            with self._get_connection() as conn:
                # ── Wipe all transaction & stock tables ──
                tables = [
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
                _dblog("[WIPE] complete — DB is fresh for new PC setup")
        except Exception as e:
            _dberr(f"[WIPE] error: {e}")

    def is_setup_done(self) -> bool:
        """
        Returns True only if:
          1. setup_done = '1' in app_config  (setup was completed)
          2. machine_fingerprint in DB matches THIS machine's MAC hash
             (so DB copied to new PC → fingerprint mismatch → show setup)
        """
        try:
            fp = self._machine_fingerprint()
            with self._get_connection() as conn:
                rows = {
                    r['key']: r['value']
                    for r in conn.execute(
                        "SELECT key, value FROM app_config WHERE key IN ('setup_done','machine_fingerprint')"
                    ).fetchall()
                }
            done = rows.get('setup_done') == '1'
            stored_fp = rows.get('machine_fingerprint', '')

            _dblog(f"[SETUP] done={done} stored_fp={stored_fp[:8]}... my_fp={fp[:8]}...")

            if not done:
                _dblog("[SETUP] setup_done != 1 -> show setup")
                return False

            if not stored_fp:
                _dblog("[SETUP] no fingerprint stored -> new install -> show setup")
                return False

            if stored_fp != fp:
                _dblog("[SETUP] app_config fingerprint mismatch -> DB copied to new PC -> WIPING")
                self._wipe_business_data()
                return False

            # Second layer: check mac_lock table inside DB
            try:
                lock_row = conn.execute(
                    "SELECT fingerprint FROM mac_lock WHERE id=1"
                ).fetchone()
                if lock_row:
                    lock_fp = lock_row['fingerprint'] if hasattr(lock_row, 'keys') else lock_row[0]
                    if lock_fp and lock_fp != fp:
                        _dblog("[SETUP] mac_lock mismatch -> DB copied to new PC -> WIPING")
                        self._wipe_business_data()
                        return False
            except Exception:
                pass  # mac_lock table may not exist on old DBs — skip

            _dblog("[SETUP] all checks passed -> show login")
            return True

        except Exception as e:
            _dberr(f"[SETUP] is_setup_done error: {e}")
            return False

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
                    INSERT OR REPLACE INTO mac_lock(id, fingerprint, locked_at, hostname)
                    VALUES(1, ?, datetime('now'), ?)
                """, (fp, hostname))
                conn.commit()
            _dblog(f"[SETUP] machine_fingerprint stored: {fp[:8]}...")
        except Exception as e:
            _dberr(f"[SETUP] mark_setup_done error: {e}")

    def save_setup(self, business_name, owner_name, owner_phone, city, pin, license_key=''):
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

                # Katti packets (voucher count -- does not reduce)
                katti = conn.execute(
                    "SELECT COALESCE(SUM(total_packets),0) as p FROM katti_vouchers"
                ).fetchone()

                # Untagged physical items (real tag_id, not yet printed/tagged)
                untagged = conn.execute("""
                    SELECT COUNT(*) as p FROM stock_inventory
                    WHERE is_tagged = 0
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','-','undefined','null')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND tag_id NOT LIKE 'OPENING-%'
                      AND gr_wt = 0
                """).fetchone()

                profile = conn.execute("SELECT owner_name FROM business_profile WHERE id=1").fetchone()
                owner = profile['owner_name'] if (profile and profile['owner_name']) else None

                # Net weight = tagged showroom + katti remaining (NOT opening — opening is fixed reference)
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

    def get_stock_ledger(self):
        """Returns tagged stock items for the inventory table -- excludes opening stock."""
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id, it_code, it_name, tag_id, pcs, touch, gr_wt, nt_wt,
                           para_stone_wt, ls_wt, huid, entry_date,
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

            # Build date variants — IST, UTC, local
            now_utc = _dt.utcnow()
            now_ist = now_utc + _td(hours=5, minutes=30)
            now_loc = _dt.now()
            dates = []
            for base in [now_ist, now_utc, now_loc]:
                for delta in [0, -1, 1]:
                    ds = (base + _td(days=delta)).strftime('%Y-%m-%d')
                    if ds not in dates:
                        dates.append(ds)

            _dblog(f'[LOCK] verify entered={entered[:4]}**** lc={lc_list} dates={dates}')

            # Try every combination
            for lc in lc_list:
                for date_str in dates:
                    expected = _hl.sha256(
                        (lc + SALT + date_str).encode('utf-8')
                    ).hexdigest()[:12].upper()
                    _dblog(f'[LOCK] lc={lc} date={date_str} exp={expected[:4]}**** got={entered[:4]}****')
                    if entered == expected:
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
                        _dblog(f'[LOCK] UNLOCKED lc={lc} date={date_str}')
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
                    # Allow phys_wt = 0 (explicitly audited as zero)
                    # Only skip if touch is invalid
                    if touch <= 0:
                        continue
                    if phys_wt < 0:
                        phys_wt = 0
                    tag_id = (
                            'OPENING-WT-'
                            + _dt.now().strftime('%Y%m%d')
                            + '-'
                            + str(int(touch * 10)).zfill(4)
                    )
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_inventory
                            (it_code, it_name, tag_id, pcs,
                             gr_wt, ls_wt, nt_wt,
                             touch, wastage, is_tagged, entry_date)
                        VALUES (?, ?, ?, 0, ?, 0, ?, ?, 0, 0, ?)
                    """, (
                        'OPENING-' + str(int(touch)),
                        'Opening Stock ' + str(touch) + '% - ' + med_date,
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
        Key formula: SHA256(lock_code + BASTION_SALT + IST_date)[:16]
        Date-based (yesterday/today/tomorrow in IST), matching the
        website unlock tool exactly -- no timestamp involved, so there
        is nothing to copy wrong. IST is used explicitly (not server
        local time) so the dev PC, shop PC, and website always agree
        regardless of what timezone each machine is actually set to.
        """
        import hashlib as _hl
        import json as _json
        from datetime import datetime as _dt, timedelta as _td

        BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'  # KEEP SECRET

        try:
            if not lock_code:
                lock_code = self._machine_fingerprint()[:8].upper()
            else:
                lock_code = str(lock_code).strip().upper()

            entered = str(admin_key).strip().upper()

            # Current time in IST, regardless of this machine's local timezone
            now_ist = _dt.utcnow() + _td(hours=5, minutes=30)
            date_candidates = [
                (now_ist - _td(days=1)).strftime('%Y-%m-%d'),
                now_ist.strftime('%Y-%m-%d'),
                (now_ist + _td(days=1)).strftime('%Y-%m-%d'),
            ]

            matched_date = None
            expected = ''
            for date_str in date_candidates:
                candidate = _hl.sha256(
                    (lock_code + BASTION_SALT + date_str).encode('utf-8')
                ).hexdigest()[:16].upper()
                if entered == candidate:
                    matched_date = date_str
                    expected = candidate
                    break
                expected = candidate  # keep last for logging if no match

            _dblog(
                f"[BASTION] lc={lock_code} dates_tried={date_candidates} entered={entered[:4]}**** matched={matched_date}")

            if matched_date:
                # Match — clear suspension completely
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
                        "VALUES('account_locked','0')"
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO app_config(key,value) "
                        "VALUES('login_attempts','0')"
                    )
                    conn.execute(
                        "DELETE FROM app_config WHERE key='exe_hash'"
                    )
                    conn.commit()
                _dblog(f"[BASTION] Suspension CLEARED — date={matched_date}")
                self.add_audit_log(
                    "BASTION Cleared by Admin",
                    f"Admin key matched for date {matched_date}",
                    "ADMIN", "security"
                )
                return {'status': 'success', 'message': 'Account restored successfully'}

            _dblog("[BASTION] Admin key did not match")
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

            record = {
                'suspended': True,
                'attack_type': code_name,
                'title': title,
                'reason': reason,
                'detail': str(detail),
                'timestamp': ts,
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
                conn.commit()

            _dberr(f"[BASTION] SUSPENDED — attack={attack_type} detail={detail}")

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
                    "('bastion_suspended','bastion_record','lock_code_cache')"
                ).fetchall()}

            suspended = rows.get('bastion_suspended', '0') == '1'
            if not suspended:
                return {'suspended': False}

            record = {}
            try:
                record = _json.loads(rows.get('bastion_record', '{}'))
            except Exception:
                pass

            lock_code = rows.get('lock_code_cache', '') or self._machine_fingerprint()[:8].upper()
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