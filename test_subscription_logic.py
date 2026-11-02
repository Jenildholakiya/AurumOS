"""Subscription system bridge method tests.

Run from the project root:  python test_subscription_logic.py
"""

import ast, io, os, sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

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
    tree = ast.parse(io.open('main.py', encoding='utf-8').read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'AurumAPI':
            return {
                item.name: [a.arg for a in item.args.args if a.arg != 'self']
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith('_')
            }
    raise AssertionError('AurumAPI class not found')


print("-- subscription bridge methods exist with correct signatures --")
methods = bridge_methods()

subscription_methods = {
    'subscription_startup_check': [],
    'subscription_sync': [],
    'subscription_check_update': [],
    'subscription_poll_check': [],
    'renew_subscription': [],
    'subscription_check_feature': ['feature_id'],
    'subscription_get_state': [],
    'subscription_start_sync': ['interval_minutes'],
}

for name, expected_args in subscription_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods,
               f"missing from bridge")
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- subscription methods are not accidentally duplicated --")
all_names = []
tree = ast.parse(io.open('main.py', encoding='utf-8').read())
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == 'AurumAPI':
        all_names = [item.name for item in node.body
                     if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
sub_names = [n for n in all_names if n.startswith('subscription_') or n == 'renew_subscription']
check("no duplicate subscription method", len(sub_names), len(set(sub_names)))

print("\n-- subscription method count is reasonable --")
check_that("at least 7 subscription methods", len(sub_names) >= 7,
           f"found {len(sub_names)}")

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
