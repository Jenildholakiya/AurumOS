-- DPDP Act 2023 Compliance Schema
-- Addresses: Consent (S.6), Privacy Notice (S.5), Data Principal Rights (S.11-14),
--            Purpose Limitation (S.4), Retention (S.8(7)), Breach Notification (S.8(6)),
--            DPO/Grievance (S.8), Cross-Border (S.16)

-- Gap 1 & 2: Consent records + privacy notice acknowledgment
CREATE TABLE IF NOT EXISTS dpdp_consent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id    TEXT NOT NULL,          -- machine fingerprint or user identifier
    principal_name  TEXT DEFAULT '',
    purpose         TEXT NOT NULL,          -- e.g. 'license_validation','telemetry','behavioral_monitoring','crm','backup'
    notice_version  TEXT NOT NULL,          -- version of privacy notice shown
    consent_given   INTEGER NOT NULL DEFAULT 0,  -- 1=yes, 0=no
    given_at        TEXT NOT NULL,          -- ISO timestamp
    withdrawn_at    TEXT DEFAULT NULL,      -- ISO timestamp if withdrawn
    ip_address      TEXT DEFAULT '',        -- for audit trail
    method          TEXT DEFAULT 'ui',      -- 'ui','api','migration'
    UNIQUE(principal_id, purpose)
);

-- Gap 3: Data principal rights requests
CREATE TABLE IF NOT EXISTS dpdp_rights_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT UNIQUE NOT NULL,   -- UUID for tracking
    principal_id    TEXT NOT NULL,
    principal_name  TEXT DEFAULT '',
    request_type    TEXT NOT NULL,          -- 'access','correction','erasure','grievance'
    status          TEXT DEFAULT 'pending', -- 'pending','processing','completed','rejected'
    details         TEXT DEFAULT '',        -- JSON: what data, what correction, reason
    submitted_at    TEXT NOT NULL,
    processed_at    TEXT DEFAULT NULL,
    response        TEXT DEFAULT '',        -- what was done / reason for rejection
    processed_by    TEXT DEFAULT ''         -- admin who processed it
);

-- Gap 4: Purpose limitation registry
CREATE TABLE IF NOT EXISTS dpdp_purpose_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_category   TEXT UNIQUE NOT NULL,   -- 'machine_id','ip_address','location','behavior_log','crm_data','backup_data','login_log','whatsapp_contact'
    purpose         TEXT NOT NULL,          -- documented lawful purpose
    legal_basis     TEXT NOT NULL,          -- 'consent','legitimate_interest','legal_obligation','contract'
    retention_days  INTEGER NOT NULL,       -- max days to keep
    description     TEXT DEFAULT ''
);

-- Gap 5: Data retention schedule & deletion log
CREATE TABLE IF NOT EXISTS dpdp_retention_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_category   TEXT NOT NULL,
    records_deleted INTEGER DEFAULT 0,
    deleted_at      TEXT NOT NULL,
    next_purge_at   TEXT DEFAULT NULL,
    method          TEXT DEFAULT 'auto'     -- 'auto','manual','erasure_request'
);

-- Gap 6: Breach notification records
CREATE TABLE IF NOT EXISTS dpdp_breach_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    breach_id       TEXT UNIQUE NOT NULL,
    detected_at     TEXT NOT NULL,
    severity        TEXT NOT NULL,          -- 'low','medium','high','critical'
    description     TEXT NOT NULL,
    data_affected   TEXT DEFAULT '',        -- JSON list of data categories
    principals_affected INTEGER DEFAULT 0,
    notified_board  INTEGER DEFAULT 0,      -- 1 if DPB notified
    notified_board_at TEXT DEFAULT NULL,
    notified_principals INTEGER DEFAULT 0,
    notified_principals_at TEXT DEFAULT NULL,
    remediation     TEXT DEFAULT '',
    status          TEXT DEFAULT 'open'     -- 'open','notified','remediated','closed'
);

-- Gap 7: DPO / Grievance officer config
-- Uses app_config table with keys:
--   dpdp_dpo_name, dpdp_dpo_email, dpdp_dpo_phone
--   dpdp_org_name, dpdp_org_address

-- Gap 8: Cross-border transfer policy
CREATE TABLE IF NOT EXISTS dpdp_transfer_policy (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    destination     TEXT NOT NULL,          -- country code or 'ALL'
    allowed         INTEGER NOT NULL DEFAULT 0,
    restriction     TEXT DEFAULT '',        -- 'blocked','restricted_govt','allowed'
    reason          TEXT DEFAULT '',
    updated_at      TEXT NOT NULL
);

-- Seed default purpose registry (Gap 4)
INSERT OR IGNORE INTO dpdp_purpose_registry (data_category, purpose, legal_basis, retention_days, description) VALUES
    ('machine_id',       'Software license validation and anti-piracy',           'contract',            -1,  'Hardware fingerprint for license binding — retained for license duration'),
    ('ip_address',       'Geo-fence enforcement and VPN detection',               'legitimate_interest',  90, 'IP logged during license checks and security events'),
    ('location',         'License geo-restriction compliance',                     'contract',             90, 'Country/region derived from IP for geo-fence'),
    ('behavior_log',     'Bastion AI security monitoring and anomaly detection',   'legitimate_interest',  180,'User activity patterns for threat scoring'),
    ('crm_data',         'Customer relationship management for jewellery business','consent',              -1, 'Customer names, phones, purchase history — business data'),
    ('backup_data',      'Data recovery and business continuity',                  'legitimate_interest',  365,'Encrypted vault backups of business database'),
    ('login_log',        'Security audit and unauthorized access detection',       'legitimate_interest',  180,'Login attempts, timestamps, success/failure'),
    ('whatsapp_contact', 'Business communication with customers',                  'consent',              -1, 'WhatsApp numbers for order notifications'),
    ('telemetry',        'Software health monitoring and crash diagnostics',       'consent',              90, 'Anonymized app health data sent to server'),
    ('admin_credentials','User authentication and access control',                 'contract',             -1, 'Hashed passwords and usernames for app login');

-- Seed default transfer policy (Gap 8) — India-only by default
INSERT OR IGNORE INTO dpdp_transfer_policy (destination, allowed, restriction, reason, updated_at) VALUES
    ('IN', 1, 'allowed',          'Domestic processing — no restriction',                              datetime('now')),
    ('ALL', 0, 'blocked',         'Cross-border transfer blocked by default per DPDP Section 16',      datetime('now'));
