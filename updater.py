# -*- coding: utf-8 -*-
"""
AurumOS Enterprise Updater — version detection, integrity verification,
atomic updates with rollback, release channels, and update logging.

main.py does the downloading (AurumAPI.start_update_with_info). This module
answers three questions and provides the enterprise update pipeline:

    CURRENT_VERSION           the number baked in when the EXE was built
    get_installed_version()   the number the last update wrote (version.lock),
                              or CURRENT_VERSION when no update has run yet
    check_for_update()        the newest release's manifest when it is newer
                              than the installed number, else None

Enterprise features:
    - Release channels (stable/beta/rc) via update_config.json
    - Manifest integrity verification (SHA256 checksum)
    - Atomic file updates with staging directory
    - Automatic rollback on failure
    - Post-update health checks
    - HTTP retry with exponential backoff
    - Structured update logging (_update_log.json)
    - Minimum version enforcement
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_OWNER = "Jenildholakiya"
REPO_NAME = "AurumOS"
MANIFEST_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/latest/download/version.json"
VERSION_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')
VERSION_CHANNEL_RE = re.compile(r'^(\d+\.\d+\.\d+)(?:-(beta|rc)\.\d+)?$')

FALLBACK_VERSION = "2.0.0"

PROTECTED_PATHS = {
    'database', 'logs', 'config.json', 'exe_trusted_hash.txt',
    'AurumOS.exe', 'version.lock', 'build_version.txt', 'build_stamp.txt',
}

DEFAULT_CONFIG = {
    "channel": "stable",
    "auto_update": True,
    "check_interval_hours": 6,
    "max_retries": 3,
}

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
REQUEST_TIMEOUT = 30


# ── bundle & version detection ─────────────────────────────────────────────

def _bundle_dir() -> Path:
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        return Path(meipass) if meipass else Path(sys.executable).parent / '_internal'
    return Path(__file__).resolve().parent


def _baked_version() -> str:
    try:
        v = (_bundle_dir() / 'build_version.txt').read_text(encoding='utf-8').strip()
        if VERSION_RE.match(v):
            return v
    except Exception:
        pass
    return FALLBACK_VERSION


CURRENT_VERSION = _baked_version()

STAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')


def _baked_stamp() -> str:
    try:
        s = (_bundle_dir() / 'build_stamp.txt').read_text(encoding='utf-8').strip()
        if STAMP_RE.match(s):
            return s
    except Exception:
        pass
    return ''


BUILD_STAMP = _baked_stamp()


def sha256(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


def get_app_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _is_protected(rel_path: str) -> bool:
    rel = rel_path.replace('\\', '/').lstrip('./')
    parts = rel.split('/')
    if parts[0] in PROTECTED_PATHS:
        return True
    if rel in PROTECTED_PATHS:
        return True
    return False


def _version_key(v: str):
    """'1.2.3' -> (1, 2, 3); anything unparsable -> (0, 0, 0)."""
    try:
        base = str(v).strip().lstrip('v').split('-')[0]
        return tuple(int(x) for x in base.split('.')[:3])
    except Exception:
        return (0, 0, 0)


def is_newer(remote: str, local: str) -> bool:
    return _version_key(remote) > _version_key(local)


# ── update configuration ───────────────────────────────────────────────────

def _config_path() -> Path:
    return get_app_root() / 'update_config.json'


def load_update_config() -> dict:
    try:
        p = _config_path()
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
    except Exception:
        pass
    return dict(DEFAULT_CONFIG)


def get_update_channel() -> str:
    return load_update_config().get('channel', 'stable')


def _channel_accepts(configured: str, release_channel: str) -> bool:
    """Whether a client on `configured` channel should see a `release_channel` release."""
    if configured == 'stable':
        return release_channel == 'stable'
    if configured == 'rc':
        return release_channel in ('stable', 'rc')
    if configured == 'beta':
        return release_channel in ('stable', 'rc', 'beta')
    return release_channel == 'stable'


# ── installed version (version.lock) ────────────────────────────────────────

def _lock_candidates() -> list:
    root = get_app_root()
    cands = [root / 'version.lock']
    if not getattr(sys, 'frozen', False):
        dist_lock = root / 'dist' / 'version.lock'
        if dist_lock != cands[0]:
            cands.insert(0, dist_lock)
    return cands


def set_installed_version(version: str):
    try:
        _lock_candidates()[0].write_text(version.strip(), encoding='utf-8')
    except Exception:
        pass


def get_installed_version() -> str:
    best, best_key = '', (0, 0, 0)
    for lock in _lock_candidates():
        try:
            if lock.exists():
                v = lock.read_text(encoding='utf-8').strip()
                if VERSION_RE.match(v) and _version_key(v) > best_key:
                    best, best_key = v, _version_key(v)
        except Exception:
            pass
    if best and _version_key(best) >= _version_key(CURRENT_VERSION):
        return best
    return CURRENT_VERSION


# ── HTTP with retry & backoff ──────────────────────────────────────────────

def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        'User-Agent': f'AurumOS/{CURRENT_VERSION}',
        'Accept': 'application/json',
        'Cache-Control': 'no-cache',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _get_json_retry(url: str, timeout: float = REQUEST_TIMEOUT, retries: int = MAX_RETRIES):
    last_err = None
    for attempt in range(retries):
        try:
            return _get_json(url, timeout)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    raise last_err


def _download_file(url: str, dest: Path, timeout: float = REQUEST_TIMEOUT, retries: int = MAX_RETRIES):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': f'AurumOS/{CURRENT_VERSION}',
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, 'wb') as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            return True
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    raise last_err


# ── manifest integrity ─────────────────────────────────────────────────────

def verify_manifest_checksum(manifest: dict) -> bool:
    """Verify the manifest's integrity by recomputing its checksum."""
    expected = manifest.get('manifest_checksum')
    if not expected:
        return True  # older manifests without checksum are accepted
    body = {k: v for k, v in manifest.items() if k != 'manifest_checksum'}
    computed = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()
    return computed == expected


# ── what GitHub has ────────────────────────────────────────────────────────

def fetch_latest_manifest(timeout=8, channel=None):
    """version.json of the release with the highest version number, or None.
    Filters by configured release channel."""
    if channel is None:
        channel = get_update_channel()

    best, best_key = None, (0, 0, 0)

    try:
        m = _get_json_retry(MANIFEST_URL, timeout)
        if isinstance(m, dict) and m.get('version'):
            v = str(m['version']).strip()
            k = _version_key(v)
            rel_channel = m.get('channel', 'stable')
            if k > best_key and _channel_accepts(channel, rel_channel):
                best, best_key = m, k
    except Exception:
        pass

    try:
        releases_url = (
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
            f"/releases?per_page=30"
        )
        releases = _get_json_retry(releases_url, timeout)
        for rel in releases:
            if rel.get('draft'):
                continue
            tag = str(rel.get('tag_name', ''))
            k = _version_key(tag)
            if k <= best_key:
                continue
            for asset in rel.get('assets', []):
                if asset.get('name') == 'version.json':
                    try:
                        m = _get_json_retry(asset['browser_download_url'], timeout)
                        if isinstance(m, dict) and m.get('version'):
                            mk = _version_key(m['version'])
                            rel_channel = m.get('channel', 'stable')
                            if mk > best_key and _channel_accepts(channel, rel_channel):
                                if not m.get('changelog') and rel.get('body'):
                                    m['changelog'] = [
                                        ln.strip()
                                        for ln in str(rel['body']).split('\n')
                                        if ln.strip()
                                    ]
                                best, best_key = m, mk
                    except Exception:
                        pass
                    break
    except Exception:
        pass

    if best and not verify_manifest_checksum(best):
        return None

    return best


def decide(manifest, installed: str):
    """The manifest if it should be installed over `installed`, else None."""
    if not isinstance(manifest, dict):
        return None
    remote = str(manifest.get('version', '') or '').strip()
    base_match = VERSION_CHANNEL_RE.match(remote)
    if not base_match:
        return None
    base_ver = base_match.group(1)

    # Minimum version enforcement: if remote carries min_version and we're below it, force
    min_ver = manifest.get('min_version')
    force_min = False
    if min_ver and VERSION_RE.match(str(min_ver)):
        if _version_key(installed) < _version_key(min_ver):
            force_min = True

    if not is_newer(base_ver, installed) and not manifest.get('force') and not force_min:
        return None

    result = dict(manifest)
    result['_remote_version'] = remote
    result['_changelog'] = '\n'.join(str(c) for c in (manifest.get('changelog') or []))
    result['available'] = True
    result['current'] = installed
    result['file_count'] = len(manifest.get('files') or [])
    result['_force_min_version'] = force_min
    return result


def check_for_update(timeout=8):
    """The newest release manifest if this copy should install it, else None."""
    manifest = fetch_latest_manifest(timeout)
    if not manifest:
        return None
    return decide(manifest, get_installed_version())


# ── atomic update engine ───────────────────────────────────────────────────

def _staging_dir() -> Path:
    return get_app_root() / '_update_staging'


def _backup_dir() -> Path:
    return get_app_root() / '_update_backup'


def _log_path() -> Path:
    return get_app_root() / '_update_log.json'


def _append_log(entry: dict):
    try:
        log = []
        lp = _log_path()
        if lp.exists():
            with open(lp, 'r', encoding='utf-8') as f:
                log = json.load(f)
        log.append(entry)
        # keep only last 50 entries
        if len(log) > 50:
            log = log[-50:]
        with open(lp, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def stage_update(manifest: dict) -> dict:
    """Download all files to staging, verify integrity. Returns status dict."""
    staging = _staging_dir()
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    files = manifest.get('files') or []
    downloaded = []
    for finfo in files:
        rel_path = finfo.get('path', '')
        if _is_protected(rel_path):
            continue
        url = finfo.get('url', '')
        expected_hash = finfo.get('sha256', '')
        if not url or not rel_path:
            continue

        dest = staging / rel_path.replace('/', os.sep)
        try:
            _download_file(url, dest)
        except Exception as e:
            return {'ok': False, 'error': f'download failed: {rel_path}: {e}', 'downloaded': downloaded}

        if expected_hash:
            actual = sha256(dest)
            if actual != expected_hash:
                return {'ok': False, 'error': f'integrity check failed: {rel_path}', 'downloaded': downloaded}

        downloaded.append(rel_path)

    return {'ok': True, 'downloaded': downloaded}


def backup_current(file_paths: list) -> bool:
    """Back up current versions of files that will be replaced."""
    backup = _backup_dir()
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True, exist_ok=True)

    root = get_app_root()
    for rel_path in file_paths:
        src = root / rel_path.replace('/', os.sep)
        if src.exists():
            dest = backup / rel_path.replace('/', os.sep)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dest))
            except Exception:
                return False
    return True


def apply_staged(file_paths: list) -> bool:
    """Move staged files into place."""
    staging = _staging_dir()
    root = get_app_root()
    for rel_path in file_paths:
        src = staging / rel_path.replace('/', os.sep)
        dest = root / rel_path.replace('/', os.sep)
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(dest))
        except Exception:
            return False
    return True


def rollback(file_paths: list) -> bool:
    """Restore files from backup."""
    backup = _backup_dir()
    root = get_app_root()
    for rel_path in file_paths:
        bak = backup / rel_path.replace('/', os.sep)
        dest = root / rel_path.replace('/', os.sep)
        if bak.exists():
            try:
                shutil.copy2(str(bak), str(dest))
            except Exception:
                return False
    return True


def health_check(manifest: dict, file_paths: list) -> bool:
    """Verify all updated files exist and match expected SHA256."""
    root = get_app_root()
    file_map = {f['path']: f.get('sha256', '') for f in (manifest.get('files') or [])}
    for rel_path in file_paths:
        dest = root / rel_path.replace('/', os.sep)
        if not dest.exists():
            return False
        expected = file_map.get(rel_path, '')
        if expected and sha256(dest) != expected:
            return False
    return True


def perform_update(manifest: dict) -> dict:
    """Full atomic update pipeline: stage → backup → apply → health check → cleanup."""
    start_time = time.time()
    version = manifest.get('version', 'unknown')
    installed = get_installed_version()

    log_entry = {
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'from_version': installed,
        'to_version': version,
        'channel': manifest.get('channel', 'stable'),
        'result': 'pending',
    }

    # Stage
    stage_result = stage_update(manifest)
    if not stage_result['ok']:
        log_entry.update(result='stage_failed', error=stage_result.get('error', ''),
                         duration_ms=int((time.time() - start_time) * 1000))
        _append_log(log_entry)
        return {'ok': False, 'phase': 'staging', 'error': stage_result['error']}

    file_paths = stage_result['downloaded']
    log_entry['files_updated'] = len(file_paths)

    # Backup
    if not backup_current(file_paths):
        log_entry.update(result='backup_failed', duration_ms=int((time.time() - start_time) * 1000))
        _append_log(log_entry)
        return {'ok': False, 'phase': 'backup', 'error': 'failed to backup current files'}

    # Apply
    if not apply_staged(file_paths):
        rollback(file_paths)
        log_entry.update(result='apply_failed_rollback', duration_ms=int((time.time() - start_time) * 1000))
        _append_log(log_entry)
        return {'ok': False, 'phase': 'apply', 'error': 'failed to apply update, rolled back'}

    # Health check
    if not health_check(manifest, file_paths):
        rollback(file_paths)
        log_entry.update(result='health_check_failed_rollback', duration_ms=int((time.time() - start_time) * 1000))
        _append_log(log_entry)
        return {'ok': False, 'phase': 'health_check', 'error': 'health check failed, rolled back'}

    # Success — record version and clean up
    base_match = VERSION_CHANNEL_RE.match(str(version))
    lock_ver = base_match.group(1) if base_match else version
    set_installed_version(lock_ver)

    shutil.rmtree(_staging_dir(), ignore_errors=True)
    shutil.rmtree(_backup_dir(), ignore_errors=True)

    log_entry.update(result='success', duration_ms=int((time.time() - start_time) * 1000))
    _append_log(log_entry)

    return {'ok': True, 'version': version, 'files_updated': len(file_paths),
            'duration_ms': log_entry['duration_ms']}


# ── backwards compatibility ────────────────────────────────────────────────

def _sha256(path):
    return sha256(Path(path) if not isinstance(path, Path) else path)


__all__ = [
    'CURRENT_VERSION',
    'FALLBACK_VERSION',
    'MANIFEST_URL',
    'check_for_update',
    'decide',
    'fetch_latest_manifest',
    'is_newer',
    'sha256',
    'get_app_root',
    '_is_protected',
    'set_installed_version',
    'get_installed_version',
    'load_update_config',
    'get_update_channel',
    'verify_manifest_checksum',
    'stage_update',
    'backup_current',
    'apply_staged',
    'rollback',
    'health_check',
    'perform_update',
]
