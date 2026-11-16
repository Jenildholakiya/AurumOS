-- Migration 0004: Operations — Purchase Orders, Stock Transfer, Repairs

CREATE TABLE IF NOT EXISTS suppliers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT DEFAULT '',
    gstin       TEXT DEFAULT '',
    city        TEXT DEFAULT '',
    address     TEXT DEFAULT '',
    metal_type  TEXT DEFAULT 'gold',
    notes       TEXT DEFAULT '',
    active      INTEGER DEFAULT 1,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no         TEXT UNIQUE,
    supplier_id   INTEGER,
    date          TEXT DEFAULT (date('now')),
    status        TEXT DEFAULT 'draft',
    items         TEXT DEFAULT '[]',
    total_weight  REAL DEFAULT 0,
    total_fine    REAL DEFAULT 0,
    total_amount  REAL DEFAULT 0,
    cgst          REAL DEFAULT 0,
    sgst          REAL DEFAULT 0,
    notes         TEXT DEFAULT '',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_no     TEXT UNIQUE,
    from_location   TEXT DEFAULT '',
    to_location     TEXT DEFAULT '',
    date            TEXT DEFAULT (date('now')),
    status          TEXT DEFAULT 'pending',
    items           TEXT DEFAULT '[]',
    total_weight    REAL DEFAULT 0,
    total_pcs       INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    approved_by     TEXT DEFAULT '',
    received_by     TEXT DEFAULT '',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS repairs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repair_no       TEXT UNIQUE,
    customer        TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    item_desc       TEXT DEFAULT '',
    metal_type      TEXT DEFAULT 'gold',
    weight          REAL DEFAULT 0,
    repair_type     TEXT DEFAULT 'repair',
    estimated_cost  REAL DEFAULT 0,
    actual_cost     REAL DEFAULT 0,
    advance         REAL DEFAULT 0,
    status          TEXT DEFAULT 'received',
    received_date   TEXT DEFAULT (date('now')),
    promised_date   TEXT DEFAULT '',
    completed_date  TEXT DEFAULT '',
    delivered_date  TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    karigar_id      INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
