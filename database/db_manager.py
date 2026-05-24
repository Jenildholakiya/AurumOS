import sqlite3
import os
import random
import json
from datetime import datetime


class DBManager:
    def __init__(self):
        self.db_dir  = os.path.join(os.getcwd(), 'database')
        self.db_path = os.path.join(self.db_dir, 'aurum_local.db')
        if not os.path.exists(self.db_dir):
            os.makedirs(self.db_dir)
        self.initialize_tables()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
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

                kv_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(katti_vouchers)").fetchall()]
                for col, defn in [('total_packets','INTEGER DEFAULT 0'),('total_pcs','INTEGER DEFAULT 0'),
                                   ('touch','REAL DEFAULT 0.00'),('box_id','TEXT DEFAULT NULL')]:
                    if col not in kv_cols:
                        cursor.execute(f"ALTER TABLE katti_vouchers ADD COLUMN {col} {defn}")

                kvi_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(katti_voucher_items)").fetchall()]
                if 'it_code' not in kvi_cols:
                    cursor.execute("ALTER TABLE katti_voucher_items ADD COLUMN it_code TEXT DEFAULT ''")

                si_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(stock_inventory)").fetchall()]
                for col, defn in [('vch_reference','TEXT'),('entry_date',"DATE DEFAULT (date('now'))"),
                                   ('wastage','REAL DEFAULT 0.00')]:
                    if col not in si_cols:
                        cursor.execute(f"ALTER TABLE stock_inventory ADD COLUMN {col} {defn}")

                sh_cols = [r['name'] for r in cursor.execute("PRAGMA table_info(sales_history)").fetchall()]
                if 'status' not in sh_cols:
                    cursor.execute("ALTER TABLE sales_history ADD COLUMN status TEXT DEFAULT 'CREDIT'")
                # Discount columns — added in v2
                for col, defn in [
                    ('discount_type',   "TEXT DEFAULT 'none'"),
                    ('discount_touch',  'REAL DEFAULT 0.0'),
                    ('discount_fine',   'REAL DEFAULT 0.0'),
                    ('discount_amount', 'REAL DEFAULT 0.0'),
                ]:
                    if col not in sh_cols:
                        cursor.execute(f"ALTER TABLE sales_history ADD COLUMN {col} {defn}")

                conn.commit()
            return True
        except Exception as e:
            print(f"❌ [DB INIT ERROR] {e}")
            return False

    def generate_unique_tag_id(self):
        while True:
            new_id = "".join([str(random.randint(0,9)) for _ in range(10)])
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
                conn.execute(query, params); conn.commit()
            return True
        except: return False

    def fetch_one(self, query, params=()):
        try:
            with self._get_connection() as conn:
                res = conn.execute(query, params).fetchone()
                return dict(res) if res else None
        except: return None

    def get_scalar(self, query, params=()):
        try:
            with self._get_connection() as conn:
                res = conn.execute(query, params).fetchone()
                return res[0] if res else 0
        except: return 0

    def is_setup_complete(self):
        return self.get_scalar("SELECT COUNT(*) FROM admin_creds") > 0

    def authenticate_user(self, username, password):
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT id, username FROM admin_creds WHERE username=? AND password=?",
                    (str(username).strip(), str(password).strip())
                ).fetchone()
                if row:
                    return {"authenticated": True, "role": "admin" if row["id"]==1 else "staff"}
                return {"authenticated": False, "role": "visitor"}
        except Exception as e:
            print(f"❌ [DB AUTH ERROR] {e}")
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
                    conn.execute("INSERT INTO admin_creds (username,password) VALUES (?,?)", (username,password))
                else:
                    conn.execute("UPDATE admin_creds SET password=? WHERE LOWER(username)=LOWER(?)", (password,username))
                conn.commit()
            return True
        except Exception as e:
            print(f"❌ [SETUP ERROR] {e}"); return False

    def get_all_staff(self):
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT id, username FROM admin_creds ORDER BY id ASC").fetchall()
                return [{"username": r["username"], "role": "admin" if r["id"]==1 else "staff"} for r in rows]
        except: return []

    def add_staff_user(self, username, password):
        try:
            u, p = str(username).strip(), str(password).strip()
            if not u or not p: return False, "Required fields missing."
            with self._get_connection() as conn:
                if conn.execute("SELECT COUNT(*) FROM admin_creds WHERE LOWER(username)=LOWER(?)", (u,)).fetchone()[0] > 0:
                    return False, "Username already taken."
                conn.execute("INSERT INTO admin_creds (username,password) VALUES (?,?)", (u,p))
                conn.commit()
            return True, f"User '{u}' registered successfully."
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
        except: return False

    def add_category(self, code, name):
        return self.execute_query("INSERT INTO categories (code,name) VALUES (?,?)", (code,name))

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
        except: return []

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
        except: return []

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
            print(f"❌ [WEIGHT STOCK IT CODES ERROR] {e}"); return []


    def delete_master_entry(self, data_type, entry_id):
        table_map = {
            'category':      ('categories',     'id'),
            'touch':         ('touch_groups',   'id'),
            'product':       ('product_master', 'code'),
            'stock':         ('stock_inventory','id'),
            'client':        ('clients_master', 'id'),
            'client_ledger': ('credit_ledger',  'id'),
            'admin_creds':   ('admin_creds',    'username')
        }
        try:
            table, col = table_map[data_type]
            return self.execute_query(f"DELETE FROM {table} WHERE {col}=?", (entry_id,))
        except: return False

    def add_stock_entry(self, **kwargs):
        try:
            tag_id = kwargs.get('tag_id')
            if not tag_id or tag_id in ("N/A","undefined","---","-",""):
                tag_id = self.generate_unique_tag_id()
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO stock_inventory
                        (it_code,it_name,tag_id,pkg_wt,para_stone_wt,size,design,
                         pcs,gr_wt,ls_wt,nt_wt,ghat_wt,touch,wastage,huid,is_tagged,entry_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,date('now'))""",
                    (str(kwargs.get('it_code','')).strip(),
                     str(kwargs.get('it_name','')),
                     str(tag_id),
                     float(kwargs.get('pkg_wt') or 0.0),
                     float(kwargs.get('para_stone_wt') or 0.0),
                     str(kwargs.get('size','-')),
                     str(kwargs.get('design','-')),
                     int(kwargs.get('pcs') or 1),
                     float(kwargs.get('gr_wt') or 0.0),
                     float(kwargs.get('ls_wt') or 0.0),
                     float(kwargs.get('nt_wt') or 0.0),
                     float(kwargs.get('ghat_wt') or 0.0),
                     float(kwargs.get('touch') or 0.0),
                     float(kwargs.get('wastage') or 0.0),
                     str(kwargs.get('huid','-')))
                )
                conn.commit()
            return True
        except Exception as e:
            print(f"❌ [DB STOCK ENTRY ERROR] {e}"); return False

    def update_stock_entry(self, entry_id, data):
        try:
            cols   = ", ".join([f"{k}=?" for k in data.keys()])
            values = list(data.values()) + [entry_id]
            return self.execute_query(f"UPDATE stock_inventory SET {cols} WHERE id=?", tuple(values))
        except: return False

    def mark_as_tagged(self, item_id):
        return self.execute_query("UPDATE stock_inventory SET is_tagged=1 WHERE id=?", (item_id,))

    def add_client(self, name, phone, metal_limit, cash_limit):
        return self.execute_query(
            "INSERT INTO clients_master (name,phone,metal_limit,cash_limit) VALUES (?,?,?,?)",
            (name, phone, float(metal_limit or 0), float(cash_limit or 0))
        )

    def get_all_clients(self):
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM clients_master ORDER BY name ASC").fetchall()]

    def update_client_limits(self, data):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE clients_master SET metal_limit=?,cash_limit=? WHERE id=?",
                    (float(data.get('metal_limit',0)), float(data.get('cash_limit',0)), int(data.get('id')))
                )
                conn.commit()
            return True
        except: return False

    def post_ledger_entry(self, **kwargs):
        return self.execute_query(
            """INSERT INTO credit_ledger
                   (client_name,vch_reference,description,metal_dr,metal_cr,cash_dr,cash_cr,gold_rate)
               VALUES (?,?,?,?,?,?,?,?)""",
            (kwargs.get('client_name'), kwargs.get('vch_id'), kwargs.get('desc'),
             float(kwargs.get('metal_dr') or 0), float(kwargs.get('metal_cr') or 0),
             float(kwargs.get('cash_dr')  or 0), float(kwargs.get('cash_cr')  or 0),
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
                return {"metal": round(res['mb'] or 0,3), "cash": round(res['cb'] or 0,2)}
        except: return {"metal":0,"cash":0}

    def get_next_vch_id(self):
        try:
            with self._get_connection() as conn:
                res = conn.execute("SELECT MAX(CAST(vch_id AS INTEGER)) FROM katti_vouchers").fetchone()
                return str(int(res[0])+1).zfill(4) if (res and res[0] is not None) else "0001"
        except: return "0001"

    def save_katti_batch(self, vch_id, total_wt, total_packets, note="", items=None, box_id=None):
        items = items or []
        try:
            safe_vch_id = str(vch_id).strip().zfill(4)
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                cursor = conn.cursor()

                touch_values  = [float(i.get('touch',0)) if isinstance(i,dict) else 0.0 for i in items]
                valid_touches = [t for t in touch_values if t > 0]
                avg_touch_val = sum(valid_touches)/len(valid_touches) if valid_touches else 0.0

                resolved_box_id = box_id
                if not resolved_box_id:
                    for item in items:
                        if isinstance(item, dict):
                            c = str(item.get('box') or '').strip()
                            if c and c not in ('','-','None','N/A'):
                                resolved_box_id = c; break

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
                        item_code  = str(item.get('it_code') or '').strip()
                        item_name  = str(item.get('name','') or item.get('it_name','')).strip()
                        item_touch = float(item.get('touch') or 0.0)
                        item_wt    = float(item.get('weight') or item.get('nt_wt') or 0.0)
                        item_pcs   = int(item.get('packets') or item.get('pcs') or 1)
                        raw_box    = str(item.get('box') or resolved_box_id or '').strip()
                        item_box   = raw_box if raw_box not in ('','-','None','N/A') else "B-001"
                    else:
                        item_code=''; item_name=str(item).strip()
                        item_touch=0.0; item_wt=0.0; item_pcs=1
                        item_box=resolved_box_id or "B-001"

                    cursor.execute(
                        "INSERT INTO katti_voucher_items (vch_id,it_code,it_name,nt_wt,touch,huid,pcs) VALUES (?,?,?,?,?,?,?)",
                        (safe_vch_id, item_code, item_name, item_wt, item_touch, item_box, item_pcs)
                    )

                    if item_code and item_wt > 0:
                        existing = cursor.execute(
                            """SELECT id, gr_wt FROM stock_inventory
                               WHERE TRIM(it_code)=?
                                 AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A'
                                      OR tag_id LIKE 'KATTI-%')
                               LIMIT 1""",
                            (item_code,)
                        ).fetchone()

                        if existing:
                            new_wt = round((existing['gr_wt'] or 0) + item_wt, 3)
                            cursor.execute(
                                "UPDATE stock_inventory SET gr_wt=?,nt_wt=?,it_name=?,touch=?,huid=?,vch_reference=? WHERE id=?",
                                (new_wt, new_wt, item_name, item_touch, item_box, safe_vch_id, existing['id'])
                            )
                            print(f"✅ [KATTI] Stock updated: {item_code} +{item_wt}g → {new_wt}g")
                        else:
                            unique_tag = f"KATTI-{safe_vch_id}-{item_code}"
                            cursor.execute(
                                """INSERT OR REPLACE INTO stock_inventory
                                       (it_code,it_name,tag_id,pcs,gr_wt,ls_wt,nt_wt,
                                        touch,wastage,is_tagged,vch_reference,huid,entry_date)
                                   VALUES (?,?,?,0,?,0,?,?,0,0,?,?,date('now'))""",
                                (item_code, item_name, unique_tag, item_wt, item_wt,
                                 item_touch, safe_vch_id, item_box)
                            )
                            print(f"✅ [KATTI] Stock created: {item_code} tag={unique_tag} wt={item_wt}g box={item_box}")

                conn.commit()
                return True
        except Exception as e:
            print(f"❌ [KATTI SAVE ERROR] {e}"); return False

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
                        "SELECT it_code,it_name,nt_wt,touch,huid,pcs FROM stock_inventory WHERE vch_reference=?", (vch_id,)
                    ).fetchall()
                return {"voucher": dict(vch), "items": [dict(i) for i in items]}
        except Exception as e:
            print(f"❌ History Exception: {e}"); return None

    def get_last_uchak_inward_vch_id(self):
        try:
            with self._get_connection() as conn:
                res = conn.execute(
                    "SELECT vch_id FROM uchak_inward_vouchers ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                if res and res['vch_id']:
                    return f"UCHK-IN-{(int(res['vch_id'].split('-')[-1])+1):03d}"
                return "UCHK-IN-001"
        except: return "UCHK-IN-001"

    def save_uchak_inward_transaction(self, vch_id, total_lines, total_pcs, total_value, items_list):
        try:
            safe_vch_id = str(vch_id).strip()
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                cursor = conn.cursor()

                old_items = cursor.execute(
                    "SELECT it_code,pcs FROM uchak_inward_items WHERE vch_id=?", (safe_vch_id,)
                ).fetchall()
                for old in old_items:
                    old_code = str(old['it_code']).strip(); old_pcs = int(old['pcs'] or 0)
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
                    code  = str(item.get('it_code','')).strip()
                    name  = str(item.get('it_name','')).strip()
                    pcs   = int(item.get('pcs') or 1)
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
                            ((existing['pcs'] or 0)+pcs, name, str(price), existing['id'])
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
                return True
        except Exception as e:
            print(f"❌ [DB UCHAK INWARD SAVE ERROR] {e}"); return False

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
        except: return None

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
                                 ((existing['pcs'] or 0)+pcs, it_name, str(price), existing['id']))
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
        except: return False

    # ─── STOCK DEDUCTION ─────────────────────────────────────────────────────
    def deduct_stock_after_sale(self, items_json):
        """
        ✅ PERMANENT FIX:
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
                    tag_id   = str(item.get('tag_id') or '').strip()
                    it_code  = str(item.get('it_code') or item.get('code') or '').strip()
                    gross_wt = float(item.get('weight') or item.get('gr_wt') or 0.0)
                    sold_pcs = int(item.get('pcs') or 1)

                    # ── Case 1: Physical tag (10-digit, NOT KATTI- prefix) ────
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
                        print(f"✅ [DEDUCT] Tagged deleted: tag_id={tag_id}")

                    # ── Case 2: Weight-based (katti stock) ────────────────────
                    elif gross_wt > 0:
                        row = None

                        # Try exact it_code match
                        if it_code:
                            row = cursor.execute(
                                """SELECT id, gr_wt FROM stock_inventory
                                   WHERE TRIM(it_code)=?
                                     AND (tag_id IS NULL OR tag_id='' OR
                                          tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                                   ORDER BY id ASC LIMIT 1""",
                                (it_code,)
                            ).fetchone()

                        # Fallback: it_code is touch value (e.g. "76")
                        if not row and it_code:
                            try:
                                touch_val = float(it_code)
                                row = cursor.execute(
                                    """SELECT id, gr_wt FROM stock_inventory
                                       WHERE touch=?
                                         AND (tag_id IS NULL OR tag_id='' OR
                                              tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                                       ORDER BY id ASC LIMIT 1""",
                                    (touch_val,)
                                ).fetchone()
                                if row:
                                    print(f"✅ [DEDUCT] Touch fallback: touch={touch_val}")
                            except ValueError:
                                pass

                        if row:
                            remaining = round((row['gr_wt'] or 0) - gross_wt, 3)
                            if remaining <= 0.001:
                                cursor.execute("DELETE FROM stock_inventory WHERE id=?", (row['id'],))
                                print(f"✅ [DEDUCT] Exhausted & deleted: {it_code}")
                            else:
                                cursor.execute(
                                    "UPDATE stock_inventory SET gr_wt=?,nt_wt=? WHERE id=?",
                                    (remaining, remaining, row['id'])
                                )
                                print(f"✅ [DEDUCT] Weight reduced: {it_code} → {remaining}g")
                        else:
                            print(f"⚠️  [DEDUCT] No stock found for it_code/touch={it_code}")

                    # ── Case 3: Piece-based (uchak) ───────────────────────────
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
                                    print(f"✅ [DEDUCT] Uchak exhausted & deleted: {piece_code} -{sold_qty}pcs")
                                else:
                                    cursor.execute(
                                        "UPDATE stock_inventory SET pcs=? WHERE id=?",
                                        (rem, row['id'])
                                    )
                                    print(f"✅ [DEDUCT] Uchak pcs reduced: {piece_code} -{sold_qty}pcs → {rem}pcs remaining")
                            else:
                                print(f"⚠️  [DEDUCT] Uchak stock not found for code: {piece_code}")

                conn.commit()
                return True
        except Exception as e:
            print(f"❌ [DB STOCK DEDUCTION ERROR] {e}")
            return False

    def record_sale(self, vch_id, customer, status, l_fine, coll, f995, dhal, rem, rate, amt, items_json,
                    disc_type='none', disc_touch=0.0, disc_fine=0.0, disc_amount=0.0):
        try:
            safe_vch_id = str(vch_id).strip()
            with self._get_connection() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                try:
                    parsed = json.loads(items_json)
                    is_uchak = any(('amount' in i or 'price' in i) for i in parsed) if isinstance(parsed,list) else False
                except: is_uchak = False
                resolved = (
                    'UCHAK_UNPAID' if (status=='CREDIT' and is_uchak) else
                    'UCHAK_PAID'   if (status=='PAID'   and is_uchak) else status
                )
                conn.execute(
                    """INSERT OR REPLACE INTO sales_history
                           (vch_id,customer,status,ledger_fine,collected_fine,fine_995,fine_dhal,
                            remaining_fine,gold_rate,total_amount,items,date,time_stamp,
                            discount_type,discount_touch,discount_fine,discount_amount)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,date('now'),time('now'),?,?,?,?)""",
                    (safe_vch_id, customer, resolved,
                     0.0 if is_uchak else float(l_fine or 0),
                     0.0 if is_uchak else float(coll   or 0),
                     0.0 if is_uchak else float(f995   or 0),
                     0.0 if is_uchak else float(dhal   or 0),
                     0.0 if is_uchak else float(rem    or 0),
                     0.0 if is_uchak else float(rate   or 0),
                     float(amt or 0), items_json,
                     str(disc_type or 'none'),
                     float(disc_touch or 0.0),
                     float(disc_fine  or 0.0),
                     float(disc_amount or 0.0))
                )
                conn.commit()
            return True
        except Exception as e:
            print(f"❌ [DB RECORD SALE ERROR] {e}"); return False


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
                    return {'status':'error','message':f'Voucher {vch_id} not found'}
                try:
                    items = json.loads(row['items'] or '[]')
                except:
                    items = []
                st        = row['status'] or ''
                is_uchak  = 'UCHAK' in st.upper()
                is_credit = 'CREDIT' in st.upper() or 'UDHAR' in st.upper()
                return {
                    'status':  'success',
                    'voucher': {
                        'vch_id':         row['vch_id'],
                        'customer':       row['customer'],
                        'date':           row['date'],
                        'status':         st,
                        'ledger_fine':    float(row['ledger_fine']    or 0),
                        'collected_fine': float(row['collected_fine'] or 0),
                        'fine_995':       float(row['fine_995']       or 0),
                        'fine_dhal':      float(row['fine_dhal']      or 0),
                        'remaining_fine': float(row['remaining_fine'] or 0),
                        'gold_rate':      float(row['gold_rate']      or 0),
                        'total_amount':   float(row['total_amount']   or 0),
                    },
                    'items':     items,
                    'is_uchak':  is_uchak,
                    'is_credit': is_credit,
                }
        except Exception as e:
            print(f'get_bill_details error: {e}')
            return {'status':'error','message':str(e)}

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
        except: return []

    # ─── DASHBOARD STATS ─────────────────────────────────────────────────────
    def get_dashboard_stats(self):
        """
        ✅ FIXED: weight stock now reads from stock_inventory directly
        (tag_id IN 'N/A', '', NULL, 'KATTI-%') so reduced weights show correctly.
        """
        try:
            with self._get_connection() as conn:
                # Tagged pieces (physical tags, 10-digit)
                showroom = conn.execute("""
                    SELECT COUNT(*) as p, SUM(nt_wt) as w FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                      AND is_tagged=1
                """).fetchone()

                # Uchak piece stock — exclude weight-based rows
                uchak = conn.execute("""
                    SELECT SUM(pcs) as p FROM stock_inventory
                    WHERE (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                      AND pcs > 0
                      AND (gr_wt IS NULL OR gr_wt = 0)
                """).fetchone()

                # ✅ Weight stock — reads LIVE from stock_inventory (shows reduced values)
                weight_stock = conn.execute("""
                    SELECT SUM(gr_wt) as w, COUNT(*) as p FROM stock_inventory
                    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND gr_wt > 0
                """).fetchone()

                # Katti voucher packets count
                katti = conn.execute(
                    "SELECT SUM(total_packets) as p FROM katti_vouchers"
                ).fetchone()

                profile = conn.execute("SELECT owner_name FROM business_profile WHERE id=1").fetchone()
                owner = profile['owner_name'] if (profile and profile['owner_name']) else None

                total_w = (showroom['w'] or 0) + (weight_stock['w'] or 0)

                return {
                    "net":          round(total_w, 3),
                    "pcs":          showroom['p'] or 0,
                    "uchak_pcs":    uchak['p'] or 0,
                    "packets":      katti['p'] or 0,
                    "katti_net":    round(weight_stock['w'] or 0, 3),
                    "owner_name":   owner
                }
        except Exception as e:
            print(f"❌ [DASHBOARD STATS ERROR] {e}")
            return {"net":0,"pcs":0,"uchak_pcs":0,"packets":0,"katti_net":0,"owner_name":None}

    def fetch_stock_ledger_by_date(self, target_date=None):
        if not target_date: target_date = datetime.now().strftime('%Y-%m-%d')
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id,it_code,it_name,pcs,touch,size,design,
                           gr_wt,para_stone_wt,ls_wt,nt_wt,ghat_wt,huid,entry_date,tag_id,
                           CASE WHEN is_tagged=1 THEN '● TAGGED' ELSE '● PENDING' END as live_status
                    FROM stock_inventory
                    WHERE entry_date=?
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                    ORDER BY id DESC
                """, (target_date,)).fetchall()]
        except: return []

    def get_available_ledger_dates(self):
        try:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT DISTINCT entry_date FROM stock_inventory
                    WHERE tag_id NOT IN ('N/A','') AND tag_id NOT LIKE 'KATTI-%'
                    ORDER BY entry_date ASC
                """).fetchall()
                return [r['entry_date'] for r in rows] if rows else [datetime.now().strftime('%Y-%m-%d')]
        except: return [datetime.now().strftime('%Y-%m-%d')]

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
                    return {k: r.get(k,0.0) if k.endswith('_wt') or k in ('touch','wastage','pcs') else r.get(k) for k in r}
                return None
        except: return None

    # ─── ANALYTICS ───────────────────────────────────────────────────────────
    def get_inventory_analytics(self):
        """
        ✅ FIXED: bins now read from stock_inventory (live, reduced weights)
        not from katti_vouchers (which has original weights).
        """
        try:
            with self._get_connection() as conn:

                # ✅ Bin heatmap — read LIVE weight from stock_inventory
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
                    c = str(raw).replace(' ','').replace('-','').upper()
                    return f"B-{c[1:].zfill(3)}" if c.startswith('B') and len(c)>1 else raw

                bin_map={}; items_map={}
                for b in list(bins_si) + list(bins_tagged):
                    k = norm(b['bin_id'])
                    bin_map[k] = bin_map.get(k,0.0) + (b['weight'] or 0.0)
                    try:
                        parsed = json.loads(b['items_json'])
                        if k not in items_map: items_map[k]=[]
                        items_map[k].extend(parsed)
                    except: pass

                bins = [{"bin_id":k,"weight":round(v,3),"items":items_map.get(k,[])}
                        for k,v in sorted(bin_map.items())]

                # Category distribution — weight based (live from stock_inventory)
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

                return {"status":"success","bins":bins,"katti_weight":k_data,
                        "manual_pcs":p_data,"uchak_pcs_chart":u_data}
        except Exception as e:
            print(f"❌ Analytics Error: {e}")
            return {"status":"error","bins":[],"katti_weight":[],"manual_pcs":[],"uchak_pcs_chart":[]}

    def get_stagnant_report(self, threshold=90):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT *, CAST((julianday('now')-julianday(timestamp)) AS INTEGER) as days_old
                    FROM stock_inventory
                    WHERE (julianday('now')-julianday(timestamp)) >= ?
                    ORDER BY days_old DESC
                """, (threshold,)).fetchall()]
        except: return []

    def get_velocity_products(self):
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT it_name, COUNT(*) as qty, SUM(nt_wt) as weight
                    FROM stock_inventory GROUP BY it_name ORDER BY qty DESC LIMIT 5
                """).fetchall()]
        except: return []

    def get_inventory_stats(self):
        """Alias used by inventory.html — returns live reduced stock values."""
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

                # Uchak piece stock — exclude weight-based rows (gr_wt > 0)
                uchak = conn.execute("""
                    SELECT COALESCE(SUM(pcs),0) as p FROM stock_inventory
                    WHERE (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
                      AND pcs > 0
                      AND (gr_wt IS NULL OR gr_wt = 0)
                """).fetchone()

                # ✅ LIVE weight from stock_inventory (reduced after sales)
                weight_stock = conn.execute("""
                    SELECT COALESCE(SUM(gr_wt),0) as w FROM stock_inventory
                    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
                      AND gr_wt > 0
                """).fetchone()

                # Katti packets (voucher count — does not reduce)
                katti = conn.execute(
                    "SELECT COALESCE(SUM(total_packets),0) as p FROM katti_vouchers"
                ).fetchone()

                # Untagged physical items (real tag_id, not yet printed/tagged)
                untagged = conn.execute("""
                    SELECT COUNT(*) as p FROM stock_inventory
                    WHERE is_tagged = 0
                      AND tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                """).fetchone()

                profile = conn.execute("SELECT owner_name FROM business_profile WHERE id=1").fetchone()
                owner = profile['owner_name'] if (profile and profile['owner_name']) else None

                total_w = (showroom['w'] or 0) + (weight_stock['w'] or 0)

                return {
                    "net":            round(total_w, 3),
                    "pcs":            showroom['p'] or 0,
                    "uchak_pcs":      uchak['p'] or 0,
                    "packets":        katti['p'] or 0,
                    "untagged_items": untagged['p'] or 0,
                    "katti_net":      round(weight_stock['w'] or 0, 3),
                    "owner_name":     owner
                }
        except Exception as e:
            print(f"❌ [INVENTORY STATS ERROR] {e}")
            return {"net":0,"pcs":0,"uchak_pcs":0,"packets":0,"untagged_items":0,"katti_net":0,"owner_name":None}

    def get_analytics_payload(self):
        """
        Alias used by inventory.html.
        ✅ FIXED: bins and katti_weight read from stock_inventory (live reduced values).
        """
        try:
            with self._get_connection() as conn:

                # ✅ Bin heatmap — LIVE weight from stock_inventory
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
                    c = str(raw).replace(' ','').replace('-','').upper()
                    return f"B-{c[1:].zfill(3)}" if c.startswith('B') and len(c)>1 else raw

                bin_map = {}; items_map = {}
                for b in list(bins_weight) + list(bins_tagged):
                    k = norm(b['bin_id'])
                    bin_map[k] = round(bin_map.get(k, 0.0) + (b['weight'] or 0.0), 3)
                    try:
                        parsed = json.loads(b['items_json'])
                        if k not in items_map: items_map[k] = []
                        items_map[k].extend(parsed)
                    except: pass

                bins = [{"bin_id": k, "weight": v, "items": items_map.get(k, [])}
                        for k, v in sorted(bin_map.items())]

                # ✅ Category distribution — LIVE weight from stock_inventory
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
                    "status":         "success",
                    "bins":           bins,
                    "katti_weight":   k_data,
                    "manual_pcs":     p_data,
                    "uchak_pcs_chart": u_data
                }
        except Exception as e:
            print(f"❌ [ANALYTICS PAYLOAD ERROR] {e}")
            return {"status":"error","bins":[],"katti_weight":[],"manual_pcs":[],"uchak_pcs_chart":[]}

    def get_stock_ledger(self):
        """Returns tagged stock items for the inventory table."""
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id, it_code, it_name, tag_id, pcs, touch, gr_wt, nt_wt,
                           para_stone_wt, ls_wt, huid, entry_date,
                           CASE WHEN is_tagged=1 THEN '● TAGGED' ELSE '● PENDING' END as live_status
                    FROM stock_inventory
                    WHERE tag_id IS NOT NULL
                      AND tag_id NOT IN ('N/A','','---','undefined','-')
                      AND tag_id NOT LIKE 'KATTI-%'
                    ORDER BY id DESC
                """).fetchall()]
        except Exception as e:
            print(f"❌ [STOCK LEDGER ERROR] {e}"); return []

    def get_opening_stock(self):
        """Returns ONLY opening stock entries.
        Excludes tagged items (real jewelry with proper tag IDs).
        Includes: N/A, KATTI-, OPENING- prefix, or NULL tag_ids."""
        try:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute("""
                    SELECT id, it_code, it_name, tag_id,
                           gr_wt, ls_wt, nt_wt, touch, wastage,
                           entry_date
                    FROM stock_inventory
                    WHERE (
                        tag_id IS NULL OR
                        tag_id = '' OR
                        tag_id = 'N/A' OR
                        tag_id = '---' OR
                        tag_id = '-' OR
                        tag_id LIKE 'KATTI-%' OR
                        tag_id LIKE 'OPENING-%'
                    )
                    AND gr_wt > 0
                    ORDER BY id DESC
                """).fetchall()]
        except Exception as e:
            print(f"❌ [OPENING STOCK ERROR] {e}"); return []

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
            print(f"❌ [UNTAGGED ITEMS ERROR] {e}"); return []

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

                # ── INWARD: katti_voucher_items (weight mode) ───────────────
                if mode == 'weight':
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
                        if not all_touch and round(tv,2) != round(touch_val,2): continue
                        vdt = r.get('vch_dt','')
                        if from_date and vdt < from_date: continue
                        if to_date   and vdt > to_date:   continue
                        wt = float(r.get('gr_wt') or 0)
                        results.append({
                            'txn_type':'IN','touch_val':tv,
                            'book_name':'Katti Inward','vch_no':r.get('vch_id',''),
                            'vch_dt':vdt,'ac_name':'Stock Inward','as_type':'Katti A/c',
                            'sign':'Dr','tag_no':'N/A','huid':r.get('huid') or '—',
                            'it_code':r.get('it_code',''),'gr_name':'Weight Gold',
                            'primary_val':r.get('vch_id',''),'it_name':r.get('it_name',''),
                            'variety':'Bulk',
                            'carat':'22K' if tv>=91 else ('18K' if tv>=75 else 'Katti'),
                            'pcs':int(r.get('pcs') or 1),'gr_wt':wt,'ls_wt':0.0,
                            'in_net_wt':wt,'out_net_wt':0.0,'bal_wt':wt,
                        })
                else:
                    # ✅ FIX: PCS Inward = current stock + sold items (reconstructed from sales_history)
                    # Sold items are DELETED from stock_inventory after sale, so we rebuild from sales

                    # Step 1 — Current remaining stock (not yet sold)
                    current_stock = conn.execute("""
                        SELECT it_code, it_name, tag_id, gr_wt, nt_wt, ls_wt,
                               touch, huid, pcs, entry_date AS vch_dt, vch_reference
                        FROM stock_inventory
                        WHERE tag_id IS NOT NULL
                          AND tag_id NOT IN ('N/A','','---','-')
                          AND tag_id NOT LIKE 'KATTI-%'
                    """).fetchall()

                    for r in current_stock:
                        r = dict(r)
                        tv = float(r.get('touch') or 0)
                        if not all_touch and round(tv,2) != round(touch_val,2): continue
                        vdt = r.get('vch_dt','')
                        if from_date and vdt < from_date: continue
                        if to_date   and vdt > to_date:   continue
                        nt = float(r.get('nt_wt') or 0)
                        results.append({
                            'txn_type':'IN','touch_val':tv,
                            'book_name':'Stock Entry','vch_no':r.get('tag_id',''),
                            'vch_dt':vdt,'ac_name':'Stock Inward','as_type':'Stock A/c',
                            'sign':'Dr','tag_no':r.get('tag_id',''),'huid':r.get('huid') or '—',
                            'it_code':r.get('it_code',''),'gr_name':'Ornaments',
                            'primary_val':r.get('vch_reference',''),'it_name':r.get('it_name',''),
                            'variety':'Standard',
                            'carat':'22K' if tv>=91 else ('18K' if tv>=75 else 'Other'),
                            'pcs':int(r.get('pcs') or 1),'gr_wt':float(r.get('gr_wt') or 0),
                            'ls_wt':float(r.get('ls_wt') or 0),
                            'in_net_wt':nt,'out_net_wt':0.0,'bal_wt':nt,
                        })

                    # Step 2 — Already sold tagged pieces (deleted from stock after sale)
                    # Reconstruct from sales_history items where tag_id is a real physical tag
                    sold_sales = conn.execute(
                        "SELECT vch_id, customer, date, items FROM sales_history ORDER BY id ASC"
                    ).fetchall()

                    seen_tags = set()  # avoid duplicate inward entries
                    for sale in sold_sales:
                        svdt = sale['date'] or ''
                        try:   sitems = json.loads(sale['items'] or '[]')
                        except: continue
                        for item in sitems:
                            tag_id = str(item.get('tag_id') or '').strip()
                            # Only real physical tags (not weight/katti)
                            if not tag_id or tag_id in ('','N/A') or tag_id.startswith('KATTI-') or len(tag_id) < 8:
                                continue
                            if tag_id in seen_tags: continue
                            seen_tags.add(tag_id)

                            tv = float(item.get('touch') or 0)
                            if not all_touch and round(tv,2) != round(touch_val,2): continue
                            if from_date and svdt < from_date: continue
                            if to_date   and svdt > to_date:   continue

                            wt = float(item.get('weight') or item.get('gr_wt') or 0)
                            ls = float(item.get('less') or item.get('ls_wt') or 0)
                            results.append({
                                'txn_type':'IN','touch_val':tv,
                                'book_name':'Stock Entry (Sold)','vch_no':tag_id,
                                'vch_dt':svdt,'ac_name':'Stock Inward','as_type':'Stock A/c',
                                'sign':'Dr','tag_no':tag_id,'huid':item.get('huid') or '—',
                                'it_code':item.get('it_code') or item.get('code') or '—',
                                'gr_name':'Ornaments',
                                'primary_val':sale['vch_id'],
                                'it_name':item.get('it_name') or item.get('code') or '—',
                                'variety':'Standard',
                                'carat':'22K' if tv>=91 else ('18K' if tv>=75 else 'Other'),
                                'pcs':1,'gr_wt':wt,'ls_wt':ls,
                                'in_net_wt':wt,'out_net_wt':0.0,'bal_wt':wt,
                            })

                # ── OUTWARD: sales_history ──────────────────────────────────
                sales = conn.execute(
                    "SELECT vch_id, customer, date, items FROM sales_history ORDER BY id ASC"
                ).fetchall()

                for sale in sales:
                    vdt = sale['date'] or ''
                    if from_date and vdt < from_date: continue
                    if to_date   and vdt > to_date:   continue
                    try:   items = json.loads(sale['items'] or '[]')
                    except: continue

                    for item in items:
                        tv = float(item.get('touch') or 0)
                        if not all_touch and round(tv,2) != round(touch_val,2): continue

                        tag_id    = str(item.get('tag_id') or '').strip()
                        is_weight = not tag_id or tag_id in ('','N/A') or tag_id.startswith('KATTI-')
                        if mode == 'weight' and not is_weight: continue
                        if mode == 'pcs'    and is_weight:     continue

                        wt = float(item.get('weight') or item.get('gr_wt') or 0)
                        results.append({
                            'txn_type':'OUT','touch_val':tv,
                            'book_name':'Sales Bill','vch_no':sale['vch_id'],
                            'vch_dt':vdt,'ac_name':sale['customer'],'as_type':'Sales A/c',
                            'sign':'Cr','tag_no':tag_id or 'N/A','huid':item.get('huid') or '—',
                            'it_code':item.get('it_code') or item.get('code') or '—',
                            'gr_name':'Weight Gold' if is_weight else 'Ornaments',
                            'primary_val':sale['vch_id'],
                            'it_name':item.get('it_name') or item.get('code') or '—',
                            'variety':'Bulk' if is_weight else 'Standard',
                            'carat':'22K' if tv>=91 else ('18K' if tv>=75 else 'Katti'),
                            'pcs':int(item.get('pcs') or 1),'gr_wt':wt,
                            'ls_wt':float(item.get('less') or 0),
                            'in_net_wt':0.0,'out_net_wt':wt,'bal_wt':wt,
                        })

                return results

        except Exception as e:
            print(f"❌ [TOUCH LEDGER DETAILS ERROR] {e}"); return []

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
            print(f"❌ [LOW STOCK ERROR] {e}"); return []

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
                    except: pass

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
                        'gr_wt':   float(r['total_wt'] or 0)
                    }

                # OUT OF STOCK = ever sold OR ever in katti, but now gr_wt <= 0.001
                all_known = ever_sold | ever_katti
                out_of_stock = []
                for code in sorted(all_known):
                    info  = current_stock.get(code, {})
                    remaining = info.get('gr_wt', 0.0)
                    if remaining <= 0.001:
                        out_of_stock.append({
                            'it_code':     code,
                            'it_name':     info.get('it_name', ''),
                            'original_wt': 0.0,
                            'remaining':   0.0
                        })

                return out_of_stock
        except Exception as e:
            print(f"❌ [OUT OF STOCK ERROR] {e}"); return []