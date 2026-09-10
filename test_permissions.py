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
check("enterprise offers all four", len(P.bundles_for_plan('enterprise')), 4)
check("lite offers nothing", P.bundles_for_plan('lite'), [])

uncovered = sorted(real - named)
print(f"\n  {len(uncovered)} bridge methods are in no bundle, so staff are refused them.")
print("  That is the safe default. Listed here so the set stays deliberate:")
for name in uncovered[:12]:
    print(f"    {name}")
if len(uncovered) > 12:
    print(f"    ... and {len(uncovered) - 12} more")

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
