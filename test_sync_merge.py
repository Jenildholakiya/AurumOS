"""Merge safety checks for two PCs that billed while apart.

Run from the project root:  python test_sync_merge.py

Two PCs working offline both reach VCH-001 and katti 0001, because each
numbers bills from its own local maximum. This pins down what must happen
when those rows finally meet:

  * neither bill is lost, and the incoming one is renumbered
  * a renumbered voucher keeps its own line items
  * one real customer entered twice stays one customer
  * every clash leaves a conflict row the owner can see
  * replaying the same sync payload changes nothing
"""
import os, sys, json, shutil, tempfile

sys.path.insert(0, os.path.abspath('.'))
work = tempfile.mkdtemp(prefix='aurum_merge_')
FAIL = []

def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)

def make_pc(name, device_id):
    d = os.path.join(work, name, 'database')
    os.makedirs(d, exist_ok=True)
    os.environ['AURUM_DB_PATH'] = os.path.join(d, 'aurum_local.db')
    for m in [m for m in list(sys.modules) if m.startswith('database')]:
        del sys.modules[m]
    from database.db_manager import DBManager
    db = DBManager()
    with db._get_connection() as c:
        c.execute("INSERT OR REPLACE INTO device_registry(id,device_id,device_name) VALUES(1,?,?)",
                  (device_id, name))
        c.commit()
    return db

def q(db, sql, params=()):
    with db._get_connection() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]

def bill(db, vch, cust, amt):
    with db._get_connection() as c:
        c.execute("INSERT INTO sales_history (vch_id,customer,status,total_amount,items) VALUES (?,?,?,?,?)",
                  (vch, cust, 'PAID', amt, json.dumps([])))
        c.commit()
    db.stamp_row_for_sync('sales_history', vch, pk_col='vch_id')

def katti(db, vch, note, items):
    with db._get_connection() as c:
        c.execute("INSERT INTO katti_vouchers (vch_id,total_weight,total_packets,note) VALUES (?,?,?,?)",
                  (vch, sum(i[1] for i in items), len(items), note))
        for nm, wt in items:
            cur = c.execute("INSERT INTO katti_voucher_items (vch_id,it_name,nt_wt,touch,pcs) VALUES (?,?,?,?,?)",
                            (vch, nm, wt, 91.6, 1))
            c.execute("UPDATE katti_voucher_items SET device_id=?, sync_version=?, local_id=? WHERE id=?",
                      (db.get_or_create_device_id(), cur.lastrowid, cur.lastrowid, cur.lastrowid))
        c.commit()
    db.stamp_row_for_sync('katti_vouchers', vch, pk_col='vch_id')

def customer(db, name, phone):
    with db._get_connection() as c:
        cur = c.execute("INSERT INTO clients_master (name,phone) VALUES (?,?)", (name, phone))
        c.commit()
    db.stamp_row_for_sync('clients_master', cur.lastrowid)

owner    = make_pc('owner',    'DEV-OWNER-11AA')
terminal = make_pc('terminal', 'DEV-TERMINAL-99ZZ')

# Both PCs work offline and independently reach the same numbers.
bill(owner,    'VCH-001', 'Owner walk-in',  10000.0)
bill(terminal, 'VCH-001', 'Staff customer', 87500.0)
bill(terminal, 'VCH-002', 'Second staff sale', 4200.0)
katti(owner,    '0001', 'Owner batch', [('Chain', 12.5)])
katti(terminal, '0001', 'Staff batch', [('Ring', 3.1), ('Bangle', 8.4)])
customer(owner,    'Ramesh Kumar', '900000001')
customer(terminal, 'Ramesh Kumar', '900000002')

changes = terminal.get_changes_since('DEV-OWNER-11AA', {}).get('changes', {})
owner.apply_incoming_rows('DEV-TERMINAL-99ZZ', changes)

print("\n-- colliding bill is kept, not dropped --")
sales = q(owner, "SELECT vch_id, customer, total_amount FROM sales_history ORDER BY vch_id")
check("owner holds 3 bills", len(sales), 3)
check("staff bill survived",
      [b['total_amount'] for b in sales if b['customer'] == 'Staff customer'], [87500.0])
check("original owner bill untouched",
      [b['vch_id'] for b in sales if b['customer'] == 'Owner walk-in'], ['VCH-001'])
staff_vch = [b['vch_id'] for b in sales if b['customer'] == 'Staff customer'][0]
check("staff bill was renumbered", staff_vch != 'VCH-001', True)
check("non-clashing bill kept its number",
      [b['vch_id'] for b in sales if b['customer'] == 'Second staff sale'], ['VCH-002'])

print("\n-- voucher line items follow the renumbered parent --")
kv = q(owner, "SELECT vch_id, note FROM katti_vouchers ORDER BY vch_id")
check("both katti vouchers present", len(kv), 2)
staff_kv = [r['vch_id'] for r in kv if r['note'] == 'Staff batch'][0]
lines = q(owner, "SELECT it_name FROM katti_voucher_items WHERE vch_id=? ORDER BY it_name", (staff_kv,))
check("staff voucher kept both its lines", [l['it_name'] for l in lines], ['Bangle', 'Ring'])
owner_lines = q(owner, "SELECT it_name FROM katti_voucher_items WHERE vch_id='0001'")
check("owner voucher did not absorb staff lines", [l['it_name'] for l in owner_lines], ['Chain'])

print("\n-- same customer on both PCs is merged, not duplicated --")
cust = q(owner, "SELECT name, phone FROM clients_master WHERE name='Ramesh Kumar'")
check("customer stored once", len(cust), 1)
check("local copy kept", cust[0]['phone'], '900000001')

print("\n-- every anomaly is visible to the owner --")
con = q(owner, "SELECT conflict_type FROM sync_conflicts ORDER BY conflict_type")
types = sorted({c['conflict_type'] for c in con})
check("clash types recorded", types, ['duplicate_record', 'duplicate_voucher_id'])
check("one conflict per clashing voucher",
      len([c for c in con if c['conflict_type'] == 'duplicate_voucher_id']), 2)

print("\n-- re-running the same sync changes nothing --")
before = q(owner, "SELECT vch_id FROM sales_history ORDER BY vch_id")
owner.apply_incoming_rows('DEV-TERMINAL-99ZZ', changes)
after = q(owner, "SELECT vch_id FROM sales_history ORDER BY vch_id")
check("no duplicate rows on replay", after, before)

shutil.rmtree(work, ignore_errors=True)
print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
