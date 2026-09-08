# -*- coding: utf-8 -*-
"""
Stage GitHub release assets from version.json manifest.
Run AFTER gen_version_manifest.py:
    python3 stage_release_assets.py [out_dir]

Reads version.json 'files' entries and copies each source file:
  _internal/ui/X -> <out>/X            (flat UI name)
  a/b.py        -> <out>/a__b.py       (flat backend name)
Prints MISSING lines for absent sources (non-fatal).
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
    staged = 0
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
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(OUT, asset))
            staged += 1
        else:
            print(f"MISSING: {rel}")
    print(f"staged: {staged} of {len(files)}")


if __name__ == '__main__':
    main()
