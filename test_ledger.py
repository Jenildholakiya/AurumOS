import sqlite3
conn = sqlite3.connect('e:/AurumOS_Client/database/aurum_local.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print('=== touch_ledger_details query (ALL, weight) ===')
# This simulates what get_touch_ledger_details does
# OPENING
rows = c.execute('''
    SELECT gr_wt, nt_wt, ls_wt, touch, huid, pcs, it_code, it_name, tag_id, entry_date AS vch_dt
    FROM stock_inventory
    WHERE tag_id LIKE "OPENING-%"
      AND touch IS NOT NULL AND touch > 0
      AND gr_wt > 0
''').fetchall()
print('OPENING rows:', len(rows))
for r in rows:
    print(dict(r))

# INWARD - katti_voucher_items
rows = c.execute('''
    SELECT kvi.it_code, kvi.it_name, kvi.nt_wt AS gr_wt, kvi.touch, kvi.huid, kvi.pcs, kv.vch_id, kv.date AS vch_dt
    FROM katti_voucher_items kvi
    JOIN katti_vouchers kv ON kvi.vch_id = kv.vch_id
''').fetchall()
print('INWARD katti rows:', len(rows))
for r in rows:
    print(dict(r))

# INWARD - stock_inventory NULL/N/A
rows = c.execute('''
    SELECT id, it_code, it_name, tag_id, gr_wt, ls_wt, nt_wt, touch, huid, pcs, entry_date AS vch_dt
    FROM stock_inventory
    WHERE (tag_id IS NULL OR tag_id = '' OR tag_id = 'N/A' OR tag_id = '---' OR tag_id = '-')
      AND tag_id NOT LIKE 'KATTI-%'
      AND tag_id NOT LIKE 'OPENING-%'
      AND gr_wt > 0
''').fetchall()
print('INWARD stock_inventory rows:', len(rows))
for r in rows:
    print(dict(r))

# OUTWARD - sales_history
rows = c.execute('SELECT vch_id, customer, date, items FROM sales_history').fetchall()
print('OUTWARD sales rows:', len(rows))
for r in rows:
    print(dict(r))

conn.close()