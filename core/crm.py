# -*- coding: utf-8 -*-
"""
The rules behind the CRM: dates, dues, loyalty and savings schemes.

Everything here is a pure function over plain values. No database, no clock
reaching out on its own, no network. That is deliberate: these are the
calculations a shop argues with you about, so they need to be checkable on
their own, and "today" is always passed in rather than looked up so a test can
sit on the awkward dates.

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


def _add_months(d, months):
    """d plus a number of months, clamped to the end of a short month.

    31 January plus one month is 28 February, not 3 March. Instalments fall on
    a day of the month, and rolling into the next one puts a payment in the
    wrong cycle.
    """
    total = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return _dt.date(year, month, min(d.day, last))


# ── dates that bring people back ────────────────────────────────────────────

def days_until_anniversary(occasion, ref=None):
    """Days from `ref` until this month-and-day comes round again.

    0 means today. The year on the stored date is the year it happened, which
    is not what is being asked; only the day and month matter.

    29 February is treated as 28 February in a common year, so a leap-year
    birthday is greeted every year rather than three years in four.
    """
    occ = parse_date(occasion)
    if not occ:
        return None
    ref = today(ref)
    month, day = occ.month, occ.day

    def _on(year):
        d, m = day, month
        if m == 2 and d == 29:
            leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
            if not leap:
                d = 28
        return _dt.date(year, m, d)

    this_year = _on(ref.year)
    if this_year >= ref:
        return (this_year - ref).days
    return (_on(ref.year + 1) - ref).days


def upcoming_occasions(customers, within_days=30, ref=None):
    """Birthdays and anniversaries falling within the next `within_days`.

    `customers` are dicts carrying at least `name`, and optionally `dob` and
    `anniversary`. Returns one entry per occasion, soonest first, so a
    customer with both appears twice, which is what a counter wants: two
    reasons to call, on two different days.
    """
    ref = today(ref)
    out = []
    for c in customers or []:
        for field, kind in (('dob', 'birthday'), ('anniversary', 'anniversary')):
            days = days_until_anniversary(c.get(field), ref)
            if days is None or days > within_days:
                continue
            occ = parse_date(c.get(field))
            entry = dict(c)
            entry.update({
                'occasion': kind,
                'occasion_date': occ.isoformat(),
                'days_until': days,
                'turns': (ref.year + (1 if days and occ.replace(year=ref.year) < ref else 0)) - occ.year,
            })
            out.append(entry)
    out.sort(key=lambda e: (e['days_until'], str(e.get('name') or '')))
    return out


def lapsed(customers, days=365, ref=None):
    """Customers who have not bought in `days`, biggest spender first.

    A customer who has never bought is not lapsed, they are new; they would
    otherwise flood the list and bury the ones worth calling.
    """
    ref = today(ref)
    out = []
    for c in customers or []:
        last = parse_date(c.get('last_purchase'))
        if not last:
            continue
        gap = (ref - last).days
        if gap < days:
            continue
        entry = dict(c)
        entry['days_since'] = gap
        out.append(entry)
    out.sort(key=lambda e: (-float(e.get('lifetime_value') or 0), -e['days_since']))
    return out


# ── money owed ──────────────────────────────────────────────────────────────

AGEING_BUCKETS = ((0, 30, '0-30'), (31, 60, '31-60'), (61, 90, '61-90'), (91, None, '90+'))


def age_dues(entries, ref=None):
    """Sort outstanding amounts into 0-30, 31-60, 61-90 and 90+ day buckets.

    A balance on its own says who owes; the age says who to ring first.
    Each entry needs `date` and `amount`; anything at or below zero is a
    payment and is ignored here.
    """
    ref = today(ref)
    buckets = {label: 0.0 for _, _, label in AGEING_BUCKETS}
    oldest = None
    total = 0.0
    for e in entries or []:
        d = parse_date(e.get('date'))
        try:
            amt = float(e.get('amount') or 0)
        except (TypeError, ValueError):
            continue
        if not d or amt <= 0:
            continue
        age = (ref - d).days
        total += amt
        if oldest is None or age > oldest:
            oldest = age
        for lo, hi, label in AGEING_BUCKETS:
            if age >= lo and (hi is None or age <= hi):
                buckets[label] = round(buckets[label] + amt, 2)
                break
    return {'buckets': buckets, 'total': round(total, 2),
            'oldest_days': oldest or 0}


def over_limit(balance, limit):
    """How far past a credit limit a customer is. 0 when inside it, and a
    limit of 0 means no limit was set rather than no credit allowed."""
    try:
        balance, limit = float(balance or 0), float(limit or 0)
    except (TypeError, ValueError):
        return 0.0
    if limit <= 0:
        return 0.0
    return round(max(0.0, balance - limit), 2)


# ── loyalty ─────────────────────────────────────────────────────────────────

DEFAULT_POINTS_PER_RUPEE = 0.01     # one point per hundred rupees
DEFAULT_RUPEES_PER_POINT = 1.0      # a point is worth a rupee when redeemed


def points_for_sale(amount, rate=DEFAULT_POINTS_PER_RUPEE):
    """Points earned on a bill. Rounded down: a shop never owes a part point."""
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return 0
    if amount <= 0:
        return 0
    return int(amount * float(rate))


def redeemable(points_balance, bill_amount, rupees_per_point=DEFAULT_RUPEES_PER_POINT,
               max_fraction=0.5):
    """The most a customer may take off this bill.

    Capped at a fraction of the bill as well as by the balance, so points
    cannot turn a sale into a giveaway. Returns (points, rupees).
    """
    try:
        balance = int(points_balance or 0)
        bill = float(bill_amount or 0)
    except (TypeError, ValueError):
        return 0, 0.0
    if balance <= 0 or bill <= 0:
        return 0, 0.0
    cap_rupees = bill * float(max_fraction)
    rupees = min(balance * float(rupees_per_point), cap_rupees)
    points = int(rupees / float(rupees_per_point)) if rupees_per_point else 0
    return points, round(points * float(rupees_per_point), 2)


# ── savings schemes ─────────────────────────────────────────────────────────
#
# The monthly gold plan an Indian jeweller runs: the customer pays the same
# amount for a set number of months, the shop adds a bonus at the end, and the
# total is redeemed against jewellery. Everything about it is dates and
# arithmetic, so it belongs here rather than in a screen.

def scheme_schedule(start_date, months, monthly_amount):
    """Every instalment due on a plan, numbered from one."""
    start = parse_date(start_date)
    try:
        months = int(months)
        amount = float(monthly_amount)
    except (TypeError, ValueError):
        return []
    if not start or months <= 0 or amount <= 0:
        return []
    return [{'instalment': i + 1,
             'due_date': _add_months(start, i).isoformat(),
             'amount': round(amount, 2)}
            for i in range(months)]


def maturity_date(start_date, months):
    """When the plan finishes: the month after the last instalment falls."""
    start = parse_date(start_date)
    try:
        months = int(months)
    except (TypeError, ValueError):
        return None
    if not start or months <= 0:
        return None
    return _add_months(start, months).isoformat()


def scheme_status(enrolment, payments, ref=None):
    """Where one customer's plan stands today.

    `enrolment` carries start_date, months, monthly_amount and optionally
    bonus_type ('month' or 'percent') and bonus_value. `payments` are the
    instalments actually received.
    """
    ref = today(ref)
    schedule = scheme_schedule(enrolment.get('start_date'),
                               enrolment.get('months'),
                               enrolment.get('monthly_amount'))
    if not schedule:
        return {'valid': False, 'reason': 'This plan has no schedule to check.'}

    paid_total = 0.0
    paid_count = 0
    for p in payments or []:
        try:
            amt = float(p.get('amount') or 0)
        except (TypeError, ValueError):
            continue
        if amt > 0:
            paid_total += amt
            paid_count += 1

    due_so_far = [s for s in schedule if parse_date(s['due_date']) <= ref]
    expected = round(sum(s['amount'] for s in due_so_far), 2)
    missed = max(0, len(due_so_far) - paid_count)
    next_due = next((s for s in schedule if parse_date(s['due_date']) > ref), None)

    bonus = 0.0
    kind = str(enrolment.get('bonus_type') or '').lower()
    try:
        value = float(enrolment.get('bonus_value') or 0)
    except (TypeError, ValueError):
        value = 0.0
    if kind == 'month':
        # The common one: pay eleven, the shop adds the twelfth.
        bonus = round(float(enrolment.get('monthly_amount') or 0) * value, 2)
    elif kind == 'percent':
        bonus = round(paid_total * value / 100.0, 2)

    total_plan = round(sum(s['amount'] for s in schedule), 2)
    matured = parse_date(maturity_date(enrolment.get('start_date'),
                                       enrolment.get('months')))
    return {
        'valid': True,
        'instalments': len(schedule),
        'paid_count': paid_count,
        'paid_total': round(paid_total, 2),
        'expected_to_date': expected,
        'behind_by': round(max(0.0, expected - paid_total), 2),
        'missed_instalments': missed,
        'next_due_date': next_due['due_date'] if next_due else '',
        'next_due_amount': next_due['amount'] if next_due else 0.0,
        'plan_total': total_plan,
        'bonus': bonus,
        'redeemable_value': round(paid_total + (bonus if paid_count >= len(schedule) else 0), 2),
        'is_mature': bool(matured and ref >= matured and paid_count >= len(schedule)),
        'maturity_date': matured.isoformat() if matured else '',
        'status': ('completed' if paid_count >= len(schedule)
                   else 'behind' if missed > 0
                   else 'on_track'),
    }


# ── segments ────────────────────────────────────────────────────────────────

def segment_for(customer, ref=None, vip_value=500000, repeat_bills=3):
    """A plain label for how a customer behaves, when none was set by hand.

    Deliberately blunt. A shopkeeper can read 'vip' or 'lapsed' off a screen
    and act on it; a score out of a hundred just raises questions.
    """
    if str(customer.get('segment') or '').strip():
        return str(customer['segment']).strip().lower()
    ref = today(ref)
    try:
        value = float(customer.get('lifetime_value') or 0)
        bills = int(customer.get('bill_count') or 0)
    except (TypeError, ValueError):
        value, bills = 0.0, 0
    last = parse_date(customer.get('last_purchase'))
    if last and (ref - last).days >= 365:
        return 'lapsed'
    if value >= vip_value:
        return 'vip'
    if bills >= repeat_bills:
        return 'repeat'
    if bills <= 1:
        return 'new'
    return 'active'
