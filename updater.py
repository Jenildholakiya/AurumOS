# -*- coding: utf-8 -*-
"""
AurumOS Updater Module
Checks GitHub releases for updates and downloads changed files.
"""

import os
import sys
import json
import hashlib
import urllib.request
import urllib.error
from pathlib import Path

# Current version baked into the EXE at build time
# Update this BEFORE building each release — must match the GitHub release tag
CURRENT_VERSION = "1.1.5"

# GitHub repo for releases
REPO_OWNER = "Jenildholakiya"
REPO_NAME = "AurumOS"
VERSION_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

# Files that should never be auto-updated (protected)
PROTECTED_PATHS = {
    'database', 'logs', 'config.json', 'exe_trusted_hash.txt',
    'AurumOS.exe', 'version.lock',
}

def sha256(path: Path) -> str:
    """Compute SHA256 hash of a file. Returns empty string if missing."""
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


def get_app_root() -> Path:
    """Return the application root directory (where updatable files live)."""
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        # If EXE is in dist/ or dist/AurumOS/, go up to project root
        if exe_dir.name.lower() in ('dist', 'aurumos'):
            return exe_dir.parent
        return exe_dir
    return Path.cwd()


def _is_protected(rel_path: str) -> bool:
    """Check if a relative path should be protected from auto-updates."""
    rel = rel_path.replace('\\', '/').lstrip('./')
    parts = rel.split('/')
    # Protect top-level folders like database/, logs/, and specific files
    if parts[0] in PROTECTED_PATHS:
        return True
    if rel in PROTECTED_PATHS:
        return True
    return False


def _version_key(v: str):
    """Convert version string to tuple for comparison."""
    try:
        return tuple(int(x) for x in v.split('.')[:3])
    except Exception:
        return (0, 0, 0)


def check_for_update(timeout=8) -> dict | None:
    """
    Check GitHub for the latest release.
    Returns dict with version info if update available, None otherwise.
    """
    try:
        req = urllib.request.Request(
            VERSION_URL,
            headers={
                'User-Agent': f'AurumOS/{CURRENT_VERSION}',
                'Accept': 'application/vnd.github.v3+json',
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError:
        return None  # No network
    except Exception:
        return None

    # Parse version from tag_name (e.g., "v1.1.4")
    tag = data.get('tag_name', '')
    if not tag.startswith('v'):
        return None
    remote_version = tag[1:]

    # Compare versions — use installed version (version.lock) if available,
    # falls back to CURRENT_VERSION for fresh installs
    local_version = get_installed_version()
    if _version_key(remote_version) <= _version_key(local_version):
        return None

    # Find version.json asset
    version_json_url = None
    for asset in data.get('assets', []):
        if asset.get('name') == 'version.json':
            version_json_url = asset.get('browser_download_url')
            break

    if not version_json_url:
        return None

    # Fetch version.json with file manifest
    try:
        req = urllib.request.Request(
            version_json_url,
            headers={'User-Agent': f'AurumOS/{CURRENT_VERSION}'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            manifest = json.loads(resp.read().decode())
    except Exception:
        return None

    # Add metadata — callers expect these keys
    manifest['_remote_version'] = remote_version
    manifest['_changelog'] = data.get('body', '')
    manifest['available'] = True
    manifest['current'] = CURRENT_VERSION
    manifest['file_count'] = len(manifest.get('files', []))
    return manifest


def _lock_candidates() -> list:
    """All places version.lock may live (newest wins).

    History left locks in two spots: beside the EXE (dist/) on some installs
    and at the project root on others. Read/write the same resolved file so
    the sidebar version can never disagree with the updater again.
    """
    root = get_app_root()
    cands = [root / 'version.lock']
    try:
        if getattr(sys, 'frozen', False):
            exe_lock = Path(sys.executable).parent / 'version.lock'
            if exe_lock != cands[0]:
                cands.insert(0, exe_lock)
        else:
            dist_lock = Path(root) / 'dist' / 'version.lock'
            if dist_lock != cands[0]:
                cands.insert(0, dist_lock)
    except Exception:
        pass
    return cands


def set_installed_version(version: str):
    """Write version.lock file to mark installed version."""
    try:
        _lock_candidates()[0].write_text(version.strip(), encoding='utf-8')
    except Exception:
        pass


def get_installed_version() -> str:
    """Read the newest version.lock among known locations, else baked version."""
    try:
        best, best_key = '', (0, 0, 0)
        for lock in _lock_candidates():
            try:
                if lock.exists():
                    v = lock.read_text(encoding='utf-8').strip()
                    if _version_key(v) > best_key:
                        best, best_key = v, _version_key(v)
            except Exception:
                pass
        if best:
            return best
    except Exception:
        pass
    return CURRENT_VERSION


# Backwards compatibility - main.py imports these
def _sha256(path):
    """Alias for sha256() used by main.py"""
    return sha256(Path(path) if not isinstance(path, Path) else path)


# Export for main.py
__all__ = [
    'CURRENT_VERSION',
    'check_for_update',
    'sha256',
    'get_app_root',
    '_is_protected',
    'set_installed_version',
    'get_installed_version',
]