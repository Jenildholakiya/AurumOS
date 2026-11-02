"""
Trial system manager for AurumOS.

Checks trial status on startup, shows countdown banner with escalating
urgency, locks the app on expiry with conversion CTA, syncs every 6 hours,
and caches for offline resilience (24h normal, 72h degraded).

Pattern follows auto_revoke.py / geo_fence.py — stdlib only.
"""

import json
import os
import sys
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

_API_BASE = "https://aurum-os-admin.vercel.app"
_TRIAL_ENDPOINT = "/api/trials/check"

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Module globals ──────────────────────────────────────────────────────
_lock = threading.Lock()
_license_key = ""
_machine_id = ""
_api_ref = None
_trial_state = {}
_cache_path = ""
_prev_days = None  # track changes for banner updates

# Cache expiry tiers (seconds)
_CACHE_NORMAL = 24 * 3600      # 24h — full features
_CACHE_DEGRADED = 72 * 3600    # 72h — degrade to lite
_SYNC_INTERVAL = 6 * 3600      # 6h

# Banner urgency tiers: (min_days, tier_name, colours)
_URGENCY_TIERS = [
    (14, "info",     "#065f46", "#ecfdf5", "#d1fae5"),   # green
    (7,  "warning",  "#92400e", "#fffbeb", "#fde68a"),   # yellow
    (3,  "urgent",   "#9a3412", "#fff7ed", "#fed7aa"),   # orange
    (1,  "critical", "#991b1b", "#fef2f2", "#fecaca"),   # red
    (0,  "expired",  "#991b1b", "#fef2f2", "#fecaca"),   # red
]


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [TRIAL] {msg}", flush=True)


def _err(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [TRIAL_ERR] {msg}", flush=True)


def _base_dir():
    return os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _BASE


# ── Cache ───────────────────────────────────────────────────────────────

def _cache_file():
    return os.path.join(_base_dir(), "database", ".trial_cache")


def _save_cache(state):
    try:
        path = _cache_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"state": state, "cached_at": time.time()}
        with open(path, "w") as f:
            json.dump(payload, f)
    except Exception as e:
        _err(f"Cache save failed: {e}")


def _load_cache():
    try:
        path = _cache_file()
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = json.load(f)
        age = time.time() - data.get("cached_at", 0)
        state = data.get("state", {})
        if age > _CACHE_DEGRADED:
            _log(f"Cache expired ({age/3600:.1f}h > 72h) — degrade to lite")
            state["degraded"] = True
            state["effective_plan"] = "lite"
        elif age > _CACHE_NORMAL:
            _log(f"Cache stale ({age/3600:.1f}h > 24h) — warn user")
            state["cache_stale"] = True
        return state
    except Exception as e:
        _err(f"Cache load failed: {e}")
        return None


# ── API ─────────────────────────────────────────────────────────────────

def _check_trial():
    global _trial_state, _prev_days
    url = (f"{_API_BASE}{_TRIAL_ENDPOINT}"
           f"?key={urllib.request.quote(_license_key)}"
           f"&machine_id={urllib.request.quote(_machine_id)}")
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "AurumOS-Desktop/1.0")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        _log(f"Check: is_trial={data.get('is_trial')} "
             f"days={data.get('days_remaining')} plan={data.get('plan')}")

        with _lock:
            _trial_state = {
                "is_trial": data.get("is_trial", False),
                "plan": data.get("plan", "lite"),
                "days_remaining": data.get("days_remaining", 0),
                "trial_end_ms": data.get("trial_end_ms"),
                "features": data.get("features", []),
                "machine_bound": data.get("machine_bound", True),
                "bound_machine_id": data.get("machine_id", ""),
                "status": "active" if data.get("days_remaining", 0) > 0 else "expired",
                "last_check": time.time(),
                "degraded": False,
                "cache_stale": False,
            }
        _save_cache(_trial_state)
        return _trial_state
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        _err(f"API check failed: {e}")
        cached = _load_cache()
        if cached:
            with _lock:
                _trial_state = cached
            return cached
        return _trial_state


# ── UI: Countdown Banner ───────────────────────────────────────────────

def _get_urgency(days):
    for min_days, tier, fg, bg, border in _URGENCY_TIERS:
        if days >= min_days:
            return tier, fg, bg, border
    return "expired", "#991b1b", "#fef2f2", "#fecaca"


def _show_countdown_banner(days, plan):
    if not _api_ref or not getattr(_api_ref, '_window', None):
        return
    tier, fg, bg, border = _get_urgency(days)
    plan_label = (plan or "Lite").capitalize()

    if days <= 0:
        _lock_for_expiry(plan_label)
        return

    if days == 1:
        msg = f"Last day of your {plan_label} trial — upgrade now to keep all features"
    elif days <= 3:
        msg = f"Only {days} days left in your {plan_label} trial"
    elif days <= 7:
        msg = f"{days} days remaining in your {plan_label} trial"
    else:
        msg = f"{days} days remaining in your {plan_label} trial"

    cta_html = ""
    if days <= 7:
        cta_html = (
            '<button onclick="window.pywebview && window.pywebview.api.navigate'
            "('login.html')\" style=\"margin-left:16px;padding:6px 18px;"
            "border-radius:6px;border:none;font-weight:600;font-size:13px;"
            f"cursor:pointer;background:{fg};color:{bg}\">"
            "Upgrade Now</button>"
        )

    js = f"""
    (function() {{
        var old = document.getElementById('aurum-trial-banner');
        if (old) old.remove();
        var bar = document.createElement('div');
        bar.id = 'aurum-trial-banner';
        bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99990;'
            + 'display:flex;align-items:center;justify-content:center;'
            + 'padding:10px 24px;font-family:system-ui,sans-serif;font-size:14px;'
            + 'font-weight:500;gap:8px;'
            + 'background:{bg};color:{fg};border-bottom:2px solid {border};';
        bar.innerHTML = '<span style="flex:1;text-align:center">'
            + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
            + 'stroke="currentColor" stroke-width="2" style="vertical-align:-3px;'
            + 'margin-right:8px"><circle cx="12" cy="12" r="10"/>'
            + '<polyline points="12 6 12 12 16 14"/></svg>'
            + '{msg}'
            + '</span>'
            + '{cta_html}'
            + '<button onclick="this.parentElement.style.display=\\'none\\'" '
            + 'style="background:none;border:none;cursor:pointer;padding:4px 8px;'
            + 'font-size:18px;color:{fg};opacity:0.6" title="Dismiss">&times;</button>';
        document.body.appendChild(bar);
        document.body.style.paddingTop = '44px';
    }})();
    """
    try:
        _api_ref._window.evaluate_js(js)
        _log(f"Banner shown: {tier} tier, {days} days")
    except Exception as e:
        _err(f"Banner inject failed: {e}")


def _lock_for_expiry(plan_label):
    if not _api_ref or not getattr(_api_ref, '_window', None):
        return

    key_display = _license_key[:4] + "****" + _license_key[-4:] if len(_license_key) >= 8 else _license_key

    js = """
    (function() {
        var old = document.getElementById('aurum-trial-banner');
        if (old) old.remove();
        var old2 = document.getElementById('aurum-trial-expired');
        if (old2) old2.remove();
        document.body.style.paddingTop = '0';
        var overlay = document.createElement('div');
        overlay.id = 'aurum-trial-expired';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;'
            + 'display:flex;align-items:center;justify-content:center;'
            + 'background:rgba(14,12,9,0.92);backdrop-filter:blur(8px);'
            + 'font-family:system-ui,sans-serif;color:#fafaf9;';
        overlay.innerHTML = '<div style="text-align:center;max-width:440px;padding:48px 40px">'
            + '<div style="width:64px;height:64px;margin:0 auto 24px;border-radius:50%;'
            + 'background:rgba(239,68,68,0.15);display:flex;align-items:center;'
            + 'justify-content:center"><svg width="28" height="28" viewBox="0 0 24 24" '
            + 'fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"/>'
            + '<polyline points="12 6 12 12 16 14"/></svg></div>'
            + '<h2 style="margin:0 0 12px;font-size:24px;font-weight:700">'
            + 'Your Trial Has Ended</h2>'
            + '<p style="margin:0 0 8px;color:#a8a29e;font-size:15px">'
            + 'Your """ + plan_label + """ evaluation period has concluded.</p>'
            + '<p style="margin:0 0 32px;color:#78716c;font-size:13px">'
            + 'License: """ + key_display + """</p>'
            + '<div style="display:flex;flex-direction:column;gap:12px;align-items:center">'
            + '<button onclick="window.pywebview && window.pywebview.api.navigate'
            + '(\\'login.html\\')" style="width:100%;max-width:280px;padding:14px 24px;'
            + 'border-radius:8px;border:none;font-weight:600;font-size:15px;cursor:pointer;'
            + 'background:#a87d1e;color:#fff">Upgrade Now</button>'
            + '<button onclick="window.pywebview && window.pywebview.api.recover_from_trial()'
            + '" style="width:100%;max-width:280px;padding:12px 24px;border-radius:8px;'
            + 'border:1px solid rgba(255,255,255,0.15);background:transparent;color:#d6d3d1;'
            + 'font-size:14px;cursor:pointer">Enter License Key</button>'
            + '<button onclick="window.open(\\'mailto:support@pentarch.agency?subject='
            + 'AurumOS+Trial+Expired\\')" style="background:none;border:none;color:#78716c;'
            + 'font-size:13px;cursor:pointer;padding:8px;text-decoration:underline">'
            + 'Contact Support</button>'
            + '</div></div>';
        document.body.appendChild(overlay);
    })();
    """
    try:
        _api_ref._window.evaluate_js(js)
        _log("Expiry lock screen shown")
    except Exception as e:
        _err(f"Expiry lock inject failed: {e}")


def _show_machine_warning():
    if not _api_ref or not getattr(_api_ref, '_window', None):
        return
    js = """
    (function() {
        if (document.getElementById('aurum-trial-machine-warn')) return;
        var d = document.createElement('div');
        d.id = 'aurum-trial-machine-warn';
        d.style.cssText = 'position:fixed;top:50px;right:24px;z-index:99991;'
            + 'background:#fef2f2;border:1px solid #fecaca;border-radius:10px;'
            + 'padding:16px 20px;max-width:340px;font-family:system-ui,sans-serif;'
            + 'font-size:13px;color:#991b1b;box-shadow:0 4px 20px rgba(0,0,0,0.08)';
        d.innerHTML = '<strong>Machine Mismatch</strong><br>'
            + 'This trial is registered to a different machine. '
            + 'Contact your administrator to transfer the trial.'
            + '<br><button onclick="this.parentElement.remove()" '
            + 'style="margin-top:8px;background:none;border:none;color:#991b1b;'
            + 'text-decoration:underline;cursor:pointer;font-size:12px">Dismiss</button>';
        document.body.appendChild(d);
        setTimeout(function(){ var el=document.getElementById('aurum-trial-machine-warn'); '
            + 'if(el) el.remove(); }, 30000);
    })();
    """
    try:
        _api_ref._window.evaluate_js(js)
        _log("Machine mismatch warning shown")
    except Exception as e:
        _err(f"Machine warning inject failed: {e}")


# ── Banner dismiss (called from SSE on trial→paid conversion) ──────────

def dismiss_banner():
    if not _api_ref or not getattr(_api_ref, '_window', None):
        return
    js = """
    (function() {
        var b = document.getElementById('aurum-trial-banner');
        if (b) b.remove();
        var e = document.getElementById('aurum-trial-expired');
        if (e) e.remove();
        document.body.style.paddingTop = '0';
    })();
    """
    try:
        _api_ref._window.evaluate_js(js)
    except Exception:
        pass


# ── Sync loop ───────────────────────────────────────────────────────────

def _sync_loop():
    while True:
        time.sleep(_SYNC_INTERVAL)
        state = _check_trial()
        if state.get("is_trial") and state.get("days_remaining") is not None:
            _show_countdown_banner(state["days_remaining"], state.get("plan"))


# ── Public API ──────────────────────────────────────────────────────────

def get_status():
    with _lock:
        return dict(_trial_state)


def start(license_key="", machine_id="", api_ref=None):
    global _license_key, _machine_id, _api_ref

    _license_key = license_key
    _machine_id = machine_id
    _api_ref = api_ref

    state = _check_trial()
    if not state.get("is_trial"):
        _log("Not a trial license — trial manager idle")
        return state

    days = state.get("days_remaining", 0)
    _log(f"Trial active: plan={state.get('plan')} days_remaining={days}")

    # Machine binding check
    bound_id = state.get("bound_machine_id", "")
    if bound_id and bound_id != _machine_id:
        _log(f"Machine mismatch: bound={bound_id[:8]} local={_machine_id[:8]}")
        _show_machine_warning()

    # Show countdown banner
    _show_countdown_banner(days, state.get("plan"))

    # Start background sync
    threading.Thread(target=_sync_loop, daemon=True).start()
    _log("6h sync loop started")

    return state
