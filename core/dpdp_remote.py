"""
Remote DPDP compliance API client for AurumOS.

Syncs consent, privacy notice, and data-subject requests with the
admin server.  All calls are fire-and-forget safe — network failures
return None and never block the app.

Pattern follows geo_fence.py / auto_revoke.py — stdlib only.
"""

import json
import urllib.request
import urllib.error
from datetime import datetime

_API_BASE = "https://aurum-os-admin.vercel.app"
_TIMEOUT = 10
_UA = "AurumOS-DPDP/1.0"


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [DPDP-REMOTE] {msg}")


def _get(path, params=None):
    url = _API_BASE + path
    if params:
        qs = "&".join(
            f"{k}={urllib.request.quote(str(v))}" for k, v in params.items()
        )
        url += "?" + qs
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        _log(f"GET {path} failed: {e}")
        return None


def _post(path, payload):
    url = _API_BASE + path
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        _log(f"POST {path} failed: {e}")
        return None


# ── Privacy Notice ────────────────────────────────────────────────

def fetch_privacy_notice():
    """GET /api/compliance/privacy — returns structured notice or None."""
    return _get("/api/compliance/privacy")


# ── Consent ───────────────────────────────────────────────────────

_PURPOSE_MAP = {
    "license_validation": "license_activation",
    "geo_fence": "ip_geolocation",
    "behavioral_monitoring": "device_fingerprinting",
    "telemetry": "usage_analytics",
    "login_audit": "network_monitoring",
    "crm": "usage_analytics",
    "whatsapp": "usage_analytics",
    "backup": "usage_analytics",
}


def _map_purpose(local_purpose):
    """Map local purpose names to the remote API purpose vocabulary."""
    return _PURPOSE_MAP.get(local_purpose, local_purpose)


def sync_consent(license_key, purpose, granted, ip=""):
    """POST /api/compliance/consent — sync one consent decision."""
    return _post("/api/compliance/consent", {
        "license_key": license_key,
        "purpose": _map_purpose(purpose),
        "granted": bool(granted),
        "ip": ip or "",
    })


def withdraw_consent(license_key, purpose):
    """POST /api/compliance/consent with granted=false."""
    return _post("/api/compliance/consent", {
        "license_key": license_key,
        "purpose": _map_purpose(purpose),
        "granted": False,
    })


def fetch_consents(license_key):
    """GET /api/compliance/consent?key=KEY — current remote consent state."""
    return _get("/api/compliance/consent", {"key": license_key})


# ── Data Subject Requests ─────────────────────────────────────────

def submit_dsr(license_key, request_type, details=""):
    """POST /api/compliance/dsr — returns {"status","request_id"} or None."""
    return _post("/api/compliance/dsr", {
        "license_key": license_key,
        "request_type": request_type,
        "details": details,
    })
