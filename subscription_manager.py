# -*- coding: utf-8 -*-
"""
AurumOS Subscription Manager — client-side enforcement.

Handles:
  - Startup validation via POST /api/subscription/check
  - Periodic sync via POST /api/subscription/check (every 30 min, 5 min when offline)
  - Feature gating via features[] array
  - Offline resilience (24h cache, then degrade to Lite)
  - SSE-driven real-time plan/status changes
  - 365-day subscription timeline
"""
import os
import sys
import json
import time
import uuid
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

_VALID_KEY_PREFIXES = ("AU-", "AR-")
def _is_valid_key_format(key):
    return isinstance(key, str) and len(key) == 22 and key.upper().startswith(_VALID_KEY_PREFIXES)

def _base_dir():
    return os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')

def _log(msg):
    try:
        print(f'[SUB] {msg}', flush=True)
    except Exception:
        pass

def _err(msg):
    try:
        print(f'[SUB_ERR] {msg}', flush=True)
    except Exception:
        pass


# ── Lite features (fallback when no subscription) ─────────────────────
LITE_FEATURES = [
    'local_mode', 'billing_retail', 'stock_entry', 'product_master',
    'client_ledger', 'staff_login_lockout', 'tag_printing_local',
    'scale_weighing', 'sales_report_basic', 'bastion_core'
]

PRO_FEATURES = LITE_FEATURES + [
    'karigar_vouchers', 'touch_groups', 'full_accounts',
    'tag_audit', 'stock_med_reports', 'tsc_network_printing', 'multi_staff',
    'analytics_dashboard', 'year_close', 'bastion_enhanced'
]

ENTERPRISE_FEATURES = PRO_FEATURES + [
    'lan_multi_pc', 'cloud_sync', 'fleet_bastion', 'customer_loyalty', 'bastion_ai',
    'nexus_management', 'bridge_server', 'custom_db_location',
    'priority_support', 'api_integration'
]

PLAN_FEATURES = {
    'lite': LITE_FEATURES,
    'pro': PRO_FEATURES,
    'enterprise': ENTERPRISE_FEATURES
}

# Features that ONLY the Enterprise plan may grant. The admin server may still
# carry stale records listing these under Pro — strip them client-side so the
# plan tier stays authoritative.
ENTERPRISE_ONLY_FEATURES = [f for f in ENTERPRISE_FEATURES if f not in PRO_FEATURES]


def _enforce_plan_tier(plan, features):
    """Drop Enterprise-only features from any non-Enterprise plan."""
    if isinstance(features, str):
        features = [f.strip() for f in features.split(',') if f.strip()]
    features = list(features or [])
    if (plan or 'lite').lower() == 'enterprise':
        return features
    stripped = [f for f in features if f not in ENTERPRISE_ONLY_FEATURES]
    if len(stripped) != len(features):
        removed = [f for f in features if f in ENTERPRISE_ONLY_FEATURES]
        _log(f'Plan tier enforcement: stripped {removed} from plan={plan}')
    return stripped

# Subscription validity = 365 days
SUBSCRIPTION_DAYS = 365

def _clamp_expiry(expires_at):
    """Clamp subscription expiry to max SUBSCRIPTION_DAYS from now.
    Server may return far-future dates (e.g. 2031) — enforce 1-year max on client."""
    if not expires_at:
        return expires_at
    try:
        from datetime import datetime, timedelta, timezone
        if isinstance(expires_at, str):
            exp = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        else:
            exp = datetime.fromtimestamp(expires_at / 1000, tz=timezone.utc) if expires_at > 1e12 else datetime.fromtimestamp(expires_at, tz=timezone.utc)
        max_exp = datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)
        if exp > max_exp:
            _log(f'Clamping expiry from {exp.isoformat()} to {max_exp.isoformat()}')
            return max_exp.isoformat()
        return expires_at
    except Exception:
        return expires_at

def _clamp_remaining(remaining_days):
    """Clamp remaining days to max SUBSCRIPTION_DAYS."""
    if remaining_days and remaining_days > SUBSCRIPTION_DAYS:
        _log(f'Clamping remaining_days from {remaining_days} to {SUBSCRIPTION_DAYS}')
        return SUBSCRIPTION_DAYS
    return remaining_days

# Sync intervals
SYNC_ONLINE_SECONDS = 30 * 60   # 30 min when online
SYNC_OFFLINE_SECONDS = 5 * 60   # 5 min retry when offline
CACHE_MAX_HOURS = 24            # cache expires after 24h

# Server may return 'expired' or 'subscription_expired' for expired subscriptions
EXPIRED_REASONS = ('expired', 'subscription_expired')


class SubscriptionManager:
    """Manages subscription state, feature gating, and server sync."""

    @staticmethod
    def _ssl_context():
        import ssl as _ssl
        try:
            return _ssl.create_default_context()
        except Exception:
            return _ssl._create_unverified_context()

    def _safe_urlopen(self, req, timeout=12):
        import ssl as _ssl, urllib.error
        ctx = self._ssl_context()
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.URLError as e:
            if isinstance(e.reason, (_ssl.SSLCertVerificationError, _ssl.SSLError, OSError)):
                ctx2 = _ssl._create_unverified_context()
                return urllib.request.urlopen(req, timeout=timeout, context=ctx2)
            raise
        except (_ssl.SSLCertVerificationError, _ssl.SSLError):
            ctx2 = _ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx2)

    def __init__(self):
        self._cache_path = os.path.join(_base_dir(), 'database', '.subscription_cache')
        self._state_path = os.path.join(_base_dir(), 'database', '.subscription_state.json')
        self._lock = threading.Lock()
        self._state = {
            'valid': False,
            'status': 'unknown',       # active | grace | expired | revoked | unknown
            'effective_plan': 'lite',
            'features': list(LITE_FEATURES),
            'subscription': {
                'status': 'unknown',
                'expires_at': None,
                'remaining_days': 0,
                'grace_remaining_days': 0,
                'renewal_amount': 0
            },
            'business': '',
            'owner': '',
            'plan': 'lite',
            'is_trial': False,
            'trial_end_ms': None,
            'last_sync': 0,
            'offline_since': 0
        }
        self._window = None
        self._sync_thread = None
        self._sync_stop = threading.Event()
        self._license_key = ''
        self._api_base = 'https://aurum-os-admin.vercel.app'
        self._force_active_until = 0  # grace period after renewal
        self._last_updated_at = None  # server's updated_at timestamp
        self._poll_thread = None
        self._poll_stop = threading.Event()
        self._poll_inflight = False
        self._load_cache()

    def set_window(self, window):
        self._window = window

    def set_api_base(self, url):
        self._api_base = url.rstrip('/')

    def set_force_active(self, seconds=300):
        """After renewal, force active state for `seconds` regardless of server response."""
        self._force_active_until = time.time() + seconds
        _log(f'Force active until {self._force_active_until:.0f} ({seconds}s from now)')

    def set_license_key(self, key):
        self._license_key = (key or '').strip().upper()

    # ── Public state accessors ──────────────────────────────────────────
    @property
    def state(self):
        with self._lock:
            return dict(self._state)

    @property
    def is_active(self):
        return self._state['status'] == 'active'

    @property
    def is_grace(self):
        return self._state['status'] == 'grace'

    @property
    def is_expired(self):
        return self._state['status'] in ('expired', 'revoked')

    @property
    def effective_plan(self):
        return self._state['effective_plan']

    @property
    def features(self):
        return list(self._state['features'])

    def has_feature(self, feature_id):
        """Check if a feature is available in the current effective plan."""
        return feature_id in self._state['features']

    def get_features(self):
        return list(self._state['features'])

    def get_state_json(self):
        """Return full state as JSON-safe dict for frontend."""
        with self._lock:
            s = dict(self._state)
            s['features'] = list(s['features'])
            s['subscription'] = dict(s['subscription'])
        return s

    # ── Startup validation ──────────────────────────────────────────────
    def startup_check(self, key=None, network=True):
        """
        Call POST /api/subscription/check on startup.
        Returns (data, source) or (None, 'none').
        """
        k = key or self._license_key
        if not k or not _is_valid_key_format(k):
            _log('No valid license key — using Lite features')
            self._set_state({
                'valid': False, 'status': 'unknown',
                'effective_plan': 'lite', 'features': list(LITE_FEATURES),
                'subscription': {'status': 'unknown', 'expires_at': None,
                                 'remaining_days': 0, 'grace_remaining_days': 0,
                                 'renewal_amount': 0},
                'business': '', 'owner': '', 'plan': 'lite',
                'is_trial': False, 'trial_end_ms': None
            })
            return None, 'none'

        if not network:
            _log('Offline startup — using cached subscription')
            self._load_cache()
            return None, 'cache'

        data, source = self._sync_with_server(k)
        if data:
            return data, source

        # Server failed — try cache
        _log('Startup: server unavailable, using cached subscription')
        self._load_cache()
        return None, 'cache'

    # ── Periodic sync ───────────────────────────────────────────────────
    def start_periodic_sync(self, interval_minutes=30):
        """Start background sync thread with adaptive retry."""
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_stop.clear()
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()
        _log(f'Periodic sync started (online={SYNC_ONLINE_SECONDS}s, offline={SYNC_OFFLINE_SECONDS}s)')

    def stop_periodic_sync(self):
        """Stop background sync thread."""
        self._sync_stop.set()

    # ── Reactivation polling (/api/poll) ──────────────────────────────
    def start_reactivation_polling(self):
        """Start 8-second poll loop for reactivation detection."""
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        _log('Reactivation polling started (8s)')

    def stop_reactivation_polling(self):
        """Stop reactivation polling."""
        self._poll_stop.set()

    def _poll_loop(self):
        """Poll /api/poll every 8 seconds for license changes."""
        while not self._poll_stop.is_set():
            self._poll_stop.wait(8)
            if self._poll_stop.is_set():
                break
            if self._poll_inflight:
                continue
            status = self._state.get('status', 'unknown')
            valid = self._state.get('valid', False)
            if status in ('active', 'grace') and valid:
                continue
            self._poll_inflight = True
            try:
                self._do_poll()
            except Exception as e:
                _err(f'Poll error: {e}')
            finally:
                self._poll_inflight = False

    def _do_poll(self):
        """Single poll cycle: POST /api/poll → if changed, fetch /api/check."""
        k = self._license_key
        if not k or not _is_valid_key_format(k):
            return

        was_active = self._state.get('valid', False) and self._state.get('status', '') in ('active', 'grace')

        payload = json.dumps({'key': k, 'updated_at': self._last_updated_at}).encode()
        headers = {'Content-Type': 'application/json', 'User-Agent': 'AurumOS/Client'}

        url = self._api_base + '/api/poll'
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with self._safe_urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            if not data.get('changed'):
                return

            _log(f'Poll: changed=True status={data.get("status")}')

            # Store updated_at
            if data.get('updated_at'):
                self._last_updated_at = data['updated_at']

            # If poll says active — trust it, set state directly (server may still say expired)
            poll_status = data.get('status', '')
            if poll_status == 'active':
                _log(f'Poll: server says active — setting state directly')
                plan = 'pro'
                features = PLAN_FEATURES.get(plan, list(LITE_FEATURES))
                self._set_state({
                    'valid': True,
                    'status': 'active',
                    'effective_plan': plan,
                    'features': features,
                    'business': '',
                    'owner': '',
                    'plan': plan,
                    'is_trial': False,
                    'trial_end_ms': None,
                    'subscription': {
                        'status': 'active',
                        'expires_at': None,
                        'remaining_days': SUBSCRIPTION_DAYS,
                        'grace_remaining_days': 15,
                        'renewal_amount': 0
                    }
                })
                self._save_cache()
                self.set_force_active(300)
                if not was_active:
                    self._notify_js('reactivated', self.get_state_json())
                return

            # For non-active changes, do full re-fetch via /api/check
            self._fetch_and_apply(k)

            # Notify JS of reactivation if now active (only on transition from non-active)
            status = self._state.get('status', 'unknown')
            if status in ('active', 'grace') and not was_active:
                self._notify_js('reactivated', self.get_state_json())

        except urllib.error.HTTPError as e:
            if e.code >= 500:
                _log(f'Poll: server error HTTP {e.code}')
            # 404 = endpoint not deployed yet — silent
        except urllib.error.URLError:
            _log('Poll: unreachable')
        except Exception as e:
            _err(f'Poll ({url}): {e}')

    def _fetch_and_apply(self, k):
        """Fetch full state from /api/check and apply."""
        machine_id = str(uuid.getnode())
        payload = json.dumps({'key': k, 'machine_id': machine_id}).encode()
        headers = {'Content-Type': 'application/json', 'User-Agent': 'AurumOS/Client'}

        for endpoint in ['/api/check', '/api/subscription/status']:
            url = self._api_base + endpoint
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
                with self._safe_urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode())

                # Store updated_at
                if data.get('updated_at'):
                    self._last_updated_at = data['updated_at']

                ok = data.get('ok', data.get('valid', False))
                remaining = data.get('remaining_days', 0)
                grace = data.get('grace_remaining_days', 0)

                if ok and remaining <= 0 and grace <= 0:
                    if time.time() < self._force_active_until:
                        remaining = SUBSCRIPTION_DAYS
                        data['remaining_days'] = remaining
                    else:
                        # Server says valid but no remaining days — trust ok flag
                        _log(f'Poll fetch: server ok=True but remaining=0 grace=0 — trusting ok flag')
                        remaining = SUBSCRIPTION_DAYS
                        data['remaining_days'] = remaining

                if ok:
                    self._apply_server_response(data)
                    self._save_cache()
                    _log(f'Poll fetch: active plan={data.get("plan")}')
                    return
                else:
                    reason = data.get('reason', data.get('status', 'unknown'))
                    if reason in ('server_error', 'timeout', 'error'):
                        _log(f'Poll fetch: transient error ({reason}) — keeping cached')
                        return
                    if reason in ('expired', 'subscription_expired', 'revoked') and time.time() < self._force_active_until:
                        _log(f'Poll fetch: server says {reason} but in force-active grace — ignoring')
                        return
                    # Server genuinely rejected — set status accordingly
                    if reason in EXPIRED_REASONS:
                        self._set_status('expired')
                        self._notify_js('expired', {'reason': 'expired'})
                    else:
                        self._set_status('revoked')
                        self._notify_js('revoked', {'reason': reason})
                    return
            except Exception:
                continue

    def store_updated_at(self, data):
        """Store updated_at from any check response."""
        if data and data.get('updated_at'):
            self._last_updated_at = data['updated_at']

    def _sync_loop(self):
        """Adaptive sync loop: 30 min when online, 5 min when offline."""
        while not self._sync_stop.is_set():
            data, source = self.sync_subscription()

            if source == 'server':
                _log(f'Sync: next retry in {SYNC_ONLINE_SECONDS // 60} min')
                self._sync_stop.wait(SYNC_ONLINE_SECONDS)
            else:
                _log(f'Sync: next retry in {SYNC_OFFLINE_SECONDS // 60} min (offline)')
                self._sync_stop.wait(SYNC_OFFLINE_SECONDS)

    def sync_subscription(self):
        """
        Call POST /api/subscription/check to get fresh state.
        Returns (data, source) where source is 'server', 'cache', or 'default'.
        """
        k = self._license_key
        if not k or not _is_valid_key_format(k):
            return None, 'none'

        # Try server
        data, source = self._sync_with_server(k)
        if data:
            return data, source

        # Server failed — try cache
        cached = self._load_cache_data()
        if cached:
            hours_old = cached.get('_hours_old', 0)
            _log(f'Sync: using cached data ({hours_old:.1f}h old)')
            self._apply_cache_data(cached)
            self._notify_js('sync_source', {'source': 'cache', 'hours_old': hours_old})
            return cached.get('state', {}), 'cache'

        # No cache — use defaults
        _log('Sync: no cache, using Lite defaults')
        with self._lock:
            self._state['effective_plan'] = 'lite'
            self._state['features'] = list(LITE_FEATURES)
            self._state['subscription']['status'] = 'unknown'
        self._notify_js('sync_source', {'source': 'default'})
        return None, 'default'

    def _sync_with_server(self, k):
        """Single check endpoint: ok=true → features, ok=false → revoke.
        Tries /api/subscription/check, falls back to /api/check."""
        machine_id = str(uuid.getnode())
        payload = json.dumps({'key': k, 'machine_id': machine_id}).encode()
        headers = {'Content-Type': 'application/json', 'User-Agent': 'AurumOS/Client'}

        # Try endpoints in order
        for endpoint in ['/api/check', '/api/subscription/status']:
            url = self._api_base + endpoint
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
                with self._safe_urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode())

                # Normalize: /api/subscription/check uses 'ok', others use 'valid'
                ok = data.get('ok', data.get('valid', False))

                # Also check: if remaining_days=0 and grace=0, treat as rejected
                # UNLESS we're in force-active grace period (after renewal)
                remaining = data.get('remaining_days', 0)
                grace = data.get('grace_remaining_days', 0)
                if ok and remaining <= 0 and grace <= 0:
                    if time.time() < self._force_active_until:
                        _log(f'Sync ({endpoint}): server says expired but in force-active grace — treating as ok')
                        remaining = SUBSCRIPTION_DAYS
                        data['remaining_days'] = remaining
                    else:
                        # Server says valid but no remaining days — trust ok flag
                        # Server may not provide remaining_days for some license types
                        _log(f'Sync ({endpoint}): server ok=True but remaining=0 grace=0 — trusting ok flag')
                        remaining = SUBSCRIPTION_DAYS
                        data['remaining_days'] = remaining

                if ok:
                    _log(f'Sync ({endpoint}): ok plan={data.get("plan")} features={len(data.get("features", []))}')
                    old_plan = self._state.get('effective_plan', 'lite')
                    self._apply_server_response(data)
                    self._save_cache()

                    new_plan = self._state.get('effective_plan', 'lite')
                    if old_plan != new_plan:
                        _log(f'Plan changed: {old_plan} -> {new_plan}')
                        self._notify_js('plan_changed', self.get_state_json())

                    self._notify_js('sync_source', {'source': 'server'})
                    return data, 'server'
                else:
                    reason = data.get('reason', data.get('status', 'unknown'))
                    if reason in ('server_error', 'timeout', 'error'):
                        _log(f'Sync ({endpoint}): transient error ({reason}) — keeping cached state')
                        return None, 'failed'
                    if reason in ('expired', 'subscription_expired', 'revoked') and time.time() < self._force_active_until:
                        _log(f'Sync ({endpoint}): server says {reason} but in force-active grace — ignoring')
                        return None, 'failed'
                    _log(f'Sync ({endpoint}): rejected reason={reason}')
                    if reason in EXPIRED_REASONS:
                        self._set_status('expired')
                        self._notify_js('expired', {'reason': 'expired'})
                    else:
                        self._set_status('revoked')
                        self._notify_js('revoked', {'reason': reason})
                    return data, 'server'

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    _log(f'Sync: {endpoint} not found, trying next')
                    continue
                if e.code >= 500:
                    _log(f'Sync: {endpoint} server error HTTP {e.code} — keeping cached state')
                    return None, 'failed'
                _log(f'Sync: {endpoint} HTTP {e.code}')
                return None, 'failed'
            except urllib.error.URLError:
                _log(f'Sync: {endpoint} unreachable')
                return None, 'failed'
            except Exception as e:
                _err(f'Sync ({endpoint}) error: {e}')
                return None, 'failed'

        _log('Sync: all endpoints failed')
        return None, 'failed'

    # ── SSE event handlers ──────────────────────────────────────────────
    def handle_sse_plan_change(self, data):
        """Handle plan_change SSE event — re-fetch subscription."""
        _log(f'SSE plan_change: {data}')
        threading.Thread(target=self.sync_subscription, daemon=True).start()

    def handle_sse_status_change(self, data):
        """Handle status_change SSE event."""
        status = data.get('status', '')
        _log(f'SSE status_change: {status}')

        if status in ('revoked', 'disabled', 'suspended'):
            self._set_status('revoked')
            self._notify_js('revoked', {'status': status, 'message': data.get('message', '')})
        elif status in EXPIRED_REASONS:
            self._set_status('expired')
            self._notify_js('expired', {'status': status, 'message': data.get('message', '')})
        elif status == 'active':
            threading.Thread(target=self.sync_subscription, daemon=True).start()

    def handle_sse_revoke(self, reason='revoked', message=''):
        """Handle revoke from existing SSE stream."""
        _log(f'SSE revoke: reason={reason}')
        if reason in EXPIRED_REASONS:
            self._set_status('expired')
            self._notify_js('expired', {'status': reason, 'message': message})
        else:
            self._set_status('revoked')
            self._notify_js('revoked', {'status': reason, 'message': message})

    # ── Feature gate check ──────────────────────────────────────────────
    def check_feature(self, feature_id):
        """
        Returns dict with:
          allowed: bool
          plan: current effective_plan
          required_plan: the plan that includes this feature
          features: current features list
        """
        if feature_id in self._state['features']:
            return {'allowed': True, 'plan': self._state['effective_plan'],
                    'required_plan': '', 'features': self._state['features']}

        required = 'enterprise'
        if feature_id in PRO_FEATURES:
            required = 'pro'
        elif feature_id in LITE_FEATURES:
            required = 'lite'

        return {'allowed': False, 'plan': self._state['effective_plan'],
                'required_plan': required, 'features': self._state['features']}

    # ── Offline resilience ──────────────────────────────────────────────
    def _handle_offline(self):
        with self._lock:
            if self._state['offline_since'] == 0:
                self._state['offline_since'] = time.time()
                _log('Offline mode started')

            elapsed = time.time() - self._state['offline_since']
            hours_offline = elapsed / 3600

            if hours_offline >= CACHE_MAX_HOURS:
                _log(f'Offline {hours_offline:.1f}h — degrading to Lite')
                self._state['effective_plan'] = 'lite'
                self._state['features'] = list(LITE_FEATURES)
                self._state['subscription']['status'] = 'unknown'

    def _on_reconnect(self):
        with self._lock:
            was_offline = self._state['offline_since'] > 0
            self._state['offline_since'] = 0
        if was_offline:
            _log('Back online — syncing subscription')
            self.sync_subscription()

    # ── Internal helpers ────────────────────────────────────────────────
    def _apply_server_response(self, data):
        """Apply server response from /api/check.
        ok=true: apply plan + features. Derive status from remaining_days/grace.
        If in force-active grace period (after renewal), ignore expired/revoked from server."""
        with self._lock:
            remaining = data.get('remaining_days', 0)
            grace = data.get('grace_remaining_days', 0)

            # Derive status
            if grace > 0:
                status = 'grace'
                valid = True
            elif remaining > 0:
                status = 'active'
                valid = True
            else:
                status = 'expired'
                valid = False

            # Force-active grace: after renewal, ignore server's expired/revoked
            if time.time() < self._force_active_until and status in ('expired', 'revoked'):
                _log(f'Force-active grace: ignoring server status={status}, keeping active')
                status = 'active'
                valid = True
                if not remaining or remaining <= 0:
                    remaining = SUBSCRIPTION_DAYS

            self._state['valid'] = valid
            self._state['status'] = status
            self._state['effective_plan'] = data.get('plan') or data.get('effective_plan') or 'lite'
            self._state['features'] = _enforce_plan_tier(
                self._state['effective_plan'],
                data.get('features', list(LITE_FEATURES))
            )
            self._state['business'] = data.get('business', '')
            self._state['owner'] = data.get('owner', '')
            self._state['plan'] = data.get('plan') or 'lite'
            self._state['is_trial'] = data.get('is_trial', False)
            self._state['trial_end_ms'] = data.get('trial_end_ms')
            self._state['last_sync'] = time.time()
            self._state['offline_since'] = 0

            self._state['subscription'] = {
                'status': status,
                'expires_at': _clamp_expiry(data.get('expires_at') or data.get('subscription_expires_at')),
                'remaining_days': _clamp_remaining(remaining),
                'grace_remaining_days': grace,
                'renewal_amount': data.get('renewal_amount', 0)
            }

            _log(f'Server: status={status} valid={valid} plan={self._state["effective_plan"]} '
                 f'remaining={remaining} grace={grace} features={len(self._state["features"])}')

            if isinstance(self._state['features'], str):
                self._state['features'] = self._state['features'].split(',')

            # Store updated_at for poll tracking
            if data.get('updated_at'):
                self._last_updated_at = data['updated_at']

    def _apply_cache_data(self, cache):
        """Apply cached data to internal state."""
        saved = cache.get('state', {})
        with self._lock:
            for k in ('valid', 'status', 'effective_plan', 'business',
                      'owner', 'plan', 'is_trial', 'trial_end_ms', 'last_sync'):
                if k in saved:
                    self._state[k] = saved[k]
            # Handle old state files that used 'subscription_status' instead of 'status'
            if self._state.get('status') == 'unknown' and 'subscription_status' in saved:
                self._state['status'] = saved['subscription_status']
            if 'features' in saved:
                self._state['features'] = _enforce_plan_tier(
                    self._state.get('effective_plan', 'lite'),
                    saved['features'] if isinstance(saved['features'], list) else list(LITE_FEATURES)
                )
            if 'subscription' in saved:
                self._state['subscription'] = saved['subscription']
            # Derive validity from status
            st = self._state.get('status', 'unknown')
            if st in ('expired', 'revoked'):
                self._state['valid'] = False
            elif st in ('active', 'grace'):
                self._state['valid'] = True
            self._state['offline_since'] = time.time() if cache.get('_hours_old', 0) > 0 else 0

    def _set_status(self, status):
        with self._lock:
            self._state['status'] = status
            if status in ('revoked', 'expired'):
                self._state['valid'] = False
                self._state['effective_plan'] = 'lite'
                self._state['features'] = list(LITE_FEATURES)
        self._save_state_file()

    def _set_state(self, data):
        with self._lock:
            for k, v in data.items():
                self._state[k] = v
        self._save_state_file()

    def _notify_js(self, event, data=None):
        """Push event to frontend JS — uses timer to avoid .NET async crash."""
        if not self._window:
            return
        try:
            payload = data if data is not None else {}
            js = 'if(window.__onSubscriptionEvent){window.__onSubscriptionEvent(' + json.dumps(event) + ',' + json.dumps(payload) + ');}'
            # Delay slightly to avoid pythonnet InvalidAsynchronousStateException
            # when evaluate_js is called from background threads
            def _do_eval():
                try:
                    self._window.evaluate_js(js)
                except Exception as _ejs:
                    _err(f'evaluate_js failed for event {event}: {_ejs}')
            threading.Timer(0.1, _do_eval).start()
        except Exception:
            pass

    # ── Cache persistence ───────────────────────────────────────────────
    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with self._lock:
                cache_data = {
                    'state': dict(self._state),
                    'cached_at': time.time(),
                    'license_key': self._license_key
                }
                cache_data['state']['features'] = list(cache_data['state']['features'])
                cache_data['state']['subscription'] = dict(cache_data['state']['subscription'])
            with open(self._cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f)
        except Exception as e:
            _err(f'Cache save error: {e}')
        # Also save to state file (read instantly on page load)
        self._save_state_file()

    def _save_state_file(self):
        """Save current state to a lightweight JSON file.
        This file is read instantly on page load — no server call needed."""
        try:
            with self._lock:
                data = dict(self._state)
                data['features'] = list(data['features'])
                data['subscription'] = dict(data['subscription'])
                data['_saved_at'] = time.time()
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            with open(self._state_path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception as e:
            _err(f'State file save error: {e}')

    def load_state_file(self):
        """Load state from file. Returns dict or None.
        Used by subscription_startup_check for instant page load."""
        try:
            if not os.path.exists(self._state_path):
                return None
            with open(self._state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            age = time.time() - data.get('_saved_at', 0)
            if age > 48 * 3600:  # 48h stale — ignore
                return None
            return data
        except Exception:
            return None

    def get_state_file_timestamp(self):
        """Return _saved_at from state file. Used by JS polling to detect changes."""
        try:
            if not os.path.exists(self._state_path):
                return 0
            with open(self._state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('_saved_at', 0)
        except Exception:
            return 0

    def _load_cache_data(self):
        """Load raw cache data with age info. Returns dict or None."""
        try:
            if not os.path.exists(self._cache_path):
                return None
            with open(self._cache_path, 'r', encoding='utf-8') as f:
                cache = json.load(f)

            cached_at = cache.get('cached_at', 0)
            hours_old = (time.time() - cached_at) / 3600
            cache['_hours_old'] = hours_old
            return cache

        except Exception as e:
            _err(f'Cache load error: {e}')
            return None

    def _load_cache(self):
        """Load cache and apply to state. Degrades to Lite if >24h old."""
        cache = self._load_cache_data()
        if not cache:
            return

        hours_old = cache.get('_hours_old', 0)

        if hours_old > CACHE_MAX_HOURS:
            _log(f'Cache is {hours_old:.1f}h old (>{CACHE_MAX_HOURS}h) — degrading to Lite')
            with self._lock:
                self._state['effective_plan'] = 'lite'
                self._state['features'] = list(LITE_FEATURES)
                self._state['status'] = 'unknown'
                self._state['offline_since'] = time.time()
            return

        self._apply_cache_data(cache)
        _log(f'Loaded cache ({hours_old:.1f}h old): plan={self._state["effective_plan"]} '
             f'status={self._state["status"]}')
