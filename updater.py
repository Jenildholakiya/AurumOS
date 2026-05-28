"""
AurumOS Auto-Updater Engine
────────────────────────────
Handles: version check, download, safe file installation.
Database folder is NEVER touched.
"""

import os
import sys
import json
import shutil
import zipfile
import threading
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

# ─────────────────────────────────────────────────
# CONFIGURE THESE FOR YOUR PROJECT
# ─────────────────────────────────────────────────
CURRENT_VERSION   = "1.0.0"
VERSION_JSON_URL  = "https://raw.githubusercontent.com/Jenildholakiya/AurumOS/main/version.json"

# These paths are NEVER overwritten during update
PROTECTED = {
    "database",
    "logs",
    "backups",
    ".env",
    "config.json",
    "updater.py",        # don't overwrite updater itself mid-run
    "build_release.py",
}
# ─────────────────────────────────────────────────


def get_app_root() -> Path:
    """Returns the AurumOS root directory — works for .py and compiled .exe"""
    if hasattr(sys, '_MEIPASS'):
        return Path(sys.executable).parent
    return Path(os.path.abspath("."))


# ── VERSION COMPARISON ────────────────────────────
def _ver_tuple(v: str):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except:
        return (0, 0, 0)

def is_newer(remote: str, local: str) -> bool:
    return _ver_tuple(remote) > _ver_tuple(local)


# ── CHECK FOR UPDATE ──────────────────────────────
def check_for_update(timeout: int = 6):
    """
    Returns dict or None (on network failure).
    {
        "available": bool,
        "version":   "1.2.0",
        "changelog": [...],
        "download_url": "https://...",
        "release_date": "2026-06-01",
        "size_mb": 2.4
    }
    """
    try:
        req  = urllib.request.Request(
            VERSION_JSON_URL,
            headers={"User-Agent": f"AurumOS/{CURRENT_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())

        latest    = data.get("version", "0.0.0")
        available = is_newer(latest, CURRENT_VERSION)

        print(f"[UPDATER] Local={CURRENT_VERSION} Remote={latest} Available={available}")
        return {
            "available":    available,
            "version":      latest,
            "current":      CURRENT_VERSION,
            "changelog":    data.get("changelog", []),
            "download_url": data.get("download_url", ""),
            "release_date": data.get("release_date", ""),
            "size_mb":      data.get("size_mb", ""),
        }

    except Exception as e:
        print(f"[UPDATER] Version check failed: {e}")
        return None


# ── DOWNLOAD + INSTALL ────────────────────────────
def download_and_install(download_url: str,
                          on_progress=None,
                          on_done=None):
    """
    Runs in background thread.
    on_progress(pct: int, msg: str)
    on_done(success: bool, message: str)
    """
    def _run():
        tmp = Path(tempfile.mkdtemp(prefix="aurumos_upd_"))
        try:
            zip_path = tmp / "update.zip"

            # ── 1. Download ───────────────────────────
            _prog(on_progress, 0, "Connecting to update server…")
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": f"AurumOS/{CURRENT_VERSION}"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                total     = int(resp.headers.get("Content-Length", 0))
                received  = 0
                chunk     = 8192
                with open(zip_path, "wb") as f:
                    while True:
                        buf = resp.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        received += len(buf)
                        if total:
                            pct = int(received / total * 65)
                            _prog(on_progress, pct,
                                  f"Downloading… {received//1024} KB / {total//1024} KB")

            _prog(on_progress, 68, "Download complete. Verifying…")

            # ── 2. Extract ────────────────────────────
            extract_dir = tmp / "extracted"
            extract_dir.mkdir()
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            _prog(on_progress, 75, "Extracted. Installing files…")

            # Unwrap single top-level folder if present
            contents = list(extract_dir.iterdir())
            src_root = contents[0] if (len(contents) == 1 and contents[0].is_dir()) \
                       else extract_dir

            # ── 3. Install files ──────────────────────
            app_root = get_app_root()
            all_files = [f for f in src_root.rglob("*") if f.is_file()]
            total_f   = len(all_files)

            for idx, src in enumerate(all_files):
                rel     = src.relative_to(src_root)
                rel_str = str(rel).replace("\\", "/")

                if _is_protected(rel_str):
                    print(f"[UPDATER] Protected — skip: {rel_str}")
                    continue

                dst = app_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"[UPDATER] Installed: {rel_str}")

                pct = 75 + int((idx + 1) / total_f * 22)
                _prog(on_progress, pct, f"Installing: {rel_str}")

            _prog(on_progress, 98, "Cleaning up…")
            shutil.rmtree(tmp, ignore_errors=True)

            _prog(on_progress, 100, "Update installed! Restart to apply changes.")
            if on_done:
                on_done(True, "Update installed successfully. Please restart AurumOS.")

        except Exception as e:
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"[UPDATER] Install error: {e}")
            if on_done:
                on_done(False, f"Update failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def _prog(cb, pct, msg):
    if cb:
        try:
            cb(pct, msg)
        except:
            pass


def _is_protected(rel: str) -> bool:
    rel_l = rel.lower().replace("\\", "/")
    for p in PROTECTED:
        p_l = p.lower().replace("\\", "/")
        if rel_l == p_l or rel_l.startswith(p_l + "/"):
            return True
    return False