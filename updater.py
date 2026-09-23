# -*- coding: utf-8 -*-
"""
AurumOS updater: which version this copy runs, and whether GitHub has a newer one.

main.py does the downloading (AurumAPI.auto_update_silent). This module only
answers three questions:

    CURRENT_VERSION           the number baked in when the EXE was built
    get_installed_version()   the number the last update wrote (version.lock),
                              or CURRENT_VERSION when no update has run yet
    check_for_update()        the newest release's manifest when it is newer
                              than the installed number (or marked force),
                              else None

The manifest comes from the release's "latest/download" link, which needs no
API token and has no rate limit. The GitHub API is only a fallback.

Every push to main creates a release (see .github/workflows/release.yml), so
the number here is not edited by hand: build.py stamps the highest git tag
into build_version.txt inside the bundle and CURRENT_VERSION reads it.
"""

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_OWNER = "Jenildholakiya"
REPO_NAME = "AurumOS"
MANIFEST_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/latest/download/version.json"
VERSION_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')

# Used only when build_version.txt is missing (running from source, or a
# build made without git). Keep it at the last number that was shipped by
# hand so such a copy still accepts the next automatic release.
FALLBACK_VERSION = "1.1.7"

# Files that should never be auto-updated (protected).
# database/ stays protected ON PURPOSE: it holds the shop's live data, and
# an overwrite would wipe it. Schema fixes travel as versioned SQL under
# migrations/ (see DBManager.apply_migrations) — data-safe by construction,
# so that folder must stay downloadable and is deliberately NOT listed here.
PROTECTED_PATHS = {
    'database', 'logs', 'config.json', 'exe_trusted_hash.txt',
    'AurumOS.exe', 'version.lock', 'build_version.txt',
}


def _bundle_dir() -> Path:
    """Where bundled data lives: _internal/ next to the EXE, or this folder."""
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


def sha256(path: Path) -> str:
    """SHA256 of a file, or '' when it is missing or unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


def get_app_root() -> Path:
    """Where updates land.

    Frozen: the folder holding AurumOS.exe. Its _internal/ui is what the app
    serves, so a downloaded screen is the one the user sees next. (The old
    rule of climbing out of a folder called dist/ or AurumOS/ sent the files
    one level too high whenever the install folder had one of those names.)
    From source: the working directory.
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _is_protected(rel_path: str) -> bool:
    """True for paths an update must never overwrite."""
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
        return tuple(int(x) for x in str(v).strip().lstrip('v').split('.')[:3])
    except Exception:
        return (0, 0, 0)


def is_newer(remote: str, local: str) -> bool:
    return _version_key(remote) > _version_key(local)


# ── installed version (version.lock) ────────────────────────────────────────

def _lock_candidates() -> list:
    """Every place version.lock may live, newest wins.

    Frozen copies keep it beside the EXE. Running from source, older code
    left one in dist/ and one at the root; both are read so the sidebar and
    the updater can never disagree.
    """
    root = get_app_root()
    cands = [root / 'version.lock']
    if not getattr(sys, 'frozen', False):
        dist_lock = root / 'dist' / 'version.lock'
        if dist_lock != cands[0]:
            cands.insert(0, dist_lock)
    return cands


def set_installed_version(version: str):
    """Record that `version` is fully installed."""
    try:
        _lock_candidates()[0].write_text(version.strip(), encoding='utf-8')
    except Exception:
        pass


def get_installed_version() -> str:
    """The newest version.lock among known locations, else the baked version."""
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


# ── what GitHub has ─────────────────────────────────────────────────────────

def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        'User-Agent': f'AurumOS/{CURRENT_VERSION}',
        'Accept': 'application/json',
        'Cache-Control': 'no-cache',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_latest_manifest(timeout=8):
    """version.json of the newest release, or None when offline.

    The direct link first (no API, no rate limit); the API second, for the
    odd case where the redirect misbehaves."""
    try:
        m = _get_json(MANIFEST_URL, timeout)
        if isinstance(m, dict) and m.get('version'):
            return m
    except Exception:
        pass
    try:
        rel = _get_json(VERSION_URL, timeout)
        for asset in rel.get('assets', []):
            if asset.get('name') == 'version.json':
                m = _get_json(asset.get('browser_download_url'), timeout)
                if isinstance(m, dict) and m.get('version'):
                    if not m.get('changelog') and rel.get('body'):
                        m['changelog'] = [ln.strip() for ln in str(rel['body']).split('\n') if ln.strip()]
                    return m
    except Exception:
        pass
    return None


def decide(manifest, installed: str):
    """The manifest if it should be installed over `installed`, else None.

    Pure, so test_release_plan.py can pin the rules: newer wins; equal or
    older is ignored unless the release is marked force (a version reset).
    """
    if not isinstance(manifest, dict):
        return None
    remote = str(manifest.get('version', '') or '').strip()
    if not VERSION_RE.match(remote):
        return None
    if not is_newer(remote, installed) and not manifest.get('force'):
        return None
    result = dict(manifest)
    result['_remote_version'] = remote
    result['_changelog'] = '\n'.join(str(c) for c in (manifest.get('changelog') or []))
    result['available'] = True
    result['current'] = installed
    result['file_count'] = len(manifest.get('files') or [])
    return result


def check_for_update(timeout=8):
    """The newest release manifest if this copy should install it, else None.

    None also means "offline" -- callers treat both the same: nothing to do.
    """
    manifest = fetch_latest_manifest(timeout)
    if not manifest:
        return None
    return decide(manifest, get_installed_version())


# Backwards compatibility: main.py once imported this name.
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
]
