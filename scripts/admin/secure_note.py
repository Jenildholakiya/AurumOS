#!/usr/bin/env python3
"""Lock or read an encrypted provider note (same AES-256-GCM as the backups).

The note is unreadable without the backup password (AURUM_BACKUP_PW, else the
DEFAULT_PASSWORD in core/auto_backup.py). A client who opens the .vault file
sees only random bytes.

  python scripts/admin/secure_note.py show  <file.vault>
  python scripts/admin/secure_note.py lock  <file.vault> "<text>"
  python scripts/admin/secure_note.py lockfile <file.vault> <source.txt>
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core import auto_backup


def _write(path, text):
    blob = auto_backup._encrypt(text.encode('utf-8'), auto_backup._password())
    with open(path, 'wb') as f:
        f.write(blob)
    print(f'OK -> {path} ({len(blob)} bytes, encrypted)')


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, path = sys.argv[1].lower(), sys.argv[2]
    if cmd == 'show':
        try:
            text = auto_backup.decrypt(open(path, 'rb').read()).decode('utf-8')
        except Exception as e:
            print(f'Could not decrypt (wrong password or not a note): {e}')
            return 1
        print(text)
        return 0
    if cmd == 'lock' and len(sys.argv) >= 4:
        _write(path, sys.argv[3])
        return 0
    if cmd == 'lockfile' and len(sys.argv) >= 4:
        _write(path, open(sys.argv[3], 'r', encoding='utf-8').read())
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
