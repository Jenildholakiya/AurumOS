"""
Run: .\.venv\Scripts\python.exe debug_restore.py
Shows current stock_inventory state to verify restoration is working.
"""
import sqlite3, json, os

db_path = os.path.join(os.getcwd(), 'database', 'aurum_local.db')
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=" * 60)
print("STOCK INVENTORY — ALL ROWS")
print("=" * 60)
rows = conn.execute("""
    SELECT id, it_code, it_name, tag_id, gr_wt, nt_wt, pcs, is_tagged, touch, entry_date
    FROM stock_inventory
    ORDER BY id DESC
""").fetchall()

for r in rows:
    print(f"  id={r['id']} | it_code={r['it_code']} | tag_id={r['tag_id']} | "
          f"gr_wt={r['gr_wt']} | pcs={r['pcs']} | is_tagged={r['is_tagged']}")

print()
print("=" * 60)
print("UCHAK ROWS (tag='N/A', pcs>0, gr_wt=0)")
print("=" * 60)
uchak = conn.execute("""
    SELECT id, it_code, it_name, pcs, gr_wt, tag_id
    FROM stock_inventory
    WHERE (tag_id='N/A' OR tag_id IS NULL OR tag_id='')
      AND pcs > 0
      AND (gr_wt IS NULL OR gr_wt = 0)
""").fetchall()
print(f"Count: {len(uchak)}, Total PCS: {sum(r['pcs'] for r in uchak)}")
for r in uchak:
    print(f"  {r['it_code']} | pcs={r['pcs']}")

print()
print("=" * 60)
print("WEIGHT ROWS (KATTI/N/A, gr_wt>0)")
print("=" * 60)
wt = conn.execute("""
    SELECT id, it_code, it_name, gr_wt, tag_id, touch
    FROM stock_inventory
    WHERE (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%')
      AND gr_wt > 0
""").fetchall()
print(f"Count: {len(wt)}, Total GR_WT: {sum(r['gr_wt'] for r in wt):.3f}g")
for r in wt:
    print(f"  {r['it_code']} | tag={r['tag_id']} | gr_wt={r['gr_wt']}g | touch={r['touch']}")

print()
print("=" * 60)
print("RECENT SALES (last 5)")
print("=" * 60)
sales = conn.execute("""
    SELECT vch_id, customer, date, items, status
    FROM sales_history ORDER BY id DESC LIMIT 5
""").fetchall()
for s in sales:
    items = json.loads(s['items'] or '[]')
    print(f"  {s['vch_id']} | {s['customer']} | {s['date']} | {len(items)} items | {s['status']}")

conn.close()