# -*- coding: utf-8 -*-
"""Karigar register — full flow: master CRUD + issue + receive + guards."""
import os
import tempfile

os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()
os.environ['AURUM_DB_PATH'] = os.path.join(os.environ['AURUM_DB_DIR'], 'karigar_test.db')

from database.db_manager import DBManager

passed, failed = 0, 0
def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")

db = DBManager()

print("-- master CRUD --")
r = db.karigar_add('Mohan Soni', '+91 98765', 'Handmade Gold', 'Shop 12', 0.5, 1.0)
check("add returns success + id", r.get('status') == 'success' and r.get('id'))
kid = r.get('id')
check("blank name refused", db.karigar_add('  ').get('status') == 'error')
all_k = db.karigar_all()
check("list carries metrics + GREEN health",
      any(k['id'] == kid and k['health'] == 'GREEN' and 'metal_given' in k for k in all_k))
check("update works",
      db.karigar_update(kid, 'Mohan Soni Jr', '', '', '', 0.5, 2.0).get('status') == 'success')

print("-- issue validation --")
check("empty items refused",
      db.karigar_create_order(kid, 'LT-1', '2026-09-22', '', 10.0, 100, []).get('status') == 'error')
check("mismatched allocation refused",
      db.karigar_create_order(kid, 'LT-1', '2026-09-22', '', 10.0, 100,
                              [{'name': 'Ring', 'fine_wt': 5.0, 'touch': 76}]).get('status') == 'error')
o = db.karigar_create_order(kid, 'LT-1042', '2026-09-22', '2026-09-30', 10.0, 100,
                            [{'name': 'Ring', 'fine_wt': 6.0, 'touch': 76},
                             {'name': 'Chain', 'fine_wt': 4.0, 'touch': 80}])
check("issue succeeds", o.get('status') == 'success' and o.get('id'))
oid = o.get('id')

print("-- dashboard metrics while ongoing --")
k = [x for x in db.karigar_all() if x['id'] == kid][0]
check("holding reflects issued metal", abs(k['metal_holding'] - 10.0) < 0.001 and k['ongoing'] == 1)
check("all orders listed", len(db.karigar_orders()) >= 1)
check("filtered by karigar", len(db.karigar_orders(kid)) == 1)
check("items carry expected return",
      all(float(i['expected_return_wt'] or 0) > 0 for i in db.karigar_orders(kid)[0]['items']))

print("-- receive --")
items = db.karigar_orders(kid)[0]['items']
recv = [{'item_id': i['id'], 'recv_gross_wt': i['expected_return_wt'], 'recv_touch': i['touch']}
        for i in items]
rr = db.karigar_receive_order(oid, recv)
check("receive succeeds", rr.get('status') == 'success')
check("double receive refused", db.karigar_receive_order(oid, recv).get('status') == 'error')
check("order completed", db.karigar_orders(kid)[0]['status'] == 'completed')
check("delete blocked while orders exist", db.karigar_delete(kid).get('status') == 'error')

print("-- over-allowance health --")
r2 = db.karigar_add('Loss Maker', '', '', '', 0.5, 0.001)
k2 = r2.get('id')
o2 = db.karigar_create_order(k2, 'LT-9', '2026-09-22', '2026-09-30', 10.0, 100,
                             [{'name': 'Bangle', 'fine_wt': 10.0, 'touch': 75}])
items2 = db.karigar_orders(k2)[0]['items']
# Return short on purpose: 90% of expected weight -> real loss.
short = [{'item_id': i['id'], 'recv_gross_wt': round(i['expected_return_wt'] * 0.9, 3),
          'recv_touch': i['touch']} for i in items2]
check("short receive still completes", db.karigar_receive_order(o2.get('id'), short).get('status') == 'success')
h2 = [x for x in db.karigar_all() if x['id'] == k2][0]
check("over-allowance flags AMBER", h2['health'] == 'AMBER' and h2['over_allowed'] > 0)

print("-- clean delete --")
r3 = db.karigar_add('Temp Delete', '', '', '', 0.5, 1.0)
check("order-free karigar removable", db.karigar_delete(r3.get('id')).get('status') == 'success')

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
