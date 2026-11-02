"""
AurumOS Remote Config — Encrypted key-value config from admin portal.
Fetch, AES-256-GCM decrypt, cache locally, react to SSE pushes.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

_LOG = lambda m: print(f'[REMOTE-CONFIG] {m}', flush=True)
_ERR = lambda m: print(f'[REMOTE-CONFIG ERR] {m}', flush=True)

# ── Shared secret — must match server's CONFIG_ENCRYPTION_SECRET env var ──
# Replace at build time or load from secure storage.
_CONFIG_SECRET = 'REPLACE_WITH_CONFIG_ENCRYPTION_SECRET'


def _derive_key(secret: str) -> bytes:
    """HMAC-SHA256(key="aurum-config-v1", data=secret) → 32-byte AES key."""
    return hmac.new(b'aurum-config-v1', secret.encode('utf-8'), hashlib.sha256).digest()


def _decrypt_value(b64_value: str, aes_key: bytes) -> str | None:
    """Decrypt a single AES-256-GCM encrypted base64 value.
    Binary layout: [12-byte IV][ciphertext][16-byte GCM auth tag]
    """
    try:
        raw = base64.b64decode(b64_value)
        if len(raw) < 29:  # 12 + 1 + 16 minimum
            return None
        iv = raw[:12]
        ciphertext_with_tag = raw[12:]
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(aes_key).decrypt(iv, ciphertext_with_tag, None).decode('utf-8')
        except ImportError:
            _ERR('cryptography library not available')
            return None
    except Exception as e:
        _ERR(f'Decrypt failed: {e}')
        return None


def fetch_and_decrypt(server_url: str, license_key: str, secret: str = '') -> dict:
    """GET /api/config/fetch?key=<KEY>, decrypt all values, return {key: plaintext}."""
    secret = secret or _CONFIG_SECRET
    aes_key = _derive_key(secret)
    url = f"{server_url.rstrip('/')}/api/config/fetch?key={license_key}"
    _LOG(f'Fetching config from {url[:80]}...')

    try:
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
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        _ERR(f'Fetch failed: {e}')
        return {}

    if body.get('status') != 'ok':
        _ERR(f'Server error: {body.get("message", "unknown")}')
        return {}

    configs = body.get('configs', [])
    result = {}
    for entry in configs:
        key = entry.get('key', '')
        enc_val = entry.get('value', '')
        if not key or not enc_val:
            continue
        plain = _decrypt_value(enc_val, aes_key)
        if plain is not None:
            result[key] = plain
        else:
            _ERR(f'Could not decrypt key={key}, skipping')
    _LOG(f'Fetched and decrypted {len(result)}/{len(configs)} config entries')
    return result


def _get_base_dir() -> str:
    return os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')


class ConfigStore:
    """Thread-safe typed config accessor with local cache and change listeners."""

    def __init__(self, cache_path: str = ''):
        self._lock = threading.Lock()
        self._configs: dict[str, str] = {}
        self._listeners: dict[str, list] = {}
        self._cache_path = cache_path or os.path.join(
            _get_base_dir(), 'database', '.config_cache.json')
        self.load_cache()

    # ── Cache ────────────────────────────────────────────────────────
    def load_cache(self):
        try:
            if os.path.exists(self._cache_path):
                with open(self._cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                with self._lock:
                    self._configs = data.get('configs', {})
                _LOG(f'Loaded {len(self._configs)} cached config entries')
        except Exception as e:
            _ERR(f'Cache load failed: {e}')

    def save_cache(self):
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'configs': self._configs,
                }, f, indent=2)
        except Exception as e:
            _ERR(f'Cache save failed: {e}')

    # ── Update from fetch ────────────────────────────────────────────
    def load_from_fetch(self, new_configs: dict):
        changed_keys = []
        with self._lock:
            for k, v in new_configs.items():
                if self._configs.get(k) != v:
                    changed_keys.append(k)
            old_keys = set(self._configs.keys()) - set(new_configs.keys())
            changed_keys.extend(old_keys)
            self._configs = dict(new_configs)
        self.save_cache()
        for k in changed_keys:
            self._fire(k)

    def _fire(self, key: str):
        with self._lock:
            cbs = list(self._listeners.get(key, []))
        val = self.get(key)
        for cb in cbs:
            try:
                cb(val)
            except Exception as e:
                _ERR(f'Listener error for {key}: {e}')

    # ── Typed getters ────────────────────────────────────────────────
    def get(self, key: str) -> str | None:
        with self._lock:
            return self._configs.get(key)

    def get_string(self, key: str, fallback: str = '') -> str:
        v = self.get(key)
        return v if v is not None else fallback

    def get_number(self, key: str, fallback: float = 0) -> float:
        v = self.get(key)
        if v is None:
            return fallback
        try:
            return float(v)
        except (ValueError, TypeError):
            return fallback

    def get_bool(self, key: str, fallback: bool = False) -> bool:
        v = self.get(key)
        if v is None:
            return fallback
        return v.lower() in ('true', '1', 'yes')

    def get_json(self, key: str, fallback=None):
        v = self.get(key)
        if v is None:
            return fallback
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return fallback

    # ── Change listener ──────────────────────────────────────────────
    def on_change(self, key: str, callback):
        with self._lock:
            self._listeners.setdefault(key, []).append(callback)
        def _unsub():
            with self._lock:
                try:
                    self._listeners[key].remove(callback)
                except (KeyError, ValueError):
                    pass
        return _unsub

    def all(self) -> dict:
        with self._lock:
            return dict(self._configs)
