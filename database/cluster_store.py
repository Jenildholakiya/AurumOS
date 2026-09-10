# -*- coding: utf-8 -*-
"""The owner PC's record of which other PCs may connect to it.

One row per terminal that has ever announced itself. A terminal is identified
by the same hardware fingerprint the security code already uses, so unplugging
and replugging or a new IP address does not turn one PC into two rows.

Lives under database/ so it reaches customers through the auto-updater.
"""

import json
import secrets

# A terminal is one of these. 'pending' means it has announced itself and is
# waiting for the owner to say what it may do; it can still log in, because the
# password check is what proves the person, but it gets the plan's default
# modules until the owner assigns otherwise.
STATUS_PENDING = 'pending'
STATUS_APPROVED = 'approved'
STATUS_BLOCKED = 'blocked'


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_terminals (
            terminal_id   TEXT PRIMARY KEY,
            hostname      TEXT    DEFAULT '',
            last_ip       TEXT    DEFAULT '',
            status        TEXT    DEFAULT 'pending',
            assigned_user TEXT    DEFAULT '',
            bundles       TEXT    DEFAULT '[]',
            secret        TEXT    DEFAULT '',
            seat_rank     INTEGER DEFAULT 0,
            first_seen    TEXT    DEFAULT (datetime('now')),
            last_seen     TEXT    DEFAULT (datetime('now')),
            approved_at   TEXT
        )
    """)


def _row_to_dict(row):
    if row is None:
        return None
    out = dict(row)
    try:
        out['bundles'] = json.loads(out.get('bundles') or '[]')
    except Exception:
        out['bundles'] = []
    # The shared secret never leaves the server.
    out.pop('secret', None)
    return out


def register_terminal(db, terminal_id, hostname='', ip=''):
    """Record a terminal that has just announced itself, or refresh one we
    already know. Returns the terminal row and the secret it should keep.

    A terminal that is already known keeps its status, its assigned user and
    its secret, so re-running setup on a staff PC does not silently reset what
    the owner decided about it.
    """
    terminal_id = str(terminal_id or '').strip()
    if not terminal_id:
        raise ValueError('terminal_id is required')

    with db._get_connection() as conn:
        ensure_schema(conn)
        existing = conn.execute(
            "SELECT * FROM cluster_terminals WHERE terminal_id=?", (terminal_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE cluster_terminals SET hostname=?, last_ip=?, "
                "last_seen=datetime('now') WHERE terminal_id=?",
                (hostname or existing['hostname'], ip or existing['last_ip'], terminal_id)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM cluster_terminals WHERE terminal_id=?", (terminal_id,)
            ).fetchone()
            return _row_to_dict(row), row['secret']

        # Seat rank is the order terminals were first accepted. If the plan is
        # later downgraded, the earliest terminals keep working and the newest
        # ones are the ones that stop, which is the least surprising outcome.
        next_rank = int(conn.execute(
            "SELECT COALESCE(MAX(seat_rank),0)+1 FROM cluster_terminals"
        ).fetchone()[0] or 1)
        secret = secrets.token_hex(32)
        conn.execute(
            "INSERT INTO cluster_terminals "
            "(terminal_id, hostname, last_ip, status, secret, seat_rank) "
            "VALUES (?,?,?,?,?,?)",
            (terminal_id, hostname, ip, STATUS_PENDING, secret, next_rank)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM cluster_terminals WHERE terminal_id=?", (terminal_id,)
        ).fetchone()
        return _row_to_dict(row), secret


def get_terminal(db, terminal_id):
    with db._get_connection() as conn:
        ensure_schema(conn)
        return _row_to_dict(conn.execute(
            "SELECT * FROM cluster_terminals WHERE terminal_id=?",
            (str(terminal_id or '').strip(),)
        ).fetchone())


def get_secret(db, terminal_id):
    with db._get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT secret FROM cluster_terminals WHERE terminal_id=?",
            (str(terminal_id or '').strip(),)
        ).fetchone()
        return row['secret'] if row else ''


def list_terminals(db):
    with db._get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM cluster_terminals ORDER BY seat_rank ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def touch_terminal(db, terminal_id, ip=''):
    with db._get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE cluster_terminals SET last_seen=datetime('now'), "
            "last_ip=COALESCE(NULLIF(?,''), last_ip) WHERE terminal_id=?",
            (ip, str(terminal_id or '').strip())
        )
        conn.commit()


def assign_terminal(db, terminal_id, username='', bundles=None, status=None):
    """The owner decides who a terminal belongs to and what it may open."""
    with db._get_connection() as conn:
        ensure_schema(conn)
        sets, params = [], []
        if username is not None:
            sets.append('assigned_user=?')
            params.append(str(username or '').strip())
        if bundles is not None:
            sets.append('bundles=?')
            params.append(json.dumps(list(bundles or [])))
        if status:
            sets.append('status=?')
            params.append(status)
            if status == STATUS_APPROVED:
                sets.append("approved_at=datetime('now')")
        if not sets:
            return get_terminal(db, terminal_id)
        params.append(str(terminal_id or '').strip())
        conn.execute(
            f"UPDATE cluster_terminals SET {', '.join(sets)} WHERE terminal_id=?",
            params
        )
        conn.commit()
    return get_terminal(db, terminal_id)


def remove_terminal(db, terminal_id):
    """Forget a terminal completely, freeing its seat."""
    with db._get_connection() as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM cluster_terminals WHERE terminal_id=?",
                     (str(terminal_id or '').strip(),))
        conn.commit()
    return True


def seats_in_use(db, exclude_terminal_id=None):
    """Terminals holding a seat. Blocked ones do not, so blocking a PC frees
    the seat for another without having to delete its history."""
    with db._get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) FROM cluster_terminals "
            "WHERE status != ? AND terminal_id != COALESCE(?, '')",
            (STATUS_BLOCKED, exclude_terminal_id)
        ).fetchone()
        return int(row[0] or 0)


def within_seat_limit(db, terminal_id, licensed):
    """Whether this terminal is inside the licence.

    Ranking by seat order rather than counting means a downgrade takes seats
    away from the newest terminals rather than from whichever one happens to
    ask first after the plan changes.
    """
    if licensed <= 0:
        return False
    with db._get_connection() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) FROM cluster_terminals t "
            "WHERE t.status != ? AND t.seat_rank < ("
            "  SELECT seat_rank FROM cluster_terminals WHERE terminal_id=?)",
            (STATUS_BLOCKED, str(terminal_id or '').strip())
        ).fetchone()
        ahead = int(row[0] or 0)
    return ahead < licensed
