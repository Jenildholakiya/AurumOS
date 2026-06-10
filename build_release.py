# -*- coding: utf-8 -*-
"""
AurumOS Release Builder
========================
Run on DEV machine before every push.

STEPS EVERY RELEASE:
  1. Edit your files
  2. Bump NEW_VERSION below  (e.g. 1.0.7 → 1.0.8)
  3. Bump CURRENT_VERSION in updater.py to same value
  4. python build_release.py
  5. git add -A && git commit -m "v1.0.8" && git push
  Done. Clients auto-update next time they open AurumOS.
"""

import os, json, hashlib, sys
from pathlib import Path
from datetime import date

# ══════════════════════════════════════════════════════════════════════════════
#  !! EDIT THESE BEFORE EVERY RELEASE !!
# ══════════════════════════════════════════════════════════════════════════════
NEW_VERSION  = "1.1.1"
RELEASE_DATE = str(date.today())
CHANGELOG = [
    "fix: update always shows correctly on client PCs",
    "fix: scale weight reading fixed",
    "fix: tag right wing QR clipping",
]
GITHUB_OWNER  = "Jenildholakiya"
GITHUB_REPO   = "AurumOS"
GITHUB_BRANCH = "main"
# ══════════════════════════════════════════════════════════════════════════════

EXCLUDE_DIRS = {
    "database", "logs", "backups",
    ".git", ".idea", ".vscode", ".venv", "venv",
    "__pycache__", "node_modules",
    "build", "dist", "temp", "ui/temp",
    ".pyarmor_output", "pyarmor_runtime_000000",
}
EXCLUDE_FILES = {
    ".env", "config.json", "aurum_config.json",
    "build_release.py", "build.py", "AurumOS.spec",
    "updater.py",          # NEVER include — contains baked CURRENT_VERSION
    "version.json",        # never update itself
    "version.lock",        # client-specific
    "debug_db.py", "tag_layout_test.py",
    "requirements.txt",
}
EXCLUDE_EXTS = {".pyc", ".pyo", ".db", ".log", ".bak", ".tmp", ".spec", ".sqlite"}
BINARY_EXTS  = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".ttf", ".otf", ".woff", ".woff2",
    ".pdf", ".zip", ".gz", ".exe", ".dll", ".pyd", ".so",
}

RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
ROOT     = Path(os.path.abspath("."))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() not in BINARY_EXTS:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def should_exclude(rel: str) -> bool:
    rel_fwd = rel.replace("\\", "/")
    name    = Path(rel_fwd).name
    ext     = Path(rel_fwd).suffix.lower()

    if ext     in EXCLUDE_EXTS:  return True
    if name    in EXCLUDE_FILES: return True
    if name.startswith("."):     return True
    if "ui/temp" in rel_fwd:     return True

    for exc in EXCLUDE_DIRS:
        exc_fwd = exc.replace("\\", "/")
        if rel_fwd == exc_fwd or rel_fwd.startswith(exc_fwd + "/"):
            return True
    for part in Path(rel_fwd).parts[:-1]:
        if part in EXCLUDE_DIRS:
            return True
    return False


def check_version_sync():
    upd = ROOT / "updater.py"
    if not upd.exists():
        return
    for line in upd.read_text(encoding="utf-8").splitlines():
        if "CURRENT_VERSION" in line and "=" in line and not line.strip().startswith("#"):
            try:
                val = line.split("=")[1].strip().strip("\"'")
                if val != NEW_VERSION:
                    print(f"\n  !! MISMATCH: updater.py CURRENT_VERSION={val!r}"
                          f"  but NEW_VERSION={NEW_VERSION!r}")
                    print(f"  Edit updater.py → CURRENT_VERSION = '{NEW_VERSION}'\n")
                    if input("  Continue anyway? (y/n): ").strip().lower() != 'y':
                        sys.exit(1)
                else:
                    print(f"  OK version sync: {val!r}")
            except Exception:
                pass
            break


def main():
    print(f"\n{'='*56}")
    print(f"  AurumOS Release Builder — v{NEW_VERSION}")
    print(f"{'='*56}\n")

    check_version_sync()

    prev_hashes = {}
    vj = ROOT / "version.json"
    if vj.exists():
        try:
            old = json.loads(vj.read_text(encoding="utf-8"))
            prev_hashes = {e["path"]: e["sha256"] for e in old.get("files", [])}
            print(f"  Previous: v{old.get('version','?')}"
                  f" ({len(prev_hashes)} files)\n")
        except Exception:
            pass

    files = []
    total_bytes = 0
    new_count = changed_count = 0

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if should_exclude(rel):
            continue

        h    = sha256_file(path)
        size = path.stat().st_size
        total_bytes += size

        files.append({
            "path":       rel,
            "sha256":     h,
            "url":        f"{RAW_BASE}/{rel}",
            "size_bytes": size,
        })

        if rel not in prev_hashes:
            print(f"  [NEW]     {rel}")
            new_count += 1
        elif prev_hashes[rel] != h:
            print(f"  [CHANGED] {rel}")
            changed_count += 1

    print(f"\n  {'─'*50}")
    print(f"  Files   : {len(files)} total")
    print(f"  New     : {new_count}")
    print(f"  Changed : {changed_count}")
    print(f"  Size    : {total_bytes // 1024:,} KB")
    print(f"  {'─'*50}\n")

    out = {
        "version":      NEW_VERSION,
        "release_date": RELEASE_DATE,
        "changelog":    CHANGELOG,
        "file_count":   len(files),
        "size_mb":      round(total_bytes / 1024 / 1024, 2),
        "download_url": "",   # leave empty for delta update
        "files":        files,
    }

    vj.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  version.json written ✓")
    print(f"\n  Next:")
    print(f"    git add -A")
    print(f"    git commit -m \"v{NEW_VERSION}\"")
    print(f"    git push\n")


if __name__ == "__main__":
    main()