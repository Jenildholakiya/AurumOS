# -*- coding: utf-8 -*-
"""
Write version.json, the manifest installed copies of AurumOS read to update.

    python scripts/release/gen_version_manifest.py --plan release_plan.json   # what the workflow runs
    python scripts/release/gen_version_manifest.py                            # version = highest git tag
    python scripts/release/gen_version_manifest.py 1.2.0 "Fix totals"         # explicit version + note

Only ui/ is listed. Every Python file is frozen inside AurumOS.exe and a loose
copy next to it is never imported, so shipping one only wastes downloads and
pretends the change arrived. Set AURUM_SHIP_BACKEND=1 to list core/, database/
and network/ anyway, for copies that run from source with python main.py.

Env:
    AURUM_UI_DIR          folder to scan. Default dist/_internal/ui (the built
                          copy); the workflow points it at ui/, which build.py
                          copies verbatim, so the hashes are the same.
    AURUM_FORCE=1         version-reset release: installs even when not newer
    AURUM_SHIP_BACKEND=1  also list backend .py files (see above)
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath('.')
DIST = os.path.join(ROOT, 'dist')
REPO_OWNER = "Jenildholakiya"
REPO_NAME = "AurumOS"
RELEASES = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"

# Never part of a release, whatever folder they sit in.
EXCLUDE = {
    'updater.py', 'main.py',            # self-update protection
    '__pycache__', '*.pyc', '.git', 'dist', '.venv', 'venv', 'build',
    '_update_staging', 'exports', 'logs',
    'database/aurum_local.db', 'database/aurum_local.db-shm', 'database/aurum_local.db-wal',
    'config.json', 'exe_trusted_hash.txt', 'version.lock', 'sign_config.json',
}

UI_DIR = os.environ.get('AURUM_UI_DIR') or os.path.join(DIST, '_internal', 'ui')
BACKEND_DIRS = ['core', 'database', 'network'] if os.environ.get('AURUM_SHIP_BACKEND') == '1' else []
BACKEND_EXTS = {'.py'}
VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def version_from_git():
    """Highest vX.Y.Z tag, the same number build.py stamps into the EXE."""
    try:
        out = subprocess.run(['git', 'tag', '-l', 'v*'], capture_output=True, text=True,
                             cwd=ROOT, timeout=30)
        found = []
        for t in out.stdout.split():
            m = re.match(r'^v(\d+)\.(\d+)\.(\d+)$', t.strip())
            if m:
                found.append(tuple(int(x) for x in m.groups()))
        if found:
            return '.'.join(str(x) for x in max(found))
    except Exception:
        pass
    return None


def version_from_updater():
    try:
        with open(os.path.join(ROOT, 'updater.py'), 'r', encoding='utf-8') as f:
            for line in f:
                m = re.match(r'(?:FALLBACK_VERSION|CURRENT_VERSION)\s*=\s*["\'](.+?)["\']', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
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
    """Relative path -> flat GitHub asset name (assets cannot contain slashes)."""
    return rel.replace('/', '__').replace('\\', '__')


def parse_args(argv):
    """Returns (version, changelog list, plan dict or None)."""
    args = list(argv)
    plan = None
    if args and args[0] == '--plan':
        if len(args) < 2:
            sys.exit("--plan needs a file")
        with open(args[1], 'r', encoding='utf-8') as f:
            plan = json.load(f)
        version = str(plan.get('version', '')).strip()
        changelog = list(plan.get('changelog') or [])
        return version, changelog, plan
    version = args[0] if args else (version_from_git() or version_from_updater())
    text = args[1] if len(args) > 1 else ''
    changelog = [ln.lstrip('-* ').strip() for ln in text.split('\n') if ln.strip()]
    return version, changelog or ["Bug fixes and improvements"], None


def main():
    version, changelog, plan = parse_args(sys.argv[1:])
    if not VERSION_RE.match(version):
        sys.exit(f"ERROR: version {version!r} is not X.Y.Z")
    forced = os.environ.get('AURUM_FORCE', '').strip() == '1' or bool(plan and plan.get('force'))
    rebuild = list(plan.get('rebuild_files') or []) if plan else []
    commit = (plan.get('commit') if plan else '') or os.environ.get('GITHUB_SHA', '')

    files = []

    # ── UI files: one flat asset per file, served live on the next screen ──
    if os.path.isdir(UI_DIR):
        for fname in sorted(os.listdir(UI_DIR)):
            fpath = os.path.join(UI_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            if os.path.getsize(fpath) == 0:
                print(f"  SKIP (empty, GitHub rejects 0-byte assets): {fname}")
                continue
            files.append({
                "path": f"_internal/ui/{fname}",
                "sha256": sha256(fpath),
                "size_bytes": os.path.getsize(fpath),
                "url": f"{RELEASES}/download/v{version}/{fname}",
            })
        print(f"  UI: {len(files)} files from {os.path.relpath(UI_DIR, ROOT)}/")
    else:
        sys.exit(f"ERROR: {UI_DIR} not found -- nothing to ship")

    # ── Backend .py (opt-in; never read by the frozen EXE) ─────────────────
    backend_count = 0
    for dirname in BACKEND_DIRS:
        dirpath = os.path.join(ROOT, dirname)
        if not os.path.isdir(dirpath):
            continue
        for fname in sorted(os.listdir(dirpath)):
            fpath = os.path.join(dirpath, fname)
            if not os.path.isfile(fpath) or os.path.getsize(fpath) == 0:
                continue
            if os.path.splitext(fname)[1].lower() not in BACKEND_EXTS:
                continue
            rel = f"{dirname}/{fname}"
            if _is_excluded(rel):
                continue
            files.append({
                "path": rel,
                "sha256": sha256(fpath),
                "size_bytes": os.path.getsize(fpath),
                "url": f"{RELEASES}/download/v{version}/{_asset_name(rel)}",
            })
            backend_count += 1
    if BACKEND_DIRS:
        print(f"  Backend: {backend_count} .py files (AURUM_SHIP_BACKEND=1)")

    # ── SQL migrations: always shipped — they are data, not code ─────────
    # migrations/NNNN_what.sql runs once per shop via
    # DBManager.apply_migrations, so a release can fix schema without a
    # rebuild. The live database/ folder itself is never shipped.
    mig_dir = os.path.join(ROOT, 'migrations')
    mig_count = 0
    if os.path.isdir(mig_dir):
        for fname in sorted(os.listdir(mig_dir)):
            if not fname.lower().endswith('.sql'):
                continue
            fpath = os.path.join(mig_dir, fname)
            if not os.path.isfile(fpath) or os.path.getsize(fpath) == 0:
                continue
            rel = f"migrations/{fname}"
            files.append({
                "path": rel,
                "sha256": sha256(fpath),
                "size_bytes": os.path.getsize(fpath),
                "url": f"{RELEASES}/download/v{version}/{_asset_name(rel)}",
            })
            mig_count += 1
        if mig_count:
            print(f"  Migrations: {mig_count} .sql file(s)")

    # Determine which files are safe to rollback (ui/ files are, frozen are not)
    for f_entry in files:
        f_entry["rollback_safe"] = f_entry["path"].startswith("_internal/ui/") or f_entry["path"].startswith("migrations/")

    channel = (plan.get('channel') if plan else None) or 'stable'
    min_ver = (plan.get('min_version') if plan else None) or None

    total_size = sum(f["size_bytes"] for f in files)
    manifest = {
        "version": version,
        "channel": channel,
        "release_date": datetime.date.today().isoformat(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "min_version": min_ver or "1.0.0",
        "download_url": f"{RELEASES}/tag/v{version}",
        "release_url": f"{RELEASES}/tag/v{version}",
        "commit": (commit or '')[:7],
        "size_bytes": total_size,
        "changelog": changelog,
        "files": files,
        "force": forced,
        "needs_rebuild": bool(rebuild),
        "rebuild_files": rebuild,
    }

    # Integrity: compute manifest_checksum over the body (excluding the checksum itself)
    body_for_hash = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode('utf-8')
    manifest["manifest_checksum"] = hashlib.sha256(body_for_hash).hexdigest()

    out_path = os.path.join(ROOT, 'version.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] version.json written - v{version} [{channel}], {len(files)} files ({total_size:,} bytes)"
          + (", FORCED" if forced else "")
          + (f", rebuild needed for {len(rebuild)} frozen file(s)" if rebuild else ""))


if __name__ == '__main__':
    main()
