#!/usr/bin/env python3
"""Decrypt an AurumOS recovery backup (.vault) back to an ordinary backup zip.

Provider-only. The .vault file on a client PC is AES-256-GCM encrypted, so it
is unreadable without the backup password. This turns it back into a normal
AurumOS backup zip, which Settings -> Restore then imports like any other.

Usage:
  python scripts/admin/restore_recovery.py <path-to-.vault> [out.zip]

The password comes from AURUM_BACKUP_PW, else the DEFAULT_PASSWORD baked into
core/auto_backup.py. Keep it secret: anyone with it can read the backup.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core import auto_backup


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.zip'
    if not os.path.isfile(src):
        print(f'Not found: {src}')
        return 1
    with open(src, 'rb') as f:
        blob = f.read()
    try:
        plain = auto_backup.decrypt(blob)
    except Exception as e:
        print(f'Could not decrypt (wrong password or not a recovery file): {e}')
        return 1
    with open(out, 'wb') as f:
        f.write(plain)
    print(f'OK -> {out} ({len(plain)} bytes)')
    print('Now open AurumOS -> Settings -> Restore and choose this zip.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
