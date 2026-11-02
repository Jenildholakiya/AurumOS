"""Finance desk / ledger bridge method tests.

Run from the project root:  python test_finance.py
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


print("-- finance/ledger bridge methods exist with correct signatures --")
methods = bridge_methods()

finance_methods = {
    'get_ledger_summary': [],
    'get_full_ledger_stream': [],
    'post_to_ledger': ['entry_data'],
    'get_ledger_statement': ['client_name'],
    'get_stock_ledger': [],
    'get_stock_ledger_by_date': ['d'],
    'get_ledger_dates': [],
    'get_touch_ledger_details': ['touch_value', 'mode', 'from_date', 'to_date'],
}

for name, expected_args in finance_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods,
               f"missing from bridge")
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- finance DB smoke test --")
REPO = os.path.abspath(os.path.dirname(__file__))
work = tempfile.mkdtemp(prefix='aurum_finance_')
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

    accounts = db.finance_accounts()
    check_that("finance_accounts returns a list", isinstance(accounts, list))

    cash = db.finance_cash_position()
    check_that("finance_cash_position returns something", cash is not None)

    daybook = db.finance_daybook(limit=10)
    check_that("finance_daybook returns a list", isinstance(daybook, list))

    tb = db.finance_trial_balance()
    check_that("finance_trial_balance returns a list", isinstance(tb, list))

    vno = db.finance_next_voucher_no('receipt')
    check_that("finance_next_voucher_no returns a string", isinstance(vno, str),
               f"got {type(vno).__name__}")

finally:
    os.chdir(_cwd)
    shutil.rmtree(work, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
