# -*- coding: utf-8 -*-
"""
AurumOS Release Builder
=======================
Run this BEFORE building the EXE to:
  1. Bump version in updater.py (CURRENT_VERSION)
  2. Generate version.json with correct download URL
  3. Git commit + push everything
  4. Then run build.py to create the EXE

Correct workflow:
  1. python build_release.py    <- sets version everywhere + pushes
  2. python build.py            <- builds EXE with new version baked in
  3. Upload dist/AurumOS.exe as GitHub release asset named AurumOS.exe
  4. Done — clients get update automatically
"""

import os, sys, re, json, subprocess
from datetime import date
from pathlib import Path

# ── CONFIGURE HERE BEFORE RUNNING ─────────────────────────────────────────────
NEW_VERSION  = "1.1.2"          # X.Y.Z  — bump this each release
RELEASE_DATE = str(date.today())

CHANGELOG = [
    "Stock Med: full physical audit and period reset",
    "Opening stock: permanently fixed, never subtracts on sale",
    "Scale: stable detection on all COM ports and baud rates",
    "Katti: edit and delete vouchers",
    "MAC lock: DB auto-wipes on new PC",
    "Permanent account lock after 3 failed logins with unlock key",
    "Inventory: correct Net Weight KPI",
    "Full Report: Opening never deducted from closing",
    "Updater: version detection fixed for EXE mode",
]

GITHUB_OWNER  = "Jenildholakiya"
GITHUB_REPO   = "AurumOS"
# Download URL points to the GitHub release asset
DOWNLOAD_URL  = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
    f"/releases/download/v{NEW_VERSION}/AurumOS.exe"
)
# ──────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.resolve()


def bump_updater_version():
    """
    Replace CURRENT_VERSION in updater.py so the new EXE knows its own version.
    This is the CRITICAL step — without it the EXE always thinks it's old.
    """
    path = ROOT / "updater.py"
    if not path.exists():
        print(f"[!] updater.py not found at {path}")
        return False

    text = path.read_text(encoding="utf-8")

    # Replace CURRENT_VERSION = 'X.Y.Z'  (handles single or double quotes)
    new_text, count = re.subn(
        r"^(CURRENT_VERSION\s*=\s*)['\"][\d.]+['\"]",
        f"CURRENT_VERSION = '{NEW_VERSION}'",
        text,
        flags=re.MULTILINE
    )
    if count == 0:
        print("[!] CURRENT_VERSION not found in updater.py")
        return False

    path.write_text(new_text, encoding="utf-8")
    print(f"[OK] updater.py CURRENT_VERSION -> {NEW_VERSION}")
    return True


def write_version_json():
    """Write version.json to repo root — GitHub serves this to clients."""
    data = {
        "version":      NEW_VERSION,
        "release_date": RELEASE_DATE,
        "min_version":  "1.0.0",
        "changelog":    CHANGELOG,
        "download_url": DOWNLOAD_URL,
        "files":        []
    }
    path = ROOT / "version.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] version.json written -> v{NEW_VERSION}")
    return True


def bump_version_info_txt():
    """Update version_info.txt for PyInstaller (Windows EXE properties)."""
    path = ROOT / "version_info.txt"
    if not path.exists():
        print("[SKIP] version_info.txt not found")
        return

    parts = NEW_VERSION.split(".")
    while len(parts) < 4:
        parts.append("0")
    v_tuple = ", ".join(parts[:4])
    v_str   = NEW_VERSION

    text = path.read_text(encoding="utf-8")

    # filevers and prodvers tuples
    text = re.sub(
        r"filevers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)",
        f"filevers=({v_tuple})",
        text
    )
    text = re.sub(
        r"prodvers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)",
        f"prodvers=({v_tuple})",
        text
    )
    # String version values
    text = re.sub(
        r"(u'FileVersion',\s*u')[^']+(')",
        f"\\g<1>{v_str}\\2",
        text
    )
    text = re.sub(
        r"(u'ProductVersion',\s*u')[^']+(')",
        f"\\g<1>{v_str}\\2",
        text
    )

    path.write_text(text, encoding="utf-8")
    print(f"[OK] version_info.txt -> {v_str}")


def git_commit_push():
    """Commit and push version files to GitHub."""
    files = ["version.json", "updater.py", "version_info.txt"]
    existing = [f for f in files if (ROOT / f).exists()]

    try:
        subprocess.run(
            ["git", "add"] + existing,
            cwd=str(ROOT), check=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"release: v{NEW_VERSION}"],
            cwd=str(ROOT), check=True
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(ROOT), check=True
        )
        print(f"[OK] Pushed v{NEW_VERSION} to GitHub")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] Git error: {e}")
        print("    Run manually: git add version.json updater.py && git commit -m 'release: vX.Y.Z' && git push")
        return False


def print_next_steps():
    print()
    print("=" * 60)
    print(f"  v{NEW_VERSION} release prepared")
    print("=" * 60)
    print()
    print("NEXT STEPS:")
    print(f"  1. python build.py          <- build EXE with v{NEW_VERSION} baked in")
    print(f"  2. Go to GitHub -> Releases -> Create release tag v{NEW_VERSION}")
    print(f"  3. Upload dist/AurumOS.exe as release asset")
    print(f"  4. Clients with old version will see update automatically")
    print()
    print(f"  Download URL: {DOWNLOAD_URL}")
    print()


if __name__ == "__main__":
    print(f"\nAurumOS Release Builder -> v{NEW_VERSION}")
    print("-" * 40)

    ok1 = bump_updater_version()
    ok2 = write_version_json()
    bump_version_info_txt()

    if ok1 and ok2:
        ans = input("\nPush to GitHub now? (y/n): ").strip().lower()
        if ans == 'y':
            git_commit_push()
        else:
            print("[SKIP] Git push skipped — run manually when ready")

    print_next_steps()