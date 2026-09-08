# -*- coding: utf-8 -*-
"""
core/io_safety.py

Hardened file and database I/O helpers for AurumOS.

Purpose: eliminate the class of "permanent I/O breakage" bugs where an
interrupted write (app crash, power loss, antivirus scan, or a concurrent
thread) leaves config.json / license keys / flag files truncated or
corrupted -- which then breaks the app permanently on the next launch.

Techniques:
  * Atomic writes -- write to a temp file in the SAME directory, fsync it,
    then os.replace() (atomic rename on both Windows and POSIX). If the
    process dies mid-write, the original file is never touched.
  * Safe reads -- never raise; return a default on any error so a
    corrupt/missing file degrades gracefully instead of crashing boot.
  * Robust SQLite -- WAL + a large busy_timeout + synchronous=NORMAL so
    concurrent terminals do not hit "database is locked".
"""
import os
import json
import tempfile


def atomic_write_bytes(path, data):
    """Atomically write *bytes* to `path`.

    Safe against interruption: the original file is replaced only after the
    new content is fully flushed to disk. Returns True on success, raises on
    genuine failure (bad path / permissions).
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".aurum_tmp_", suffix=".tmp", dir=parent or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def atomic_write_text(path, text, encoding="utf-8"):
    """Atomically write a *str* to `path`."""
    data = text.encode(encoding) if isinstance(text, str) else text
    return atomic_write_bytes(path, data)


def atomic_write_json(path, obj, indent=2, encoding="utf-8"):
    """Atomically write a JSON-serializable object to `path`."""
    return atomic_write_text(path, json.dumps(obj, indent=indent), encoding=encoding)


def safe_read_bytes(path, default=None):
    """Read raw bytes from `path`; return `default` on any error."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return default


def safe_read_text(path, default=None, encoding="utf-8"):
    """Read text from `path`; return `default` on any error."""
    data = safe_read_bytes(path)
    if data is None:
        return default
    try:
        return data.decode(encoding, errors="replace")
    except Exception:
        return default


def safe_read_json(path, default=None):
    """Parse JSON from `path`; return `default` on missing/corrupt file."""
    text = safe_read_text(path)
    if text is None:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def robust_sqlite_connect(db_path, timeout=30.0, busy_timeout=30000,
                          check_same_thread=False, row_factory=None):
    """Open a SQLite connection tuned for concurrent multi-terminal use.

    Applies WAL + a large busy_timeout + synchronous=NORMAL via a single
    connection factory, so simultaneous writes queue instead of raising
    "database is locked". Raises only if the file is genuinely unreachable.
    """
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=timeout,
                           check_same_thread=check_same_thread)
    try:
        if row_factory is not None:
            conn.row_factory = row_factory
        cur = conn.cursor()
        cur.execute("PRAGMA busy_timeout = %d;" % int(busy_timeout))
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.close()
    except Exception:
        # Pragmas are best-effort; the connection is still usable.
        pass
    return conn
