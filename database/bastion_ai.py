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


# ── ALERT CONFIG — fill these in ──────────────────────────────────
# Your Gmail address and App Password (not regular password)
# Go to: Google Account → Security → App Passwords → Generate
ALERT_EMAIL_FROM     = 'aurumos.software@gmail.com'
ALERT_EMAIL_PASSWORD = 'ttyx niad ocea oebo'
ALERT_EMAIL_TO       = 'jenildholakiya8305@gmail.com'

# Threat score thresholds
SCORE_WARN    = 40    # log + queue alert
SCORE_SUSPEND = 75    # auto-suspend account


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
        self._daily_score      = 0
        self._event_count_day  = 0

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

    def _runner(self, name, target, interval):
        """Wraps each thread with error recovery and sleep."""
        # Stagger startup so all threads don't hit DB at once
        import random
        time.sleep(random.uniform(2, 8))

        while self._running:
            try:
                target()
            except Exception as e:
                _err(f"{name} error: {e}")
            # Sleep in small chunks so stop() is responsive
            for _ in range(interval):
                if not self._running:
                    return
                time.sleep(1)

    # ══════════════════════════════════════════════════════════════
    # THREAD 1 — DB WATCHDOG
    # ══════════════════════════════════════════════════════════════

    def _thread_db_watchdog(self):
        """
        Hashes row counts of all tables every 30s.
        If DB changed without a valid write in last 60s → threat.
        """
        current_hash = self._compute_db_hash()
        if current_hash is None:
            return

        if self._last_db_hash is None:
            self._last_db_hash = current_hash
            return

        if current_hash == self._last_db_hash:
            return   # no change — all good

        # DB changed — was it a legitimate write?
        seconds_since_write = time.time() - self._last_write_ts

        if seconds_since_write < 90:
            # App wrote to DB recently — legitimate
            self._last_db_hash = current_hash
            return

        # DB changed WITHOUT a recent app write → external edit detected
        detail = (
            f"DB hash changed without app write. "
            f"Last app write: {int(seconds_since_write)}s ago. "
            f"Old={self._last_db_hash[:8]} New={current_hash[:8]}"
        )
        score = 65
        # Check learned threshold
        if self._thresholds.get('external_edit_threshold'):
            score = int(self._thresholds['external_edit_threshold'])

        self._record_event(
            event_type   = 'db_external_edit',
            severity     = SEV_HIGH,
            score        = score,
            detail       = detail,
            action_taken = 'DETECTED'
        )
        self._last_db_hash = current_hash

        if score >= SCORE_SUSPEND:
            self._auto_suspend('db_edit', detail)
        else:
            self._queue_alert(
                subject = f"AurumOS BASTION: Database Tampering Detected",
                body    = (
                    f"Threat: External DB Edit\n"
                    f"Score: {score}/100\n"
                    f"Detail: {detail}\n"
                    f"Time: {datetime.now().strftime('%d %b %Y %I:%M %p')}\n\n"
                    f"Run aurum_health.py to investigate."
                )
            )

    def _compute_db_hash(self):
        """Hash row counts of all main tables."""
        try:
            tables = [
                'stock_inventory', 'sales_history', 'katti_vouchers',
                'katti_voucher_items', 'credit_ledger', 'admin_creds'
            ]
            parts = []
            with self.db._get_connection() as conn:
                for t in tables:
                    try:
                        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        parts.append(f"{t}:{n}")
                    except Exception:
                        parts.append(f"{t}:?")
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

            # Check registry
            try:
                key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, REG_PATH)
                _wr.QueryValueEx(key, 'SessionCache')
                _wr.CloseKey(key)
            except FileNotFoundError:
                self._record_event(
                    event_type   = 'session_registry_missing',
                    severity     = SEV_HIGH,
                    score        = 55,
                    detail       = 'Session registry key missing during active session',
                    action_taken = 'DETECTED'
                )
                self._queue_alert(
                    subject = 'AurumOS BASTION: Session Registry Tampered',
                    body    = (
                        f"Session registry key was deleted during an active session.\n"
                        f"This may indicate a session hijack attempt.\n"
                        f"Time: {datetime.now().strftime('%d %b %Y %I:%M %p')}"
                    )
                )
                return

            # Check temp file
            token_file = getattr(self.db, '_session_token_file', None)
            if token_file and not os.path.exists(token_file):
                score = 50
                self._record_event(
                    event_type   = 'session_file_missing',
                    severity     = SEV_MEDIUM,
                    score        = score,
                    detail       = f'Session temp file deleted: {token_file}',
                    action_taken = 'DETECTED'
                )
                if score >= SCORE_SUSPEND:
                    self._auto_suspend('session_tamper', 'Session temp file deleted during active session')

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

        if healed:
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

                # Learn: how often external edits happen
                # If never seen → set strict threshold
                ext_edits = conn.execute(
                    "SELECT COUNT(*) FROM bastion_events "
                    "WHERE event_type='db_external_edit' AND ts >= ?",
                    (month_ago,)
                ).fetchone()[0]

                if ext_edits == 0:
                    # Never seen — set strict
                    self._set_learned('external_edit_threshold', 65)
                else:
                    # Seen before — slightly relax (may be legitimate tool)
                    self._set_learned('external_edit_threshold', 75)
                    _log(f"Note: {ext_edits} external DB edits recorded this month")

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
                success = self._send_email(alert['subject'], alert['body'], alert['html_body'] or None)
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

    def _auto_suspend(self, attack_type, detail):
        """Trigger BASTION suspension and queue alert."""
        _err(f"AUTO-SUSPEND: {attack_type} — {detail}")
        try:
            self.db.bastion_suspend(attack_type, detail)
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

    def _send_email(self, subject, body, html_body=None):
        """Send email via Gmail SMTP. Returns True on success."""
        if not ALERT_EMAIL_FROM or 'your.gmail' in ALERT_EMAIL_FROM or '@' not in ALERT_EMAIL_FROM:
            _err("!!! EMAIL NOT SENT -- ALERT_EMAIL_FROM is still a placeholder in bastion_ai.py !!!")
            return False
        if not ALERT_EMAIL_PASSWORD or 'xxxx' in ALERT_EMAIL_PASSWORD:
            _err("!!! EMAIL NOT SENT -- ALERT_EMAIL_PASSWORD is still a placeholder in bastion_ai.py !!!")
            return False
        try:
            pw = str(ALERT_EMAIL_PASSWORD).replace(' ', '')  # App Passwords often pasted with spaces

            msg = MIMEMultipart('alternative')
            msg['From']    = ALERT_EMAIL_FROM
            msg['To']      = ALERT_EMAIL_TO
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))

            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=15) as server:
                server.login(ALERT_EMAIL_FROM, pw)
                server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.as_string())
            _log(f"Email sent OK to {ALERT_EMAIL_TO}")
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