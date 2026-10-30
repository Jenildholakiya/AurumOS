# -*- coding: utf-8 -*-
"""
Stage GitHub release assets from version.json.
Run AFTER gen_version_manifest.py:
    python3 scripts/release/stage_release_assets.py [out_dir]

Copies every file the manifest lists, under the flat name its URL ends in:
  _internal/ui/X -> <out>/X
  a/b.py         -> <out>/a__b.py
Exits non-zero if any listed file is missing: the manifest and this checkout
are the same tree, so a gap here is a bug, and an asset that never uploads
would 404 on every terminal that tries to update.
"""
import json
import os
import shutil
import sys

ROOT = os.path.abspath('.')
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'release_assets')


def main():
    with open(os.path.join(ROOT, 'version.json'), 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    os.makedirs(OUT, exist_ok=True)
    staged, missing = 0, []
    files = manifest.get('files', [])
    for entry in files:
        rel = str(entry.get('path', '')).replace('\\', '/')
        if not rel:
            continue
        if rel.startswith('_internal/ui/'):
            src = os.path.join(ROOT, 'ui', rel.split('/')[-1])
            asset = rel.split('/')[-1]
        else:
            src = os.path.join(ROOT, rel)
            asset = rel.replace('/', '__')
        if os.path.isfile(src) and os.path.getsize(src) > 0:
            shutil.copyfile(src, os.path.join(OUT, asset))
            staged += 1
        else:
            missing.append(rel)
            print(f"MISSING: {rel}")
    print(f"staged: {staged} of {len(files)}")
    if missing:
        sys.exit(f"ERROR: {len(missing)} listed file(s) not found -- refusing to publish a manifest that would 404")


if __name__ == '__main__':
    main()
