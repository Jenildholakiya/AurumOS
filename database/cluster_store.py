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


# ── Relaying data between PCs ────────────────────────────────────────────
# The shop server is a hub, not a peer. A staff PC sends only its own rows and
# receives everyone else's, so a bill taken at one counter reaches the owner
# and every other counter without any PC having to talk to any other directly.
#
# The merge itself is db_manager's apply_incoming_rows, which already handles
# duplicate voucher numbers, line items following a renumbered parent, and
# replayed payloads. Nothing here re-implements any of that.

# Outbound progress is kept in sync_state under a reserved peer id. That table
# already means "how far have I pulled from X"; this is the mirror of it, and
# using a reserved id avoids a schema change to record it.
OUTBOUND_PEER = '__outbound__'


def cursors_by_origin(db):
    """How far this PC has pulled from every other, as {origin: {table: version}}.

    Sent with a pull so the other end returns only what is new. Empty on a
    fresh PC, which is what makes a first sync fetch the shop's history.
    """
    out = {}
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT peer_device_id, table_name, last_version FROM sync_state "
            "WHERE peer_device_id != ?", (OUTBOUND_PEER,)
        ).fetchall()
    for r in rows:
        out.setdefault(r['peer_device_id'], {})[r['table_name']] = int(r['last_version'] or 0)
    return out


def outbound_cursor(db):
    """How much of this PC's own data the other end has already acknowledged."""
    out = {}
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT table_name, last_version FROM sync_state WHERE peer_device_id=?",
            (OUTBOUND_PEER,)
        ).fetchall()
    return {r['table_name']: int(r['last_version'] or 0) for r in rows}


def record_outbound(db, changes):
    """Remember what was just accepted, so the next push sends only the delta.

    Recorded after the other end confirms, never before: a push that fails
    halfway is retried in full rather than silently skipped.
    """
    if not changes:
        return
    with db._get_connection() as conn:
        for table, rows in changes.items():
            if not rows:
                continue
            highest = max(int(r.get('sync_version') or 0) for r in rows)
            conn.execute(
                "INSERT OR REPLACE INTO sync_state "
                "(peer_device_id, table_name, last_version, last_synced_at) "
                "VALUES (?,?,?,datetime('now'))",
                (OUTBOUND_PEER, table, highest))
        conn.commit()


def changes_from_others(db, asking_device_id, since_by_origin, limit=400):
    """Everything this PC holds that did not come from the PC asking.

    Grouped by the device each row originated on, because the receiver tracks
    its progress per origin. Without that grouping a terminal could not tell
    the server's own bills from another counter's, and would lose its place
    every time a third PC joined.
    """
    asking = str(asking_device_id or '')
    relayed = {}
    with db._get_connection() as conn:
        for table in db.SYNC_TABLES:
            try:
                origins = [
                    row[0] for row in conn.execute(
                        f"SELECT DISTINCT device_id FROM {table} "
                        f"WHERE device_id IS NOT NULL AND device_id != '' AND device_id != ?",
                        (asking,)).fetchall()
                ]
            except Exception:
                continue                      # table predates the sync columns
            for origin in origins:
                seen = int((since_by_origin.get(origin) or {}).get(table, 0) or 0)
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE device_id=? AND sync_version>? "
                    f"ORDER BY sync_version ASC LIMIT ?",
                    (origin, seen, limit)).fetchall()
                if rows:
                    relayed.setdefault(origin, {})[table] = [dict(r) for r in rows]
    return relayed

