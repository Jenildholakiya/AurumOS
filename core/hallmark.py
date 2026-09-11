# -*- coding: utf-8 -*-
"""
The rules behind hallmarking: purity grades, identification numbers, lots
sent to an assaying centre, and what a lot costs.

Everything here is a pure function over plain values. No database, no clock
reaching out on its own, no network. "Today" is always passed in, so a test
can sit on the awkward dates rather than waiting for one.

Background, because the vocabulary matters and the law is specific.

A jeweller in India may not sell gold jewellery without a hallmark. The
pieces go to an Assaying and Hallmarking Centre, which tests the metal and
marks each piece with three things: the BIS standard mark, the fineness of
the metal, and a six-character Hallmark Unique Identification, the HUID.
The HUID belongs to that one piece for ever. A piece whose assay comes back
below what was claimed is failed and comes home to be melted again, not
sold.

So the shop's real problem is not the number. It is the gap: stock that is
bought but cannot legally be sold until it comes back from the centre, and
a lot that has been away too long. That is what this module measures.

db_manager.py stores and fetches; main.py exposes; this module decides.
"""

import datetime as _dt
import re

# ── shared helpers ──────────────────────────────────────────────────────────

DATE_RE = re.compile(r'^(\d{4})-(\d{1,2})-(\d{1,2})')


def parse_date(value):
    """'YYYY-MM-DD' (with or without a time after it) -> date, else None."""
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.datetime):
        return value.date()
    m = DATE_RE.match(str(value or '').strip())
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def today(value=None):
    """The day to reckon from. Passed in everywhere so tests can pick it."""
    return parse_date(value) or _dt.date.today()


def _num(value, default=0.0):
    try:
        return float(str(value).replace(',', '').strip())
    except Exception:
        return default


# ── purity ──────────────────────────────────────────────────────────────────

# Fineness is parts per thousand of pure metal. These are the grades BIS
# will mark; anything else has to be sold as the grade below it, which is
# why a shop cannot invent its own karat.
GOLD_GRADES = {
    9:  375,
    14: 585,
    18: 750,
    20: 833,
    22: 916,
    23: 958,
    24: 995,
}

# Hallmarking silver is voluntary, but a shop that does it uses these.
SILVER_GRADES = {
    800: 800, 835: 835, 900: 900, 925: 925, 970: 970, 990: 990,
}

KARAT_RE = re.compile(r'(\d{1,2})\s*(?:k|kt|karat|carat)?', re.I)


def karat_of(value):
    """'22K', '22 kt', 22, '22.0' -> 22. Anything unrecognised -> 0."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    m = KARAT_RE.search(str(value))
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def fineness(karat):
    """Parts per thousand for a karat BIS will mark, else 0."""
    return GOLD_GRADES.get(karat_of(karat), 0)


def grade_mark(karat):
    """What is struck on the piece: '22K916'. Empty if it is not a
    markable grade, which is the answer a shop needs before it packs a lot."""
    k = karat_of(karat)
    f = GOLD_GRADES.get(k)
    return '%dK%d' % (k, f) if f else ''


def is_markable(karat):
    return bool(GOLD_GRADES.get(karat_of(karat)))


def nearest_markable(karat):
    """The grade a piece would have to be sold as. Always rounds DOWN: a
    shop may under-claim purity, never over-claim it."""
    k = karat_of(karat)
    below = [g for g in sorted(GOLD_GRADES) if g <= k]
    return below[-1] if below else 0


# ── the identification number ───────────────────────────────────────────────

# Six characters, letters and digits, struck by the centre. It is not a
# checksum and there is nothing to verify it against offline, so the only
# honest checks are shape and uniqueness.
HUID_LEN = 6
HUID_RE = re.compile(r'^[A-Z0-9]{6}$')

# Struck by hand and read off a loupe, so these get mistyped for each other.
CONFUSABLE = 'O0I1'


def normalise_huid(raw):
    """Upper case, spaces and dashes dropped. Not validated."""
    return re.sub(r'[\s\-]', '', str(raw or '')).upper()


def validate_huid(raw):
    """(ok, reason). Reason is what to show the person typing it."""
    h = normalise_huid(raw)
    if not h:
        return False, 'Enter the six-character number struck on the piece.'
    if len(h) != HUID_LEN:
        return False, 'A HUID is six characters. That one is %d.' % len(h)
    if not HUID_RE.match(h):
        return False, 'A HUID is letters and digits only.'
    return True, ''


def huid_warning(raw):
    """Not an error. A HUID may legitimately contain O, 0, I or 1; this
    only says which characters are worth a second look before saving."""
    h = normalise_huid(raw)
    hits = sorted({c for c in h if c in CONFUSABLE})
    if not hits:
        return ''
    return 'Check %s against the piece: easy to mistake.' % ' and '.join(hits)


def duplicate_huids(rows):
    """HUIDs appearing more than once in what is being saved. Two pieces
    cannot share one, so this is a typing mistake caught before it lands."""
    seen, dupes = {}, []
    for r in rows:
        h = normalise_huid(r.get('huid'))
        if not h:
            continue
        seen[h] = seen.get(h, 0) + 1
    for h, n in seen.items():
        if n > 1:
            dupes.append(h)
    return sorted(dupes)


# ── what a lot costs ────────────────────────────────────────────────────────

# BIS publishes these and has changed them before, so they are a default a
# shop can override per centre, not a constant to bury in the arithmetic.
DEFAULT_PER_PIECE = 45.0
DEFAULT_MIN_CHARGE = 200.0
GST_RATE = 18.0


def charge_for(pieces, per_piece=None, minimum=None, gst_rate=None):
    """What the centre will bill for a lot.

    Charged per article with a floor on the lot, which is why sending two
    pieces costs the same as sending four: the minimum, not the count, is
    what a small lot pays. Returns the parts as well as the total so a shop
    can see why.
    """
    n = max(0, int(_num(pieces, 0)))
    rate = _num(per_piece, DEFAULT_PER_PIECE) if per_piece not in (None, '') else DEFAULT_PER_PIECE
    floor = _num(minimum, DEFAULT_MIN_CHARGE) if minimum not in (None, '') else DEFAULT_MIN_CHARGE
    gst = _num(gst_rate, GST_RATE) if gst_rate not in (None, '') else GST_RATE

    by_piece = round(n * rate, 2)
    base = max(by_piece, floor) if n else 0.0
    tax = round(base * gst / 100.0, 2)
    return {
        'pieces': n,
        'per_piece': rate,
        'by_piece': by_piece,
        'minimum': floor,
        'minimum_applied': bool(n and by_piece < floor),
        'base': round(base, 2),
        'gst_rate': gst,
        'gst': tax,
        'total': round(base + tax, 2),
    }


def worth_sending(pieces, per_piece=None, minimum=None):
    """How many more pieces would ride along for nothing.

    A lot below the minimum is paying for pieces it did not send. Shops
    lose real money to this by sending four pieces at a time.
    """
    n = max(0, int(_num(pieces, 0)))
    rate = _num(per_piece, DEFAULT_PER_PIECE) if per_piece not in (None, '') else DEFAULT_PER_PIECE
    floor = _num(minimum, DEFAULT_MIN_CHARGE) if minimum not in (None, '') else DEFAULT_MIN_CHARGE
    if rate <= 0:
        return 0
    covered = int(floor // rate)
    return max(0, covered - n)


# ── a lot's life ────────────────────────────────────────────────────────────

DEFAULT_TURNAROUND_DAYS = 7

STATE_OUT = 'out'
STATE_DUE = 'due'
STATE_LATE = 'late'
STATE_DONE = 'received'
STATE_VOID = 'cancelled'


def due_date(sent_date, days=DEFAULT_TURNAROUND_DAYS):
    d = parse_date(sent_date)
    if not d:
        return ''
    try:
        n = int(_num(days, DEFAULT_TURNAROUND_DAYS))
    except Exception:
        n = DEFAULT_TURNAROUND_DAYS
    return (d + _dt.timedelta(days=max(0, n))).isoformat()


def days_out(sent_date, on=None, received_date=''):
    """Days the lot has been away. Stops counting when it comes back."""
    start = parse_date(sent_date)
    if not start:
        return 0
    end = parse_date(received_date) or today(on)
    return max(0, (end - start).days)


def lot_state(lot, on=None):
    """Where a lot stands, in the order a shop worries about it."""
    if str(lot.get('status') or '').lower() == 'cancelled':
        return STATE_VOID
    if parse_date(lot.get('received_date')):
        return STATE_DONE
    ref = today(on)
    due = parse_date(lot.get('expected_date')) or parse_date(
        due_date(lot.get('sent_date')))
    if not due:
        return STATE_OUT
    if ref > due:
        return STATE_LATE
    if ref == due:
        return STATE_DUE
    return STATE_OUT


RESULT_PENDING = 'pending'
RESULT_PASS = 'pass'
RESULT_FAIL = 'fail'


def summarise(items):
    """Counts and weights for one lot, or for every lot at once."""
    out = {'pieces': 0, 'gross_wt': 0.0, 'pending': 0, 'passed': 0,
           'failed': 0, 'marked': 0}
    for it in items or []:
        out['pieces'] += 1
        out['gross_wt'] += _num(it.get('gr_wt'), 0.0)
        res = str(it.get('result') or RESULT_PENDING).lower()
        if res == RESULT_PASS:
            out['passed'] += 1
        elif res == RESULT_FAIL:
            out['failed'] += 1
        else:
            out['pending'] += 1
        if normalise_huid(it.get('huid')):
            out['marked'] += 1
    out['gross_wt'] = round(out['gross_wt'], 3)
    return out


def can_send(item):
    """(ok, reason) for putting one piece in a lot.

    A piece already marked must not go again, and a piece with no weight is
    a stock record that was never finished, which the centre would reject.
    """
    if normalise_huid(item.get('huid')):
        return False, 'Already hallmarked.'
    if _num(item.get('gr_wt'), 0) <= 0:
        return False, 'No weight recorded.'
    if not str(item.get('tag_id') or '').strip():
        return False, 'No tag number.'
    if not is_markable(item.get('karat')):
        k = karat_of(item.get('karat'))
        if not k:
            return False, 'No purity recorded, so nothing can be claimed.'
        near = nearest_markable(k)
        if near:
            return False, ('%dK is not a grade BIS marks. It would have to go '
                           'as %dK.' % (k, near))
        return False, '%dK is not a grade BIS marks.' % k
    return True, ''


def check_receipt(row, seen=None):
    """(ok, reason) for one piece coming back.

    A pass needs a number; a fail must not carry one, because a failed
    piece was never marked and recording one would put an unhallmarked
    piece into saleable stock.
    """
    res = str(row.get('result') or '').lower()
    huid = normalise_huid(row.get('huid'))
    if res not in (RESULT_PASS, RESULT_FAIL, RESULT_PENDING):
        return False, 'Say whether it passed or failed.'
    if res == RESULT_FAIL:
        if huid:
            return False, 'A failed piece carries no number. Clear it.'
        return True, ''
    if res == RESULT_PENDING:
        return True, ''
    ok, why = validate_huid(huid)
    if not ok:
        return False, why
    if seen and huid in seen:
        return False, 'That number is already on another piece.'
    return True, ''


def next_lot_no(existing, on=None):
    """HM-2026-0007. Counts within the year, so a new year starts at one
    and a shop can say "the seventh lot this year" and be understood."""
    ref = today(on)
    year = ref.year
    prefix = 'HM-%d-' % year
    top = 0
    for raw in existing or []:
        s = str(raw or '').strip().upper()
        if not s.startswith(prefix):
            continue
        tail = s[len(prefix):]
        if tail.isdigit():
            top = max(top, int(tail))
    return '%s%04d' % (prefix, top + 1)


def dashboard(pending, lots, on=None):
    """Everything the panel puts across the top, worked out in one place.

    `pending` is stock with no number on it. `lots` each carry their items.
    """
    ref = today(on)
    waiting = summarise(pending)
    out_now = {'lots': 0, 'pieces': 0, 'gross_wt': 0.0}
    late = {'lots': 0, 'pieces': 0, 'gross_wt': 0.0}
    failed = {'pieces': 0, 'gross_wt': 0.0}
    marked = 0
    rows = []

    for lot in lots or []:
        items = lot.get('items') or []
        s = summarise(items)
        state = lot_state(lot, ref)
        row = dict(lot)
        row['items'] = items
        row['state'] = state
        row['days_out'] = days_out(lot.get('sent_date'), ref,
                                   lot.get('received_date'))
        row['expected_date'] = (lot.get('expected_date')
                                or due_date(lot.get('sent_date')))
        row.update({'pieces': s['pieces'], 'gross_wt': s['gross_wt'],
                    'passed': s['passed'], 'failed': s['failed'],
                    'pending': s['pending']})
        rows.append(row)

        if state in (STATE_OUT, STATE_DUE, STATE_LATE):
            # Only the pieces that have not come back. A lot half received
            # is half in the shop, and counting all of it as away sends
            # someone looking for jewellery that is on the shelf.
            still = [i for i in items
                     if str(i.get('result') or RESULT_PENDING).lower()
                     == RESULT_PENDING]
            wt = round(sum(_num(i.get('gr_wt'), 0.0) for i in still), 3)
            out_now['lots'] += 1
            out_now['pieces'] += len(still)
            out_now['gross_wt'] += wt
            if state == STATE_LATE:
                late['lots'] += 1
                late['pieces'] += len(still)
                late['gross_wt'] += wt
        if state == STATE_DONE:
            marked += s['passed']
        failed['pieces'] += s['failed']
        for it in items:
            if str(it.get('result') or '').lower() == RESULT_FAIL:
                failed['gross_wt'] += _num(it.get('gr_wt'), 0.0)

    # Worst first: late, then merely out, then the newest.
    order = {STATE_LATE: 0, STATE_DUE: 1, STATE_OUT: 2, STATE_DONE: 3,
             STATE_VOID: 4}
    rows.sort(key=lambda r: (order.get(r['state'], 9),
                             -int(r.get('days_out') or 0)))

    for key in ('gross_wt',):
        out_now[key] = round(out_now[key], 3)
        late[key] = round(late[key], 3)
        failed[key] = round(failed[key], 3)

    return {
        'today': ref.isoformat(),
        'waiting': {'pieces': waiting['pieces'],
                    'gross_wt': waiting['gross_wt']},
        'out': out_now,
        'late': late,
        'failed': failed,
        'marked': marked,
        'lots': rows,
        'saleable_gap': waiting['pieces'] + out_now['pieces'],
    }
