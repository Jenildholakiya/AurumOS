# -*- coding: utf-8 -*-
"""
AurumOS Updater
===============
CURRENT_VERSION is bumped automatically by build_release.py before each build.
The EXE freezes this value — so the EXE always knows its own version.

update flow:
  1. check_for_update()  -- fetch version.json from GitHub, compare
  2. download_and_install(url)  -- download new EXE, write bat, relaunch
"""

import os, sys, json, ssl, threading, tempfile, subprocess
import urllib.request, urllib.error
from pathlib import Path

# ── VERSION — bumped by build_release.py before every build ──────────────────
CURRENT_VERSION = '1.1.2'   # DO NOT change manually — use build_release.py

# ── GITHUB ────────────────────────────────────────────────────────────────────
GITHUB_OWNER     = "Jenildholakiya"
GITHUB_REPO      = "AurumOS"
GITHUB_BRANCH    = "main"
VERSION_JSON_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}"
    f"/{GITHUB_REPO}/{GITHUB_BRANCH}/version.json"
)

# ─────────────────────────────────────────────────────────────────────────────


def get_app_dir() -> Path:
    """
    Returns the folder where AurumOS.exe lives.

    EXE mode: sys.executable = E:/AurumOs_Client/dist/AurumOS.exe
              -> returns E:/AurumOs_Client/dist/

    Dev mode: returns cwd
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent.resolve()
    return Path(os.path.abspath('.')).resolve()


def get_exe_path() -> Path:
    """Returns the actual running EXE path."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve()
    # Dev mode — no real exe
    return Path(sys.executable).resolve()


def get_current_version() -> str:
    """
    Returns the version this EXE was built with.
    Always reads CURRENT_VERSION constant — hardcoded at build time.
    Never reads from files (files are unreliable in EXE mode).
    """
    return str(CURRENT_VERSION).strip()


def _parse_version(v: str):
    """Parse '1.0.9' -> (1, 0, 9) for comparison."""
    try:
        parts = str(v).strip().lstrip('v').split('.')
        return tuple(int(x) for x in parts[:3])
    except Exception:
        return (0, 0, 0)


def is_newer(remote: str, local: str) -> bool:
    """Returns True if remote version is newer than local."""
    return _parse_version(remote) > _parse_version(local)


def _fetch_url(url: str, timeout: int = 10) -> bytes:
    """
    Fetch URL bytes. Works in EXE mode:
    - Disables SSL verification (PyInstaller doesn't bundle certs)
    - Adds User-Agent (GitHub rejects default urllib agent)
    - Loads GitHub token from aurum_config.json if available
    """
    # Load token if available
    token = ""
    try:
        cfg = get_app_dir() / "aurum_config.json"
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            token = data.get("github_token", "")
    except Exception:
        pass

    # Build request
    req = urllib.request.Request(url)
    req.add_header("User-Agent", f"AurumOS/{CURRENT_VERSION}")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"token {token}")

    # SSL context — disable verify (certs not bundled in EXE)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
    return resp.read()


def check_for_update(timeout: int = 10) -> dict:
    """
    Fetch version.json from GitHub and compare with CURRENT_VERSION.

    Returns dict:
      available     : bool
      version       : str  (remote version)
      current       : str  (this EXE's version)
      changelog     : list
      release_date  : str
      download_url  : str
    OR None on network error.
    """
    local = get_current_version()
    print(f"[UPDATER] Local version: {local}")
    print(f"[UPDATER] Fetching: {VERSION_JSON_URL}")

    try:
        raw  = _fetch_url(VERSION_JSON_URL, timeout=timeout)
        info = json.loads(raw.decode("utf-8"))

        remote  = str(info.get("version", "0.0.0")).strip()
        update  = is_newer(remote, local)

        print(f"[UPDATER] Remote version: {remote}  update_available={update}")

        return {
            "available":    update,
            "version":      remote,
            "current":      local,
            "changelog":    info.get("changelog", []),
            "release_date": info.get("release_date", ""),
            "download_url": info.get("download_url", ""),
            "min_version":  info.get("min_version", "1.0.0"),
        }

    except Exception as e:
        err = str(e).encode("ascii", errors="replace").decode("ascii")
        print(f"[UPDATER] check_for_update error: {err}")
        return None


def download_and_install(download_url: str,
                         on_progress=None,
                         on_done=None):
    """
    Download new EXE to temp folder.
    Write a .bat file that:
      1. Waits for AurumOS.exe to close
      2. Copies new EXE over old EXE
      3. Relaunches AurumOS.exe
    Then launches the bat and exits this process.

    on_progress(pct, message) — called with 0-100
    on_done(success, message) — called when done or failed
    """

    def _run():
        try:
            exe_path = get_exe_path()
            url      = (download_url or "").strip()

            if not url:
                if on_done: on_done(False, "No download URL in version.json")
                return

            # ── Download ───────────────────────────────────────────────
            if on_progress: on_progress(5, "Connecting...")
            print(f"[UPDATER] Downloading: {url}")

            try:
                data = _fetch_url(url, timeout=180)
                print(f"[UPDATER] Downloaded {len(data):,} bytes")
            except Exception as de:
                msg = str(de).encode("ascii", errors="replace").decode("ascii")
                print(f"[UPDATER] Download failed: {msg}")
                if on_done: on_done(False, f"Download failed: {msg}")
                return

            if on_progress: on_progress(80, "Preparing installer...")

            # ── Save to temp ───────────────────────────────────────────
            tmp_dir = Path(tempfile.mkdtemp(prefix="aurumos_upd_"))
            new_exe = tmp_dir / "AurumOS_new.exe"
            new_exe.write_bytes(data)
            print(f"[UPDATER] New EXE saved: {new_exe}")

            # ── Write bat ──────────────────────────────────────────────
            bat_path = tmp_dir / "aurumos_update.bat"
            bat_lines = [
                "@echo off",
                "title AurumOS Updater",
                "echo Waiting for AurumOS to close...",
                "taskkill /F /IM AurumOS.exe /T >NUL 2>&1",
                "timeout /t 3 /nobreak >NUL",
                ":wait",
                'tasklist /FI "IMAGENAME eq AurumOS.exe" 2>NUL | find /I "AurumOS.exe" >NUL',
                "if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto wait)",
                "echo Installing update...",
                f'copy /Y "{new_exe}" "{exe_path}"',
                "if errorlevel 1 (echo FAILED to replace EXE & pause & exit /b 1)",
                "echo Update installed successfully!",
                "timeout /t 2 /nobreak >NUL",
                f'start "" "{exe_path}"',
                'del "%~f0"',
            ]
            bat_path.write_text("\r\n".join(bat_lines), encoding="ascii")
            print(f"[UPDATER] Bat written: {bat_path}")

            if on_progress: on_progress(95, "Launching installer...")

            # ── Launch bat and exit ────────────────────────────────────
            subprocess.Popen(
                ['cmd.exe', '/c', str(bat_path)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                close_fds=True
            )
            print("[UPDATER] Updater launched — exiting app")
            if on_done: on_done(True, "Update started. App will restart.")

            # Give on_done callback time to run before exit
            threading.Timer(1.5, lambda: os._exit(0)).start()

        except Exception as e:
            msg = str(e).encode("ascii", errors="replace").decode("ascii")
            print(f"[UPDATER] download_and_install error: {msg}")
            if on_done: on_done(False, msg)

    threading.Thread(target=_run, daemon=True).start()