"""
AurumOS Release Builder
────────────────────────
Run on YOUR PC to package and publish an update.

Usage:
    python build_release.py 1.2.1 "Added discount feature" "Fixed today filter"

What it does:
    1. Bumps CURRENT_VERSION in updater.py
    2. Creates aurumos-1.2.1.zip  (excludes database/, .git, etc.)
    3. Writes version.json
    4. Prints exact upload instructions
"""

import os, sys, re, json, zipfile, fnmatch
from pathlib import Path
from datetime import datetime

# ── CONFIGURE ───────────────────────────────────────────────────
GITHUB_USER    = "Jenildholakiya"
GITHUB_REPO    = "AurumOS"
# Raw URL where version.json lives (GitHub raw)
VERSION_URL    = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/version.json"
# Download URL pattern for GitHub releases
DOWNLOAD_BASE  = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/download"

# Files/folders NEVER included in the ZIP
EXCLUDE = {
    "database", "__pycache__", ".git", ".venv", "venv",
    "build", "dist", "*.pyc", "*.pyo", "*.db", "*.sqlite",
    "build_release.py", "temp", "logs", "backups",
    "*.zip", ".env", ".DS_Store", "Thumbs.db",
}
# ────────────────────────────────────────────────────────────────


def should_exclude(path: Path, root: Path) -> bool:
    rel   = path.relative_to(root)
    parts = rel.parts
    for part in parts:
        if part in EXCLUDE:
            return True
        if part.startswith("."):
            return True
    for pattern in EXCLUDE:
        if "*" in pattern and fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def bump_version(file: Path, new_ver: str):
    text    = file.read_text(encoding="utf-8")
    updated = re.sub(
        r'CURRENT_VERSION\s*=\s*["\'][^"\']*["\']',
        f'CURRENT_VERSION   = "{new_ver}"',
        text
    )
    file.write_text(updated, encoding="utf-8")
    print(f"  ✔  Version bumped → {new_ver} in {file.name}")


def make_zip(root: Path, version: str):
    zip_name = f"aurumos-{version}.zip"
    zip_path = root / zip_name
    count    = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path == zip_path:
                continue
            if should_exclude(path, root):
                continue
            if path.is_file():
                arc = f"AurumOS/{path.relative_to(root)}"
                zf.write(path, arc)
                print(f"  + {arc}")
                count += 1

    size_mb = round(zip_path.stat().st_size / 1024 / 1024, 2)
    print(f"\n  📦  {zip_name}  ({size_mb} MB, {count} files)")
    return zip_path, size_mb, zip_name


def write_version_json(root: Path, version: str,
                        changelog: list, zip_name: str, size_mb: float):
    data = {
        "version":      version,
        "release_date": datetime.now().strftime("%Y-%m-%d"),
        "size_mb":      size_mb,
        "download_url": f"{DOWNLOAD_BASE}/v{version}/{zip_name}",
        "changelog":    changelog,
        "min_version":  "1.0.0",
        "notes":        "Restart AurumOS after updating."
    }
    vpath = root / "version.json"
    vpath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✔  version.json written")
    return data


def main():
    if len(sys.argv) < 2:
        print("Usage: python build_release.py <version> [changelog...]")
        print("E.g.:  python build_release.py 1.2.1 'Fixed billing' 'New dashboard'")
        sys.exit(1)

    version   = sys.argv[1].strip()
    changelog = list(sys.argv[2:]) if len(sys.argv) > 2 else ["Improvements and bug fixes."]
    root      = Path(os.path.abspath("."))

    print(f"\n{'═'*58}")
    print(f"  AurumOS Release Builder  ·  v{version}")
    print(f"{'═'*58}\n")

    # 1. Bump version
    updater = root / "updater.py"
    if updater.exists():
        bump_version(updater, version)
    else:
        print("  ⚠  updater.py not found — skipping version bump")

    # 2. Build ZIP
    print(f"\n  Building ZIP…\n")
    zip_path, size_mb, zip_name = make_zip(root, version)

    # 3. Write version.json
    write_version_json(root, version, changelog, zip_name, size_mb)

    # 4. Print instructions
    print(f"\n{'═'*58}")
    print(f"  ✅  Release v{version} ready!")
    print(f"{'═'*58}\n")
    print(f"  Changelog:")
    for item in changelog:
        print(f"    • {item}")
    print(f"""
  UPLOAD STEPS  (takes ~2 minutes)
  ─────────────────────────────────────────────────────
  1. Open:  https://github.com/{GITHUB_USER}/{GITHUB_REPO}

  2. Click:  Releases → Draft a new release

  3. Tag:    v{version}
     Title:  AurumOS v{version}

  4. Upload these 2 files:
       📦  {zip_name}
       📄  version.json

  5. Click "Publish release"

  ✅  Done! All clients see the update notification
      next time they open AurumOS.
  ─────────────────────────────────────────────────────
""")


if __name__ == "__main__":
    main()