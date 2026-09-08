# -*- coding: utf-8 -*-
"""
Generate version.json manifest for the update system.
Run AFTER building: python gen_version_manifest.py

Scans updatable files (UI + backend Python) and writes a version.json
ready to upload as a GitHub release asset.

Updates:
  - _internal/ui/*.html, *.js, *.css, *.png  → downloaded directly (live reload)
  - core/*.py, database/*.py, network/*.py    → staged, applied on restart

Usage:
    python gen_version_manifest.py                    # auto-detect version
    python gen_version_manifest.py 1.2.0              # override version
    python gen_version_manifest.py 1.2.0 "Bug fixes" # override version + changelog
"""

import os
import sys
import json
import hashlib
import re

ROOT = os.path.abspath('.')
DIST = os.path.join(ROOT, 'dist')
REPO_OWNER = "Jenildholakiya"
REPO_NAME = "AurumOS"

# Files/dirs to NEVER include in manifest
EXCLUDE = {
    'updater.py',       # self-update protection
    'main.py',          # self-update protection
    '__pycache__',
    '*.pyc',
    '.git',
    'dist',
    '.venv',
    'venv',
    'build',
    '_update_staging',
    'exports',
    'logs',
    'database/aurum_local.db',      # user data
    'database/aurum_local.db-shm',  # user data
    'database/aurum_local.db-wal',  # user data
    'config.json',                  # user config
    'exe_trusted_hash.txt',
    'version.lock',
    'sign_config.json',
}

# What to scan
# UI_DIR defaults to the built bundle, but the release workflow points it at
# source ui/ via AURUM_UI_DIR (build copies UI files verbatim, hashes match).
UI_DIR = os.environ.get('AURUM_UI_DIR') or os.path.join(DIST, '_internal', 'ui')
BACKEND_DIRS = ['core', 'database', 'network']
BACKEND_EXTS = {'.py'}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def get_version_from_updater():
    updater = os.path.join(ROOT, 'updater.py')
    with open(updater, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.match(r'CURRENT_VERSION\s*=\s*["\'](.+?)["\']', line)
            if m:
                return m.group(1)
    return "0.0.0"


def _is_excluded(rel):
    basename = os.path.basename(rel)
    if basename in EXCLUDE:
        return True
    for pat in EXCLUDE:
        if pat.startswith('*.') and basename.endswith(pat[1:]):
            return True
        if '/' in pat and rel.startswith(pat):
            return True
    return False


def _asset_name(rel):
    """Convert relative path to a flat GitHub asset name (no slashes)."""
    return rel.replace('/', '__').replace('\\', '__')


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else get_version_from_updater()
    changelog = [sys.argv[2]] if len(sys.argv) > 2 else ["Bug fixes and improvements"]

    files = []

    # ── Scan UI files (dist/_internal/ui/) ─────────────────────────────
    if os.path.isdir(UI_DIR):
        for fname in sorted(os.listdir(UI_DIR)):
            fpath = os.path.join(UI_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            if os.path.getsize(fpath) == 0:
                print(f"  SKIP (empty): {fname}")
                continue
            rel = f"_internal/ui/{fname}"
            asset = fname  # flat name: dashboard.html, boot.js, etc.
            url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/v{version}/{asset}"
            files.append({
                "path": rel,
                "sha256": sha256(fpath),
                "size_bytes": os.path.getsize(fpath),
                "url": url,
            })
        print(f"  UI: {len(files)} files from _internal/ui/")
    else:
        print(f"  WARNING: {UI_DIR} not found — no UI files included")

    # ── Scan backend Python files (core/, database/, network/) ──────────
    backend_count = 0
    for dirname in BACKEND_DIRS:
        dirpath = os.path.join(ROOT, dirname)
        if not os.path.isdir(dirpath):
            continue
        for fname in sorted(os.listdir(dirpath)):
            fpath = os.path.join(dirpath, fname)
            if not os.path.isfile(fpath):
                continue
            if os.path.getsize(fpath) == 0:
                print(f"  SKIP (empty): {dirname}/{fname}")
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in BACKEND_EXTS:
                continue
            rel = f"{dirname}/{fname}"
            if _is_excluded(rel):
                print(f"  SKIP (excluded): {rel}")
                continue
            asset = _asset_name(rel)  # core__tag_engine.py
            url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/v{version}/{asset}"
            files.append({
                "path": rel,
                "sha256": sha256(fpath),
                "size_bytes": os.path.getsize(fpath),
                "url": url,
            })
            backend_count += 1
    print(f"  Backend: {backend_count} Python files from core/, database/, network/")

    if not files:
        print("ERROR: No files found. Run build.py first.")
        sys.exit(1)

    total_size = sum(f["size_bytes"] for f in files)

    manifest = {
        "version": version,
        "release_date": __import__('datetime').date.today().isoformat(),
        "min_version": "1.0.0",
        "download_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/v{version}/AurumOS-{version}.zip",
        "size_bytes": total_size,
        "changelog": changelog,
        "files": files,
        "force": False,
    }

    out_path = os.path.join(ROOT, 'version.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] version.json written - v{version}, {len(files)} files ({total_size:,} bytes)")
    print(f"  Upload to your GitHub release:")
    print(f"    1. version.json (this manifest)")
    print(f"    2. Each _internal/ui/* file (dashboard.html, boot.js, sidebar.css, ...)")
    print(f"    3. Each backend file as flat name: core__tag_engine.py, database__db_manager.py, ...")
    print(f"  Asset names in the release must match the 'url' filename in each entry.")


if __name__ == '__main__':
    main()
