# -*- coding: utf-8 -*-
"""
Auto-Revoke Rule Engine — polls the admin REST API for auto-revoke
rules and enforces them locally on license activation / heartbeat.

Mirrors the bastion_sync.py pattern: poll a Vercel REST endpoint,
cache rules locally for offline operation, enforce on each heartbeat.

Rule types:
  machine_limit      — max distinct machines per key
  rapid_activation   — too many activations in N minutes
  vpn_detection      — activation from a known VPN/proxy IP
  country_mismatch   — activation from a country not in the allow-list
  after_hours        — activation outside permitted hours (UTC)

Severity: "warn" (log event, don't block) | "revoke" (log + revoke license)
"""

import threading, time, os, sys, json
from datetime import datetime, timezone

_API_BASE = "https://aurum-os-admin.vercel.app"
_RULES_ENDPOINT = "/api/auto-revoke-rules"
_EVENTS_ENDPOINT = "/api/license-events"

_lock = threading.Lock()
_rules: list = []
_server_context: dict = {}
_last_fetch: float = 0
_cache_path: str = ""
_license_key: str = ""
_machine_id: str = ""
_db = None
_api_ref = None

# ── Logging ──────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_FILE = os.path.join(_BASE, "auto_revoke.log")


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [AUTO-REVOKE] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ── Cache ────────────────────────────────────────────────────────

def _init_cache_path():
    global _cache_path
    db_dir = os.path.join(_BASE, "database")
    os.makedirs(db_dir, exist_ok=True)
    _cache_path = os.path.join(db_dir, ".auto_revoke_cache.json")


def _save_cache(rules_data):
    if not _cache_path:
        _init_cache_path()
    try:
        with open(_cache_path, "w", encoding="utf-8") as f:
            json.dump({
                "rules": rules_data,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }, f)
    except Exception as e:
        _log(f"Cache save error: {e}")


def _load_cache():
    if not _cache_path:
        _init_cache_path()
    try:
        if os.path.exists(_cache_path):
            with open(_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("rules", [])
    except Exception as e:
        _log(f"Cache load error: {e}")
    return []


# ── Fetch rules ──────────────────────────────────────────────────

def _fetch_rules():
    import urllib.request, urllib.error, urllib.parse
    params = {}
    if _license_key:
        params["key"] = _license_key
    if _machine_id:
        params["machine_id"] = _machine_id
    qs = urllib.parse.urlencode(params)
    url = _API_BASE + _RULES_ENDPOINT + ("?" + qs if qs else "")
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "AurumOS-AutoRevoke/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("status") == "ok" and isinstance(body.get("rules"), list):
            return body["rules"], body.get("context", {})
        if isinstance(body, list):
            return body, {}
    except urllib.error.URLError as e:
        _log(f"Fetch error (network): {e}")
    except Exception as e:
        _log(f"Fetch error: {e}")
    return None, {}


def _poll():
    global _rules, _server_context, _last_fetch
    fetched, ctx = _fetch_rules()
    if fetched is not None:
        with _lock:
            old_count = len(_rules)
            _rules = [r for r in fetched if r.get("enabled", True)]
            _server_context = ctx or {}
            _last_fetch = time.time()
        _save_cache(_rules)
        if len(_rules) != old_count:
            _log(f"Rules updated: {old_count} -> {len(_rules)} active")
        if ctx:
            _log(f"Context: machines={len(ctx.get('known_machines', []))} "
                 f"activations={len(ctx.get('recent_activations', []))} "
                 f"country={ctx.get('country', '?')} vpn={ctx.get('vpn_detected', '?')}")
    else:
        # Offline — load from cache if we have no rules yet
        with _lock:
            if not _rules:
                cached = _load_cache()
                if cached:
                    _rules = cached
                    _log(f"Loaded {len(_rules)} rules from cache (offline)")


# ── Rule enforcement ─────────────────────────────────────────────

def get_rules():
    with _lock:
        return list(_rules)


def check_machine_limit(rule, context):
    """Check if the license key is used on too many machines."""
    max_machines = rule.get("config", {}).get("max_machines", 3)
    known_machines = context.get("known_machines", [])
    current_machine = context.get("machine_id", _machine_id)
    if current_machine and current_machine not in known_machines:
        known_machines = known_machines + [current_machine]
    if len(known_machines) > max_machines:
        return {
            "violated": True,
            "reason": f"Machine limit exceeded: {len(known_machines)}/{max_machines}",
            "machine_count": len(known_machines),
            "max_machines": max_machines,
        }
    return {"violated": False}


def check_rapid_activation(rule, context):
    """Check if there have been too many activations in a time window."""
    cfg = rule.get("config", {})
    max_activations = cfg.get("max_activations", 5)
    window_minutes = cfg.get("window_minutes", 60)
    recent_activations = context.get("recent_activations", [])
    cutoff = time.time() - (window_minutes * 60)
    recent = [a for a in recent_activations if a.get("timestamp", 0) > cutoff]
    if len(recent) >= max_activations:
        return {
            "violated": True,
            "reason": f"Rapid activation: {len(recent)} in {window_minutes}min (max {max_activations})",
            "activation_count": len(recent),
        }
    return {"violated": False}


def check_vpn_detection(rule, context):
    """Check if the activation IP is a known VPN/proxy."""
    # The server-side API provides vpn_detected in the activation context
    if context.get("vpn_detected"):
        return {
            "violated": True,
            "reason": "VPN/proxy detected during activation",
            "ip": context.get("ip", "unknown"),
        }
    return {"violated": False}


def check_country_mismatch(rule, context):
    """Check if activation country is in the allowed list."""
    cfg = rule.get("config", {})
    allowed_countries = cfg.get("allowed_countries", [])
    if not allowed_countries:
        return {"violated": False}
    country = context.get("country", "").upper()
    if country and country not in [c.upper() for c in allowed_countries]:
        return {
            "violated": True,
            "reason": f"Country mismatch: {country} not in {allowed_countries}",
            "country": country,
        }
    return {"violated": False}


def check_after_hours(rule, context):
    """Check if activation is outside permitted hours (UTC)."""
    cfg = rule.get("config", {})
    start_hour = cfg.get("start_hour", 0)
    end_hour = cfg.get("end_hour", 24)
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    if start_hour <= end_hour:
        in_window = start_hour <= current_hour < end_hour
    else:
        in_window = current_hour >= start_hour or current_hour < end_hour
    if not in_window:
        return {
            "violated": True,
            "reason": f"After-hours activation: {current_hour}:00 UTC (allowed {start_hour}:00-{end_hour}:00)",
            "current_hour": current_hour,
        }
    return {"violated": False}


# ── IP clone detection (tiered) ──────────────────────────────────

_CLONE_TIERS = [
    # (min_ips, tier_name, severity_action)
    (2, "medium",   "warn"),
    (3, "high",     "warn"),
    (4, "critical", "revoke"),
]

_CLONE_MESSAGES = {
    "medium": {
        "title": "License Activity Notice",
        "body": (
            "Your license key has been detected on multiple networks "
            "simultaneously. If this is unexpected, please secure your "
            "license key immediately. Continued shared usage may result "
            "in automatic revocation."
        ),
    },
    "high": {
        "title": "License Sharing Warning",
        "body": (
            "Your license key is actively being used from {ip_count} "
            "different IP addresses. This violates the terms of your "
            "license agreement. Please contact support if you believe "
            "this is an error. Unauthorized sharing will result in "
            "license revocation within 24 hours."
        ),
    },
    "critical": {
        "title": "License Suspended — Cloning Detected",
        "body": (
            "Your license key has been flagged for simultaneous use "
            "across multiple locations. Your access has been temporarily "
            "suspended pending review. Contact support with your license "
            "key to resolve this. Reference code: CLONE-{ref_code}"
        ),
    },
}


def check_ip_clone(rule, context):
    """Detect simultaneous use from multiple IPs — tiered response."""
    active_ips = context.get("active_ips", [])
    ip_count = len(active_ips)
    if ip_count < 2:
        return {"violated": False}

    # Find the matching tier (highest that applies)
    tier_name = "medium"
    for min_ips, name, _ in _CLONE_TIERS:
        if ip_count >= min_ips:
            tier_name = name

    # Build the warning message
    msg = _CLONE_MESSAGES.get(tier_name, _CLONE_MESSAGES["medium"])
    ref_code = str(int(time.time()))
    body = msg["body"].format(ip_count=ip_count, ref_code=ref_code)

    return {
        "violated": True,
        "reason": f"IP clone detected: {ip_count} simultaneous IPs",
        "ip_count": ip_count,
        "active_ips": active_ips,
        "clone_tier": tier_name,
        "warning_title": msg["title"],
        "warning_body": body,
        "ref_code": f"CLONE-{ref_code}" if tier_name == "critical" else None,
    }


_RULE_CHECKERS = {
    "machine_limit": check_machine_limit,
    "rapid_activation": check_rapid_activation,
    "vpn_detection": check_vpn_detection,
    "country_mismatch": check_country_mismatch,
    "after_hours": check_after_hours,
    "ip_clone": check_ip_clone,
}


def evaluate_rules(context=None):
    """Evaluate all active rules against the given context.

    Server-polled context (known_machines, recent_activations, country,
    vpn_detected, ip) is merged automatically so callers don't need it.

    Returns:
        list of dicts: [{rule_type, severity, reason, ...}, ...]
        Only violated rules are included. Empty list = all clear.
    """
    if context is None:
        context = {}
    # Merge server-provided context (polled every 30s) as defaults
    with _lock:
        srv = dict(_server_context)
    for k, v in srv.items():
        context.setdefault(k, v)
    context.setdefault("machine_id", _machine_id)
    context.setdefault("license_key", _license_key)

    violations = []
    with _lock:
        rules_snapshot = list(_rules)

    for rule in rules_snapshot:
        rule_type = rule.get("rule_type", "")
        checker = _RULE_CHECKERS.get(rule_type)
        if not checker:
            continue
        try:
            result = checker(rule, context)
            if result.get("violated"):
                # ip_clone tier overrides the rule's base severity
                severity = rule.get("severity", "warn")
                if rule_type == "ip_clone" and result.get("clone_tier"):
                    tier_sev = dict((n, s) for _, n, s in _CLONE_TIERS).get(result["clone_tier"])
                    if tier_sev:
                        severity = tier_sev
                violations.append({
                    "rule_id": rule.get("id"),
                    "rule_type": rule_type,
                    "severity": severity,
                    "reason": result.get("reason", ""),
                    **{k: v for k, v in result.items() if k not in ("violated", "reason")},
                })
        except Exception as e:
            _log(f"Rule check error ({rule_type}): {e}")

    return violations


# ── Event logging ────────────────────────────────────────────────

def log_event(event_type, details=None):
    """Log an auto-revoke event to the admin server."""
    import urllib.request, urllib.error
    if details is None:
        details = {}
    payload = {
        "license_key": _license_key,
        "machine_id": _machine_id,
        "event_type": event_type,
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    url = _API_BASE + _EVENTS_ENDPOINT
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "AurumOS-AutoRevoke/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        _log(f"Event logged: {event_type}")
    except Exception as e:
        _log(f"Event log error: {e}")
        # Save to local queue for retry
        _queue_event(payload)


def _queue_event(payload):
    """Queue a failed event for later retry."""
    queue_path = os.path.join(_BASE, "database", ".revoke_event_queue.json")
    try:
        queue = []
        if os.path.exists(queue_path):
            with open(queue_path, "r", encoding="utf-8") as f:
                queue = json.load(f)
        queue.append(payload)
        # Keep max 100 queued events
        if len(queue) > 100:
            queue = queue[-100:]
        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(queue, f)
    except Exception as e:
        _log(f"Event queue error: {e}")


def _flush_event_queue():
    """Try to send any queued events."""
    import urllib.request, urllib.error
    queue_path = os.path.join(_BASE, "database", ".revoke_event_queue.json")
    if not os.path.exists(queue_path):
        return
    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            queue = json.load(f)
        if not queue:
            return
    except Exception:
        return

    remaining = []
    for payload in queue:
        url = _API_BASE + _EVENTS_ENDPOINT
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", "AurumOS-AutoRevoke/1.0")
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
        except Exception:
            remaining.append(payload)

    try:
        if remaining:
            with open(queue_path, "w", encoding="utf-8") as f:
                json.dump(remaining, f)
        else:
            os.remove(queue_path)
    except Exception:
        pass


# ── Enforcement action ───────────────────────────────────────────

def enforce(context=None):
    """Run all rule checks and take action on violations.

    Returns:
        dict: {"action": "allow"|"warn"|"revoke", "violations": [...]}
    """
    violations = evaluate_rules(context)
    if not violations:
        return {"action": "allow", "violations": []}

    # Determine highest severity
    has_revoke = any(v["severity"] == "revoke" for v in violations)
    action = "revoke" if has_revoke else "warn"

    # Log each violation
    for v in violations:
        log_event(f"rule_violation:{v['rule_type']}", {
            "severity": v["severity"],
            "reason": v["reason"],
            **{k: val for k, val in v.items() if k not in ("rule_type", "severity", "reason", "rule_id")},
        })

    if has_revoke:
        _log(f"REVOKE triggered: {[v['rule_type'] for v in violations if v['severity'] == 'revoke']}")
        _do_revoke(violations)
    else:
        _log(f"WARN: {[v['rule_type'] for v in violations]}")
        _show_warning(violations)

    return {"action": action, "violations": violations}


def _show_warning(violations):
    """Show a toast/modal warning for warn-level violations via pywebview."""
    if not _api_ref:
        return
    for v in violations:
        title = v.get("warning_title", "License Warning")
        body = v.get("warning_body", v.get("reason", "A license rule was triggered."))
        tier = v.get("clone_tier", "")
        tier_class = f"clone-{tier}" if tier else "warn"
        js = (
            "try{if(typeof showAutoRevokeWarning==='function'){"
            "showAutoRevokeWarning(%s,%s,%s)"
            "}else{"
            "var d=document.createElement('div');"
            "d.className='ar-warning %s';"
            "d.innerHTML='<strong>'+%s+'</strong><br>'+%s;"
            "Object.assign(d.style,{position:'fixed',top:'20px',right:'20px',"
            "zIndex:'99999',padding:'16px 24px',borderRadius:'12px',"
            "background:'#FFF3CD',color:'#856404',border:'1px solid #FFEEBA',"
            "maxWidth:'420px',fontSize:'14px',boxShadow:'0 4px 20px rgba(0,0,0,0.15)'});"
            "document.body.appendChild(d);"
            "setTimeout(function(){d.remove()},15000)"
            "}}catch(e){}"
        ) % (
            json.dumps(title), json.dumps(body), json.dumps(tier_class),
            tier_class,
            json.dumps(title), json.dumps(body),
        )
        try:
            _api_ref._window.evaluate_js(js)
            _log(f"Showed warning: {title}")
        except Exception as e:
            _log(f"Failed to show warning: {e}")


def _do_revoke(violations):
    """Write the .revoked flag and fire the revoked screen."""
    base = _BASE
    flag_path = os.path.join(base, "database", ".revoked")
    reasons = [v["rule_type"] for v in violations if v["severity"] == "revoke"]
    reason_str = "auto_revoke:" + ",".join(reasons)
    try:
        with open(flag_path, "w") as f:
            f.write(reason_str)
        _log(f"Wrote .revoked flag: {reason_str}")
    except Exception as e:
        _log(f"Failed to write .revoked flag: {e}")

    log_event("license_revoked", {"reasons": reasons, "violations": violations})

    # Show clone-specific critical message before locking
    clone_violations = [v for v in violations if v.get("clone_tier") == "critical"]
    if clone_violations and _api_ref:
        v = clone_violations[0]
        js = (
            "try{if(typeof showAutoRevokeWarning==='function'){"
            "showAutoRevokeWarning(%s,%s,'clone-critical')"
            "}}catch(e){}"
        ) % (json.dumps(v.get("warning_title", "")), json.dumps(v.get("warning_body", "")))
        try:
            _api_ref._window.evaluate_js(js)
        except Exception:
            pass

    if _api_ref:
        try:
            _api_ref.fire_revoked_screen("revoked")
        except Exception as e:
            _log(f"Failed to fire revoked screen: {e}")


# ── Background loop ──────────────────────────────────────────────

def _loop(interval):
    while True:
        _poll()
        _flush_event_queue()
        time.sleep(interval)


def start(license_key="", machine_id="", db_manager=None, api_ref=None, interval=30):
    """Start the auto-revoke background sync.

    Args:
        license_key: The current license key
        machine_id: This machine's fingerprint
        db_manager: DBManager instance for local queries
        api_ref: Reference to the main Api class (for fire_revoked_screen)
        interval: Polling interval in seconds
    """
    global _license_key, _machine_id, _db, _api_ref
    _license_key = license_key
    _machine_id = machine_id
    _db = db_manager
    _api_ref = api_ref

    _init_cache_path()
    _log("=" * 40)
    _log("AUTO-REVOKE ENGINE STARTING")
    _log(f"Key: {license_key[:10]}..." if license_key else "Key: (none)")
    _log(f"Machine: {machine_id[:8]}..." if machine_id else "Machine: (none)")
    _log(f"API: {_API_BASE}")

    # Initial poll
    _poll()
    rules = get_rules()
    _log(f"Active rules: {len(rules)}")
    for r in rules:
        _log(f"  {r.get('rule_type')} [{r.get('severity')}]")

    # Start background thread
    t = threading.Thread(target=_loop, args=(interval,), daemon=True)
    t.start()
    _log(f"Polling every {interval}s")
    _log("=" * 40)
