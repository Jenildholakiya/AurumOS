# -*- coding: utf-8 -*-
"""
AurumOS BASTION AI — Self-Learning Security Monitor
====================================================
Runs silently in background. Monitors software integrity only.
Learns THIS shop's normal usage pattern over time.
Auto-heals safe issues. Auto-suspends on confirmed attacks.
Queues alerts — sends via email when internet available.

Threads:
  1. DB Watchdog     (every 30s)  — detects external DB edits
  2. Session Guard   (every 60s)  — detects session tampering
  3. Auto Healer     (every 5min) — fixes backup, WAL, logs
  4. Pattern Learner (every 24h)  — learns normal behaviour
  5. Alert Sender    (every 5min) — sends queued email alerts

Place this file in: database\bastion_ai.py
"""

import threading, time, os, sys, hashlib, json, sqlite3
import smtplib, ssl
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database.bastion_report import generate_bastion_report_async

from database.db_manager import _dberr

# ── ALERT CONFIG — fill these in ──────────────────────────────────
# Your Gmail address and App Password (not regular password)
# Go to: Google Account → Security → App Passwords → Generate
ALERT_EMAIL_FROM     = 'aurumos.software@gmail.com'
ALERT_EMAIL_PASSWORD = 'ttyx niad ocea oebo'
ALERT_EMAIL_TO       = 'jenildholakiya8305@gmail.com'

# Threat score thresholds
SCORE_WARN    = 40    # log + queue alert
SCORE_SUSPEND = 75    # auto-suspend account

# ── SMARTER-DECISIONS TUNING ──────────────────────────────────────
# Progressive escalation: a threat class climbs WARN -> RESTRICT ->
# SUSPEND instead of locking on the first trigger.
ESCALATION_WINDOW = 86400   # s — repeated threats within 24h escalate; older ones decay
SUSPEND_LEVEL     = 3       # escalate to this level before auto-suspend is allowed
CORRO_WINDOW      = 600     # s — corroboration looks back this far for a 2nd signal
ALLOW_WINDOW      = 600     # s — an admin allowlist entry stays valid this long


# ── SEVERITY LEVELS ───────────────────────────────────────────────
SEV_LOW      = 'LOW'
SEV_MEDIUM   = 'MEDIUM'
SEV_HIGH     = 'HIGH'
SEV_CRITICAL = 'CRITICAL'


def _log(msg):
    try:
        safe = str(msg).encode('ascii', errors='replace').decode('ascii')
        print(f"[BASTION_AI] {safe}", flush=True)
    except Exception:
        pass


def _err(msg):
    try:
        safe = str(msg).encode('ascii', errors='replace').decode('ascii')
        print(f"[BASTION_AI_ERR] {safe}", flush=True)
    except Exception:
        pass


class BastionAI:
    """
    BASTION AI — background security intelligence.
    Attach to DBManager and start after app init.
    """

    def __init__(self, db_manager):
        self.db       = db_manager
        self._running = False
        self._threads = []

        # State shared between threads
        self._last_db_hash     = None
        self._last_write_ts    = time.time()   # updated by db_manager hooks
        self._session_active   = False

        # Tables whose row counts define the integrity hash, plus cached
        # per-table counts so the watchdog can tell WHICH tables moved.
        self._hash_tables      = [
            'stock_inventory', 'sales_history', 'katti_vouchers',
            'katti_voucher_items', 'credit_ledger', 'admin_creds'
        ]
        self._last_table_counts  = {}
        self._prev_table_counts  = {}
        self._daily_score      = 0
        self._event_count_day  = 0
        self._suspicious_count = 0
        self._consecutive_clean = 0

        # Smarter-decisions state
        self._restricted      = False   # soft-lock: risky ops blocked, billing OK
        self._recent_signals = []       # [(ts, event_type)] for corroboration
        self._startup_time    = time.time()  # grace period to avoid false alerts
        self._grace_period    = 120     # 2 minutes grace after startup

        # Load learned thresholds from DB
        self._thresholds = self._load_thresholds()
        _log("Initialized")

    # ══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════

    def start(self):
        """Start all background threads. Call once after app init."""
        if self._running:
            return
        self._running = True

        specs = [
            ("DB-Watchdog",     self._thread_db_watchdog,     30),
            ("Session-Guard",   self._thread_session_guard,   60),
            ("Auto-Healer",     self._thread_auto_healer,    300),
            ("Pattern-Learner", self._thread_pattern_learner, 86400),
            ("Alert-Sender",    self._thread_alert_sender,   300),
        ]

        for name, target, interval in specs:
            t = threading.Thread(
                target=self._runner,
                args=(name, target, interval),
                daemon=True,
                name=f"BASTION-{name}"
            )
            t.start()
            self._threads.append(t)
            _log(f"Thread started: {name} every {interval}s")

    def stop(self):
        """Clean shutdown. Call on app exit."""
        _log("Stopping all threads...")
        self._running = False

    def notify_write(self):
        """
        Call this from db_manager before any legitimate write.
        Prevents false positives in DB watchdog.
        """
        self._last_write_ts = time.time()

    def notify_session_active(self, active=True):
        """Call from main.py after login / before logout."""
        self._session_active = active

    def get_weekly_report(self):
        """
        Generates a summary of the past 7 days of Bastion events.
        """
        try:
            # Assuming you have a database connection or a way to fetch logs
            # This is a placeholder; replace with your actual DB query logic
            logs = self.db.get_logs_last_7_days()

            report = {
                'total_threats': len([e for e in logs if e['severity'] == 'HIGH']),
                'auto_healed': len([e for e in logs if e['action_taken'] == 'HEALED']),
                'summary': 'Weekly system analysis complete.'
            }
            return report
        except Exception as e:
            _dberr(f"[BASTION_AI] Failed to generate weekly report: {str(e)}")
            return {'error': str(e)}

    def log_feature_status(self):
        """Prints the current security configuration status to the terminal on startup."""
        _log("══════════════════════════════════════════════════")
        _log("BASTION AI — Active Security Configuration:")

        # Mapping of internal keys to readable names
        status_map = {
            'db_watchdog_enabled': 'DB Watchdog',
            'session_guard_enabled': 'Session Guard',
            'auto_healer_enabled': 'Auto-Healer',
            'pattern_learner_enabled': 'Pattern Learner',
            'alert_sender_enabled': 'Alert Sender'
        }

        try:
            # Fetch current status from your DB
            settings = self.db.get_global_settings()

            for key, name in status_map.items():
                status = "ON" if settings.get(key, True) else "OFF"
                color_prefix = "✔" if status == "ON" else "✘"
                _log(f"  {color_prefix} {name:20} : {status}")

        except Exception as e:
            _err(f"Could not load feature status: {e}")

        _log("══════════════════════════════════════════════════")

    def push_to_health_dashboard(self):
        """Push real event data to the Vercel health dashboard. Silent no-op if not configured."""
        url = os.environ.get('AURUM_HEALTH_URL', '')
        secret = os.environ.get('AURUM_ADMIN_SECRET', '')
        client_id = os.environ.get('AURUM_CLIENT_ID', '')
        if not url or not secret or not client_id:
            return False
        try:
            import urllib.request, json as _json
            report = self.get_weekly_report()
            status = self.db.bastion_get_status()
            payload = _json.dumps({
                'client_id':  client_id,
                'events':     report.get('recent', []),
                'thresholds': report.get('thresholds', {}),
                'suspended':  bool(status.get('suspended')),
                'summary':    f"{report.get('total_events',0)} events, {report.get('auto_healed',0)} auto-healed, {report.get('high_threats',0)} high threats this week.",
            }).encode('utf-8')
            req = urllib.request.Request(
                url, data=payload, method='POST',
                headers={'Content-Type': 'application/json', 'x-admin-secret': secret}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            _log("Pushed events to health dashboard")
            return True
        except Exception as e:
            _err(f"push_to_health_dashboard: {e}")
            return False

    # ══════════════════════════════════════════════════════════════
    # THREAD RUNNER
    # ══════════════════════════════════════════════════════════════

    # Inside BastionAI class
    def _is_feature_enabled(self, feature_id):
        """
        Checks the Global Master Config in Supabase.
        If the client is offline, it falls back to the last cached setting.
        """
        try:
            # Example API call to your Supabase/Vercel proxy
            # This function fetches the master config for ALL clients
            settings = self.db.get_global_settings()
            return settings.get(feature_id, True)
        except:
            return True  # Default to ON if network is down (Fail-safe)

    def _runner(self, name, target, interval):
        while self._running:
            # MAP THREAD NAMES TO DB SETTINGS
            mapping = {
                "DB-Watchdog": "db_watchdog_enabled",
                "Session-Guard": "session_guard_enabled",
                "Auto-Healer": "auto_healer_enabled",
                "Pattern-Learner": "pattern_learner_enabled",
                "Alert-Sender": "alert_sender_enabled"
            }

            feature_key = mapping.get(name.replace("BASTION-", ""))

            if not feature_key or self._is_feature_enabled(feature_key):
                try:
                    target()
                except Exception as e:
                    _err(f"{name} error: {e}")
            else:
                _log(f"Feature {name} is globally DISABLED. Skipping.")

            for _ in range(interval):
                if not self._running: return
                time.sleep(1)

    # ══════════════════════════════════════════════════════════════
    # THREAD 1 — DB WATCHDOG
    # ══════════════════════════════════════════════════════════════

    def _thread_db_watchdog(self):
        """
        Hashes row counts every 30s and detects EXTERNAL db edits.

        KEY PRINCIPLE (fixes false suspensions on normal business):
        A row-count change is NOT proof of tampering. Normal shop activity
        — making bills, stock entries, katti vouchers, payments — constantly
        changes these tables WHILE THE USER IS LOGGED IN. So:

          1. Active session  -> change is legitimate in-app activity. Never flag.
          2. Recent in-app write (notify_write within 180s) -> legitimate.
          3. Otherwise (app idle / logged out, no recent write) -> suspicious.
             Even then, a change limited to business tables (e.g. a delayed
             background save that missed the write hook) only raises an ALERT
             — it can NEVER auto-suspend. Auto-suspend is reserved for
             high-confidence tampering: credentials (admin_creds) touched, or
             rows DELETED, while the app is idle.
        """
        prev_counts = dict(self._prev_table_counts)
        current_hash = self._compute_db_hash()
        if current_hash is None:
            return

        if self._last_db_hash is None:
            self._last_db_hash = current_hash
            self._prev_table_counts = dict(self._last_table_counts)
            return

        if current_hash == self._last_db_hash:
            self._consecutive_clean += 1
            self._prev_table_counts = dict(self._last_table_counts)
            return

        # (0) ADMIN ALLOWLIST — a known-good maintenance op (backup restore,
        # data import, schema fix). If an admin marked the current change as
        # expected, accept it outright and never flag it.
        if self._is_allowlisted('db_external_change'):
            self._last_db_hash = current_hash
            self._last_write_ts = time.time()
            self._suspicious_count = 0
            self._prev_table_counts = dict(self._last_table_counts)
            self._record_event(
                event_type   = 'db_allowlisted',
                severity     = SEV_LOW,
                score        = 0,
                detail       = 'DB change matched admin allowlist — ignored',
                action_taken = 'ALLOWED'
            )
            _log("DB change matched allowlist — ignored")
            return

        new_counts = dict(self._last_table_counts)
        seconds_since_write = time.time() - self._last_write_ts

        # (1) ACTIVE SESSION — a logged-in user operating the app. Every DB
        # change here is, by definition, a legitimate in-app write (billing,
        # stock, etc.). This is the main guard against false suspensions.
        if self._session_active:
            self._last_db_hash = current_hash
            self._last_write_ts = time.time()
            self._suspicious_count = 0
            self._prev_table_counts = new_counts
            _log("DB changed during active session (legit app activity) — OK")
            return

        # (2) RECENT IN-APP WRITE — a save committed just now (or a notify_write
        # hook fired). Generous 180s window covers commit + WAL flush latency.
        if seconds_since_write < 180:
            self._last_db_hash = current_hash
            self._last_write_ts = time.time()
            self._prev_table_counts = new_counts
            _log(f"DB changed legitimately ({int(seconds_since_write)}s since write) — OK")
            return

        # (3) GENUINE EXTERNAL-EDIT SUSPICION: DB changed with NO active session
        # and NO recent in-app write. Work out which tables moved so we can grade
        # confidence — appends to business tables are weak evidence; credential
        # changes or deletions are strong evidence.
        SENSITIVE = {'admin_creds'}
        changed, sensitive_changed, deletions = [], False, False
        for t in self._hash_tables:
            old = prev_counts.get(t)
            new = new_counts.get(t)
            if old is None or new is None or old == new:
                continue
            changed.append(t)
            if t in SENSITIVE:
                sensitive_changed = True
            if new < old:
                deletions = True

        self._suspicious_count = getattr(self, '_suspicious_count', 0) + 1
        detail = (
            f"DB changed while app idle (no session, no recent write). "
            f"Last write: {int(seconds_since_write)}s ago. "
            f"Changed tables: {', '.join(changed) or 'unknown'}. "
            f"Suspicious count: {self._suspicious_count}/2. "
            f"Old={self._last_db_hash[:8]} New={current_hash[:8]}"
        )
        _log(f"[WATCHDOG] Suspicious change #{self._suspicious_count}: {detail}")

        self._last_db_hash = current_hash
        self._prev_table_counts = new_counts

        # Only act on a CONFIRMED pattern (2+ occurrences while idle).
        if self._suspicious_count < 2:
            self._record_event(
                event_type='db_suspicious_change',
                severity=SEV_MEDIUM,
                score=25,
                detail=detail,
                action_taken='WATCHING'
            )
            return

        # Confirmed external-edit pattern.
        self._suspicious_count = 0
        learned = int(self._thresholds.get('external_edit_threshold', 65))

        if sensitive_changed or deletions:
            # High-confidence tampering (creds touched / rows deleted while idle).
            score = max(learned, 80)        # may cross the auto-suspend bar
            confidence = 'HIGH'
        else:
            # Business-table change only (likely a delayed/missed in-app write).
            # Capped safely BELOW the auto-suspend threshold so it can NEVER
            # lock the user out — it only raises an alert for human review.
            score = min(learned, 60)
            confidence = 'LOW'

        self._record_event(
            event_type='db_external_edit',
            severity=SEV_HIGH if confidence == 'HIGH' else SEV_MEDIUM,
            score=score,
            detail=f"[{confidence} confidence] {detail}",
            action_taken='DETECTED'
        )

        # Smarter decision: hand off to the escalation engine. It applies
        # progressive escalation (WARN -> RESTRICT -> SUSPEND) and only
        # suspends when CORROBORATED by a second independent signal — so a
        # single weak/false trigger can never lock the user out.
        self._evaluate_threat('db_edit', detail, critical=False)

    def _compute_db_hash(self):
        """Hash row counts of all main tables. Also caches per-table counts
        so the watchdog can tell WHICH tables changed (and whether sensitive
        tables like admin_creds were touched)."""
        try:
            tables = self._hash_tables
            parts = []
            counts = {}
            with self.db._get_connection() as conn:
                for t in tables:
                    try:
                        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        counts[t] = n
                        parts.append(f"{t}:{n}")
                    except Exception:
                        counts[t] = None
                        parts.append(f"{t}:?")
            self._last_table_counts = counts
            return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]
        except Exception as e:
            _err(f"compute_db_hash: {e}")
            return None

    # ══════════════════════════════════════════════════════════════
    # THREAD 2 — SESSION GUARD
    # ══════════════════════════════════════════════════════════════

    def _thread_session_guard(self):
        """
        Checks session token stores every 60s.
        Only checks during active session.
        """
        if not self._session_active:
            return

        try:
            import winreg as _wr
            REG_PATH = r'SOFTWARE\Microsoft\InputMethod\AOS'

            # Check registry — create if missing (setup may not have run yet)
            try:
                key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, REG_PATH)
                _wr.QueryValueEx(key, 'SessionCache')
                _wr.CloseKey(key)
            except FileNotFoundError:
                # Registry key missing — try to create it silently
                try:
                    import uuid as _uuid
                    token = str(_uuid.uuid4())
                    key = _wr.CreateKey(_wr.HKEY_CURRENT_USER, REG_PATH)
                    _wr.SetValueEx(key, 'SessionCache', 0, _wr.REG_SZ, token)
                    _wr.CloseKey(key)
                    self.db._session_token_file = os.path.join(
                        os.path.dirname(self.db.db_path) if hasattr(self.db, 'db_path') else '.',
                        '.session_token'
                    )
                    # Also create the temp file
                    try:
                        with open(self.db._session_token_file, 'w') as f:
                            f.write(token)
                    except Exception:
                        pass
                    _log('Session registry recreated automatically')
                except Exception as _reg_err:
                    _err(f'Session registry recreate failed: {_reg_err}')
                    # Just log — not a security threat, just missing setup
                    _log('Session registry missing — not a security threat')

            # Check temp file — auto-create if missing (startup scenario)
            token_file = getattr(self.db, '_session_token_file', None)
            if token_file:
                if not os.path.exists(token_file):
                    # Auto-create the temp file instead of triggering alert
                    try:
                        import uuid as _uuid
                        token = str(_uuid.uuid4())
                        with open(token_file, 'w') as f:
                            f.write(token)
                        _log('Session token file recreated automatically')
                    except Exception as _tf_err:
                        _err(f'Session token file recreate failed: {_tf_err}')
                        # Only log — not a security threat during startup

        except ImportError:
            pass   # non-Windows — skip registry check
        except Exception as e:
            _err(f"session_guard: {e}")

    # ══════════════════════════════════════════════════════════════
    # THREAD 3 — AUTO HEALER
    # ══════════════════════════════════════════════════════════════

    def _thread_auto_healer(self):
        """
        Fixes SAFE issues automatically. Never touches business data.
        Heals: backup age, WAL file, log rotation, DB integrity.
        """
        healed = []

        # ── Heal 1: Backup age ────────────────────────────────────
        try:
            secret_dir  = r"C:\ProgramData\AurumOS"
            backup_path = os.path.join(secret_dir, 'aurum_backup.db')
            if os.path.exists(backup_path):
                age_hours = (time.time() - os.path.getmtime(backup_path)) / 3600
                max_age   = float(self._thresholds.get('backup_max_age_hours', 2))
                if age_hours > max_age:
                    self.db._mirror_data() if hasattr(self.db, '_mirror_data') else None
                    healed.append(f"Backup triggered (was {int(age_hours)}h old)")
                    self._record_event(
                        event_type   = 'auto_heal_backup',
                        severity     = SEV_LOW,
                        score        = 5,
                        detail       = f"Backup was {int(age_hours)}h old — triggered",
                        action_taken = 'HEALED',
                        auto_healed  = 1
                    )
        except Exception as e:
            _err(f"heal backup: {e}")

        # ── Heal 2: WAL file ──────────────────────────────────────
        try:
            wal = self.db.db_path + '-wal'
            if os.path.exists(wal):
                wal_mb = os.path.getsize(wal) / (1024 * 1024)
                if wal_mb > 50:
                    conn = sqlite3.connect(self.db.db_path, timeout=5)
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
                    healed.append(f"WAL checkpoint ({wal_mb:.0f}MB)")
                    self._record_event(
                        event_type   = 'auto_heal_wal',
                        severity     = SEV_LOW,
                        score        = 5,
                        detail       = f"WAL was {wal_mb:.0f}MB — checkpointed",
                        action_taken = 'HEALED',
                        auto_healed  = 1
                    )
        except Exception as e:
            _err(f"heal WAL: {e}")

        # ── Heal 3: Log rotation ──────────────────────────────────
        try:
            base    = os.path.dirname(self.db.db_path)
            log_dir = os.path.join(os.path.dirname(base), 'logs')
            log_f   = os.path.join(log_dir, 'aurumos.log')
            if os.path.exists(log_f) and os.path.getsize(log_f) > 500_000:
                # Keep last 200 lines
                with open(log_f, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                with open(log_f, 'w', encoding='utf-8') as f:
                    f.writelines(lines[-200:])
                healed.append("Log rotated")
                self._record_event(
                    event_type   = 'auto_heal_log',
                    severity     = SEV_LOW,
                    score        = 2,
                    detail       = f"Log rotated ({len(lines)} lines → 200)",
                    action_taken = 'HEALED',
                    auto_healed  = 1
                )
        except Exception as e:
            _err(f"heal log: {e}")

        # ── Heal 4: Stale registry token (after crash) ────────────
        try:
            import winreg as _wr
            REG_PATH   = r'SOFTWARE\Microsoft\InputMethod\AOS'
            ram_token  = getattr(self.db, '_session_token', None)
            if not ram_token and not self._session_active:
                # App not running session — safe to clear stale registry
                try:
                    key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, REG_PATH, 0, _wr.KEY_SET_VALUE)
                    _wr.DeleteValue(key, 'SessionCache')
                    _wr.CloseKey(key)
                    healed.append("Stale registry token cleared")
                    self._record_event(
                        event_type   = 'auto_heal_registry',
                        severity     = SEV_LOW,
                        score        = 3,
                        detail       = "Stale session registry cleared after crash",
                        action_taken = 'HEALED',
                        auto_healed  = 1
                    )
                except FileNotFoundError:
                    pass   # already clean
        except ImportError:
            pass
        except Exception as e:
            _err(f"heal registry: {e}")

        # ── Heal 5: DB integrity check ────────────────────────────
        try:
            conn   = sqlite3.connect(self.db.db_path, timeout=5)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            if result and result[0] != 'ok':
                _err(f"DB integrity failed: {result[0]}")
                self._record_event(
                    event_type   = 'db_integrity_fail',
                    severity     = SEV_CRITICAL,
                    score        = 85,
                    detail       = f"Integrity check: {result[0]}",
                    action_taken = 'HEALING'
                )
                # Attempt restore from backup
                if hasattr(self.db, 'restore_from_backup'):
                    self.db.restore_from_backup()
                    healed.append("DB restored from backup")
                    # A restore legitimately rewrites the whole DB — allowlist it
                    # so the next watchdog cycle does not mistake it for tampering.
                    try:
                        self.allow_known_change('db_external_change', 'auto-heal DB restore')
                    except Exception:
                        pass
                    self._queue_alert(
                        subject = 'AurumOS BASTION: DB Corruption — Auto-Restored',
                        body    = (
                            f"CRITICAL: Database integrity check failed.\n"
                            f"BASTION AI automatically restored from backup.\n"
                            f"Error: {result[0]}\n"
                            f"Time: {datetime.now().strftime('%d %b %Y %I:%M %p')}\n\n"
                            f"Please verify data with client immediately."
                        )
                    )
        except Exception as e:
            _err(f"heal integrity: {e}")

        # Any heal that touched the DB is a legitimate in-app write — tell the
        # watchdog so it does not mistake the change for external tampering.
        if healed:
            try:
                self.notify_write()
            except Exception:
                pass
            _log(f"Auto-healed: {', '.join(healed)}")

    # ══════════════════════════════════════════════════════════════
    # THREAD 4 — PATTERN LEARNER
    # ══════════════════════════════════════════════════════════════

    def _thread_pattern_learner(self):
        """
        Runs every 24h. Analyzes event history.
        Updates bastion_learning table with refined thresholds.
        """
        try:
            _log("Pattern learning cycle started...")
            month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

            with self.db._get_connection() as conn:
                # Learn: average events per day
                rows = conn.execute(
                    "SELECT DATE(ts) as day, COUNT(*) as cnt "
                    "FROM bastion_events WHERE ts >= ? "
                    "GROUP BY DATE(ts)",
                    (month_ago,)
                ).fetchall()

                if rows:
                    avg_daily = sum(r['cnt'] for r in rows) / len(rows)
                    max_daily = max(r['cnt'] for r in rows)
                    self._set_learned('avg_daily_events', avg_daily)
                    self._set_learned('max_daily_events', max_daily)
                    # Threshold: flag if daily events > 3x average
                    self._set_learned('daily_event_threshold', avg_daily * 3)

                # Learn: login hours from login_log
                login_rows = conn.execute(
                    "SELECT CAST(strftime('%H', login_time) AS INTEGER) as hr "
                    "FROM login_log WHERE login_time >= ?",
                    (month_ago,)
                ).fetchall()

                if len(login_rows) >= 5:
                    hours = [r['hr'] for r in login_rows]
                    min_hr = max(0,  min(hours) - 1)
                    max_hr = min(23, max(hours) + 1)
                    self._set_learned('login_hour_min', min_hr)
                    self._set_learned('login_hour_max', max_hr)
                    _log(f"Learned login hours: {min_hr}:00 - {max_hr}:00")

                # LEARNING FIX: a busy shop must NOT be pushed into the
                # auto-suspend zone. Many DB changes during normal business
                # (billing, stock, katti) are HEALTHY — we should grow MORE
                # lenient, not more aggressive. The threshold now only nudges
                # the *alert severity* of genuine idle edits; the watchdog caps
                # business-table-only scores below SUSPEND on its own.
                ext_edits = conn.execute(
                    "SELECT COUNT(*) FROM bastion_events "
                    "WHERE event_type='db_external_edit' AND ts >= ?",
                    (month_ago,)
                ).fetchone()[0]

                suspicious_only = conn.execute(
                    "SELECT COUNT(*) FROM bastion_events "
                    "WHERE event_type='db_suspicious_change' AND ts >= ?",
                    (month_ago,)
                ).fetchone()[0]

                if suspicious_only > 0 and ext_edits == 0:
                    # Near-misses that never escalated = busy, healthy shop.
                    # Stay lenient so alerts remain informational, never locking.
                    self._set_learned('external_edit_threshold', 60)
                    _log(f"Learned: busy shop ({suspicious_only} near-misses, 0 escalations) — threshold kept lenient at 60")
                elif ext_edits > 0:
                    # Real escalations observed — keep moderate (never into suspend zone).
                    self._set_learned('external_edit_threshold', 70)
                    _log(f"Note: {ext_edits} confirmed external edits — threshold set to 70")
                else:
                    self._set_learned('external_edit_threshold', 65)

                # Learn: backup frequency
                backup_heals = conn.execute(
                    "SELECT COUNT(*) FROM bastion_events "
                    "WHERE event_type='auto_heal_backup' AND ts >= ?",
                    (month_ago,)
                ).fetchone()[0]

                # If backup needed > 10x this month, tighten interval
                if backup_heals > 10:
                    self._set_learned('backup_max_age_hours', 1)
                else:
                    self._set_learned('backup_max_age_hours', 2)

            # Reload thresholds
            self._thresholds = self._load_thresholds()
            _log(f"Pattern learning complete. Thresholds: {self._thresholds}")

            # Write weekly report to log
            self._write_weekly_report()

        except Exception as e:
            _err(f"pattern_learner: {e}")

    # ══════════════════════════════════════════════════════════════
    # THREAD 5 — ALERT SENDER
    # ══════════════════════════════════════════════════════════════

    def _thread_alert_sender(self):
        """
        Every 5 minutes: check for queued alerts.
        If internet available → send email.
        Works offline — queues until connection available.
        Also pushes events to the health dashboard (if configured).
        """
        if self._has_internet():
            self.push_to_health_dashboard()

        try:
            with self.db._get_connection() as conn:
                # Older DBs may not have the 'recipient' column yet — select defensively.
                try:
                    pending = conn.execute(
                        "SELECT id, subject, body, html_body, recipient FROM bastion_alerts WHERE sent=0 ORDER BY id ASC LIMIT 5"
                    ).fetchall()
                except Exception:
                    pending = conn.execute(
                        "SELECT id, subject, body, html_body FROM bastion_alerts WHERE sent=0 ORDER BY id ASC LIMIT 5"
                    ).fetchall()

            if not pending:
                return

            # Check internet (quick DNS check — no data sent)
            if not self._has_internet():
                _log(f"{len(pending)} alert(s) queued — no internet yet")
                return

            sent_ids = []
            for alert in pending:
                try:
                    recipient = alert['recipient']
                except (IndexError, KeyError):
                    recipient = None
                success = self._send_email(alert['subject'], alert['body'],
                                           alert['html_body'] or None, recipient)
                if success:
                    sent_ids.append(alert['id'])
                    _log(f"Alert sent: {alert['subject'][:40]}")

            if sent_ids:
                ts_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with self.db._get_connection() as conn:
                    for aid in sent_ids:
                        conn.execute(
                            "UPDATE bastion_alerts SET sent=1, sent_at=? WHERE id=?",
                            (ts_now, aid)
                        )
                    conn.commit()

        except Exception as e:
            _err(f"alert_sender: {e}")

    # ══════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════

    def _record_event(self, event_type, severity=SEV_LOW, score=0,
                      detail='', actor='system', action_taken='LOGGED', auto_healed=0):
        """Write one event to bastion_events table."""
        try:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with self.db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO bastion_events "
                    "(ts, event_type, severity, score, detail, actor, action_taken, auto_healed) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (ts, event_type, severity, score,
                     str(detail)[:500], actor, action_taken, auto_healed)
                )
                conn.commit()
            _log(f"Event [{severity}] {event_type} score={score} — {str(detail)[:60]}")
        except Exception as e:
            _err(f"record_event: {e}")

    # ══════════════════════════════════════════════════════════════
    # SMARTER DECISIONS — escalation engine + corroboration + allowlist
    # ══════════════════════════════════════════════════════════════

    def _bump_escalation(self, attack_type):
        """Increment + persist the escalation level for a threat class.
        Decays back to 1 if the last event was older than ESCALATION_WINDOW,
        so isolated incidents never accumulate into a permanent lock."""
        try:
            now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            now_ts = time.time()
            with self.db._get_connection() as conn:
                row = conn.execute(
                    "SELECT level, last_ts FROM bastion_escalation WHERE attack_type=?",
                    (attack_type,)
                ).fetchone()
                level = 1
                if row:
                    try:
                        last_ts = datetime.strptime(row['last_ts'], '%Y-%m-%d %H:%M:%S').timestamp()
                    except Exception:
                        last_ts = 0
                    level = row['level'] + 1 if (now_ts - last_ts) < ESCALATION_WINDOW else 1
                conn.execute(
                    "INSERT OR REPLACE INTO bastion_escalation(attack_type,level,last_ts,updated_at) "
                    "VALUES(?,?,?,?)",
                    (attack_type, level, now, now)
                )
                conn.commit()
                return level
        except Exception as e:
            _err(f"bump_escalation: {e}")
            return 1

    def _distinct_recent_signals(self):
        """Count distinct threat signal types in the last CORRO_WINDOW secs.
        Used for corroboration: a lone detector should never auto-suspend."""
        now = time.time()
        self._recent_signals = [(t, e) for (t, e) in self._recent_signals if now - t < CORRO_WINDOW]
        return len({e for (t, e) in self._recent_signals})

    def _decide_action(self, attack_type, critical):
        """Smarter decision: progressive escalation + corroboration.
          - CRITICAL signals (exe tamper, fingerprint mismatch, …) suspend now.
          - Others escalate: 1=WARN, 2=RESTRICT, 3+=SUSPEND.
          - A non-critical signal only ever suspends if CORROBORATED by a
            second, independent detector (>=2 distinct recent signals). This
            is what stops a single false positive from locking the user out."""
        level   = self._bump_escalation(attack_type)
        distinct = self._distinct_recent_signals()
        if critical:
            return 'SUSPEND', level
        if level >= SUSPEND_LEVEL and distinct >= 2:
            return 'SUSPEND', level
        if level >= 2:
            return 'RESTRICT', level
        return 'WARN', level

    def _evaluate_threat(self, attack_type, detail, critical=False):
        """Central smarter-decision entry point. Replaces ad-hoc _auto_suspend
        calls. Records the signal, decides the action, and executes it."""
        # Grace period: skip all alerts for first 2 minutes after startup
        elapsed = time.time() - self._startup_time
        if elapsed < self._grace_period:
            _log(f'Skipping threat {attack_type} — grace period ({int(elapsed)}s/{self._grace_period}s)')
            return

        self._recent_signals.append((time.time(), attack_type))
        action, level = self._decide_action(attack_type, critical)

        if action == 'SUSPEND':
            self._record_event(
                event_type = attack_type,
                severity   = SEV_CRITICAL,
                score      = 95,
                detail     = f"[ESCALATED→SUSPEND L{level}] {detail}",
                action_taken = 'SUSPENDED'
            )
            self._auto_suspend(attack_type, detail)
            return

        if action == 'RESTRICT':
            self._restricted = True
            self._record_event(
                event_type = attack_type,
                severity   = SEV_HIGH,
                score      = 70,
                detail     = f"[RESTRICTED L{level}] {detail}",
                action_taken = 'RESTRICTED'
            )
            self._queue_alert(
                subject = 'AurumOS BASTION: Restricted Mode Engaged',
                body    = (
                    f"Threat: {attack_type}\nEscalation level: {level}\n\n"
                    f"BASTION entered RESTRICTED mode — sensitive actions are "
                    f"blocked but billing can continue, pending review.\n"
                    f"Detail: {detail}\n"
                    f"Time: {datetime.now().strftime('%d %b %Y %I:%M %p')}"
                )
            )
            return

        # WARN
        self._record_event(
            event_type = attack_type,
            severity   = SEV_MEDIUM,
            score      = 40,
            detail     = f"[WARN L{level}] {detail}",
            action_taken = 'WATCHING'
        )
        self._queue_alert(
            subject = 'AurumOS BASTION: Suspicious Activity (Watch)',
            body    = (
                f"Threat: {attack_type}\nEscalation level: {level}\n\n"
                f"No auto-suspend (single signal / low confidence).\n"
                f"Detail: {detail}\n"
                f"Time: {datetime.now().strftime('%d %b %Y %I:%M %p')}"
            )
        )

    def allow_known_change(self, event_type='db_external_change', note=''):
        """Admin / maintenance whitelist: tell BASTION a DB change at this
        moment is expected (backup restore, data import, schema fix). The next
        watchdog cycle will treat a matching change as legitimate and ignore it."""
        try:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with self.db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO bastion_allowlist(event_type,note,ts) VALUES(?,?,?)",
                    (event_type, str(note)[:200], ts)
                )
                conn.commit()
            _log(f"Allowlist added for {event_type}: {note}")
        except Exception as e:
            _err(f"allow_known_change: {e}")

    def _is_allowlisted(self, event_type):
        """True if an admin allowlist entry for this event type is still valid."""
        try:
            cutoff = (datetime.now() - timedelta(seconds=ALLOW_WINDOW)).strftime('%Y-%m-%d %H:%M:%S')
            with self.db._get_connection() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM bastion_allowlist WHERE event_type=? AND ts >= ?",
                    (event_type, cutoff)
                ).fetchone()[0]
                return n > 0
        except Exception:
            return False

    def _auto_suspend(self, attack_type, detail):
        """Trigger BASTION suspension and queue alert."""
        _err(f"AUTO-SUSPEND: {attack_type} — {detail}")
        try:
            self.db.bastion_suspend(attack_type, detail)

            # Capture metadata for forensics BEFORE generating the report
            try:
                status = self.db.bastion_get_status()
                ts = status.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                lock_code = self.db._machine_fingerprint()[:8].upper()
            except:
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                lock_code = 'UNKNOWN'

            # TRIGGER FORENSIC PDF GENERATION (Background Thread)
            # This will save the PDF to C:\AurumOS\Reports and save the path in app_config
            generate_bastion_report_async(self.db, attack_type, detail, ts, lock_code, None)

            self._record_event(
                event_type   = 'bastion_auto_suspend',
                severity     = SEV_CRITICAL,
                score        = 100,
                detail       = f"Auto-suspended: {attack_type} — {detail}",
                action_taken = 'SUSPENDED'
            )

            # CRITICAL: must be the EXACT raw timestamp string stored by
            # bastion_suspend() -- this is what the unlock key formula
            # hashes against. Generating a second, separately-formatted
            # "now" here (as before) silently produces a timestamp that
            # looks similar but never matches, making every key wrong.
            try:
                status = self.db.bastion_get_status()
                ts_raw = status.get('timestamp', '')
            except Exception:
                ts_raw = ''
            ts = ts_raw  # exact string for the keygen prompt -- copy this one
            try:
                lock_code = self.db._machine_fingerprint()[:8].upper()
            except Exception:
                lock_code = 'UNKNOWN'
            try:
                shop_id = self.db.get_or_create_shop_id()
            except Exception:
                shop_id = 'UNKNOWN'
            try:
                with self.db._get_connection() as conn:
                    row = conn.execute("SELECT biz_name FROM business_profile WHERE id=1").fetchone()
                    biz_name = (row['biz_name'] if row and row['biz_name'] else '').strip()
                    if not biz_name:
                        row2 = conn.execute("SELECT value FROM app_config WHERE key='business_name'").fetchone()
                        biz_name = (row2['value'] if row2 and row2['value'] else '').strip()
                biz_name = biz_name or 'Unknown Jeweller (name not set in app)'
            except Exception:
                biz_name = 'Unknown Jeweller (lookup failed)'
            try:
                device_id = self.db.get_or_create_device_id()
            except Exception:
                device_id = 'UNKNOWN'
            try:
                hostname = os.environ.get('COMPUTERNAME') or __import__('socket').gethostname()
            except Exception:
                hostname = 'UNKNOWN'
            try:
                week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                with self.db._get_connection() as conn:
                    events_week = conn.execute(
                        "SELECT COUNT(*) FROM bastion_events WHERE ts >= ?", (week_ago,)
                    ).fetchone()[0]
            except Exception:
                events_week = '?'

            titles = {
                'session_tamper':       'Session Tampering Detected',
                'db_edit':              'Database Tampering Detected',
                'fingerprint_mismatch': 'Unauthorized PC Access Detected',
                'exe_tamper':           'EXE File Modified',
                'replay_attack':        'Replay Attack Detected',
            }
            attack_title = titles.get(attack_type, attack_type.replace('_', ' ').title())

            plain_body = (
                f"CRITICAL: BASTION AI automatically suspended an account.\n\n"
                f"Jeweller     : {biz_name}\n"
                f"Attack Type : {attack_title} ({attack_type})\n"
                f"Detail      : {detail}\n"
                f"Lock Code   : {lock_code}\n"
                f"Shop ID     : {shop_id}\n"
                f"Device ID   : {device_id}\n"
                f"PC Name     : {hostname}\n"
                f"Time        : {ts}\n"
                f"Events (7d) : {events_week}\n\n"
                f"The client will see the BASTION red lock screen on every page.\n"
                f"Run unlock_keygen.py (mode 2) with the Lock Code above to generate\n"
                f"the 16-character BASTION key, then send it to the client.\n"
                f"Or run aurum_health.py for full diagnosis."
            )

            html_body = f"""
<div style="background:#f2efe7;padding:32px 16px;font-family:'Helvetica Neue',Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 30px rgba(14,12,9,0.08);">

    <!-- Header -->
    <div style="background:#0e0c09;padding:28px 32px;position:relative;">
      <div style="height:3px;width:100%;position:absolute;top:0;left:0;background:linear-gradient(90deg,#a87d1e,#c9a227,transparent);"></div>
      <table style="width:100%;"><tr>
        <td>
          <span style="font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#c9a227;font-weight:700;">&#9632; AurumOS BASTION Security</span>
        </td>
        <td style="text-align:right;">
          <span style="display:inline-block;background:rgba(239,68,68,0.15);color:#ef4444;font-size:10px;font-weight:700;letter-spacing:1px;padding:4px 10px;border-radius:20px;border:1px solid rgba(239,68,68,0.4);">CRITICAL</span>
        </td>
      </tr></table>
      <div style="margin-top:14px;display:inline-block;background:rgba(201,162,39,0.15);border:1px solid rgba(201,162,39,0.4);border-radius:6px;padding:4px 10px;">
        <span style="color:#c9a227;font-size:12px;font-weight:700;">&#128274; {biz_name}</span>
      </div>
      <h1 style="color:#ffffff;font-size:24px;font-weight:800;margin:10px 0 4px;letter-spacing:0.5px;">Account Auto-Suspended</h1>
      <p style="color:#a89878;font-size:13px;margin:0;">{attack_title}</p>
    </div>

    <!-- Body -->
    <div style="padding:28px 32px;">
      <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:14px 18px;margin-bottom:24px;">
        <p style="margin:0;font-size:13.5px;color:#7f1d1d;line-height:1.6;">{detail}</p>
      </div>

      <div style="margin-bottom:8px;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#a87d1e;font-weight:700;">Incident Details</div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;width:140px;">Jeweller</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;font-weight:700;">{biz_name}</td></tr>
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;width:140px;">Attack Type</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;font-weight:700;">{attack_type}</td></tr>
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;">Lock Code</td><td style="padding:9px 0;"><span style="font-family:'Courier New',monospace;font-weight:700;color:#a87d1e;background:#faf6ea;padding:3px 8px;border-radius:5px;letter-spacing:1px;">{lock_code}</span></td></tr>
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;">Shop ID</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;font-family:'Courier New',monospace;">{shop_id}</td></tr>
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;">Device ID</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;font-family:'Courier New',monospace;">{device_id}</td></tr>
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;">PC Name</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;">{hostname}</td></tr>
        <tr style="border-bottom:1px solid #f0ece2;"><td style="padding:9px 0;color:#7a7268;font-size:13px;">Events (7 days)</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;">{events_week}</td></tr>
        <tr><td style="padding:9px 0;color:#7a7268;font-size:13px;">Time</td><td style="padding:9px 0;color:#2c2a24;font-size:13px;">{ts}</td></tr>
      </table>

      <div style="background:#faf6ea;border:1px solid #f0e6cc;border-radius:10px;padding:18px 20px;">
        <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#a87d1e;font-weight:700;margin-bottom:8px;">Next Steps</div>
        <p style="margin:0 0 8px;font-size:13px;color:#5c5347;line-height:1.7;">The client is now seeing the BASTION lock screen on every page of the app &mdash; all saves are blocked until unlocked.</p>
        <p style="margin:0;font-size:13px;color:#5c5347;line-height:1.7;">Run <code style="background:#f0e6cc;padding:2px 6px;border-radius:4px;font-family:'Courier New',monospace;">unlock_keygen.py</code> (mode 2) using the Lock Code above to generate the 16-character BASTION key, then send it to the client.</p>
      </div>
    </div>

    <!-- Footer -->
    <div style="background:#faf8f3;padding:16px 32px;border-top:1px solid #f0ece2;text-align:center;">
      <span style="font-size:11px;color:#a89878;">AurumOS BASTION AI &middot; Automated Security Alert</span>
    </div>
  </div>
</div>
"""

            self._queue_alert(
                subject   = "AurumOS BASTION ALERT: Account Auto-Suspended",
                body      = plain_body,
                html_body = html_body
            )
        except Exception as e:
            _err(f"auto_suspend: {e}")

    def _queue_alert(self, subject, body, html_body=''):
        """Save alert to DB — sent when internet available."""
        # Grace period: skip all alerts for first 2 minutes after startup
        elapsed = time.time() - self._startup_time
        if elapsed < self._grace_period:
            _log(f'Skipping alert — grace period ({int(elapsed)}s/{self._grace_period}s)')
            return
        try:
            with self.db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO bastion_alerts (subject, body, html_body) VALUES (?,?,?)",
                    (subject, body, html_body)
                )
                conn.commit()
            _log(f"Alert queued: {subject[:40]}")
        except Exception as e:
            _err(f"queue_alert: {e}")

    def _send_email(self, subject, body, html_body=None, recipient=None):
        """Send email via Gmail SMTP. Returns True on success.
        `recipient` overrides the default ALERT_EMAIL_TO (used for owner
        staff-conduct alerts); falls back to the default when blank/invalid."""
        if not ALERT_EMAIL_FROM or 'your.gmail' in ALERT_EMAIL_FROM or '@' not in ALERT_EMAIL_FROM:
            _err("!!! EMAIL NOT SENT -- ALERT_EMAIL_FROM is still a placeholder in bastion_ai.py !!!")
            return False
        if not ALERT_EMAIL_PASSWORD or 'xxxx' in ALERT_EMAIL_PASSWORD:
            _err("!!! EMAIL NOT SENT -- ALERT_EMAIL_PASSWORD is still a placeholder in bastion_ai.py !!!")
            return False
        # Choose recipient: per-alert owner email if valid, else the default.
        to_addr = str(recipient).strip() if recipient else ''
        if '@' not in to_addr:
            to_addr = ALERT_EMAIL_TO
        try:
            pw = str(ALERT_EMAIL_PASSWORD).replace(' ', '')  # App Passwords often pasted with spaces

            msg = MIMEMultipart('alternative')
            msg['From']    = ALERT_EMAIL_FROM
            msg['To']      = to_addr
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))

            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=15) as server:
                server.login(ALERT_EMAIL_FROM, pw)
                server.sendmail(ALERT_EMAIL_FROM, to_addr, msg.as_string())
            _log(f"Email sent OK to {to_addr}")
            return True
        except smtplib.SMTPAuthenticationError as e:
            _err(f"send_email AUTH FAILED -- check App Password / 2FA enabled: {e}")
            return False
        except smtplib.SMTPException as e:
            _err(f"send_email SMTP error: {e}")
            return False
        except OSError as e:
            _err(f"send_email NETWORK error (no internet / blocked port 465?): {e}")
            return False
        except Exception as e:
            _err(f"send_email: {type(e).__name__}: {e}")
            return False

    def _has_internet(self):
        """Quick offline check — no data sent, just DNS resolution."""
        try:
            import socket
            socket.setdefaulttimeout(3)
            socket.gethostbyname('smtp.gmail.com')
            return True
        except Exception:
            return False

    def _load_thresholds(self):
        """Load learned thresholds from bastion_learning table."""
        defaults = {
            'backup_max_age_hours':    2,
            'external_edit_threshold': 65,
            'daily_event_threshold':   100,
            'login_hour_min':          6,
            'login_hour_max':          23,
        }
        try:
            with self.db._get_connection() as conn:
                rows = conn.execute("SELECT key, value FROM bastion_learning").fetchall()
            for r in rows:
                try:
                    defaults[r['key']] = float(r['value'])
                except Exception:
                    defaults[r['key']] = r['value']
        except Exception:
            pass
        return defaults

    def _set_learned(self, key, value):
        """Write one learned threshold to DB."""
        try:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with self.db._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bastion_learning(key,value,updated_at) VALUES(?,?,?)",
                    (key, str(value), ts)
                )
                conn.commit()
        except Exception as e:
            _err(f"set_learned: {e}")

    def _write_weekly_report(self):
        """Append weekly report to the AurumOS log file."""
        try:
            report = self.get_weekly_report()
            base    = os.path.dirname(self.db.db_path)
            log_dir = os.path.join(os.path.dirname(base), 'logs')
            os.makedirs(log_dir, exist_ok=True)
            rpt_path = os.path.join(log_dir, 'bastion_report.log')

            lines = [
                "",
                "=" * 55,
                f"  BASTION AI Weekly Report",
                f"  Generated: {datetime.now().strftime('%d %b %Y %I:%M %p')}",
                "=" * 55,
                f"  Total events    : {report.get('total_events', 0)}",
                f"  Auto-healed     : {report.get('auto_healed', 0)}",
                f"  HIGH threats    : {report.get('high_threats', 0)}",
                "",
                "  Learned Thresholds:",
            ]
            for k, v in report.get('thresholds', {}).items():
                lines.append(f"    {k}: {v}")

            lines.append("")
            lines.append("  Recent Events:")
            for ev in report.get('recent', [])[:10]:
                lines.append(
                    f"    [{ev.get('severity','?'):8}] {ev.get('ts','')[:16]}  "
                    f"{ev.get('event_type','')}  score={ev.get('score',0)}"
                )
            lines.append("=" * 55)
            lines.append("")

            with open(rpt_path, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines))

            _log(f"Weekly report written: {rpt_path}")
        except Exception as e:
            _err(f"write_weekly_report: {e}")