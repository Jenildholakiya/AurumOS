"""Silent single-file encrypted recovery backup.

Every few minutes the whole shop — database, config, subscription state and
photos — is snapshotted into ONE file and overwritten in place. The file is
encrypted with AES-256-GCM, so without the backup password it is random bytes:
a copy taken off the PC cannot be read, opened or restored. There is no prompt
and no UI; it runs on a background timer like the update and sync loops.

This is a recovery backup for the software provider. Disclose it in the licence
agreement ("AurumOS keeps an encrypted local recovery backup") — the encryption
and provider-only restore are unaffected by saying so, and a covert copy of a
shop's customers' personal data is a problem the one line removes.

Restore: `scripts/admin/restore_recovery.py` decrypts the file back to an
ordinary AurumOS backup zip, which the app's Settings → Restore then imports.

The password is read from AURUM_BACKUP_PW, falling back to DEFAULT_PASSWORD
below. Change DEFAULT_PASSWORD before you build, or set the env var, so the key
is yours alone. The location is AURUM_BACKUP_DIR, else %LOCALAPPDATA%\\AurumOS\\
recovery (so it is out of the shop's way, not inside the app folder).
"""

import json
import os
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import datetime

# ── Provider secrets — set these before building, or via env ───────────────
DEFAULT_PASSWORD = 'Jenil@8305'
MAGIC = b'AURUMBK1'
INTERVAL_SECONDS = 300          # 5 minutes

_thread = None
_lock = threading.Lock()


def _log(msg):
    try:
        print(f'[AUTOBACKUP] {msg}')
    except Exception:
        pass


def _password():
    pw = os.environ.get('AURUM_BACKUP_PW', '').strip()
    return pw or DEFAULT_PASSWORD


def backup_dir():
    override = os.environ.get('AURUM_BACKUP_DIR', '').strip()
    if override:
        return override
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'AurumOS', 'recovery')


def backup_path():
    return os.path.join(backup_dir(), 'aurum_recovery.vault')


# ── Crypto ─────────────────────────────────────────────────────────────────
def _derive_key(password, salt):
    """32-byte AES key from the password and a per-file salt (scrypt)."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    kdf = Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1)
    return kdf.derive(password.encode('utf-8'))


def _encrypt(plaintext, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    ct = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    return MAGIC + salt + nonce + ct


def decrypt(blob, password=None):
    """Recover the plain backup-zip bytes from an encrypted .vault blob."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob or blob[:8] != MAGIC:
        raise ValueError('Not an AurumOS recovery file.')
    salt, nonce, ct = blob[8:24], blob[24:36], blob[36:]
    key = _derive_key(password or _password(), salt)
    return AESGCM(key).decrypt(nonce, ct, MAGIC)


# ── Snapshot + zip ─────────────────────────────────────────────────────────
def _build_zip_bytes(db_path, base_dir, photo_dir):
    """A consistent full backup as in-memory zip bytes (same shape the app's
    Settings → Restore already understands)."""
    work = backup_dir()
    os.makedirs(work, exist_ok=True)
    snap = os.path.join(work, '.snap.db')
    try:
        src = sqlite3.connect(db_path, timeout=30)
        dst = sqlite3.connect(snap, timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        files = [(snap, 'database/aurum_local.db')]
        cfg = os.path.join(base_dir, 'config.json')
        if os.path.isfile(cfg):
            files.append((cfg, 'config.json'))
        db_dir = os.path.join(base_dir, 'database')
        for name in ('.subscription_cache', '.subscription_state.json', '.sse_cache'):
            p = os.path.join(db_dir, name)
            if os.path.isfile(p):
                files.append((p, 'database/' + name))
        if photo_dir and os.path.isdir(photo_dir):
            for dp, _dirs, fns in os.walk(photo_dir):
                for f in fns:
                    full = os.path.join(dp, f)
                    rel = os.path.relpath(full, base_dir).replace(os.sep, '/')
                    files.append((full, rel))

        manifest = {
            'app': 'AurumOS', 'kind': 'full-backup', 'version': 1,
            'created': datetime.now().isoformat(timespec='seconds'),
            'source': 'auto-recovery',
            'files': [arc for _, arc in files],
        }
        tmp_zip = os.path.join(work, '.snap.zip')
        with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('backup-manifest.json', json.dumps(manifest, indent=2))
            for full, arc in files:
                try:
                    z.write(full, arc)
                except Exception as e:
                    _log(f'skip {arc}: {e}')
        with open(tmp_zip, 'rb') as f:
            return f.read()
    finally:
        for tmp in (snap, os.path.join(work, '.snap.zip')):
            try:
                os.remove(tmp)
            except Exception:
                pass


def run_once(db_path, base_dir, photo_dir):
    """Write one fresh encrypted backup, overwriting the single file atomically."""
    with _lock:
        plain = _build_zip_bytes(db_path, base_dir, photo_dir)
        blob = _encrypt(plain, _password())
        dest = backup_path()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)       # atomic overwrite — no half-written file
        return len(blob)


# ── Background loop ────────────────────────────────────────────────────────
def start(get_db_path, base_dir, get_photo_dir, interval=INTERVAL_SECONDS):
    """Start the 5-minute backup loop once. get_db_path / get_photo_dir are
    callables so the loop always reads the live paths."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def _loop():
        time.sleep(30)             # let startup settle before the first write
        while True:
            try:
                db_path = get_db_path()
                photo_dir = get_photo_dir() if callable(get_photo_dir) else get_photo_dir
                size = run_once(db_path, base_dir, photo_dir)
                _log(f'wrote {size} bytes -> {backup_path()}')
            except Exception as e:
                _log(f'cycle failed: {e}')
            time.sleep(max(60, int(interval)))

    _thread = threading.Thread(target=_loop, daemon=True, name='AurumAutoBackup')
    _thread.start()
    _log(f'loop started ({interval}s) -> {backup_path()}')
