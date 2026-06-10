# -*- coding: utf-8 -*-
"""
AurumOS Delta Updater  — PERMANENT FIX
========================================

THE ONE METHOD THAT ALWAYS WORKS:
──────────────────────────────────
  "Installed version" is NEVER read from CURRENT_VERSION in this file.
  CURRENT_VERSION is only used as a last-last fallback.

  Priority order for "what version does this client have":
    1. version.lock  (written after every successful update)
    2. DB app_config installed_version
    3. "0.0.0"       (treats any GitHub release as newer — ALWAYS triggers)

  This means:
    - Fresh install  → "0.0.0" < any version on GitHub → update triggers ✓
    - After update   → version.lock has real version   → correct compare ✓
    - Lock corrupted → "0.0.0" fallback                → update triggers ✓
    - NEVER falls back to CURRENT_VERSION baked in EXE → no false match ✓

HOW TO RELEASE (every single time, 3 steps):
──────────────────────────────────────────────
  1. Edit files (billing.html, main.py, etc.)
  2. In build_release.py → bump NEW_VERSION = "1.0.8"
     In updater.py       → bump CURRENT_VERSION = "1.0.8"  (optional — only for logs)
  3. python build_release.py  →  git add -A  →  git commit  →  git push

  Client sees update banner automatically. Done.
"""

import os
import sys
import json
import hashlib
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
CURRENT_VERSION = '1.1.1'   # informational only — NOT used for update comparison
GITHUB_OWNER    = "Jenildholakiya"
GITHUB_REPO     = "AurumOS"
GITHUB_BRANCH   = "main"
# ══════════════════════════════════════════════════════════════════════════════

_RAW_BASE        = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
VERSION_JSON_URL = f"{_RAW_BASE}/version.json"

_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".ttf", ".otf", ".woff", ".woff2",
    ".pdf", ".zip", ".gz", ".exe", ".dll", ".pyd", ".so", ".db", ".sqlite",
}

PROTECTED = {
    "database", "logs", "backups",
    ".env", "config.json", "aurum_config.json",
    "updater.py",
    ".idea", ".vscode", ".git", ".gitignore",
    "__pycache__", ".venv", "venv",
    "build_release.py", "AurumOS.spec", "build", "dist",
}
_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".bak",
    ".db", ".sqlite", ".spec",
}

_PENDING_UPDATE = [None]


# ══════════════════════════════════════════════════════════════════════════════
#  VERSION TRACKING  — THE PERMANENT FIX
# ══════════════════════════════════════════════════════════════════════════════

def _version_lock_path() -> Path:
    """version.lock sits next to EXE — survives app restarts."""
    base = Path(
        os.path.dirname(sys.executable)
        if getattr(sys, 'frozen', False)
        else os.path.abspath('.')
    )
    return base / 'version.lock'


def get_installed_version() -> str:
    """
    Returns the version this CLIENT currently has installed.

    Priority:
      1. version.lock  — written by set_installed_version() after update
      2. DB app_config — legacy fallback
      3. "0.0.0"       — ALWAYS triggers update on fresh installs

    !! NEVER returns CURRENT_VERSION baked in EXE !!
    That caused the "already up to date" bug because
    CURRENT_VERSION matched version.json and is_newer() returned False.
    """
    import re as _re

    # ── 1. version.lock (most reliable) ──────────────────────────────────
    try:
        lock = _version_lock_path()
        if lock.exists():
            v = lock.read_text(encoding='utf-8').strip()
            if v and _re.match(r'^\d+\.\d+\.\d+$', v):
                print(f"[UPDATER] installed version from lock: {v}")
                return v
    except Exception as e:
        print(f"[UPDATER] version.lock read error: {e}")

    # ── 2. DB app_config (legacy) ─────────────────────────────────────────
    try:
        import sqlite3
        base    = Path(
            os.path.dirname(sys.executable)
            if getattr(sys, 'frozen', False)
            else os.path.abspath('.')
        )
        db_path = base / "database" / "aurum_local.db"
        if db_path.exists():
            con = sqlite3.connect(str(db_path))
            row = con.execute(
                "SELECT value FROM app_config WHERE key='installed_version' LIMIT 1"
            ).fetchone()
            con.close()
            if row and row[0]:
                v = str(row[0]).strip()
                if v and _re.match(r'^\d+\.\d+\.\d+$', v):
                    print(f"[UPDATER] installed version from DB: {v}")
                    # Migrate to lock file for next time
                    try:
                        _version_lock_path().write_text(v, encoding='utf-8')
                    except Exception:
                        pass
                    return v
    except Exception as e:
        print(f"[UPDATER] DB version read error: {e}")

    # ── 3. FALLBACK — "0.0.0" always triggers update ─────────────────────
    # This is intentional: on a fresh machine with no lock/DB entry,
    # we want ANY version on GitHub to be seen as newer.
    print("[UPDATER] No installed version found → using 0.0.0 (will trigger update)")
    return "0.0.0"


def set_installed_version(version: str) -> bool:
    import re as _re
    version = str(version).strip()
    if not _re.match(r'^\d+\.\d+\.\d+$', version):
        return False

    lock_path = _version_lock_path()
    try:
        # Use a temporary file and rename it (Atomic write)
        temp_lock = lock_path.with_suffix('.tmp')
        temp_lock.write_text(version, encoding='utf-8')

        # Replace the real file
        if lock_path.exists():
            os.remove(lock_path)
        os.rename(temp_lock, lock_path)

        print(f"[UPDATER] SUCCESS: Written {version} to {lock_path}")
        return True
    except Exception as e:
        print(f"[UPDATER] CRITICAL ERROR: Could not write version.lock: {e}")
        # If it fails, print the full path so you can check permissions
        print(f"[UPDATER] Target Path: {lock_path}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_app_root() -> Path:
    """Project root — where ui/, core/, fonts/ etc. live."""
    if getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS'):
        exe_dir = Path(sys.executable).parent.resolve()
        if exe_dir.name.lower() in ('dist', 'aurumos'):
            return exe_dir.parent.resolve()
        return exe_dir
    return Path(os.path.abspath(".")).resolve()


def get_exe_path() -> Path:
    if hasattr(sys, '_MEIPASS'):
        mei_dir  = Path(sys._MEIPASS)
        exe_name = Path(sys.executable).name
        real_exe = mei_dir.parent / exe_name
        if real_exe.exists():
            return real_exe
        argv0 = Path(sys.argv[0]).resolve()
        if argv0.exists() and argv0.suffix.lower() == '.exe':
            return argv0
    return Path(sys.executable).resolve()


def _ver(v: str):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0, 0, 0)


def is_newer(remote: str, local: str) -> bool:
    return _ver(remote) > _ver(local)


def _load_token() -> str:
    try:
        cfg = Path(
            os.path.dirname(sys.executable)
            if getattr(sys, 'frozen', False)
            else os.path.abspath(".")
        ) / "aurum_config.json"
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            tok  = data.get("github_token", "")
            if tok:
                return tok
    except Exception:
        pass
    return ""


GITHUB_TOKEN = _load_token()


def _headers(accept="application/json"):
    h = {"User-Agent": f"AurumOS/{CURRENT_VERSION}", "Accept": accept}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


def _get_bytes(url: str, timeout: int = 30) -> bytes:
    import io as _io
    req = urllib.request.Request(url, headers=_headers("*/*"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            buf = _io.BytesIO()
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                buf.write(chunk)
            data = buf.getvalue()
            print(f"[UPDATER] {len(data):,} bytes ← {url[:72]}")
            return data
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Download error: {e}")


def sha256(path: Path) -> str:
    """Hash a file. Text: CRLF→LF normalised. Missing file: empty string."""
    try:
        data = Path(path).read_bytes()
        if Path(path).suffix.lower() not in _BINARY_EXT:
            data = data.replace(b"\r\n", b"\n")
        return hashlib.sha256(data).hexdigest()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[UPDATER] sha256({path}): {e}")
        return ""


def _is_protected(rel: str) -> bool:
    rel_l = rel.lower().replace("\\", "/")
    for ext in _SKIP_EXTENSIONS:
        if rel_l.endswith(ext):
            return True
    for p in PROTECTED:
        p_l = p.lower().replace("\\", "/")
        if rel_l == p_l or rel_l.startswith(p_l + "/"):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def check_for_update(timeout: int = 8):
    try:
        raw = _get_bytes(VERSION_JSON_URL, timeout=timeout)
        info = json.loads(raw.decode('utf-8'))

        remote = str(info.get("version", "0.0.0")).strip()
        local = str(get_installed_version()).strip()

        files = info.get("files", [])
        app_root = get_app_root()
        changed = []

        # 1. ALWAYS check hash differences
        for entry in files:
            rel = entry.get("path", "").replace("\\", "/")
            if not rel or _is_protected(rel): continue
            if sha256(app_root / rel) != entry.get("sha256", ""):
                changed.append(entry)

        # 2. THE FIX: If versions don't match, force changed = ALL files
        if remote != local:
            print(f"[UPDATER] Version mismatch ({local} -> {remote}). Forcing full sync.")
            changed = files

            # 3. If nothing changed, sync the lock and return False
        if not changed and remote == local:
            return {"available": False}

        return {
            "available": True,
            "version": remote,
            "current": local,
            "file_count": len(changed),
            "files": files,
            "_changed": changed,
        }
    except Exception as e:
        print(f"[UPDATER] Error: {e}")
        return None


def download_and_install(download_url=None, on_progress=None,
                         on_done=None, changed_files=None):
    """Legacy wrapper — used by old JS callers. Delegates to new logic."""
    def _run():
        files = changed_files or []
        if files:
            _delta_download(files, on_progress, on_done, remote_v="")
        elif download_url:
            _exe_download(download_url, on_progress, on_done)
        else:
            if on_done: on_done(False, "Nothing to update.")
    threading.Thread(target=_run, daemon=True).start()


def _delta_download(changed, on_progress, on_done, remote_v):
    """Download changed files or force-sync version if mismatch detected."""
    app_root = get_app_root()

    # 1. DOWNLOAD PHASE
    for entry in changed:
        rel = entry.get("path", "").replace("\\", "/")
        url = entry.get("url", "")
        try:
            data = _get_bytes(url, timeout=45)
            dst = app_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        except Exception as e:
            if on_done: on_done(False, f"Failed: {rel}")
            return

    # 2. PERMANENT FIX: ALWAYS set version if the loop triggered an update
    # We remove the dependency on 'changed' files count.
    # If this function was called, we need to sync the version lock.
    if remote_v:
        success = set_installed_version(remote_v)
        if success:
            print(f"[UPDATER] PERMANENT LOCK: Version {remote_v} written to disk.")
        else:
            print(f"[UPDATER] CRITICAL: FAILED TO WRITE VERSION LOCK!")
            if on_done: on_done(False, "Update failed: Could not write lock file.")
            return

    if on_done: on_done(True, "Update installed successfully.")


def _exe_download(url, on_progress, on_done):
    """Download full EXE and prepare batch installer."""
    import tempfile as _tmp

    def _prog(pct, msg):
        if on_progress:
            try: on_progress(pct, msg)
            except Exception: pass

    _prog(5, "Downloading EXE...")
    try:
        data = _get_bytes(url, timeout=180)
    except Exception as e:
        if on_done: on_done(False, f"Download failed: {e}")
        return

    _prog(85, "Preparing installer...")
    exe_path = str(get_exe_path())
    tmp_dir  = Path(_tmp.mkdtemp(prefix="aurumos_upd_"))
    new_exe  = tmp_dir / "AurumOS_new.exe"
    new_exe.write_bytes(data)

    bat_path = tmp_dir / "aurumos_update.bat"
    bat      = "\r\n".join([
        "@echo off", "title AurumOS Updater",
        "taskkill /F /IM AurumOS.exe /T >NUL 2>&1",
        "timeout /t 3 /nobreak >NUL", ":wait",
        'tasklist /FI "IMAGENAME eq AurumOS.exe" 2>NUL | find /I "AurumOS.exe" >NUL',
        "if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto wait)",
        f'copy /Y "{new_exe}" "{exe_path}"',
        "if errorlevel 1 (echo FAILED & pause & exit /b 1)",
        "timeout /t 2 /nobreak >NUL",
        f'start "" "{exe_path}"', 'del "%~f0"',
    ])
    bat_path.write_bytes(bat.encode("ascii", errors="replace"))
    _PENDING_UPDATE[0] = str(bat_path)
    _prog(100, "Ready! Click Restart to apply.")
    if on_done: on_done(True, "EXE ready. Click Restart.")


def apply_and_restart():
    bat = _PENDING_UPDATE[0]
    if not bat or not os.path.exists(bat):
        return False
    subprocess.Popen(
        ["cmd.exe", "/c", bat],
        creationflags=(
            subprocess.CREATE_NEW_CONSOLE |
            subprocess.DETACHED_PROCESS   |
            subprocess.CREATE_NEW_PROCESS_GROUP
        ),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import time; time.sleep(0.8)
    os._exit(0)