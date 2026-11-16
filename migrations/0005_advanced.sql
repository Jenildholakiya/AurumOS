-- Migration 0005: Advanced — Multi-branch register

CREATE TABLE IF NOT EXISTS branch_register (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_name TEXT NOT NULL,
    branch_code TEXT UNIQUE,
    city        TEXT DEFAULT '',
    owner_name  TEXT DEFAULT '',
    last_sync   TEXT DEFAULT '',
    summary     TEXT DEFAULT '{}',
    active      INTEGER DEFAULT 1,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
