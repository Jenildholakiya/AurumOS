"""Backup system bridge method tests.

Run from the project root:  python test_backup.py
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


print("-- backup bridge methods exist with correct signatures --")
methods = bridge_methods()

backup_methods = {
    'ensure_backup_structure': [],
    'get_backup_progress': [],
    'export_backup': [],
    'reveal_backup': ['path'],
    'pick_backup_file': [],
    'import_backup': ['zip_path'],
}

for name, expected_args in backup_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods,
               f"missing from bridge")
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- ensure_backup_structure smoke test --")
REPO = os.path.abspath(os.path.dirname(__file__))
work = tempfile.mkdtemp(prefix='aurum_backup_')
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

    result = api.ensure_backup_structure()
    check_that("ensure_backup_structure does not crash", True)

    progress = api.get_backup_progress()
    check_that("get_backup_progress returns something", progress is not None)

finally:
    os.chdir(_cwd)
    shutil.rmtree(work, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
