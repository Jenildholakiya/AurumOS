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
    import main as _main

    db = DBManager()
    api = _main.AurumAPI.__new__(_main.AurumAPI)
    api.db = db
    api._window = None
    api._session_username = 'owner'

    before = api.get_all_karigars()
    check_that("get_all_karigars returns a list", isinstance(before, list))

    added = api.add_karigar('Test Karigar', '9876543210', 'Gold', 'Workshop Lane', 92.0, 500)
    check_that("add_karigar returns a result", added is not None)

    after = api.get_all_karigars()
    check_that("list grew by one", len(after) == len(before) + 1,
               f"before={len(before)} after={len(after)}")

    kid = after[-1].get('id') or after[-1].get('kid') if after else None
    if kid:
        updated = api.update_karigar(kid, 'Updated Karigar', '1111111111',
                                     'Silver', 'New Addr', 91.5, 600)
        check_that("update_karigar succeeds", updated is not None)

        deleted = api.delete_karigar(kid)
        check_that("delete_karigar succeeds", deleted is not None)

        final = api.get_all_karigars()
        check("back to original count", len(final), len(before))
    else:
        FAIL.append("could not get karigar id for update/delete tests")

    orders = api.get_karigar_orders(0)
    check_that("get_karigar_orders returns a list", isinstance(orders, list))

finally:
    os.chdir(_cwd)
    shutil.rmtree(work, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
