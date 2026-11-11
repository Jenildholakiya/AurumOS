# -*- coding: utf-8 -*-
"""
AurumOS DPDP Act 2023 Compliance Engine
Addresses all 8 identified compliance gaps:
  1. Consent Management (Section 6)
  2. Privacy Notice (Section 5)
  3. Data Principal Rights (Section 11-14)
  4. Purpose Limitation (Section 4)
  5. Data Retention (Section 8(7))
  6. Breach Notification (Section 8(6))
  7. DPO & Grievance Redressal (Section 8)
  8. Cross-Border Transfer Controls (Section 16)
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta

_LOG = lambda m: print(f'[DPDP] {m}', flush=True)
_ERR = lambda m: print(f'[DPDP-ERR] {m}', flush=True)

NOTICE_VERSION = '1.0.0'

PURPOSES = [
    'license_validation', 'telemetry', 'behavioral_monitoring',
    'crm', 'backup', 'login_audit', 'whatsapp', 'geo_fence',
]

# ─────────────────────────────────────────────────────────
# Helper: get DB connection from the running DBManager
# ─────────────────────────────────────────────────────────

def _get_db():
    """Return a WAL-mode SQLite connection to the app database."""
    from core.io_safety import robust_sqlite_connect
    db_path = os.environ.get('AURUM_DB_PATH', '').strip()
    if not db_path:
        import sys as _s
        base = os.path.dirname(_s.executable) if getattr(_s, 'frozen', False) else os.path.abspath('.')
        txt = os.path.join(base, 'database', '..', 'db_path.txt')
        if os.path.exists(os.path.join(base, 'db_path.txt')):
            try:
                db_path = open(os.path.join(base, 'db_path.txt'), 'r').read().strip()
            except Exception:
                pass
        if not db_path:
            db_path = os.path.join(base, 'database', 'aurum_local.db')
    return robust_sqlite_connect(db_path)


# ═════════════════════════════════════════════════════════
# GAP 1: CONSENT MANAGEMENT (Section 6)
# ═════════════════════════════════════════════════════════

def record_consent(principal_id: str, purpose: str, given: bool,
                   principal_name: str = '', ip_address: str = '',
                   method: str = 'ui') -> bool:
    try:
        conn = _get_db()
        now = datetime.utcnow().isoformat() + 'Z'
        if given:
            conn.execute("""
                INSERT INTO dpdp_consent
                    (principal_id, principal_name, purpose, notice_version,
                     consent_given, given_at, ip_address, method)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(principal_id, purpose) DO UPDATE SET
                    consent_given=1, given_at=excluded.given_at,
                    withdrawn_at=NULL, notice_version=excluded.notice_version,
                    ip_address=excluded.ip_address, method=excluded.method
            """, (principal_id, principal_name, purpose, NOTICE_VERSION,
                  now, ip_address, method))
        else:
            conn.execute("""
                INSERT INTO dpdp_consent
                    (principal_id, principal_name, purpose, notice_version,
                     consent_given, given_at, ip_address, method)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(principal_id, purpose) DO UPDATE SET
                    consent_given=0, withdrawn_at=excluded.given_at,
                    ip_address=excluded.ip_address
            """, (principal_id, principal_name, purpose, NOTICE_VERSION,
                  now, ip_address, method))
        conn.commit()
        conn.close()
        _LOG(f"Consent {'given' if given else 'withdrawn'}: {purpose} for {principal_id[:16]}...")
        return True
    except Exception as e:
        _ERR(f"record_consent: {e}")
        return False


def record_bulk_consent(principal_id: str, consents: dict,
                        principal_name: str = '', ip_address: str = '',
                        method: str = 'ui') -> bool:
    """Record consent for multiple purposes at once. consents = {purpose: bool}."""
    try:
        conn = _get_db()
        now = datetime.utcnow().isoformat() + 'Z'
        for purpose, given in consents.items():
            if given:
                conn.execute("""
                    INSERT INTO dpdp_consent
                        (principal_id, principal_name, purpose, notice_version,
                         consent_given, given_at, ip_address, method)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(principal_id, purpose) DO UPDATE SET
                        consent_given=1, given_at=excluded.given_at,
                        withdrawn_at=NULL, notice_version=excluded.notice_version,
                        ip_address=excluded.ip_address, method=excluded.method
                """, (principal_id, principal_name, purpose, NOTICE_VERSION,
                      now, ip_address, method))
            else:
                conn.execute("""
                    INSERT INTO dpdp_consent
                        (principal_id, principal_name, purpose, notice_version,
                         consent_given, given_at, ip_address, method)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT(principal_id, purpose) DO UPDATE SET
                        consent_given=0, withdrawn_at=excluded.given_at,
                        ip_address=excluded.ip_address
                """, (principal_id, principal_name, purpose, NOTICE_VERSION,
                      now, ip_address, method))
        conn.commit()
        conn.close()
        _LOG(f"Bulk consent recorded for {principal_id[:16]}...: {len(consents)} purposes")
        return True
    except Exception as e:
        _ERR(f"record_bulk_consent: {e}")
        return False


def withdraw_consent(principal_id: str, purpose: str) -> bool:
    return record_consent(principal_id, purpose, given=False)


def has_consent(principal_id: str, purpose: str) -> bool:
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT consent_given FROM dpdp_consent WHERE principal_id=? AND purpose=?",
            (principal_id, purpose)
        ).fetchone()
        conn.close()
        return bool(row and row[0] == 1)
    except Exception:
        return False


def get_all_consents(principal_id: str) -> list:
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT purpose, consent_given, given_at, withdrawn_at, notice_version "
            "FROM dpdp_consent WHERE principal_id=? ORDER BY purpose",
            (principal_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def is_consent_required() -> bool:
    """Check if any principal on this machine has pending consent."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT value FROM app_config WHERE key='machine_fingerprint'"
        ).fetchone()
        if not row:
            conn.close()
            return True
        fp = row[0] or ''
        existing = conn.execute(
            "SELECT COUNT(*) FROM dpdp_consent WHERE principal_id=? AND consent_given=1",
            (fp,)
        ).fetchone()
        conn.close()
        return (existing[0] or 0) < len(PURPOSES)
    except Exception:
        return True


# ═════════════════════════════════════════════════════════
# GAP 2: PRIVACY NOTICE (Section 5)
# ═════════════════════════════════════════════════════════

def get_privacy_notice() -> dict:
    """Return the structured privacy notice content for display."""
    dpo = get_dpo_info()
    return {
        'version': NOTICE_VERSION,
        'last_updated': '2026-11-11',
        'data_fiduciary': dpo.get('org_name', 'AurumOS Software Provider'),
        'dpo_contact': {
            'name': dpo.get('name', ''),
            'email': dpo.get('email', ''),
            'phone': dpo.get('phone', ''),
        },
        'data_collected': [
            {'category': 'Machine Identity', 'data': 'Motherboard serial, CPU ID, disk serial, MAC address, hostname',
             'purpose': 'License validation and anti-piracy', 'retention': 'Duration of license'},
            {'category': 'IP Address & Location', 'data': 'Public IP address, country/region',
             'purpose': 'Geo-fence enforcement and VPN detection', 'retention': '90 days'},
            {'category': 'Login Activity', 'data': 'Login timestamps, success/failure, device info',
             'purpose': 'Security audit and unauthorized access detection', 'retention': '180 days'},
            {'category': 'Behavioral Patterns', 'data': 'Usage patterns, anomaly scores, security events',
             'purpose': 'Bastion AI threat detection', 'retention': '180 days'},
            {'category': 'Business Data (CRM)', 'data': 'Customer names, phone numbers, purchase history',
             'purpose': 'Customer relationship management', 'retention': 'Until deletion requested'},
            {'category': 'WhatsApp Contacts', 'data': 'Phone numbers for business messaging',
             'purpose': 'Order notifications and customer communication', 'retention': 'Until deletion requested'},
            {'category': 'Backup Data', 'data': 'Encrypted database backups (.vault files)',
             'purpose': 'Business continuity and data recovery', 'retention': '365 days'},
            {'category': 'Telemetry', 'data': 'License key, machine ID, anonymized health data',
             'purpose': 'Software health and crash diagnostics', 'retention': '90 days'},
        ],
        'rights': [
            'Right to access your personal data (Section 11)',
            'Right to correction of inaccurate data (Section 12)',
            'Right to erasure of personal data (Section 13)',
            'Right to grievance redressal (Section 14)',
            'Right to withdraw consent at any time (Section 6(6))',
            'Right to nominate another person to exercise rights (Section 14(2))',
        ],
        'cross_border': 'Personal data is processed and stored locally on your machine. '
                        'License validation telemetry may be transmitted to servers in India only, '
                        'unless the Central Government notifies permitted jurisdictions under Section 16(1).',
        'grievance_url': '',
        'governing_law': 'Digital Personal Data Protection Act, 2023 (India)',
    }


def get_privacy_notice_html() -> str:
    """Render the privacy notice as an HTML fragment for embedding in agreement.html."""
    notice = get_privacy_notice()
    rows = ''
    for d in notice['data_collected']:
        rows += f"""<tr>
            <td style="font-weight:600">{d['category']}</td>
            <td>{d['data']}</td>
            <td>{d['purpose']}</td>
            <td>{d['retention']}</td>
        </tr>"""
    rights_li = ''.join(f'<li>{r}</li>' for r in notice['rights'])
    dpo = notice['dpo_contact']
    dpo_html = ''
    if dpo['name'] or dpo['email']:
        dpo_html = f"<p><strong>Data Protection Officer:</strong> {dpo['name']}"
        if dpo['email']:
            dpo_html += f" — {dpo['email']}"
        if dpo['phone']:
            dpo_html += f" — {dpo['phone']}"
        dpo_html += '</p>'

    return f"""
    <div class="dpdp-notice" id="dpdp-privacy-notice">
      <h3>Privacy Notice — DPDP Act 2023 (Version {notice['version']})</h3>
      <p>Under the Digital Personal Data Protection Act, 2023, you are a <strong>Data Principal</strong>.
         This notice explains what personal data we collect, why, how long we keep it, and your rights.</p>
      <p><strong>Data Fiduciary:</strong> {notice['data_fiduciary']}</p>
      {dpo_html}
      <h4>Data We Collect</h4>
      <table class="dpdp-table">
        <thead><tr><th>Category</th><th>Data</th><th>Purpose</th><th>Retention</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <h4>Your Rights</h4>
      <ul>{rights_li}</ul>
      <h4>Cross-Border Transfers</h4>
      <p>{notice['cross_border']}</p>
      <p><em>Last updated: {notice['last_updated']}</em></p>
    </div>"""


# ═════════════════════════════════════════════════════════
# GAP 3: DATA PRINCIPAL RIGHTS (Sections 11-14)
# ═════════════════════════════════════════════════════════

def submit_rights_request(principal_id: str, request_type: str,
                          details: str = '', principal_name: str = '') -> str:
    """Submit a data rights request. Returns request_id."""
    try:
        request_id = f"DPDP-{uuid.uuid4().hex[:12].upper()}"
        conn = _get_db()
        conn.execute("""
            INSERT INTO dpdp_rights_requests
                (request_id, principal_id, principal_name, request_type,
                 details, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (request_id, principal_id, principal_name, request_type,
              details, datetime.utcnow().isoformat() + 'Z'))
        conn.commit()
        conn.close()
        _LOG(f"Rights request {request_id}: {request_type} from {principal_id[:16]}...")
        return request_id
    except Exception as e:
        _ERR(f"submit_rights_request: {e}")
        return ''


def process_rights_request(request_id: str, response: str,
                           processed_by: str = 'system',
                           status: str = 'completed') -> bool:
    try:
        conn = _get_db()
        conn.execute("""
            UPDATE dpdp_rights_requests
            SET status=?, response=?, processed_at=?, processed_by=?
            WHERE request_id=?
        """, (status, response, datetime.utcnow().isoformat() + 'Z',
              processed_by, request_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        _ERR(f"process_rights_request: {e}")
        return False


def get_rights_requests(principal_id: str = None, status: str = None) -> list:
    try:
        conn = _get_db()
        q = "SELECT * FROM dpdp_rights_requests WHERE 1=1"
        params = []
        if principal_id:
            q += " AND principal_id=?"
            params.append(principal_id)
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY submitted_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def execute_access_request(principal_id: str) -> dict:
    """Gather all personal data held for a principal (right of access)."""
    result = {}
    try:
        conn = _get_db()
        # Consent records
        result['consents'] = [dict(r) for r in conn.execute(
            "SELECT * FROM dpdp_consent WHERE principal_id=?", (principal_id,)
        ).fetchall()]
        # Login logs
        try:
            result['login_logs'] = [dict(r) for r in conn.execute(
                "SELECT * FROM login_log ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()]
        except Exception:
            result['login_logs'] = []
        # Bastion AI logs
        try:
            result['security_logs'] = [dict(r) for r in conn.execute(
                "SELECT * FROM logs ORDER BY ts DESC LIMIT 100"
            ).fetchall()]
        except Exception:
            result['security_logs'] = []
        # Machine lock info
        try:
            result['machine_identity'] = [dict(r) for r in conn.execute(
                "SELECT * FROM mac_lock"
            ).fetchall()]
        except Exception:
            result['machine_identity'] = []
        # Rights request history
        result['rights_requests'] = [dict(r) for r in conn.execute(
            "SELECT * FROM dpdp_rights_requests WHERE principal_id=?", (principal_id,)
        ).fetchall()]
        conn.close()
    except Exception as e:
        _ERR(f"execute_access_request: {e}")
    return result


def execute_erasure_request(principal_id: str, categories: list = None) -> dict:
    """Erase personal data for a principal. Returns what was deleted."""
    deleted = {}
    try:
        conn = _get_db()
        cats = categories or ['behavior_log', 'login_log', 'ip_address', 'telemetry']
        if 'behavior_log' in cats:
            r = conn.execute("DELETE FROM logs WHERE 1=1")
            deleted['security_logs'] = r.rowcount
        if 'login_log' in cats:
            try:
                r = conn.execute("DELETE FROM login_log WHERE 1=1")
                deleted['login_logs'] = r.rowcount
            except Exception:
                pass
        # Withdraw all consents for erased categories
        for cat in cats:
            conn.execute(
                "UPDATE dpdp_consent SET consent_given=0, withdrawn_at=? WHERE principal_id=? AND purpose=?",
                (datetime.utcnow().isoformat() + 'Z', principal_id, cat))
        # Log the erasure
        conn.execute("""
            INSERT INTO dpdp_retention_log (data_category, records_deleted, deleted_at, method)
            VALUES (?, ?, ?, 'erasure_request')
        """, (','.join(cats), sum(deleted.values()),
              datetime.utcnow().isoformat() + 'Z'))
        conn.commit()
        conn.close()
        _LOG(f"Erasure completed for {principal_id[:16]}...: {deleted}")
    except Exception as e:
        _ERR(f"execute_erasure_request: {e}")
    return deleted


# ═════════════════════════════════════════════════════════
# GAP 4: PURPOSE LIMITATION (Section 4)
# ═════════════════════════════════════════════════════════

def check_purpose_allowed(data_category: str) -> dict:
    """Check if processing a data category has a documented lawful purpose."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM dpdp_purpose_registry WHERE data_category=?",
            (data_category,)
        ).fetchone()
        conn.close()
        if row:
            return {'allowed': True, **dict(row)}
        return {'allowed': False, 'reason': 'No registered purpose for this data category'}
    except Exception:
        return {'allowed': False, 'reason': 'Purpose registry unavailable'}


def get_purpose_registry() -> list:
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM dpdp_purpose_registry ORDER BY data_category").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════
# GAP 5: DATA RETENTION (Section 8(7))
# ═════════════════════════════════════════════════════════

def run_retention_purge() -> dict:
    """Auto-delete data past its retention period. Call periodically."""
    purged = {}
    try:
        conn = _get_db()
        registry = conn.execute(
            "SELECT data_category, retention_days FROM dpdp_purpose_registry WHERE retention_days > 0"
        ).fetchall()
        now = datetime.utcnow()
        for row in registry:
            cat = row['data_category']
            days = row['retention_days']
            cutoff = (now - timedelta(days=days)).isoformat() + 'Z'
            count = 0
            if cat == 'behavior_log':
                r = conn.execute("DELETE FROM logs WHERE ts < ?", (cutoff,))
                count = r.rowcount
            elif cat == 'login_log':
                try:
                    r = conn.execute("DELETE FROM login_log WHERE timestamp < ?", (cutoff,))
                    count = r.rowcount
                except Exception:
                    pass
            elif cat == 'ip_address':
                # IP data is in logs — already handled by behavior_log purge
                pass
            elif cat == 'telemetry':
                # Telemetry is sent to server; local retention is in logs
                pass
            elif cat == 'backup_data':
                count = _purge_old_backups(days)

            if count > 0:
                purged[cat] = count
                conn.execute("""
                    INSERT INTO dpdp_retention_log
                        (data_category, records_deleted, deleted_at, next_purge_at, method)
                    VALUES (?, ?, ?, ?, 'auto')
                """, (cat, count, now.isoformat() + 'Z',
                      (now + timedelta(days=1)).isoformat() + 'Z'))
        conn.commit()
        conn.close()
        if purged:
            _LOG(f"Retention purge: {purged}")
    except Exception as e:
        _ERR(f"run_retention_purge: {e}")
    return purged


def _purge_old_backups(max_days: int) -> int:
    """Remove .vault backup files older than max_days."""
    count = 0
    try:
        import sys as _s
        base = os.path.dirname(_s.executable) if getattr(_s, 'frozen', False) else os.path.abspath('.')
        backup_dir = os.path.join(base, 'database')
        cutoff = datetime.utcnow() - timedelta(days=max_days)
        for f in os.listdir(backup_dir):
            if f.endswith('.vault'):
                fp = os.path.join(backup_dir, f)
                mtime = datetime.utcfromtimestamp(os.path.getmtime(fp))
                if mtime < cutoff:
                    try:
                        os.remove(fp)
                        count += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return count


def get_retention_log(limit: int = 50) -> list:
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM dpdp_retention_log ORDER BY deleted_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════
# GAP 6: BREACH NOTIFICATION (Section 8(6))
# ═════════════════════════════════════════════════════════

def report_breach(severity: str, description: str,
                  data_affected: list = None,
                  principals_affected: int = 0) -> str:
    """Log a data breach. Returns breach_id."""
    try:
        breach_id = f"BRH-{uuid.uuid4().hex[:12].upper()}"
        conn = _get_db()
        conn.execute("""
            INSERT INTO dpdp_breach_log
                (breach_id, detected_at, severity, description,
                 data_affected, principals_affected)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (breach_id, datetime.utcnow().isoformat() + 'Z',
              severity, description,
              json.dumps(data_affected or []), principals_affected))
        conn.commit()
        conn.close()
        _LOG(f"BREACH LOGGED: {breach_id} severity={severity}")
        if severity in ('high', 'critical'):
            _trigger_breach_notification(breach_id)
        return breach_id
    except Exception as e:
        _ERR(f"report_breach: {e}")
        return ''


def _trigger_breach_notification(breach_id: str):
    """Prepare breach notification for DPB and affected principals."""
    try:
        conn = _get_db()
        breach = conn.execute(
            "SELECT * FROM dpdp_breach_log WHERE breach_id=?", (breach_id,)
        ).fetchone()
        if not breach:
            conn.close()
            return
        dpo = get_dpo_info()
        notification = {
            'breach_id': breach_id,
            'detected_at': breach['detected_at'],
            'severity': breach['severity'],
            'description': breach['description'],
            'data_affected': breach['data_affected'],
            'principals_affected': breach['principals_affected'],
            'fiduciary': dpo.get('org_name', ''),
            'dpo_name': dpo.get('name', ''),
            'dpo_email': dpo.get('email', ''),
            'action_required': 'Notify Data Protection Board within 72 hours per Section 8(6)',
        }
        # Store notification payload in app_config for admin dashboard pickup
        from core.io_safety import atomic_write_json
        import sys as _s
        base = os.path.dirname(_s.executable) if getattr(_s, 'frozen', False) else os.path.abspath('.')
        atomic_write_json(
            os.path.join(base, 'database', '.breach_notification.json'),
            notification
        )
        conn.execute("""
            UPDATE dpdp_breach_log SET status='notified' WHERE breach_id=?
        """, (breach_id,))
        conn.commit()
        conn.close()
        _LOG(f"Breach notification prepared: {breach_id}")
    except Exception as e:
        _ERR(f"_trigger_breach_notification: {e}")


def mark_breach_notified(breach_id: str, board: bool = False,
                         principals: bool = False) -> bool:
    try:
        conn = _get_db()
        now = datetime.utcnow().isoformat() + 'Z'
        if board:
            conn.execute(
                "UPDATE dpdp_breach_log SET notified_board=1, notified_board_at=? WHERE breach_id=?",
                (now, breach_id))
        if principals:
            conn.execute(
                "UPDATE dpdp_breach_log SET notified_principals=1, notified_principals_at=? WHERE breach_id=?",
                (now, breach_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_breaches(status: str = None) -> list:
    try:
        conn = _get_db()
        if status:
            rows = conn.execute(
                "SELECT * FROM dpdp_breach_log WHERE status=? ORDER BY detected_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dpdp_breach_log ORDER BY detected_at DESC"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════
# GAP 7: DPO & GRIEVANCE REDRESSAL (Section 8)
# ═════════════════════════════════════════════════════════

def set_dpo_info(name: str = '', email: str = '', phone: str = '',
                 org_name: str = '', org_address: str = '') -> bool:
    try:
        conn = _get_db()
        for key, val in [('dpdp_dpo_name', name), ('dpdp_dpo_email', email),
                         ('dpdp_dpo_phone', phone), ('dpdp_org_name', org_name),
                         ('dpdp_org_address', org_address)]:
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key, value) VALUES(?, ?)",
                    (key, val))
        conn.commit()
        conn.close()
        _LOG("DPO info updated")
        return True
    except Exception as e:
        _ERR(f"set_dpo_info: {e}")
        return False


def get_dpo_info() -> dict:
    try:
        conn = _get_db()
        info = {}
        for key, field in [('dpdp_dpo_name', 'name'), ('dpdp_dpo_email', 'email'),
                           ('dpdp_dpo_phone', 'phone'), ('dpdp_org_name', 'org_name'),
                           ('dpdp_org_address', 'org_address')]:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key=?", (key,)
            ).fetchone()
            info[field] = (row[0] if row else '') or ''
        conn.close()
        return info
    except Exception:
        return {'name': '', 'email': '', 'phone': '', 'org_name': '', 'org_address': ''}


# ═════════════════════════════════════════════════════════
# GAP 8: CROSS-BORDER TRANSFER CONTROLS (Section 16)
# ═════════════════════════════════════════════════════════

def is_transfer_allowed(country_code: str) -> dict:
    """Check if data transfer to a country is allowed under DPDP Section 16."""
    try:
        conn = _get_db()
        # Check specific country first
        row = conn.execute(
            "SELECT * FROM dpdp_transfer_policy WHERE destination=?",
            (country_code.upper(),)
        ).fetchone()
        if row:
            conn.close()
            return {'allowed': bool(row['allowed']), 'restriction': row['restriction'],
                    'reason': row['reason']}
        # Fall back to ALL policy
        row = conn.execute(
            "SELECT * FROM dpdp_transfer_policy WHERE destination='ALL'"
        ).fetchone()
        conn.close()
        if row:
            return {'allowed': bool(row['allowed']), 'restriction': row['restriction'],
                    'reason': row['reason']}
        return {'allowed': False, 'restriction': 'blocked',
                'reason': 'No transfer policy configured — blocked by default'}
    except Exception:
        return {'allowed': False, 'restriction': 'error',
                'reason': 'Transfer policy check failed'}


def update_transfer_policy(country_code: str, allowed: bool,
                           restriction: str = '', reason: str = '') -> bool:
    try:
        conn = _get_db()
        conn.execute("""
            INSERT INTO dpdp_transfer_policy (destination, allowed, restriction, reason, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                allowed=excluded.allowed, restriction=excluded.restriction,
                reason=excluded.reason, updated_at=excluded.updated_at
        """, (country_code.upper(), 1 if allowed else 0, restriction, reason))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        _ERR(f"update_transfer_policy: {e}")
        return False


def get_transfer_policies() -> list:
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM dpdp_transfer_policy ORDER BY destination"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════
# INTEGRATION: Hook into Bastion AI for breach detection
# ═════════════════════════════════════════════════════════

def check_for_breach_conditions(score: int, event_type: str, detail: str) -> str:
    """Called by Bastion AI when anomaly score is high. Returns breach_id if breach logged."""
    breach_triggers = {
        'db_tamper': ('critical', 'Database tampering detected'),
        'unauthorized_access': ('high', 'Unauthorized access attempt'),
        'data_exfiltration': ('critical', 'Potential data exfiltration'),
        'brute_force': ('medium', 'Brute force attack detected'),
    }
    if score >= 75 or event_type in breach_triggers:
        sev, desc = breach_triggers.get(event_type, ('medium', event_type))
        if score >= 90:
            sev = 'critical'
        return report_breach(
            severity=sev,
            description=f"{desc}: {detail}",
            data_affected=['login_log', 'behavior_log'],
            principals_affected=1
        )
    return ''


# ═════════════════════════════════════════════════════════
# COMPLIANCE STATUS DASHBOARD
# ═════════════════════════════════════════════════════════

def get_compliance_status() -> dict:
    """Full DPDP compliance status for admin dashboard."""
    status = {
        'overall': 'compliant',
        'gaps': [],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }
    try:
        conn = _get_db()

        # Check consent coverage
        consent_count = conn.execute(
            "SELECT COUNT(DISTINCT purpose) FROM dpdp_consent WHERE consent_given=1"
        ).fetchone()[0] or 0
        if consent_count < len(PURPOSES):
            status['gaps'].append('consent_incomplete')

        # Check DPO configured
        dpo = get_dpo_info()
        if not dpo.get('name') or not dpo.get('email'):
            status['gaps'].append('dpo_not_configured')

        # Check pending rights requests
        pending = conn.execute(
            "SELECT COUNT(*) FROM dpdp_rights_requests WHERE status='pending'"
        ).fetchone()[0] or 0
        if pending > 0:
            status['gaps'].append(f'pending_requests:{pending}')

        # Check open breaches
        open_breaches = conn.execute(
            "SELECT COUNT(*) FROM dpdp_breach_log WHERE status IN ('open','notified')"
        ).fetchone()[0] or 0
        if open_breaches > 0:
            status['gaps'].append(f'open_breaches:{open_breaches}')

        # Check purpose registry
        purposes = conn.execute("SELECT COUNT(*) FROM dpdp_purpose_registry").fetchone()[0] or 0
        if purposes == 0:
            status['gaps'].append('no_purpose_registry')

        conn.close()

        if status['gaps']:
            status['overall'] = 'partial'
        if 'dpo_not_configured' in status['gaps']:
            status['overall'] = 'non_compliant'

    except Exception as e:
        status['overall'] = 'error'
        status['error'] = str(e)

    return status
