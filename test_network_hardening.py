"""Network configuration bridge method tests.

Run from the project root:  python test_network_hardening.py
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


print("-- network bridge methods exist with correct signatures --")
methods = bridge_methods()

network_methods = {
    'print_tag_network': ['tags'],
    'get_network_config': [],
    'save_network_config': ['cfg'],
    'fetch_live_network_nodes': [],
}

for name, expected_args in network_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods,
               f"missing from bridge")
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- network methods accept expected parameter types --")
check_that("save_network_config takes exactly one arg",
           len(methods.get('save_network_config', [])) == 1,
           f"args: {methods.get('save_network_config')}")
check_that("print_tag_network takes exactly one arg",
           len(methods.get('print_tag_network', [])) == 1,
           f"args: {methods.get('print_tag_network')}")

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
