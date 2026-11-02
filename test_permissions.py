"""Checks for the staff permission policy.

Run from the project root:  python test_permissions.py

The policy in core/permissions.py names bridge methods as plain strings. A
typo there is silent and dangerous in both directions: a misspelt entry in
OWNER_ONLY stops guarding anything, and a misspelt entry in a bundle locks a
staff member out of a page that is supposed to work. So the first thing this
does is read the real method names off AurumAPI and hold the policy against
them.

main.py is parsed rather than imported, because importing it starts a webview,
a licence check and several background threads.
"""

import ast
import inspect
import io
import os
import sys

sys.path.insert(0, os.path.abspath('.'))

from core import permissions as P

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


def bridge_methods():
    """Public method names on AurumAPI, read straight out of main.py."""
    tree = ast.parse(io.open('main.py', encoding='utf-8').read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'AurumAPI':
            return {
                item.name for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith('_')
            }
    raise AssertionError('AurumAPI class not found in main.py')


print("-- every method the policy names must exist on the bridge --")
real = bridge_methods()
print(f"  AurumAPI exposes {len(real)} public methods")

named = set(P.OWNER_ONLY) | set(P.ALWAYS)
for bundle in P.BUNDLES.values():
    named |= set(bundle['methods'])

# 'cluster' is the dispatcher added by the multi-PC work; allow it to be
# missing until that lands, but nothing else.
pending = {'cluster'}
unknown = sorted(named - real - pending)
check("no policy entry names a method that does not exist", unknown, [])

print("\n-- the dangerous methods are actually covered --")
must_guard = [
    'add_staff', 'delete_staff', 'update_staff_password',
    'change_owner_password', 'update_settings', 'delete_bill',
    'do_year_close', 'change_db_location', 'renew_subscription',
]
missing = [m for m in must_guard if m in real and m not in P.OWNER_ONLY]
check("no high-risk method left unguarded", missing, [])

print("\n-- staff are refused owner-only work whatever they hold --")
every_bundle = list(P.BUNDLES)
check("staff cannot add staff", P.is_allowed('add_staff', 'staff', every_bundle), False)
check("staff cannot delete a bill", P.is_allowed('delete_bill', 'staff', every_bundle), False)
check("staff cannot change settings", P.is_allowed('update_settings', 'staff', every_bundle), False)
check("staff cannot reach private methods", P.is_allowed('_do_login_check', 'staff', every_bundle), False)
check("owner is unrestricted", P.is_allowed('add_staff', 'admin', []), True)

print("\n-- a billing terminal gets billing and nothing more --")
check("billing staff may raise a bill", P.is_allowed('generate_bill', 'staff', ['billing']), True)
check("billing staff may read the session", P.is_allowed('get_session', 'staff', ['billing']), True)
check("billing staff may not enter stock", P.is_allowed('add_stock_entry', 'staff', ['billing']), False)
check("billing staff may not save a katti", P.is_allowed('save_katti_voucher', 'staff', ['billing']), False)
check("staff with no bundle can still log out", P.is_allowed('logout', 'staff', []), True)
check("staff with no bundle cannot bill", P.is_allowed('generate_bill', 'staff', []), False)

print("\n-- sidebar pages follow the software type --")
check("wholesale billing lands on billing.html",
      P.pages_for(['billing'], 'wholesale'), ['billing.html', 'tag_audit.html'])
check("retail billing lands on pos_billing.html",
      P.pages_for(['billing'], 'retail'), ['pos_billing.html', 'tag_audit.html'])
check("overlapping bundles do not repeat a page",
      P.pages_for(['inventory', 'karigar'], 'wholesale').count('uchak_stock_entry.html'), 1)

print("\n-- seat counts --")
check("lite connects no one", P.licensed_seats('lite', []), 0)
check("pro connects one PC", P.licensed_seats('pro', []), 1)
check("enterprise connects four", P.licensed_seats('enterprise', []), 4)
check("a purchased seat count overrides the plan",
      P.licensed_seats('pro', ['billing_retail', 'terminals=6']), 6)
check("separator style does not matter",
      P.licensed_seats('pro', ['terminal:3']), 3)
check("an absurd count is capped", P.licensed_seats('pro', ['terminals=9999']), P.MAX_SEATS)
check("an unrelated feature is not mistaken for a seat count",
      P.licensed_seats('pro', ['multi_staff', 'lan_multi_pc']), 1)
check("an unknown plan grants nothing", P.licensed_seats('platinum', []), 0)

print("\n-- what an owner may hand out --")
check("pro offers billing only", P.bundles_for_plan('pro'), ['billing'])
check("enterprise offers every bundle there is",
      sorted(P.bundles_for_plan('enterprise')), sorted(P.BUNDLES))
check("lite offers nothing", P.bundles_for_plan('lite'), [])

# A bundle in no plan is a screen nobody on staff can ever reach, however
# well it is built. Both the CRM and hallmarking sat like that until this
# was checked: written, wired, and unreachable for any staff login.
orphans = sorted(b for b in P.BUNDLES
                 if not any(b in P.bundles_for_plan(plan) for plan in P.PLAN_BUNDLES))
check("every bundle belongs to some plan", orphans, [])

# The menu is one list; the policy is another. An entry a plan grants but
# a mode does not allow is a link into a refusal.
for _bid, _b in sorted(P.BUNDLES.items()):
    _pages = _b.get('pages', {})
    check("%s names the pages it unlocks" % _bid,
          bool(_pages.get('retail') or _pages.get('wholesale')), True)

print("\n-- the guard, and the bridge surviving it --")


class FakeApi:
    """Stands in for AurumAPI with real signatures on the methods that matter."""

    def __init__(self):
        self._session_role = None
        self._session_username = ''
        self._session_bundles = []

    def get_session(self):
        return 'session'

    def generate_bill(self, bill_data):
        return 'billed:' + str(bill_data)

    def add_stock_entry(self, data):
        return 'stocked'

    def add_staff(self, username, password, permissions=None):
        return 'staff added'

    def delete_bill(self, vch_id):
        return 'deleted'

    def _private(self):
        return 'private'


def pywebview_view(obj):
    """Exactly how pywebview builds the JavaScript API, from its own source:
    walk dir(), keep methods and functions, take the argument list and drop
    the first entry as self."""
    seen = {}
    for name in dir(obj):
        if name.startswith('_'):
            continue
        attr = getattr(obj, name)
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            seen[name] = inspect.getfullargspec(attr).args[1:]
    return seen


api = FakeApi()
before = pywebview_view(api)
count = P.install_guard(api)
after = pywebview_view(api)

check_that("the guard wrapped the methods", count >= 5, f'wrapped {count}')
check("every method is still visible to the page",
      sorted(after), sorted(before))
check("and each keeps its own argument list", after, before)
check("argument names really did survive",
      after.get('add_staff'), ['username', 'password', 'permissions'])
check("installing twice does nothing the second time", P.install_guard(api), 0)

print("\n-- what the guard actually stops --")
api._session_role = None
check("before anyone signs in, nothing is blocked",
      api.generate_bill('x'), 'billed:x')

api._session_role = 'admin'
check("the owner is untouched", api.add_staff('a', 'b'), 'staff added')
check("and can still delete a bill", api.delete_bill('VCH-1'), 'deleted')

api._session_role = 'staff'
api._session_bundles = ['billing']
check("billing staff can raise a bill", api.generate_bill('x'), 'billed:x')
check("billing staff can read the session", api.get_session(), 'session')

blocked = api.add_staff('mallory', 'pw')
check_that("billing staff cannot add a staff account",
           isinstance(blocked, dict) and blocked.get('denied') is True, str(blocked))
check_that("nor delete a bill",
           isinstance(api.delete_bill('VCH-1'), dict), str(api.delete_bill('VCH-1')))
check_that("nor enter stock they were not given",
           isinstance(api.add_stock_entry({}), dict), str(api.add_stock_entry({})))
check_that("a refusal explains itself rather than throwing",
           'access' in blocked.get('message', '').lower(), str(blocked))

api._session_bundles = ['billing', 'inventory']
check("granting inventory lets the same PC enter stock",
      api.add_stock_entry({}), 'stocked')

print("\n-- the same, against every real signature in main.py --")
# The fake above only proves the idea. This rebuilds a stub carrying the exact
# signature of every public method on AurumAPI and checks that the guard leaves
# the bridge's view of all of them untouched. Cheaper and safer than importing
# main.py, which would start a window, a licence check and several threads.
tree = ast.parse(io.open('main.py', encoding='utf-8').read())
cls_node = next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == 'AurumAPI')

lines, skipped = [], []
for item in cls_node.body:
    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if item.name.startswith('_'):
        continue
    try:
        lines.append(f'    def {item.name}({ast.unparse(item.args)}):\n        return "ok"')
    except Exception:
        skipped.append(item.name)

namespace = {}
exec('class RealShape:\n' + '\n'.join(lines), namespace)
shape = namespace['RealShape']()
shape._session_role = None
shape._session_bundles = []

real_before = pywebview_view(shape)
P.install_guard(shape)
real_after = pywebview_view(shape)

check("no signature could not be reproduced", skipped, [])
check_that(f"all {len(real_before)} real methods still reach the page",
           len(real_before) == len(real_after) and len(real_before) > 190,
           f'{len(real_before)} before, {len(real_after)} after')
check("and every argument list is byte-for-byte unchanged",
      real_after, real_before)

drifted = [n for n in real_before if real_before[n] != real_after.get(n)]
check("no single method drifted", drifted, [])

print("\n-- the app's own calls still work while staff are signed in --")


class InnerApi:
    """Mirrors the shapes that would break: a method calling another method,
    and a background thread calling in from outside."""

    def __init__(self):
        self._session_role = 'staff'
        self._session_bundles = ['billing']

    def generate_bill(self, bill_data):
        # An allowed method reaching a restricted one internally, which is
        # exactly what billing does when it writes to the ledger.
        return self.delete_bill('VCH-1')

    def delete_bill(self, vch_id):
        return 'deleted ' + vch_id

    def check_license_revoked(self, network=True):
        return 'valid'

    def get_recent_logs(self, lines=50):
        return ['a log line']


inner = InnerApi()
P.install_guard(inner)

check("a licence check on a timer is not refused mid-shift",
      inner.check_license_revoked(), 'valid')
check("an allowed method may use a restricted one internally",
      inner.generate_bill('x'), 'deleted VCH-1')
check_that("but the page still cannot call that restricted one directly",
           isinstance(inner.delete_bill('VCH-1'), dict),
           str(inner.delete_bill('VCH-1')))
check_that("and a method neither internal nor granted stays refused",
           isinstance(inner.get_recent_logs(), dict), str(inner.get_recent_logs()))

check("every internal name is a real bridge method",
      sorted(set(P.INTERNAL) - real), [])

uncovered = sorted(real - named)
print(f"\n  {len(uncovered)} bridge methods are in no bundle, so staff are refused them.")
print("  That is the safe default. Listed here so the set stays deliberate:")
for name in uncovered[:12]:
    print(f"    {name}")
if len(uncovered) > 12:
    print(f"    ... and {len(uncovered) - 12} more")

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
