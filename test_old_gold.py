import os
import sys
import tempfile

sys.path.insert(0, 'E:\\AurumOS_Client')
os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()
os.environ['AURUM_DB_PATH'] = os.path.join(os.environ['AURUM_DB_DIR'], 't.db')

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

print('-- pos bill with old gold persists everything --')
ok = db.record_sale(
    'R-1001', 'Asha Patel', '9876543210', 'PAID', 0, 10.5, 0, 0, 0,
    7470, 50000, '[]', 'none', 0, 0, 0, 'cash',
    15000.0, 2.5, 'Gold bangle, broken clasp', 75.0, 8000.0)
check('sale recorded', ok)
rows = db.get_old_gold_register()
check('one entry', len(rows) == 1)
e = rows[0] if rows else {}
check('customer', e.get('customer') == 'Asha Patel')
check('mobile', e.get('mobile') == '9876543210')
check('bill no', e.get('vch_id') == 'R-1001')
check('weight', abs(float(e.get('old_gold_wt') or 0) - 2.5) < 0.001)
check('value', abs(float(e.get('old_gold_value') or 0) - 15000.0) < 0.01)
check('desc persisted', e.get('old_gold_desc') == 'Gold bangle, broken clasp')
check('touch persisted', abs(float(e.get('old_gold_touch') or 0) - 75.0) < 0.01)
check('rate persisted', abs(float(e.get('old_gold_rate') or 0) - 8000.0) < 0.01)
check('date present', bool(e.get('date')))
check('time present', bool(e.get('time_stamp')))
check('bill total present', abs(float(e.get('total_amount') or 0) - 50000.0) < 0.01)

print('-- old bills without detail still list --')
db.record_sale('R-1002', 'Walk-in', '', 'PAID', 0, 5.0, 0, 0, 0,
               7470, 20000, '[]', 'none', 0, 0, 0, 'cash', 5000.0, 1.0)
rows2 = db.get_old_gold_register()
check('two entries', len(rows2) == 2)
check('newest first', rows2[0].get('vch_id') == 'R-1002')

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
