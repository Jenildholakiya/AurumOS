# -*- coding: utf-8 -*-
"""The hallmark centre's side of a lot, served over the shop network.

The shop packs a lot and sends the pieces physically. From this module the
centre gets the same lot on a web page instead of on paper:

  * a share URL per lot: which pieces, each with its name, weight, purity
    and grade, so the centre knows what arrived before opening the box;
  * a Kanban board: lots as cards in Sent -> Received -> Processing -> Ready,
    dragged with a mouse or a finger;
  * a lot popup: every piece with Pass / Fail buttons and a HUID field;
  * a gate: a lot cannot move to Ready while any piece is still unmarked.

Everything here is plain functions over the shop database handle, so the
cluster HTTP server stays thin and the rules stay testable without it.

Only lots that are still away are shared. Booked-in and cancelled lots are
the shop's own register, not the centre's work queue.
"""

# The centre's pipeline, in order. 'sent' is where every new lot starts;
# 'ready' means every piece is marked and the shop may come and collect.
STAGES = ('sent', 'received', 'processing', 'ready')

STAGE_LABELS = {
    'sent':       'Sent',
    'received':   'Received',
    'processing': 'In process',
    'ready':      'Ready to collect',
}

RESULTS = ('pass', 'fail', 'pending')

# How long one portal link stays open: long enough to cover a slow assay
# turnaround several times over, short enough that a forwarded email stops
# working on its own. Issuing a new link still revokes the old one, and a
# centre that needs longer just asks the shop for a fresh link.
PORTAL_TOKEN_TTL = 30 * 24 * 3600


def _now():
    import time as _t
    return _t.time()


def _log(msg):
    try:
        import logging
        logging.getLogger('aurumos.hmshare').info(f'[HMSHARE] {msg}')
    except Exception:
        pass


def ensure_stage_column(db):
    """Migrations for the centre portal. Safe to call on every request.

    * hallmark_lots.centre_stage — the Kanban column.
    * hallmark_centres.email — where portal links go.
    * hm_portal_tokens — one row per issued portal link.
    * hm_sessions — centre logins, so restarting the shop program does not
      throw every centre out (each still expires on its own clock).
    """
    try:
        with db._get_connection() as conn:
            cols = [r[1] for r in conn.execute(
                'PRAGMA table_info(hallmark_lots)').fetchall()]
            if 'centre_stage' not in cols:
                conn.execute(
                    "ALTER TABLE hallmark_lots ADD COLUMN centre_stage "
                    "TEXT DEFAULT 'sent'")
                conn.execute(
                    "UPDATE hallmark_lots SET centre_stage='sent' "
                    "WHERE centre_stage IS NULL OR centre_stage=''")
            ccols = [r[1] for r in conn.execute(
                'PRAGMA table_info(hallmark_centres)').fetchall()]
            if 'email' not in ccols:
                conn.execute(
                    "ALTER TABLE hallmark_centres ADD COLUMN email TEXT "
                    "DEFAULT ''")
            if 'portal_pin_hash' not in ccols:
                conn.execute(
                    "ALTER TABLE hallmark_centres ADD COLUMN portal_pin_hash "
                    "TEXT DEFAULT ''")
            if 'portal_pin_salt' not in ccols:
                conn.execute(
                    "ALTER TABLE hallmark_centres ADD COLUMN portal_pin_salt "
                    "TEXT DEFAULT ''")
            if 'public_uid' not in ccols:
                conn.execute(
                    "ALTER TABLE hallmark_centres ADD COLUMN public_uid "
                    "TEXT DEFAULT ''")
            for _r in conn.execute(
                    "SELECT id FROM hallmark_centres WHERE "
                    "COALESCE(public_uid,'')=''").fetchall():
                _uid = ''
                for _try in range(5):
                    import secrets as _se
                    _cand = _se.token_urlsafe(9)
                    _dup = conn.execute(
                        "SELECT 1 FROM hallmark_centres WHERE public_uid=?",
                        (_cand,)).fetchone()
                    if not _dup:
                        _uid = _cand
                        break
                if _uid:
                    conn.execute(
                        "UPDATE hallmark_centres SET public_uid=? WHERE id=?",
                        (_uid, _r['id']))
            # Saved logins the owner can re-share: one row per login ever
            # set, newest last. Stored retrievable (not hashed) on purpose —
            # a hash cannot be read back out to tell the centre. This file
            # never leaves the shop PC; the portal only ever sees hashes.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hm_centre_pin_history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "centre_id INTEGER NOT NULL, pin TEXT NOT NULL, "
                "created_at TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hm_portal_tokens ("
                "token TEXT PRIMARY KEY, centre_id INTEGER NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                "revoked INTEGER DEFAULT 0)")
            tcols = [r[1] for r in conn.execute(
                'PRAGMA table_info(hm_portal_tokens)').fetchall()]
            if 'expires_at' not in tcols:
                conn.execute("ALTER TABLE hm_portal_tokens ADD COLUMN "
                             "expires_at REAL DEFAULT 0")
            # Old links predate expiry: give each a full lifetime from now
            # rather than killing working centres on update day.
            conn.execute("UPDATE hm_portal_tokens SET expires_at=? "
                         "WHERE COALESCE(expires_at,0) <= 0",
                         (_now() + PORTAL_TOKEN_TTL,))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hm_sessions ("
                "sid TEXT PRIMARY KEY, centre_id INTEGER NOT NULL, "
                "expires_at REAL NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
        return True
    except Exception as e:
        _log(f'portal migration failed: {e}')
        return False


def _grade_of(item):
    try:
        from core import hallmark as _hm
        return _hm.grade_mark(item.get('karat')) or ''
    except Exception:
        return ''


def _normalise_huid(raw):
    try:
        from core import hallmark as _hm
        return _hm.normalise_huid(raw)
    except Exception:
        return str(raw or '').strip().upper()


def _open_lot_rows(db):
    """Lots still away, oldest first, each with its pieces attached."""
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            lots = [dict(r) for r in conn.execute(
                "SELECT * FROM hallmark_lots "
                "WHERE COALESCE(status,'') NOT IN ('cancelled') "
                "  AND COALESCE(received_date,'') = '' "
                "ORDER BY sent_date, id").fetchall()]
            if not lots:
                return []
            ids = tuple(l['id'] for l in lots)
            marks = '(' + ','.join('?' * len(ids)) + ')'
            by_lot = {}
            for r in conn.execute(
                    "SELECT * FROM hallmark_lot_items WHERE lot_id IN "
                    + marks + " ORDER BY id", ids).fetchall():
                by_lot.setdefault(r['lot_id'], []).append(dict(r))
            for lot in lots:
                lot['items'] = by_lot.get(lot['id'], [])
                if not lot.get('centre_stage'):
                    lot['centre_stage'] = 'sent'
        return lots
    except Exception as e:
        _log(f'open lots: {e}')
        return []


def _summarise_items(items):
    pieces = len(items or [])
    pending = sum(1 for i in (items or [])
                  if str(i.get('result') or 'pending').lower() == 'pending')
    passed = sum(1 for i in (items or [])
                 if str(i.get('result') or '').lower() == 'pass')
    failed = sum(1 for i in (items or [])
                 if str(i.get('result') or '').lower() == 'fail')
    gross = round(sum(float(i.get('gr_wt') or 0) for i in (items or [])), 3)
    return {'pieces': pieces, 'pending': pending, 'passed': passed,
            'failed': failed, 'gross_wt': gross,
            'ready': pieces > 0 and pending == 0}


def _piece_public(item):
    """What the centre may see about one piece. No cost figures."""
    d = dict(item or {})
    return {
        'tag_id':  d.get('tag_id') or '',
        'it_name': d.get('it_name') or '',
        'gr_wt':   float(d.get('gr_wt') or 0),
        'nt_wt':   float(d.get('nt_wt') or 0),
        'karat':   d.get('karat') or '',
        'grade':   _grade_of(d),
        'result':  str(d.get('result') or 'pending').lower(),
        'huid':    d.get('huid') or '',
    }


def _lot_public(db, lot):
    """What the centre may see about one lot. No charges, no shop notes."""
    items = [_piece_public(i) for i in (lot.get('items') or [])]
    return {
        'lot_no':        lot.get('lot_no') or '',
        'centre_name':   lot.get('centre_name') or '',
        'sent_date':     lot.get('sent_date') or '',
        'expected_date': lot.get('expected_date') or '',
        'centre_stage':  lot.get('centre_stage') or 'sent',
        'summary':       _summarise_items(lot.get('items') or []),
        'items':         items,
    }


def shop_name(db):
    try:
        return db.get_config('business_name', '') or ''
    except Exception:
        return ''


def shop_slug(db):
    """URL-safe shop name for vanity portal links: 'DJ Jewellers' ->
    'dj-jewellers'. Falls back to 'shop' so a link always has a shape."""
    import re as _re
    slug = _re.sub(r'[^a-z0-9]+', '-', str(shop_name(db) or '').lower())
    slug = slug.strip('-')
    return slug or 'shop'


def centre_public_uid(db, centre_id):
    """The centre's long public id for vanity links, minted once and then
    stable for ever. Sequential ids would let anyone walk centre 1, 2, 3…;
    a 12-character random id cannot be walked. The login PIN stays the
    real gate either way."""
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT public_uid FROM hallmark_centres WHERE id=?",
                (int(centre_id or 0),)).fetchone()
            return str((row and row['public_uid']) or '')
    except Exception:
        return ''


def resolve_centre_ref(db, ref):
    """A vanity tail back to a centre id: long public uid first, with the
    old numeric id accepted so nothing already shared breaks."""
    ensure_stage_column(db)
    ref = str(ref or '').strip()
    if not ref:
        return 0
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM hallmark_centres WHERE public_uid=?",
                (ref,)).fetchone()
            if row:
                return int(row['id'])
            if ref.isdigit():
                row = conn.execute(
                    "SELECT id FROM hallmark_centres WHERE id=?",
                    (int(ref),)).fetchone()
                if row:
                    return int(row['id'])
    except Exception:
        pass
    return 0


def portal_url(base, slug, uid):
    """Public centre link: {base}/{shop-slug}/{long-id}.

    No token in the URL means no link to revoke: one login, one board,
    per centre. The id is long and random (see centre_public_uid), and
    the login PIN stays the real gate.
    """
    return (str(base or '').rstrip('/')
            + '/' + str(slug or 'shop').strip('/')
            + '/' + str(uid or '').strip())


# ── centres: directory, detail, portal tokens ────────────────────────

def _centre_lots(db, centre_id):
    """Every lot of one centre, newest first, with items and a bucket.

    Buckets match the centre page tabs: out (away, any stage), ready
    (all marked, awaiting collection), done (booked in or cancelled).
    """
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            lots = [dict(r) for r in conn.execute(
                "SELECT * FROM hallmark_lots WHERE centre_id=? "
                "ORDER BY id DESC", (int(centre_id),)).fetchall()]
            if lots:
                ids = tuple(l['id'] for l in lots)
                marks = '(' + ','.join('?' * len(ids)) + ')'
                by_lot = {}
                for r in conn.execute(
                        "SELECT * FROM hallmark_lot_items WHERE lot_id IN "
                        + marks + " ORDER BY id", ids).fetchall():
                    by_lot.setdefault(r['lot_id'], []).append(dict(r))
                for lot in lots:
                    lot['items'] = by_lot.get(lot['id'], [])
                    if not lot.get('centre_stage'):
                        lot['centre_stage'] = 'sent'
        return lots
    except Exception as e:
        _log(f'centre lots: {e}')
        return []


def _bucket_of(lot):
    if str(lot.get('status') or '') == 'cancelled' or \
            str(lot.get('received_date') or ''):
        return 'done'
    return 'ready' if _summarise_items(
        lot.get('items') or [])['ready'] else 'out'


def centres_overview(db):
    """Every centre with live lot counts, for the dashboard table."""
    ensure_stage_column(db)
    try:
        centres = db.hm_centres()
    except Exception:
        centres = []
    out = []
    for c in (centres or []):
        lots = _centre_lots(db, c.get('id'))
        buckets = {'out': 0, 'ready': 0, 'done': 0}
        pcs_out = 0
        for lot in lots:
            b = _bucket_of(lot)
            buckets[b] = buckets.get(b, 0) + 1
            if b in ('out', 'ready'):
                pcs_out += len(lot.get('items') or [])
        try:
            with db._get_connection() as conn:
                tok = conn.execute(
                    "SELECT token, created_at FROM hm_portal_tokens "
                    "WHERE centre_id=? AND revoked=0 "
                    "AND COALESCE(expires_at,0) > ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (c.get('id'), _now())).fetchone()
        except Exception:
            tok = None
        out.append({
            'id': c.get('id'), 'name': c.get('name') or '',
            'code': c.get('code') or '', 'phone': c.get('phone') or '',
            'email': c.get('email') or '', 'active': c.get('active', 1),
            'per_piece': c.get('per_piece'), 'lots_total': len(lots),
            'lots_out': buckets.get('out', 0),
            'lots_ready': buckets.get('ready', 0),
            'lots_done': buckets.get('done', 0),
            'pcs_away': pcs_out,
            'has_portal': bool(tok),
            'portal_since': tok['created_at'] if tok else '',
            'has_pin': centre_has_pin(db, c.get('id')),
        })
    return {'status': 'success', 'centres': out}


def centre_detail(db, centre_id):
    """One centre with its lots split for the tabs."""
    ensure_stage_column(db)
    centre = None
    try:
        for c in db.hm_centres():
            if int(c.get('id') or 0) == int(centre_id or 0):
                centre = c
                break
    except Exception:
        centre = None
    if not centre:
        return {'status': 'not_found', 'message': 'No such centre.'}
    lots = _centre_lots(db, centre_id)
    tabs = {'recent': [], 'out': [], 'ready': [], 'done': [], 'all': []}
    for lot in lots:
        pub = _lot_public(db, lot)
        pub['bucket'] = _bucket_of(lot)
        pub['status_code'] = lot.get('status') or ''
        pub['received_date'] = lot.get('received_date') or ''
        tabs['all'].append(pub)
        tabs[pub['bucket']].append(pub)
    tabs['recent'] = tabs['all'][:10]
    return {'status': 'success', 'centre': {
                'id': centre.get('id'), 'name': centre.get('name') or '',
                'code': centre.get('code') or '',
                'phone': centre.get('phone') or '',
                'email': centre.get('email') or '',
                'per_piece': centre.get('per_piece'),
                'min_charge': centre.get('min_charge'),
                'turnaround_days': centre.get('turnaround_days'),
                'has_pin': centre_has_pin(db, centre.get('id'))},
            'tabs': tabs}


def _valid_email(addr):
    s = str(addr or '').strip()
    if '@' not in s:
        return ''
    local, _, domain = s.partition('@')
    if not local or '.' not in domain:
        return ''
    return s


# ── centre portal login ──────────────────────────────────────────
# One login per centre: the owner sets a short PIN on the centre page,
# the centre types it on the portal link before the board opens. Stored
# salted + hashed, never plain; compared in constant time.

def _hash_pin(pin, salt):
    import hashlib
    return hashlib.sha256(
        (str(salt or '') + '\x00' + str(pin or '')).encode('utf-8')
        ).hexdigest()


def set_centre_pin(db, centre_id, pin=''):
    """Set (or clear, with a blank) a centre's portal login PIN.

    PINs are unique across centres: the portal login asks for nothing but
    a PIN, so one PIN must find exactly one dashboard.
    """
    import hmac
    ensure_stage_column(db)
    pin = str(pin or '').strip()
    if pin and (len(pin) < 4 or len(pin) > 32):
        return {'status': 'error',
                'message': 'A login PIN is 4 to 32 characters.'}
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM hallmark_centres WHERE id=?",
                (int(centre_id or 0),)).fetchone()
            if not row:
                return {'status': 'not_found',
                        'message': 'No such centre.'}
            cid = row['id']
            if pin:
                for other in conn.execute(
                        "SELECT id, name, portal_pin_hash, portal_pin_salt "
                        "FROM hallmark_centres WHERE id<>? "
                        "AND COALESCE(portal_pin_hash,'')<>''", (cid,)):
                    if hmac.compare_digest(
                            _hash_pin(pin, other['portal_pin_salt'] or ''),
                            str(other['portal_pin_hash'] or '')):
                        return {'status': 'error',
                                'message': 'That login is already used by '
                                           + str(other['name'] or 'a centre')
                                           + '. Pick another.'}
                import secrets
                salt = secrets.token_hex(8)
                conn.execute(
                    "UPDATE hallmark_centres SET portal_pin_hash=?, "
                    "portal_pin_salt=? WHERE id=?",
                    (_hash_pin(pin, salt), salt, cid))
                # Remember it for the login table, once per distinct login.
                last = conn.execute(
                    "SELECT pin FROM hm_centre_pin_history "
                    "WHERE centre_id=? ORDER BY id DESC LIMIT 1",
                    (cid,)).fetchone()
                if not last or str(last['pin'] or '') != pin:
                    from datetime import datetime as _dt
                    conn.execute(
                        "INSERT INTO hm_centre_pin_history"
                        "(centre_id, pin, created_at) VALUES(?,?,?)",
                        (cid, pin,
                         _dt.now().strftime('%Y-%m-%d %H:%M')))
            else:
                conn.execute(
                    "UPDATE hallmark_centres SET portal_pin_hash='', "
                    "portal_pin_salt='' WHERE id=?", (cid,))
            conn.commit()
        _log(f'set pin: centre {cid} has_pin={bool(pin)}')
        return {'status': 'success',
                'has_pin': bool(pin)}
    except Exception as e:
        _log(f'set pin: {e}')
        return {'status': 'error', 'message': str(e)}


def pin_history(db, centre_id):
    """Every login ever set for this centre, newest first. The table on
    the centre page reads this — rows live until the owner deletes them,
    even across restarts."""
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT pin, created_at FROM hm_centre_pin_history "
                "WHERE centre_id=? ORDER BY id DESC LIMIT 50",
                (int(centre_id or 0),)).fetchall()]
    except Exception:
        return []


def pin_forget(db, centre_id, pin=''):
    """Delete one saved login from the table. If it is also the login the
    portal currently accepts, that is cleared too, so the table never lies
    about what opens the board."""
    import hmac
    ensure_stage_column(db)
    pin = str(pin or '').strip()
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM hallmark_centres WHERE id=?",
                (int(centre_id or 0),)).fetchone()
            if not row:
                return {'status': 'not_found',
                        'message': 'No such centre.'}
            cid = row['id']
            conn.execute(
                "DELETE FROM hm_centre_pin_history "
                "WHERE centre_id=? AND pin=?", (cid, pin))
            removed_active = False
            cur = conn.execute(
                "SELECT portal_pin_hash, portal_pin_salt "
                "FROM hallmark_centres WHERE id=?", (cid,)).fetchone()
            if cur and str(cur['portal_pin_hash'] or '') and pin:
                if hmac.compare_digest(
                        _hash_pin(pin, cur['portal_pin_salt'] or ''),
                        str(cur['portal_pin_hash'] or '')):
                    conn.execute(
                        "UPDATE hallmark_centres SET portal_pin_hash='', "
                        "portal_pin_salt='' WHERE id=?", (cid,))
                    removed_active = True
            conn.commit()
        return {'status': 'success', 'has_pin': centre_has_pin(db, cid),
                'removed_active': removed_active}
    except Exception as e:
        _log(f'forget pin: {e}')
        return {'status': 'error', 'message': str(e)}


def centre_has_pin(db, centre_id):
    """Whether this centre's portal asks for a login."""
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT portal_pin_hash FROM hallmark_centres WHERE id=?",
                (int(centre_id or 0),)).fetchone()
            return bool(row and str(row['portal_pin_hash'] or '').strip())
    except Exception:
        return False


def verify_centre_pin(db, centre_id, pin=''):
    """Constant-time PIN check. Blank PIN never verifies.

    Stripped before hashing, exactly like set_centre_pin stores it, so a
    trailing space from a phone keyboard can never lock a centre out.
    """
    import hmac
    ensure_stage_column(db)
    pin = str(pin or '').strip()
    if not pin:
        return False
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT portal_pin_hash, portal_pin_salt "
                "FROM hallmark_centres WHERE id=?",
                (int(centre_id or 0),)).fetchone()
        if not row or not str(row['portal_pin_hash'] or ''):
            return False
        want = _hash_pin(pin, row['portal_pin_salt'] or '')
        return hmac.compare_digest(str(want), str(row['portal_pin_hash']))
    except Exception:
        return False


def portal_issue(db, centre_id, email=''):
    """Save the centre's email and mint (or renew) its portal link token.

    One live token per centre: issuing a new link revokes the old one, so
    a forwarded email cannot live for ever in someone else's inbox.
    Returns the token; the caller builds the URL around it.
    """
    email = _valid_email(email)
    if not email:
        return {'status': 'error',
                'message': 'Type the centre\u2019s email first.'}
    ensure_stage_column(db)
    centre = None
    try:
        for c in db.hm_centres():
            if int(c.get('id') or 0) == int(centre_id or 0):
                centre = c
                break
    except Exception:
        centre = None
    if not centre:
        return {'status': 'not_found', 'message': 'No such centre.'}
    try:
        import secrets
        token = secrets.token_urlsafe(24)
        with db._get_connection() as conn:
            conn.execute("UPDATE hallmark_centres SET email=? WHERE id=?",
                         (email, centre['id']))
            conn.execute("UPDATE hm_portal_tokens SET revoked=1 "
                         "WHERE centre_id=?", (centre['id'],))
            conn.execute("INSERT INTO hm_portal_tokens(token, centre_id, expires_at) "
                         "VALUES(?,?,?)", (token, centre['id'], _now() + PORTAL_TOKEN_TTL))
            conn.commit()
        _log(f"portal issued for centre {centre['id']} -> {email}")
        return {'status': 'success', 'token': token, 'email': email,
                'centre': centre.get('name') or ''}
    except Exception as e:
        _log(f'portal issue: {e}')
        return {'status': 'error', 'message': str(e)}


def portal_centre(db, token):
    """The centre behind a portal token, or None for bad/revoked/expired ones."""
    token = str(token or '').strip()
    if not token:
        return None
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT t.centre_id, c.name FROM hm_portal_tokens t "
                "JOIN hallmark_centres c ON c.id = t.centre_id "
                "WHERE t.token=? AND t.revoked=0 "
                "AND COALESCE(t.expires_at,0) > ?", (token, _now())).fetchone()
            if not row:
                return None
            return {'id': row['centre_id'], 'name': row['name'] or ''}
    except Exception:
        return None


def portal_board(db, token):
    """The Kanban payload scoped to one centre's open lots only."""
    centre = portal_centre(db, token)
    if not centre:
        return {'status': 'denied',
                'message': 'This portal link is no longer valid.'}
    lots = [l for l in _open_lot_rows(db)
            if int(l.get('centre_id') or 0) == int(centre['id'])]
    by_stage = {s: [] for s in STAGES}
    for lot in lots:
        pub = _lot_public(db, lot)
        by_stage.setdefault(pub['centre_stage'], by_stage['sent']).append(pub)
    return {'status': 'success', 'shop': shop_name(db),
            'centre': centre['name'],
            'stages': [{'id': s, 'label': STAGE_LABELS[s],
                        'lots': by_stage.get(s, [])} for s in STAGES]}


def portal_lot(db, token, lot_no):
    """One lot through a portal token: only its own centre's lots."""
    centre = portal_centre(db, token)
    if not centre:
        return {'status': 'denied',
                'message': 'This portal link is no longer valid.'}
    out = lot_payload(db, lot_no)
    if out.get('status') != 'success':
        return out
    lots = [l for l in _open_lot_rows(db)
            if str(l.get('lot_no') or '') == str(lot_no or '').strip()
            and int(l.get('centre_id') or 0) == int(centre['id'])]
    if not lots:
        return {'status': 'denied',
                'message': 'That lot does not belong to this portal.'}
    out['centre'] = centre['name']
    return out


def portal_piece(db, token, tag_id, result='', huid=''):
    """Mark a piece through a portal token, scoped to its centre."""
    centre = portal_centre(db, token)
    if not centre:
        return {'status': 'denied',
                'message': 'This portal link is no longer valid.'}
    tag_id = str(tag_id or '').strip()
    found = None
    for lot in _open_lot_rows(db):
        if int(lot.get('centre_id') or 0) != int(centre['id']):
            continue
        for it in (lot.get('items') or []):
            if str(it.get('tag_id') or '') == tag_id:
                found = lot
                break
        if found:
            break
    if not found:
        return {'status': 'denied',
                'message': 'That piece does not belong to this portal.'}
    return set_piece(db, found.get('lot_no'), tag_id, result, huid)


def portal_stage(db, token, lot_no, stage):
    """Move a stage through a portal token, gated like the shop board."""
    centre = portal_centre(db, token)
    if not centre:
        return {'status': 'denied',
                'message': 'This portal link is no longer valid.'}
    mine = [l for l in _open_lot_rows(db)
            if str(l.get('lot_no') or '') == str(lot_no or '').strip()
            and int(l.get('centre_id') or 0) == int(centre['id'])]
    if not mine:
        return {'status': 'denied',
                'message': 'That lot does not belong to this portal.'}
    return move_stage(db, lot_no, stage)


def board_payload(db):
    """Every open lot, staged for the Kanban board."""
    lots = _open_lot_rows(db)
    by_stage = {s: [] for s in STAGES}
    for lot in lots:
        pub = _lot_public(db, lot)
        by_stage.setdefault(pub['centre_stage'], by_stage['sent']).append(pub)
    return {'status': 'success', 'shop': shop_name(db),
            'stages': [{'id': s, 'label': STAGE_LABELS[s],
                        'lots': by_stage.get(s, [])} for s in STAGES]}


def lot_payload(db, lot_no):
    """One lot in full, for the share link and the board popup."""
    lot_no = str(lot_no or '').strip()
    if not lot_no:
        return {'status': 'error', 'message': 'A lot number is needed.'}
    for lot in _open_lot_rows(db):
        if str(lot.get('lot_no') or '').strip() == lot_no:
            out = _lot_public(db, lot)
            out['status'] = 'success'
            out['shop'] = shop_name(db)
            return out
    return {'status': 'not_found',
            'message': 'That lot is not away at the moment.'}


def set_piece(db, lot_no, tag_id, result='', huid=''):
    """Mark one piece pass, fail, or back to pending, with its HUID.

    Refuses lots the shop already booked in or cancelled, unknown tags,
    and anything but pass/fail/pending, so a mistyped request from the
    board cannot corrupt the register.
    """
    lot_no = str(lot_no or '').strip()
    tag_id = str(tag_id or '').strip()
    result = str(result or 'pending').strip().lower()
    if result not in RESULTS:
        return {'status': 'error',
                'message': 'A piece is pass, fail or pending.'}
    if not lot_no or not tag_id:
        return {'status': 'error',
                'message': 'A lot number and a tag are needed.'}
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            lot = conn.execute(
                "SELECT * FROM hallmark_lots WHERE lot_no=?", (lot_no,)
                ).fetchone()
            if not lot:
                return {'status': 'not_found',
                        'message': 'That lot is not away at the moment.'}
            lot = dict(lot)
            if str(lot.get('status') or '') == 'cancelled' or \
                    str(lot.get('received_date') or ''):
                return {'status': 'locked',
                        'message': 'That lot is already closed.'}
            row = conn.execute(
                "SELECT * FROM hallmark_lot_items "
                "WHERE lot_id=? AND tag_id=?", (lot['id'], tag_id)).fetchone()
            if not row:
                return {'status': 'not_found',
                        'message': 'That piece is not in this lot.'}
            huid = _normalise_huid(huid)
            conn.execute(
                "UPDATE hallmark_lot_items SET result=?, huid=? "
                "WHERE lot_id=? AND tag_id=?",
                (result, huid, lot['id'], tag_id))
            conn.commit()
        _log(f'{lot_no}/{tag_id} -> {result} {huid}')
        out = lot_payload(db, lot_no)
        out['message'] = 'Saved.'
        return out
    except Exception as e:
        _log(f'set piece: {e}')
        return {'status': 'error', 'message': str(e)}


def move_stage(db, lot_no, stage):
    """Drag a lot to another column.

    Moving to Ready is refused while any piece is still unmarked, which is
    the whole point of the gate: the shop only comes to collect a lot whose
    every piece has an answer. Moving back is always allowed.
    """
    lot_no = str(lot_no or '').strip()
    stage = str(stage or '').strip().lower()
    if stage not in STAGES:
        return {'status': 'error',
                'message': 'That is not a board column.'}
    if not lot_no:
        return {'status': 'error', 'message': 'A lot number is needed.'}
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            lot = conn.execute(
                "SELECT * FROM hallmark_lots WHERE lot_no=?", (lot_no,)
                ).fetchone()
            if not lot:
                return {'status': 'not_found',
                        'message': 'That lot is not away at the moment.'}
            lot = dict(lot)
            if str(lot.get('status') or '') == 'cancelled' or \
                    str(lot.get('received_date') or ''):
                return {'status': 'locked',
                        'message': 'That lot is already closed.'}
            if stage == 'ready':
                items = [dict(r) for r in conn.execute(
                    "SELECT * FROM hallmark_lot_items WHERE lot_id=?",
                    (lot['id'],)).fetchall()]
                left = [i for i in items
                        if str(i.get('result') or 'pending').lower()
                        == 'pending']
                if not items:
                    return {'status': 'blocked',
                            'message': 'The lot has no pieces in it.'}
                if left:
                    names = ', '.join(
                        str(i.get('tag_id') or '?') for i in left[:5])
                    more = '' if len(left) <= 5 else \
                        f' and {len(left) - 5} more'
                    return {'status': 'blocked',
                            'message': f'Mark every piece first: {names}'
                                       f'{more} still unmarked.'}
            conn.execute(
                "UPDATE hallmark_lots SET centre_stage=? WHERE id=?",
                (stage, lot['id']))
            conn.commit()
        _log(f'{lot_no} -> {stage}')
        out = lot_payload(db, lot_no)
        return out
    except Exception as e:
        _log(f'move stage: {e}')
        return {'status': 'error', 'message': str(e)}


# ── PIN-only portal login ────────────────────────────────────────
# The root page asks for nothing but a PIN. PINs are unique
# (set_centre_pin refuses doubles), so one PIN finds exactly one centre.

def live_token(db, centre_id):
    """The centre's newest live (unrevoked, unexpired) portal token, or ''."""
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT token FROM hm_portal_tokens WHERE centre_id=? "
                "AND revoked=0 AND COALESCE(expires_at,0) > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (int(centre_id or 0), _now())).fetchone()
            return str(row['token'] or '') if row else ''
    except Exception:
        return ''


def silent_token(db, centre_id):
    """A portal token without revoking or emailing anything.

    For PIN logins by centres that never got a link: the token only ever
    travels inside the logged-in session, never in an email.
    """
    ensure_stage_column(db)
    try:
        import secrets
        token = secrets.token_urlsafe(24)
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM hallmark_centres WHERE id=?",
                (int(centre_id or 0),)).fetchone()
            if not row:
                return ''
            conn.execute("INSERT INTO hm_portal_tokens(token, centre_id, expires_at) "
                         "VALUES(?,?,?)", (token, row['id'], _now() + PORTAL_TOKEN_TTL))
            conn.commit()
        return token
    except Exception as e:
        _log(f'silent token: {e}')
        return ''


def any_centre_has_pin(db):
    """Whether any centre has a portal login at all. The root portal page
    uses this to tell 'nobody set a login yet' apart from 'wrong login'."""
    ensure_stage_column(db)
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM hallmark_centres "
                "WHERE COALESCE(portal_pin_hash,'')<>'' LIMIT 1").fetchone()
            return bool(row)
    except Exception:
        return False


def find_centre_by_pin(db, pin=''):
    """The centre whose login PIN this is, or None.

    Stripped before hashing, exactly like set_centre_pin stores it.
    """
    import hmac
    ensure_stage_column(db)
    pin = str(pin or '').strip()
    if not pin:
        return None
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, portal_pin_hash, portal_pin_salt "
                "FROM hallmark_centres "
                "WHERE COALESCE(portal_pin_hash,'')<>''").fetchall()
            for r in rows:
                want = _hash_pin(pin, r['portal_pin_salt'] or '')
                if hmac.compare_digest(
                        str(want), str(r['portal_pin_hash'] or '')):
                    return {'id': r['id'], 'name': r['name'] or ''}
    except Exception:
        pass
    return None


# ── Centre login sessions (DB-backed) ─────────────────────────────
# A login survives a restart of the shop program; it still dies on its own
# clock (HM_SESSION_TTL, 12h like before). Tokens are random 256-bit, the
# cookie is HttpOnly, and expired rows are pruned on every read.

HM_SESSION_TTL = 12 * 3600


def hm_session_create(db, centre_id, ttl=None):
    """Log a centre in. Returns (sid, max_age_seconds) or ('', 0)."""
    import secrets as _secrets
    import time as _time
    ensure_stage_column(db)
    try:
        sid = _secrets.token_urlsafe(32)
        expires = _time.time() + float(ttl if ttl else HM_SESSION_TTL)
        with db._get_connection() as conn:
            conn.execute("DELETE FROM hm_sessions WHERE expires_at <= ?",
                         (_time.time(),))
            conn.execute("INSERT INTO hm_sessions(sid, centre_id, expires_at)"
                         " VALUES(?,?,?)",
                         (sid, int(centre_id), expires))
            conn.commit()
        return sid, int(expires - _time.time())
    except Exception as e:
        _log(f'session create: {e}')
        return '', 0


def hm_session_get(db, sid):
    """The logged-in centre id behind a session cookie, or None."""
    import time as _time
    sid = str(sid or '').strip()
    if not sid or db is None:
        return None
    ensure_stage_column(db)
    try:
        now = _time.time()
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT centre_id, expires_at FROM hm_sessions WHERE sid=?",
                (sid,)).fetchone()
            if not row:
                return None
            if float(row['expires_at'] or 0) < now:
                conn.execute("DELETE FROM hm_sessions WHERE sid=?", (sid,))
                conn.commit()
                return None
            return int(row['centre_id'])
    except Exception:
        return None


def hm_session_drop(db, sid):
    """Log out one session. Never raises."""
    try:
        sid = str(sid or '').strip()
        if not sid or db is None:
            return
        ensure_stage_column(db)
        with db._get_connection() as conn:
            conn.execute("DELETE FROM hm_sessions WHERE sid=?", (sid,))
            conn.commit()
    except Exception:
        pass
