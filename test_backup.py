# -*- coding: utf-8 -*-
"""Full backup: export everything to zip, restore it back, byte-honest."""
import json
import os
import sqlite3
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, 'E:\\AurumOS_Client')
# NOTE: keep the temp dir in a local, not env: importing main below
# overwrites AURUM_DB_DIR at module level (main.py:164).
_WORK = tempfile.mkdtemp()
os.environ['AURUM_DB_DIR'] = _WORK
os.environ['AURUM_DB_PATH'] = os.path.join(_WORK, 't.db')

from database.db_manager import DBManager

passed, failed = 0, 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}')


db = DBManager()
db.add_stock_entry(it_code='RING22', it_name='Backup Ring', tag_id='BK-TAG-1',
                   gr_wt=5.0, nt_wt=4.8, touch=91.6, pcs=1)
with open(os.path.join(os.environ['AURUM_DB_DIR'], '..', 'x'), 'w'):
    pass

# Fake an AurumAPI shell carrying just the backup methods (no window boot).
import types
import main as _main

api = types.SimpleNamespace()
api.db = db
api._backup_running = False
api._restore_running = False
api._backup_state = {}
api._restore_state = {}
for _name in ('_backup_set', '_restore_set', '_app_base_dir',
              '_export_worker', '_restore_worker', '_audit'):
    setattr(api, _name, getattr(_main.AurumAPI, _name).__get__(api, object)
            if _name != '_audit' else lambda *a, **k: None)

# main._app_base_dir uses sys.executable in frozen mode only; dev returns cwd.
# Point the run at the temp dir by chdir (via _WORK, not the env var).
os.chdir(_WORK)
os.makedirs('exports', exist_ok=True)
os.makedirs('database', exist_ok=True)

print('-- export --')
api._export_worker()
st = api._backup_state
check('export ok', st.get('ok') and st.get('done') and st.get('pct') == 100)
zpath = st.get('file') or ''
check('zip exists', bool(zpath) and os.path.isfile(zpath))
names = []
if zpath and os.path.isfile(zpath):
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        manifest = json.loads(z.read('backup-manifest.json').decode('utf-8'))
check('manifest declares full backup', manifest.get('kind') == 'full-backup')
check('db inside', 'database/aurum_local.db' in names)

print('-- restore guards --')
api._restore_worker(os.path.join('exports', 'nope.zip'))
check('missing zip refused',
      api._restore_state.get('done') and not api._restore_state.get('ok'))
with open(os.path.join('exports', 'fake.zip'), 'w') as f:
    f.write('not a backup')
api._restore_worker(os.path.join('exports', 'fake.zip'))
check('non-backup zip refused',
      api._restore_state.get('done') and not api._restore_state.get('ok'))

print('-- restore round trip --')
# Corrupt the live DB marker: add a row, restore, expect it gone.
with db._get_connection() as conn:
    conn.execute("INSERT INTO clients_master(name, phone) VALUES('ZZZ-TEMP','1')")
    conn.commit()
with db._get_connection() as conn:
    n_before = conn.execute('SELECT COUNT(*) FROM clients_master').fetchone()[0]
api._restore_worker(zpath)
st = api._restore_state
check('restore ok', st.get('ok') and st.get('done'))
check('restart flagged', st.get('needs_restart') is True)
check('safety copy recorded', bool(st.get('backup_of_old')))
with db._get_connection() as conn:
    n_after = conn.execute('SELECT COUNT(*) FROM clients_master').fetchone()[0]
    tags = conn.execute(
        'SELECT COUNT(*) FROM stock_inventory WHERE tag_id=?',
        ('BK-TAG-1',)).fetchone()[0]
check('temp row gone after restore', n_after == n_before - 1)
check('backed-up tag back', tags == 1)
safety = st.get('backup_of_old') or ''
check('safety copy exists', bool(safety) and os.path.isfile(safety))
with sqlite3.connect(safety) as sc:
    check('safety copy intact',
          sc.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
