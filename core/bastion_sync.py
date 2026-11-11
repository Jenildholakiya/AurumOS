# -*- coding: utf-8 -*-
"""
Bastion Settings Sync — polls the admin REST API every 30s
and writes toggle state into the local DB so BastionAI threads
respect the dashboard switches.

API:  GET https://aurum-os-admin.vercel.app/api/bastion-settings
"""

import threading, time, os, sys, json
from datetime import datetime

_API_URL = "https://aurum-os-admin.vercel.app/api/bastion-settings"

_FEATURES = [
    "db_watchdog_enabled",
    "session_guard_enabled",
    "file_integrity_enabled",
    "anti_debugger_enabled",
    "honeypot_enabled",
    "auto_healer_enabled",
    "pattern_learner_enabled",
    "alert_sender_enabled",
    "dpdp_retention_auto_purge",
    "dpdp_breach_notification",
    "dpdp_consent_enforcement",
]

_lock = threading.Lock()
_state = {k: True for k in _FEATURES}
_db = None

# ── Logging ──────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(_BASE, "bastion_sync.log")


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ── Public API ───────────────────────────────────────────────────

def is_enabled(feature_id: str) -> bool:
    with _lock:
        return _state.get(feature_id, True)


def _fetch():
    """GET the admin REST API and return the data dict, or None."""
    import urllib.request
    try:
        req = urllib.request.Request(_API_URL, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("status") == "ok" and body.get("data"):
            return body["data"]
    except Exception as e:
        _log(f"Fetch error: {e}")
    return None


def _poll():
    data = _fetch()
    if not data:
        return
    with _lock:
        for key in _FEATURES:
            if key in data:
                old = _state[key]
                new = bool(data[key])
                if old != new:
                    _log(f"⚡ {key}: {'ON' if old else 'OFF'} → {'ON' if new else 'OFF'}")
                    if _db:
                        try:
                            _db.set_global_setting(key, new)
                        except Exception as e:
                            _log(f"DB write failed for {key}: {e}")
                _state[key] = new


def _loop(interval):
    while True:
        _poll()
        time.sleep(interval)


def start_sync(db_manager=None, interval=30):
    """Start the background sync. Pass db_manager so toggles persist locally."""
    global _db
    _db = db_manager
    _log("=" * 40)
    _log("BASTION SETTINGS SYNC STARTING")
    _log(f"API: {_API_URL}")
    _poll()
    for k, v in _state.items():
        _log(f"  {'✅' if v else '❌'} {k}")
    t = threading.Thread(target=_loop, args=(interval,), daemon=True)
    t.start()
    _log(f"Syncing every {interval}s")
    _log("=" * 40)
