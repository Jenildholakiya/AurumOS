import sqlite3
import json

conn = sqlite3.connect('database/aurum_local.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
rows = c.execute("SELECT id,it_code,tag_id,it_name,gr_wt,nt_wt,huid,pcs FROM stock_inventory WHERE it_code LIKE '%68%' OR tag_id LIKE '%68%' ORDER BY id").fetchall()
print('Found rows:', len(rows))
print(json.dumps([dict(r) for r in rows], indent=2))

canonical = c.execute("SELECT id,it_code,gr_wt FROM stock_inventory WHERE it_code='KATTI-68' LIMIT 1").fetchone()
if canonical:
    canonical_id = canonical['id']
    canonical_weight = canonical['gr_wt'] or 0.0
    duplicates = c.execute(
        "SELECT id,it_code,gr_wt FROM stock_inventory WHERE id!=? AND (it_code='68' OR it_code='KATTI-RESTORE-68')",
        (canonical_id,)
    ).fetchall()
    if duplicates:
        print('Merging duplicates into canonical row id', canonical_id)
        total = canonical_weight
        for dup in duplicates:
            dup_weight = dup['gr_wt'] or 0.0
            total += dup_weight
            print(f"  Deleting duplicate id={dup['id']} code={dup['it_code']} gr_wt={dup_weight}")
            c.execute("DELETE FROM stock_inventory WHERE id=?", (dup['id'],))
        c.execute("UPDATE stock_inventory SET gr_wt=?,nt_wt=? WHERE id=?", (round(total, 3), round(total, 3), canonical_id))
        conn.commit()
        print('Canonical row updated to', round(total, 3))
    else:
        print('No duplicate rows to merge.')
else:
    print('Canonical KATTI-68 row not found; no merge performed.')

conn.close()
