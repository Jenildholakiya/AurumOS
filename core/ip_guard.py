"""
AurumOS IP Rules & Rate Limiting — blocks/warns/throttles per admin rules.
Called on startup, periodically, and before outbound API calls.
"""
import json
import os
import sys
import time
import threading
import urllib.request
import urllib.error

_LOG = lambda m: print(f'[IP-GUARD] {m}', flush=True)
_ERR = lambda m: print(f'[IP-GUARD ERR] {m}', flush=True)

_CACHE_MAX_AGE = 24 * 3600  # 24h fail-open window


def _get_base_dir():
    return os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')


def _cache_path():
    return os.path.join(_get_base_dir(), 'database', '.ip_check_cache.json')


def _http_get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'AurumOS-Desktop/1.0',
    })
    ctx = None
    try:
        import ssl
        ctx = ssl.create_default_context()
    except Exception:
        pass
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode('utf-8'))


# ── Client IP resolution ─────────────────────────────────────────────────

_cached_ip = ''
_cached_ip_at = 0.0

def resolve_client_ip(force=False) -> str:
    global _cached_ip, _cached_ip_at
    if not force and _cached_ip and (time.time() - _cached_ip_at) < 600:
        return _cached_ip
    for url in ('https://api.ipify.org?format=json', 'https://api.my-ip.io/v2/ip.json'):
        try:
            data = _http_get_json(url, timeout=5)
            ip = data.get('ip') or data.get('address') or ''
            if ip:
                _cached_ip = ip.strip()
                _cached_ip_at = time.time()
                _LOG(f'Resolved IP: {_cached_ip}')
                return _cached_ip
        except Exception:
            continue
    _ERR('Could not resolve client IP')
    return _cached_ip or ''


# ── IP check ─────────────────────────────────────────────────────────────

def check_ip(server_url: str, license_key: str, ip: str = '') -> dict:
    """Returns {'allowed': bool, 'action': str}.  Fails open on network error."""
    ip = ip or resolve_client_ip()
    if not ip or not license_key:
        return {'allowed': True, 'action': 'none'}

    url = f"{server_url.rstrip('/')}/api/ip-rules/check?key={license_key}&ip={ip}"
    try:
        result = _http_get_json(url)
        _save_cache(result)
        _LOG(f"IP check: allowed={result.get('allowed')} action={result.get('action')}")
        return result
    except Exception as e:
        _ERR(f'IP check failed: {e}')
        cached = _load_cache()
        if cached:
            _LOG('Using cached IP check result')
            return cached
        return {'allowed': True, 'action': 'none'}


def _save_cache(result: dict):
    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'result': result, 'ts': time.time()}, f)
    except Exception:
        pass


def _load_cache() -> dict | None:
    try:
        path = _cache_path()
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if time.time() - data.get('ts', 0) > _CACHE_MAX_AGE:
            return None
        return data.get('result')
    except Exception:
        return None


# ── Rate limit check ─────────────────────────────────────────────────────

def check_rate_limit(server_url: str, license_key: str, endpoint: str, ip: str = '') -> dict:
    """Returns {'allowed': bool, 'action': str, 'remaining': int, 'retryAfter': int}.
    Fails open on network error."""
    ip = ip or resolve_client_ip()
    if not ip or not license_key:
        return {'allowed': True, 'remaining': -1}

    url = (f"{server_url.rstrip('/')}/api/rate-limits/check"
           f"?key={license_key}&ip={ip}&endpoint={endpoint}")
    try:
        result = _http_get_json(url)
        _LOG(f"Rate limit [{endpoint}]: allowed={result.get('allowed')} remaining={result.get('remaining')}")
        return result
    except Exception as e:
        _ERR(f'Rate limit check failed: {e}')
        return {'allowed': True, 'remaining': -1}


# ── Periodic IP check loop ───────────────────────────────────────────────

class IPGuard:
    """Runs IP check on startup and every interval_sec. Calls on_action(action, result)
    when the server returns a non-'none' action."""

    def __init__(self, server_url: str, license_key: str, on_action=None, interval_sec=1800):
        self.server_url = server_url
        self.license_key = license_key
        self.on_action = on_action
        self.interval_sec = interval_sec
        self._stop = False
        self._thread = None
        self.last_result = None

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            try:
                result = check_ip(self.server_url, self.license_key)
                self.last_result = result
                action = result.get('action', 'none')
                if action != 'none' and not result.get('allowed', True):
                    if self.on_action:
                        try:
                            self.on_action(action, result)
                        except Exception as e:
                            _ERR(f'on_action callback error: {e}')
            except Exception as e:
                _ERR(f'IP check loop error: {e}')
            for _ in range(self.interval_sec):
                if self._stop:
                    return
                time.sleep(1)

    def check_rate(self, endpoint: str) -> dict:
        return check_rate_limit(self.server_url, self.license_key, endpoint)
