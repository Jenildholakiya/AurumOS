"""Pin the CRM rules: dates, dues, loyalty and savings schemes.

Run from the project root:  python test_crm.py

These are the calculations a shopkeeper will argue with. A birthday list that
misses February, an instalment that lands in the wrong month, a points balance
that lets someone walk out with free gold: each is a small arithmetic slip
that only shows up in front of a customer.

core/crm.py takes "today" as an argument everywhere, so these tests sit on the
awkward dates on purpose: leap years, month ends, and the turn of the year.
Nothing here touches a database, a clock or the network.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core import crm                                           # noqa: E402

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


def check_that(label, ok, detail=''):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        if detail:
            print(f"         {detail}")
        FAIL.append(label)


print("-- reading a date --")
check("plain date", crm.parse_date('2026-09-11'), dt.date(2026, 9, 11))
check("a timestamp is accepted", crm.parse_date('2026-09-11 14:03:00'), dt.date(2026, 9, 11))
check("single-digit parts", crm.parse_date('2026-9-1'), dt.date(2026, 9, 1))
check("junk is not a date", crm.parse_date('soon'), None)
check("nothing is not a date", crm.parse_date(''), None)
check("an impossible date is refused", crm.parse_date('2026-02-30'), None)

print("-- how long until it comes round again --")
ref = '2026-09-11'
check("today is zero days away", crm.days_until_anniversary('1990-09-11', ref), 0)
check("tomorrow", crm.days_until_anniversary('1990-09-12', ref), 1)
check("yesterday means next year", crm.days_until_anniversary('1990-09-10', ref), 364)
check("the year stored does not matter", crm.days_until_anniversary('1955-09-20', ref),
      crm.days_until_anniversary('2001-09-20', ref))
check("across the new year", crm.days_until_anniversary('1990-01-05', '2026-12-30'), 6)
check("no date, no answer", crm.days_until_anniversary('', ref), None)

print("-- the 29th of February --")
# A leap-year birthday must still be greeted in a common year, not skipped.
check("greeted on the 28th in a common year",
      crm.days_until_anniversary('2000-02-29', '2027-02-28'), 0)
check("and on the day itself in a leap year",
      crm.days_until_anniversary('2000-02-29', '2028-02-29'), 0)
check_that("never returns nothing for a real leap date",
           crm.days_until_anniversary('2000-02-29', '2027-06-01') is not None)

print("-- who to call this week --")
people = [
    {'name': 'Asha',  'dob': '1990-09-12', 'anniversary': '2015-09-14'},
    {'name': 'Bhavin', 'dob': '1985-12-01'},
    {'name': 'Chirag', 'anniversary': '2010-09-11'},
    {'name': 'Divya'},
]
soon = crm.upcoming_occasions(people, within_days=7, ref=ref)
check("only those with a date in range", len(soon), 3)
check("soonest first", [e['name'] for e in soon], ['Chirag', 'Asha', 'Asha'])
check("today's is first", soon[0]['days_until'], 0)
check("both of a customer's dates appear",
      sorted(e['occasion'] for e in soon if e['name'] == 'Asha'),
      ['anniversary', 'birthday'])
check("someone months away is left out",
      [e['name'] for e in crm.upcoming_occasions(people, 7, ref) if e['name'] == 'Bhavin'], [])
check("a wider window reaches them",
      any(e['name'] == 'Bhavin' for e in crm.upcoming_occasions(people, 120, ref)), True)

print("-- who has stopped coming --")
customers = [
    {'name': 'Old Money', 'last_purchase': '2024-01-01', 'lifetime_value': 800000},
    {'name': 'Small Spend', 'last_purchase': '2024-01-01', 'lifetime_value': 2000},
    {'name': 'Recent', 'last_purchase': '2026-08-01', 'lifetime_value': 50000},
    {'name': 'Never Bought', 'lifetime_value': 0},
]
gone = crm.lapsed(customers, days=365, ref=ref)
check("only those overdue", [c['name'] for c in gone], ['Old Money', 'Small Spend'])
check("biggest spender first", gone[0]['name'], 'Old Money')
check("and it says how long", gone[0]['days_since'], (dt.date(2026, 9, 11) - dt.date(2024, 1, 1)).days)
check_that("someone who never bought is new, not lapsed",
           all(c['name'] != 'Never Bought' for c in gone))

print("-- how old the money owed is --")
dues = [
    {'date': '2026-09-01', 'amount': 1000},   # 10 days
    {'date': '2026-08-01', 'amount': 2000},   # 41 days
    {'date': '2026-07-01', 'amount': 3000},   # 72 days
    {'date': '2026-01-01', 'amount': 4000},   # 253 days
    {'date': '2026-09-05', 'amount': -500},   # a payment, not a due
]
aged = crm.age_dues(dues, ref=ref)
check("0-30", aged['buckets']['0-30'], 1000.0)
check("31-60", aged['buckets']['31-60'], 2000.0)
check("61-90", aged['buckets']['61-90'], 3000.0)
check("90+", aged['buckets']['90+'], 4000.0)
check("total ignores payments", aged['total'], 10000.0)
check("and it names the oldest", aged['oldest_days'], 253)
check("nothing owed is not an error", crm.age_dues([], ref=ref)['total'], 0.0)

print("-- credit limits --")
check("inside the limit", crm.over_limit(4000, 5000), 0.0)
check("past it", crm.over_limit(6500, 5000), 1500.0)
check("no limit set means no breach", crm.over_limit(99999, 0), 0.0)
check("junk does not raise", crm.over_limit('abc', 5000), 0.0)

print("-- loyalty --")
check("a point per hundred rupees", crm.points_for_sale(45000), 450)
check("rounded down, never part points", crm.points_for_sale(45099), 450)
check("nothing spent, nothing earned", crm.points_for_sale(0), 0)
check("a refund earns nothing", crm.points_for_sale(-500), 0)
check("redeeming within balance and cap", crm.redeemable(300, 10000), (300, 300.0))
check("the bill caps it at half", crm.redeemable(9000, 1000), (500, 500.0))
check("no points, nothing off", crm.redeemable(0, 5000), (0, 0.0))
check("no bill, nothing off", crm.redeemable(500, 0), (0, 0.0))

print("-- a savings plan's instalments --")
sched = crm.scheme_schedule('2026-01-31', 12, 5000)
check("one per month", len(sched), 12)
check("the first is the start date", sched[0]['due_date'], '2026-01-31')
# 31 January plus a month is 28 February, not 3 March: an instalment must not
# roll into the following cycle.
check("a short month clamps, not rolls", sched[1]['due_date'], '2026-02-28')
check("and the next returns to the 31st", sched[2]['due_date'], '2026-03-31')
check("leap February", crm.scheme_schedule('2028-01-31', 2, 100)[1]['due_date'], '2028-02-29')
check("a plan with no months has no schedule", crm.scheme_schedule('2026-01-01', 0, 500), [])
check("nor one with no money", crm.scheme_schedule('2026-01-01', 12, 0), [])
check("maturity is after the last instalment", crm.maturity_date('2026-01-31', 12), '2027-01-31')

print("-- where a plan stands --")
plan = {'start_date': '2026-01-11', 'months': 12, 'monthly_amount': 5000,
        'bonus_type': 'month', 'bonus_value': 1}
paid8 = [{'amount': 5000} for _ in range(8)]
st = crm.scheme_status(plan, paid8, ref=ref)          # 11 Sep: 9 due
check("instalments counted", st['instalments'], 12)
check("paid so far", st['paid_total'], 40000.0)
check("expected by today", st['expected_to_date'], 45000.0)
check("behind by one", st['behind_by'], 5000.0)
check("one missed", st['missed_instalments'], 1)
check("and it is flagged", st['status'], 'behind')
check("next instalment", st['next_due_date'], '2026-10-11')
check("not matured", st['is_mature'], False)

on_track = crm.scheme_status(plan, [{'amount': 5000} for _ in range(9)], ref=ref)
check("paid up is on track", on_track['status'], 'on_track')
check("and owes nothing today", on_track['behind_by'], 0.0)

done = crm.scheme_status(plan, [{'amount': 5000} for _ in range(12)], ref='2027-02-01')
check("finished", done['status'], 'completed')
check("the bonus is a month's instalment", done['bonus'], 5000.0)
check("redeemable is payments plus bonus", done['redeemable_value'], 65000.0)
check("and it has matured", done['is_mature'], True)

pct = crm.scheme_status(dict(plan, bonus_type='percent', bonus_value=8),
                        [{'amount': 5000} for _ in range(12)], ref='2027-02-01')
check("a percentage bonus", pct['bonus'], 4800.0)
check("a plan with nothing set is refused",
      crm.scheme_status({}, [], ref=ref)['valid'], False)
check("part payments do not earn the bonus early",
      crm.scheme_status(plan, paid8, ref=ref)['redeemable_value'], 40000.0)

print("-- what kind of customer this is --")
check("a set label wins", crm.segment_for({'segment': 'Wholesale'}), 'wholesale')
check("big spender", crm.segment_for(
    {'lifetime_value': 900000, 'bill_count': 4, 'last_purchase': '2026-09-01'}, ref), 'vip')
check("comes back", crm.segment_for(
    {'lifetime_value': 60000, 'bill_count': 5, 'last_purchase': '2026-09-01'}, ref), 'repeat')
check("first purchase", crm.segment_for(
    {'lifetime_value': 5000, 'bill_count': 1, 'last_purchase': '2026-09-01'}, ref), 'new')
check("gone quiet beats everything else", crm.segment_for(
    {'lifetime_value': 900000, 'bill_count': 9, 'last_purchase': '2024-01-01'}, ref), 'lapsed')

# ── storage and the bridge ──────────────────────────────────────────────────
# The half above is arithmetic. This half is the part that has to survive a
# real database and the allowlist between the screens and Python: a rule that
# is right and a column that never gets written is still a broken feature.

import shutil                                                  # noqa: E402
import tempfile                                                # noqa: E402

REPO = os.path.abspath(os.path.dirname(__file__))
work = tempfile.mkdtemp(prefix='aurum_crm_')
os.makedirs(os.path.join(work, 'database'), exist_ok=True)
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
_cwd = os.getcwd()
os.chdir(work)

try:
    sys.path.insert(0, REPO)
    from database.db_manager import DBManager                  # noqa: E402
    import main as _main                                       # noqa: E402

    db = DBManager()
    api = _main.AurumAPI.__new__(_main.AurumAPI)
    api.db = db
    api._window = None
    api._session_username = 'owner'

    print("-- the customer record has somewhere to put a birthday --")
    import sqlite3                                             # noqa: E402
    conn = sqlite3.connect(os.environ['AURUM_DB_PATH'])
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clients_master)")}
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    for col in ('dob', 'anniversary', 'email', 'whatsapp', 'pan', 'gstin',
                'segment', 'referred_by', 'household', 'notes'):
        check_that(f"clients_master.{col}", col in cols)
    for t in ('customer_notes', 'customer_followups', 'loyalty_ledger',
              'savings_schemes', 'scheme_enrolments', 'scheme_payments',
              'customer_orders'):
        check_that(f"table {t}", t in tables)

    print("-- saving a customer --")
    r = api.crm_save_customer({'name': 'Asha Patel', 'phone': '9999',
                               'dob': '1990-09-14', 'anniversary': '2015-09-12',
                               'city': 'Rajkot'})
    check("saved", r.get('status'), 'success')
    again = api.crm_save_customer({'name': 'Asha Patel', 'city': 'Ahmedabad'})
    check("saving the same name updates rather than duplicating",
          again.get('id'), r.get('id'))
    rows = [c for c in db.crm_customers() if c['name'] == 'Asha Patel']
    check("only one row", len(rows), 1)
    check("the change took", rows[0]['city'], 'Ahmedabad')
    check("and the birthday survived it", rows[0]['dob'], '1990-09-14')
    check("a customer with no name is refused",
          api.crm_save_customer({'name': '  '}).get('status'), 'error')
    check_that("a column that does not exist is ignored, not fatal",
               api.crm_save_customer({'name': 'Asha Patel',
                                      'not_a_column': 'x'}).get('status') == 'success')

    print("-- loyalty is recorded, not just displayed --")
    check("nothing to begin with", db.crm_loyalty_balance('Asha Patel'), 0)
    awarded = api.crm_award_loyalty('Asha Patel', 45000, 'R-1')
    check("a point per hundred rupees", awarded['earned'], 450)
    check("balance follows", db.crm_loyalty_balance('Asha Patel'), 450)
    used = api.crm_redeem_loyalty('Asha Patel', 200, 10000, 'R-2')
    check("redeemed", used['used'], 200)
    check("and taken off the balance", db.crm_loyalty_balance('Asha Patel'), 250)
    over = api.crm_redeem_loyalty('Asha Patel', 99999, 10000, 'R-3')
    check("cannot redeem more than the bill allows", over['used'], 250)
    check("nothing left", db.crm_loyalty_balance('Asha Patel'), 0)
    check("redeeming nothing is refused",
          api.crm_redeem_loyalty('Asha Patel', 50, 10000).get('status'), 'error')

    print("-- a savings plan, enrolled and paid --")
    sch = api.crm_save_scheme({'name': '11+1 Plan', 'months': 12,
                               'monthly_amount': 5000,
                               'bonus_type': 'month', 'bonus_value': 1})
    check("plan created", sch.get('status'), 'success')
    check("a plan with no months is refused",
          api.crm_save_scheme({'name': 'Bad', 'months': 0,
                               'monthly_amount': 100}).get('status'), 'error')
    en = api.crm_enrol(sch['id'], 'Asha Patel', '2026-01-11')
    check("enrolled", en.get('status'), 'success')
    for _ in range(8):
        api.crm_add_payment(en['id'], 5000, '2026-02-01')
    plans = api.crm_enrolments('Asha Patel')
    check("one enrolment", len(plans), 1)
    check("payments counted", plans[0]['paid_total'], 40000.0)
    check("the plan name comes through", plans[0]['scheme_name'], '11+1 Plan')
    check("an instalment needs an amount",
          api.crm_add_payment(en['id'], 0).get('status'), 'error')

    print("-- notes, follow-ups and orders --")
    check("note saved", api.crm_add_note('Asha Patel', 'Prefers rose gold').get('status'), 'success')
    check("and read back", len(api.crm_notes('Asha Patel')), 1)
    check("an empty note is refused",
          api.crm_add_note('Asha Patel', '   ').get('status'), 'error')
    check("follow-up saved",
          api.crm_add_followup('Asha Patel', 'Call about bangle', '2026-09-12').get('status'),
          'success')
    check("it is open", len(api.crm_followups('open')), 1)
    fid = api.crm_followups('open')[0]['id']
    api.crm_close_followup(fid)
    check("and closes", len(api.crm_followups('open')), 0)

    o = api.crm_save_order({'customer': 'Asha Patel', 'kind': 'repair',
                            'description': 'Chain clasp',
                            'promised_date': '2026-09-20', 'advance': 500})
    check("order saved", o.get('status'), 'success')
    check("an order needs a description",
          api.crm_save_order({'customer': 'Asha Patel'}).get('status'), 'error')
    api.crm_close_order(o['id'])
    closed = [x for x in api.crm_orders('all') if x['id'] == o['id']][0]
    check("closing marks it done", closed['status'], 'done')
    # Closing used to go through the save path, which meant any field the
    # caller left out was overwritten with a placeholder.
    check("and leaves the customer alone", closed['customer'], 'Asha Patel')
    check("and the description", closed['description'], 'Chain clasp')

    print("-- the dashboard, in one call --")
    api.crm_save_customer({'name': 'Bhavin Shah', 'phone': '8888', 'dob': '1985-09-13'})
    d = api.crm_dashboard(30, 365)
    check("it loads", d.get('status'), 'success')
    check_that("counts are there", 'customers' in (d.get('counts') or {}))
    check_that("occasions are listed", len(d.get('occasions') or []) >= 2)
    check_that("plans are listed", len(d.get('enrolments') or []) >= 1)
    check_that("every plan carries its computed status",
               all('status' in e for e in d.get('enrolments') or []))
    check_that("the dues buckets exist",
               set((d.get('dues_total') or {}).keys()) == {'0-30', '31-60', '61-90', '90+'})

    print("-- one customer in full --")
    full = api.crm_customer('Asha Patel')
    check("found", full.get('status'), 'success')
    check_that("with their plans", len(full.get('plans') or []) >= 1)
    check_that("their notes", len(full.get('notes') or []) >= 1)
    check_that("and an ageing breakdown", 'buckets' in (full.get('ageing') or {}))
    check("somebody who does not exist",
          api.crm_customer('Nobody At All').get('status'), 'not_found')

finally:
    try:
        os.chdir(_cwd)
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
