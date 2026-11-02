"""Karigar register and order workflow tests.

Run from the project root:  python test_karigar.py
"""

import ast, io, os, sys, tempfile, shutil

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


print("-- karigar bridge methods exist with correct signatures --")
methods = bridge_methods()

karigar_methods = {
    'get_all_karigars': [],
    'add_karigar': ['name', 'phone', 'specialty', 'address', 'ghat_norm', 'allowance'],
    'update_karigar': ['kid', 'name', 'phone', 'specialty', 'address', 'ghat_norm', 'allowance'],
    'delete_karigar': ['kid'],
    'get_karigar_orders': ['karigar_id'],
    'create_karigar_order': ['karigar_id', 'order_no', 'issue_date', 'due_date',
                             'given_fine_wt', 'given_touch', 'items'],
    'receive_karigar_order': ['order_id', 'received_items'],
}

for name, expected_args in karigar_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods,
               f"missing from bridge")
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- katti voucher methods exist --")
katti_methods = {
    'get_katti_vch_id': [],
    'save_katti_voucher': ['vch_id', 'total_wt', 'total_packets', 'note', 'items'],
    'get_all_katti_vouchers': [],
    'delete_katti_voucher': ['vch_id'],
    'update_katti_voucher': ['vch_id', 'note', 'items'],
}
for name, expected_args in katti_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods)
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- karigar CRUD via database layer --")
REPO = os.path.abspath(os.path.dirname(__file__))
work = tempfile.mkdtemp(prefix='aurum_karigar_')
os.makedirs(os.path.join(work, 'database'), exist_ok=True)
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
_cwd = os.getcwd()
os.chdir(work)
try:
    sys.path.insert(0, REPO)
    from database.db_manager import DBManager

    db = DBManager()

    db.ensure_karigar_tables()
    check_that("ensure_karigar_tables does not crash", True)

    before = db.karigar_all()
    check_that("karigar_all returns a list", isinstance(before, list))

    db.karigar_add('Test Karigar', '9876543210', 'Gold', 'Workshop Lane', 92.0, 500)

    after = db.karigar_all()
    check_that("list grew by one", len(after) == len(before) + 1,
               f"before={len(before)} after={len(after)}")

finally:
    os.chdir(_cwd)
    shutil.rmtree(work, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
