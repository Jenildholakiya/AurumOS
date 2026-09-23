# -*- coding: utf-8 -*-
"""Tag scan pricing — per-tag making formula, one truth for the till."""
import os
import sys
import tempfile

sys.path.insert(0, 'E:\\AurumOS_Client')
os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()
os.environ['AURUM_DB_PATH'] = os.path.join(os.environ['AURUM_DB_DIR'], 't.db')

from database.db_manager import DBManager
from main import AurumAPI  # noqa: F401  (formula lives on the class)

passed, failed = 0, 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}')


price = AurumAPI._price_tag_line

print('-- per-gram making --')
line = price({'it_code': 'RING22', 'tag_id': 'T1', 'nt_wt': 5.8,
              'touch': 91.6, 'eff_making_charge': 450,
              'eff_making_type': 'per_gram'}, 7000.0, 1)
check('fine', line['fine'] == round(5.8 * 91.6 / 100, 3))
check('base = wt/10*rate', line['amount'] - line['making'] == round(5.8 / 10 * 7000, 2))
check('making = 450*5.8', line['making'] == round(450 * 5.8, 2))
check('amount = base+making',
      line['amount'] == round(5.8 / 10 * 7000 + 450 * 5.8, 2))
check('type carried', line['making_type'] == 'per_gram'
      and line['making_rate'] == 450)

print('-- percent making --')
line = price({'nt_wt': 10.0, 'touch': 75.0, 'eff_making_charge': 12,
              'eff_making_type': 'percent'}, 7000.0, 1)
base = round(10.0 / 10 * 7000, 2)
check('making = 12% of base', line['making'] == round(base * 12 / 100, 2))
check('not per-gram (would be 120)', line['making'] != 120.0)

print('-- fixed making --')
line = price({'nt_wt': 10.0, 'touch': 75.0, 'eff_making_charge': 1500,
              'eff_making_type': 'fixed'}, 7000.0, 2)
check('making = flat*qty', line['making'] == 3000.0)

print('-- product-master fallback + unknowns --')
line = price({'nt_wt': 0, 'gr_wt': 4.0, 'touch': 80.0,
              'making_charge': 0, 'making_type': ''}, 6000.0, 1)
check('gross fallback weight', line['weight'] == 4.0)
check('unknown type defaults percent', line['making_type'] == 'percent')
check('zero making when none set', line['making'] == 0.0)

print('-- scan_tag end to end --')
db = DBManager()
db.add_stock_entry(it_code='RING22', it_name='Scan Ring', tag_id='SCAN-001',
                   gr_wt=6.0, nt_wt=5.8, touch=91.6, huid='H1',
                   making_type='per_gram', making_charge=450.0, pcs=1)


class FakeAPI(AurumAPI):
    def __init__(self, db):
        self.db = db


api = FakeAPI(db)
res = api.scan_tag('scan-001', 7000.0, 1)
check('exact tag found (case-insensitive)',
      res.get('status') == 'success' and res['line']['tag_id'] == 'SCAN-001')
check('net weight used, not gross', res['line']['weight'] == 5.8)
check('making priced', res['line']['making'] == round(450 * 5.8, 2))
res2 = api.scan_tag('SCAN-001', 0, 1)
check('zero rate still prices making',
      res2.get('status') == 'success' and res2['line']['making'] > 0)
check('missing tag reports not_found',
      api.scan_tag('NOPE-999', 7000.0).get('status') == 'not_found')
check('blank tag refused',
      api.scan_tag('   ', 7000.0).get('status') == 'error')

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
