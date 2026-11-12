"""
Geo-fence enforcement for AurumOS.

Detects the user's country via free geo-IP services, checks the admin
API for country restrictions, and shows a blocking overlay when the
license is not permitted in that region.

Pattern follows bastion_sync.py / auto_revoke.py — stdlib only.
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

_API_BASE = "https://aurum-os-admin.vercel.app"
_GEO_ENDPOINT = "/api/geo-fence/check"
_EVENTS_ENDPOINT = "/api/license-events"

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Session-level cache so we only hit the geo-IP service once
_geo_cache = None  # {"country_code": "US", "ip": "1.2.3.4", "country_name": "United States"}


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [GEO-FENCE] {msg}")


# ── Country detection ───────────────────────────────────────────────

def detect_country():
    """Detect the user's country from their public IP.

    Tries ip-api.com first, falls back to ipinfo.io.
    Returns dict with country_code, ip, country_name or None on failure.
    """
    global _geo_cache
    if _geo_cache is not None:
        return _geo_cache

    # Provider 1: ip-api.com (free, no key)
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=status,countryCode,country,query",
            headers={"User-Agent": "AurumOS-GeoFence/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") == "success":
            _geo_cache = {
                "country_code": data["countryCode"],
                "ip": data.get("query", ""),
                "country_name": data.get("country", ""),
            }
            _log(f"Detected country: {_geo_cache['country_code']} via ip-api.com")
            return _geo_cache
    except Exception as e:
        _log(f"ip-api.com failed: {e}")

    # Provider 2: ipinfo.io (free tier, no key for basic)
    try:
        req = urllib.request.Request(
            "https://ipinfo.io/json",
            headers={"User-Agent": "AurumOS-GeoFence/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if data.get("country"):
            _geo_cache = {
                "country_code": data["country"],
                "ip": data.get("ip", ""),
                "country_name": data.get("country", ""),
            }
            _log(f"Detected country: {_geo_cache['country_code']} via ipinfo.io")
            return _geo_cache
    except Exception as e:
        _log(f"ipinfo.io failed: {e}")

    _log("Could not detect country from any provider")
    return None


# ── Admin API check ─────────────────────────────────────────────────

def check_geo_fence(license_key, country_code):
    """Ask the admin server if this license is allowed in the given country.

    Returns dict: {"allowed": bool, "action": "none"|"warn"|"revoke"|"alert"}
    """
    url = (
        f"{_API_BASE}{_GEO_ENDPOINT}"
        f"?key={urllib.request.quote(license_key)}"
        f"&country={urllib.request.quote(country_code)}"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "AurumOS-GeoFence/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        _log(f"Geo-fence response: allowed={data.get('allowed')}, action={data.get('action')}")
        return {
            "allowed": data.get("allowed", True),
            "action": data.get("action", "none"),
        }
    except Exception as e:
        _log(f"Geo-fence API error: {e}")
        return {"allowed": True, "action": "none"}


# ── Event logging ───────────────────────────────────────────────────

def _log_event(license_key, event_type, details=None):
    if details is None:
        details = {}
    payload = {
        "license_key": license_key,
        "machine_id": "",
        "event_type": event_type,
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    url = _API_BASE + _EVENTS_ENDPOINT
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "AurumOS-GeoFence/1.0")
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


# ── Blocked screen ──────────────────────────────────────────────────

def _show_blocked_screen(api_ref, action, license_key, country_code, country_name, ip_address):
    """Inject a full-screen blocking overlay via pywebview."""
    if not api_ref or not getattr(api_ref, "_window", None):
        _log("No window reference — cannot show blocked screen")
        return

    masked_key = license_key[:4] + "****" + license_key[-4:] if len(license_key) > 8 else license_key

    dismiss_btn = ""
    if action in ("warn", "alert"):
        dismiss_btn = (
            "<button onclick=\"this.parentElement.parentElement.remove()\" "
            "style=\"padding:10px 28px;border-radius:8px;border:1px solid #ccc;"
            "background:#fff;color:#333;font-size:15px;cursor:pointer;"
            "margin-right:10px\">Dismiss</button>"
        )

    close_btn = ""
    if action == "revoke":
        close_btn = (
            "<button onclick=\"try{window.pywebview&&window.pywebview.api.close_app"
            "?window.pywebview.api.close_app():window.close()}catch(e){window.close()}\" "
            "style=\"padding:10px 28px;border-radius:8px;border:none;"
            "background:#dc3545;color:#fff;font-size:15px;cursor:pointer;"
            "margin-right:10px\">Close Application</button>"
        )

    support_btn = (
        "<button onclick=\"try{window.pywebview&&window.pywebview.api.contact_support"
        "?window.pywebview.api.contact_support():void 0}catch(e){}\" "
        "style=\"padding:10px 28px;border-radius:8px;border:1px solid #6c757d;"
        "background:#fff;color:#6c757d;font-size:15px;cursor:pointer\">Contact Support</button>"
    )

    html = (
        "<div id='gf-overlay' style='position:fixed;inset:0;z-index:999999;"
        "background:rgba(0,0,0,0.85);display:flex;align-items:center;"
        "justify-content:center;font-family:system-ui,sans-serif'>"
        "<div style='background:#fff;border-radius:16px;padding:48px;max-width:520px;"
        "width:90%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.3)'>"
        "<div style='font-size:48px;margin-bottom:16px'>&#127760;</div>"
        "<h1 style='margin:0 0 12px;font-size:22px;color:#1a1a1a'>"
        "This software is not available in your region</h1>"
        "<p style='color:#555;font-size:15px;line-height:1.6;margin:0 0 20px'>"
        f"Your license <strong>{masked_key}</strong> cannot be activated from "
        f"<strong>{country_name}</strong>. This restriction was set by the "
        "software administrator.<br><br>"
        "If you believe this is an error, contact your administrator or "
        "try again from an approved location.</p>"
        "<p style='color:#999;font-size:12px;margin:0 0 28px'>"
        f"Detected country: {country_code} &middot; IP: {ip_address} "
        f"&middot; Action: {action}</p>"
        f"<div>{dismiss_btn}{close_btn}{support_btn}</div>"
        "</div></div>"
    )

    js = (
        "try{var e=document.getElementById('gf-overlay');"
        "if(e)e.remove();"
        "document.body.insertAdjacentHTML('beforeend',%s)"
        "}catch(x){}"
    ) % json.dumps(html)

    try:
        api_ref._window.evaluate_js(js)
        _log(f"Showed blocked screen: action={action}")
    except Exception as e:
        _log(f"Failed to show blocked screen: {e}")


# ── Revoke helper ───────────────────────────────────────────────────

def _do_geo_revoke(api_ref, license_key, country_code, country_name, ip_address):
    """Write .revoked flag and fire the revoked screen for geo-fence revocation."""
    flag_path = os.path.join(_BASE, "database", ".revoked")
    reason_str = f"geo_fence:{country_code}"
    try:
        with open(flag_path, "w") as f:
            f.write(reason_str)
        _log(f"Wrote .revoked flag: {reason_str}")
    except Exception as e:
        _log(f"Failed to write .revoked flag: {e}")

    _show_blocked_screen(api_ref, "revoke", license_key, country_code, country_name, ip_address)

    if api_ref:
        try:
            api_ref.fire_revoked_screen("revoked")
        except Exception:
            pass


# ── Main enforcement ────────────────────────────────────────────────

def enforce(license_key="", api_ref=None):
    """Run the full geo-fence check flow.

    1. Detect country from IP
    2. Call admin API to check if allowed
    3. Show blocked screen / revoke / log as needed

    Returns dict: {"allowed": bool, "action": str, "country_code": str}
    """
    if not license_key:
        _log("No license key — skipping geo-fence check")
        return {"allowed": True, "action": "none", "country_code": ""}

    geo = detect_country()
    if not geo:
        _log("Country detection failed — allowing by default")
        return {"allowed": True, "action": "none", "country_code": ""}

    cc = geo["country_code"]
    cn = geo.get("country_name", cc)
    ip = geo.get("ip", "unknown")

    result = check_geo_fence(license_key, cc)

    if result["allowed"]:
        _log(f"License allowed in {cc}")
        try:
            from core.dpdp_compliance import is_transfer_allowed
            xfer = is_transfer_allowed(cc)
            if not xfer['allowed']:
                _log(f"[DPDP] Cross-border transfer blocked for {cc}: {xfer['reason']}")
        except Exception:
            pass
        return {"allowed": True, "action": "none", "country_code": cc}

    action = result["action"]
    _log(f"License BLOCKED in {cc}, action={action}")

    _log_event(license_key, f"geo_fence:{action}", {
        "country_code": cc,
        "country_name": cn,
        "ip": ip,
        "action": action,
    })

    if action == "revoke":
        _do_geo_revoke(api_ref, license_key, cc, cn, ip)
    else:
        _show_blocked_screen(api_ref, action, license_key, cc, cn, ip)

    return {"allowed": False, "action": action, "country_code": cc}
