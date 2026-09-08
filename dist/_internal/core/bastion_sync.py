import os
import sys
import time
import threading
from datetime import datetime

_state = {
    "db_watchdog_enabled": True,
    "session_guard_enabled": True,
    "file_integrity_enabled": True,
    "anti_debugger_enabled": True,
    "honeypot_enabled": True,
    "auto_healer_enabled": True,
    "pattern_learner_enabled": True,
    "alert_sender_enabled": True,
}
_lock = threading.Lock()
_sb = None

# ── Log file next to your .exe or .py ──────────────
if getattr(__builtins__, '__spec__', None):
    # Running from EXE
    _BASE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(_BASE, "bastion.log")


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    # Always write to file — works in EXE and dev
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # Also try terminal — works in dev mode
    try:
        print(line, flush=True)
    except Exception:
        pass


def _get_sb():
    global _sb
    if _sb:
        return _sb
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
        if url and key:
            _sb = create_client(url, key)
            _log("Connected to Supabase")
        else:
            _log("WARNING: SUPABASE_URL or KEY not set")
    except Exception as e:
        _log(f"Supabase init failed: {e}")
    return _sb


def is_enabled(feature_id: str) -> bool:
    with _lock:
        return _state.get(feature_id, True)


def _poll():
    sb = _get_sb()
    if not sb:
        return
    try:
        res = (
            sb.table("global_bastion_settings")
            .select("*")
            .eq("id", 1)
            .single()
            .execute()
        )
        if res.data:
            with _lock:
                for key in _state:
                    if key in res.data:
                        old = _state[key]
                        new = bool(res.data[key])
                        if old != new:
                            _log(f"⚡ {key}: {'ON' if old else 'OFF'} → {'ON' if new else 'OFF'}")
                        _state[key] = new
    except Exception as e:
        _log(f"Sync error: {e}")


def _loop(interval):
    while True:
        _poll()
        time.sleep(interval)


def start_sync(interval=30):
    _log("=" * 40)
    _log("BASTION SYNC STARTING")
    _log(f"Log: {LOG_FILE}")
    _poll()
    for k, v in _state.items():
        _log(f"  {'✅' if v else '❌'} {k}")
    t = threading.Thread(target=_loop, args=(interval,), daemon=True)
    t.start()
    _log(f"Syncing every {interval}s")
    _log("=" * 40)