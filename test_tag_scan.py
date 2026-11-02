"""Tag scanning and audit bridge method tests.

Run from the project root:  python test_tag_scan.py
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


print("-- tag scan bridge methods exist with correct signatures --")
methods = bridge_methods()

tag_methods = {
    'scan_tag': ['tag_id', 'gold_rate_10g', 'qty'],
    'tag_audit_start': ['started_by', 'touch_filter'],
    'tag_audit_scan': ['session_id', 'tag_id', 'book_tags'],
    'tag_audit_get_status': ['session_id'],
    'tag_audit_mark_absent': ['session_id', 'tag_id', 'reason', 'marked_by'],
    'tag_audit_remove_absence': ['session_id', 'tag_id'],
    'tag_audit_remove_scan': ['session_id', 'tag_id'],
    'tag_audit_close': ['session_id', 'closed_by'],
    'tag_audit_start_snapshot': ['session_id', 'touch_filter'],
    'tag_audit_get_sessions': ['limit'],
    'tag_audit_get_available_touches': [],
    'tag_audit_delete_session': ['session_id'],
}

for name, expected_args in tag_methods.items():
    check_that(f"{name} exists on AurumAPI", name in methods,
               f"missing from bridge")
    if name in methods:
        check(f"{name} signature", methods[name], expected_args)

print("\n-- tag audit DB smoke test --")
REPO = os.path.abspath(os.path.dirname(__file__))
work = tempfile.mkdtemp(prefix='aurum_tagscan_')
os.makedirs(os.path.join(work, 'database'), exist_ok=True)
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
_cwd = os.getcwd()
os.chdir(work)
try:
    sys.path.insert(0, REPO)
    from database.db_manager import DBManager

    db = DBManager()

    db.tag_audit_init_tables()
    check_that("tag_audit_init_tables does not crash", True)

    sessions = db.tag_audit_get_sessions(10)
    check_that("tag_audit_get_sessions callable", sessions is not None,
               f"got {type(sessions).__name__}")

    touches = db.tag_audit_get_available_touches()
    check_that("tag_audit_get_available_touches callable", touches is not None,
               f"got {type(touches).__name__}")

finally:
    os.chdir(_cwd)
    shutil.rmtree(work, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
