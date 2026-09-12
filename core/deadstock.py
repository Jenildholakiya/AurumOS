# -*- coding: utf-8 -*-
"""
Which metal has not moved, and what it is costing to leave it there.

Everything here is a pure function over plain values. No database, no clock
reaching out on its own. "Today" is always passed in, so a test can sit on
the awkward dates rather than waiting a year for one.

Why a shop needs this.

Stock that has sat on the shelf since last Diwali is the largest idle sum in
most jewellery shops, and it is invisible. The daily screens all answer
"what happened today". Nothing answers "what has been happening to this
piece for eleven months", so a tray quietly fills with designs nobody wants
while the owner buys more of the same.

Two shops read it differently and both are served here. A retail shop counts
pieces and rupees: a ring is dead or it is not. A wholesaler counts weight
and fine weight, because to them the piece is metal that can be melted and
remade, and what matters is how many grams are asleep.

The valuation deliberately matches the dashboard's, to the formula:

    fine weight = (net weight, or gross when there is no net) x touch / 100
    value       = fine weight x the ten-gram rate / 10

Two screens disagreeing about what the stock is worth is worse than one
screen not existing.

db_manager.py fetches; main.py exposes; this module decides.
"""

import datetime as _dt
import re

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


# ── how old ─────────────────────────────────────────────────────────────────

# A season, half a year, a year, and longer. Chosen to match how a jeweller
# already talks: "since the last season", "since last Diwali", "over a year".
BANDS = (
    ('fresh',   'Under 3 months',   0,   90),
    ('slow',    '3 to 6 months',    91,  180),
    ('stale',   '6 to 12 months',   181, 365),
    ('dead',    'Over a year',      366, None),
)

# What a shop means by "dead" unless it says otherwise. Half a year is long
# enough to have missed a wedding season.
DEFAULT_DEAD_DAYS = 180
CHOICES = (90, 180, 270, 365)


def age_days(entry_date, on=None):
    """Days since the piece came into stock. Never negative: a date typed in
    the future is a typing mistake, not a piece that arrives next week."""
    came = parse_date(entry_date)
    if not came:
        return None
    return max(0, (today(on) - came).days)


def band_for(days):
    """('stale', '6 to 12 months') for an age, or ('', '') if unknown."""
    if days is None:
        return '', ''
    for key, label, lo, hi in BANDS:
        if days >= lo and (hi is None or days <= hi):
            return key, label
    return '', ''


# ── what it is worth ────────────────────────────────────────────────────────

def fine_weight_exact(row):
    """Pure metal in the piece, in grams, unrounded.

    Net weight where it is recorded, gross where it is not, times the touch.
    The same expression the dashboard uses, on purpose.
    """
    r = row or {}
    wt = _num(r.get('nt_wt'), 0.0) or _num(r.get('gr_wt'), 0.0)
    touch = _num(r.get('touch'), 0.0)
    if wt <= 0 or touch <= 0:
        return 0.0
    return wt * touch / 100.0


def fine_weight(row):
    """The same, rounded to the milligram for a person to read."""
    return round(fine_weight_exact(row), 3)


def metal_value(row, rate_per_10g):
    """What the metal alone would fetch at today's rate.

    Taken from the unrounded weight. Rounding each piece to the milligram
    before multiplying put the total four rupees away from the dashboard's,
    and a shop comparing two screens does not care which is the rounding
    error.
    """
    rate = _num(rate_per_10g, 0.0)
    if rate <= 0:
        return 0.0
    return round(fine_weight_exact(row) * rate / 10.0, 2)


def ticket_value(row):
    """What the shop has priced the piece at, if it has priced it.

    Retail sets a sale price on a piece; a wholesaler does not. Zero means
    nobody priced it, which is different from a piece worth nothing.
    """
    return round(_num((row or {}).get('sale_price'), 0.0), 2)


TAGGED = 'piece'
UNTAGGED = 'weight'


def kind_of(row):
    """A finished piece with a tag, or loose metal by weight.

    They are separated because a shop does something different with each. A
    ring that has not sold gets re-polished, discounted or offered to a
    customer. Loose metal is not dead in the same sense; it is raw material
    waiting for the next order, and counting it as dead stock would bury the
    number that matters under one that does not.
    """
    r = row or {}
    if 'is_tagged' in r and r.get('is_tagged') is not None:
        # The column is an explicit statement. A bulk row may still carry a
        # reference in tag_id, and reading that as "finished piece" once put
        # half a kilo of loose metal on a list of dead rings.
        flag = str(r.get('is_tagged')).strip().lower()
        return TAGGED if flag in ('1', 'true', 'yes') else UNTAGGED
    tag = str(r.get('tag_id') or '').strip()
    if tag and not tag.upper().startswith('KATTI-'):
        return TAGGED
    return UNTAGGED


# ── the report ──────────────────────────────────────────────────────────────

def classify(rows, rate_per_10g=0.0, on=None):
    """Each row with its age, its band, its metal and its value attached."""
    out = []
    for row in rows or []:
        r = dict(row)
        days = age_days(r.get('entry_date'), on)
        key, label = band_for(days)
        r['age_days'] = days
        r['band'] = key
        r['band_label'] = label
        r['fine_wt'] = fine_weight(r)
        r['metal_value'] = metal_value(r, rate_per_10g)
        r['ticket_value'] = ticket_value(r)
        r['kind'] = kind_of(r)
        out.append(r)
    # Oldest first. The report exists to put the worst offender on top.
    out.sort(key=lambda x: (-(x['age_days'] or 0), str(x.get('tag_id') or '')))
    return out


def is_dead(row, threshold_days=DEFAULT_DEAD_DAYS):
    """Has this piece been sitting longer than the shop's patience."""
    days = (row or {}).get('age_days')
    if days is None:
        return False
    return days >= max(1, int(_num(threshold_days, DEFAULT_DEAD_DAYS)))


def _totals(rows, rate_per_10g=0.0):
    """Sums taken from the unrounded weights, rounded once at the end.

    Adding up numbers that have each already been rounded is how a total
    drifts away from the same total computed elsewhere.
    """
    rate = _num(rate_per_10g, 0.0)
    t = {'pieces': 0, 'gross_wt': 0.0, 'fine_wt': 0.0,
         'metal_value': 0.0, 'ticket_value': 0.0}
    for r in rows:
        exact = fine_weight_exact(r)
        t['pieces'] += 1
        t['gross_wt'] += _num(r.get('gr_wt'), 0.0)
        t['fine_wt'] += exact
        t['metal_value'] += (exact * rate / 10.0) if rate > 0 else 0.0
        t['ticket_value'] += _num(r.get('ticket_value'), 0.0)
    t['gross_wt'] = round(t['gross_wt'], 3)
    t['fine_wt'] = round(t['fine_wt'], 3)
    t['metal_value'] = round(t['metal_value'], 2)
    t['ticket_value'] = round(t['ticket_value'], 2)
    return t


def by_band(rows, rate_per_10g=0.0):
    """Totals per age band, in order, including bands with nothing in them.

    An empty band is worth printing: a shop reading the report wants to see
    that nothing has crossed into "over a year" yet.
    """
    out = []
    for key, label, _lo, _hi in BANDS:
        picked = [r for r in rows if r.get('band') == key]
        entry = {'band': key, 'label': label}
        entry.update(_totals(picked, rate_per_10g))
        out.append(entry)
    return out


def by_group(rows, field, limit=0, rate_per_10g=0.0):
    """Totals per category, per purity, or per anything else on the row.

    Biggest first by value, because the question is always "where is the
    money", and a shop with no categories set up still gets one honest row
    rather than an empty table.
    """
    buckets = {}
    for r in rows:
        name = str(r.get(field) or '').strip() or 'Not set'
        buckets.setdefault(name, []).append(r)
    out = []
    for name, picked in buckets.items():
        entry = {'name': name}
        entry.update(_totals(picked, rate_per_10g))
        out.append(entry)
    out.sort(key=lambda x: (-x['metal_value'], -x['fine_wt'], x['name']))
    return out[:limit] if limit else out


def report(rows, rate_per_10g=0.0, threshold_days=DEFAULT_DEAD_DAYS,
           on=None, mode='retail'):
    """Everything the screen shows, worked out in one place.

    `rows` is everything still in stock. What comes back separates the
    finished pieces from the loose metal, and inside the pieces separates
    what has passed the shop's threshold from what has not.
    """
    ref = today(on)
    everything = classify(rows, rate_per_10g, ref)
    pieces = [r for r in everything if r['kind'] == TAGGED]
    weight = [r for r in everything if r['kind'] == UNTAGGED]
    dead = [r for r in pieces if is_dead(r, threshold_days)]

    oldest = dead[0]['age_days'] if dead else 0
    all_total = _totals(pieces, rate_per_10g)
    dead_total = _totals(dead, rate_per_10g)

    share = 0.0
    if all_total['metal_value'] > 0:
        share = round(dead_total['metal_value'] * 100.0
                      / all_total['metal_value'], 1)
    elif all_total['fine_wt'] > 0:
        share = round(dead_total['fine_wt'] * 100.0 / all_total['fine_wt'], 1)

    return {
        'today': ref.isoformat(),
        'mode': 'wholesale' if str(mode).lower() == 'wholesale' else 'retail',
        'threshold_days': int(_num(threshold_days, DEFAULT_DEAD_DAYS)),
        'rate_per_10g': round(_num(rate_per_10g, 0.0), 2),
        'rows': dead,
        'all_pieces': all_total,
        'dead': dead_total,
        'weight_stock': _totals(weight, rate_per_10g),
        'share_of_stock': share,
        'oldest_days': oldest,
        'bands': by_band(pieces, rate_per_10g),
        'by_category': by_group(dead, 'category', 0, rate_per_10g),
        'by_purity': by_group(dead, 'karat', 0, rate_per_10g),
        'has_rate': _num(rate_per_10g, 0.0) > 0,
    }


def headline(rep):
    """One sentence an owner can act on, in the language of their trade."""
    r = rep or {}
    dead = r.get('dead') or {}
    n = dead.get('pieces') or 0
    if not n:
        return 'Nothing has been sitting longer than %d days.' % (
            r.get('threshold_days') or DEFAULT_DEAD_DAYS)

    days = r.get('oldest_days') or 0
    months = max(1, int(round(days / 30.0)))
    if r.get('mode') == 'wholesale':
        what = '%.3fg fine' % (dead.get('fine_wt') or 0.0)
    elif r.get('has_rate'):
        what = 'Rs %s' % format_rupees(dead.get('metal_value') or 0.0)
    else:
        what = '%.3fg fine' % (dead.get('fine_wt') or 0.0)

    return ('%d piece%s, %s, none of it moving. The oldest has been here %d '
            'month%s.' % (n, '' if n == 1 else 's', what, months,
                          '' if months == 1 else 's'))


def format_rupees(amount):
    """Indian grouping: 2,85,000 rather than 285,000."""
    n = int(round(_num(amount, 0.0)))
    sign = '-' if n < 0 else ''
    s = str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ','.join(parts) + ',' + tail
