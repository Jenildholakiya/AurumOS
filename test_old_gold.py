"""Old gold register tests.

Run from the project root:  python test_old_gold.py
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


print("-- old gold bridge method exists --")
methods = bridge_methods()
check_that("get_old_gold_register exists", 'get_old_gold_register' in methods)
check("get_old_gold_register signature", methods.get('get_old_gold_register'), ['limit'])

print("\n-- old gold register via database layer --")
REPO = os.path.abspath(os.path.dirname(__file__))
work = tempfile.mkdtemp(prefix='aurum_oldgold_')
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

    result = api.get_old_gold_register(100)
    check_that("get_old_gold_register returns a list", isinstance(result, list),
               f"got {type(result).__name__}")
    check_that("empty DB returns empty list", len(result) == 0,
               f"got {len(result)} rows")

finally:
    os.chdir(_cwd)
    shutil.rmtree(work, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
