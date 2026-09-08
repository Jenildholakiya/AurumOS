import sqlite3
conn = sqlite3.connect('e:/AurumOS_Client/database/aurum_local.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Check stock_inventory
print('=== stock_inventory ===')
rows = c.execute('SELECT id, it_code, it_name, tag_id, gr_wt, nt_wt, touch, huid, pcs, is_tagged FROM stock_inventory').fetchall()
for r in rows:
    print(dict(r))

# Check katti_vouchers
print('\n=== katti_vouchers ===')
rows = c.execute('SELECT * FROM katti_vouchers').fetchall()
for r in rows:
    print(dict(r))

# Check katti_voucher_items
print('\n=== katti_voucher_items ===')
rows = c.execute('SELECT * FROM katti_voucher_items').fetchall()
for r in rows:
    print(dict(r))

# Check sales_history
print('\n=== sales_history ===')
rows = c.execute('SELECT * FROM sales_history').fetchall()
for r in rows:
    print(dict(r))

# Check opening stock
print('\n=== OPENING stock ===')
rows = c.execute('SELECT * FROM stock_inventory WHERE tag_id LIKE "OPENING-%"').fetchall()
for r in rows:
    print(dict(r))

conn.close()