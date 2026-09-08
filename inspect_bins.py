import sqlite3
import json

conn = sqlite3.connect('database/aurum_local.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
print('=== stock_inventory rows with HUID ===')
rows = c.execute("SELECT id,it_code,it_name,tag_id,gr_wt,nt_wt,touch,huid,is_tagged FROM stock_inventory WHERE huid IS NOT NULL AND TRIM(huid) NOT IN ('','-','None','N/A') ORDER BY id").fetchall()
print('count', len(rows))
for r in rows:
    print({k: r[k] for k in r.keys()})

print('\n=== analytics payload bins query ===')
rows = c.execute("SELECT UPPER(TRIM(huid)) as bin_id, SUM(nt_wt) as weight, json_group_array(json_object('it_name', it_name, 'nt_wt', nt_wt, 'it_code', it_code)) as items_json FROM stock_inventory WHERE huid IS NOT NULL AND TRIM(huid) NOT IN ('','-','None','N/A') AND (tag_id IS NULL OR tag_id='' OR tag_id='N/A' OR tag_id LIKE 'KATTI-%') AND nt_wt > 0 GROUP BY UPPER(TRIM(huid))").fetchall()
print('count', len(rows))
for r in rows:
    print({'bin_id': r['bin_id'], 'weight': r['weight'], 'items': json.loads(r['items_json'])})

print('\n=== tagged piece bins query ===')
rows = c.execute("SELECT UPPER(TRIM(huid)) as bin_id, SUM(nt_wt) as weight, json_group_array(json_object('it_name', it_name, 'nt_wt', nt_wt, 'it_code', it_code)) as items_json FROM stock_inventory WHERE huid IS NOT NULL AND TRIM(huid) NOT IN ('','-','None','N/A') AND tag_id NOT IN ('N/A','') AND tag_id NOT LIKE 'KATTI-%' AND is_tagged=1 GROUP BY UPPER(TRIM(huid))").fetchall()
print('count', len(rows))
for r in rows:
    print({'bin_id': r['bin_id'], 'weight': r['weight'], 'items': json.loads(r['items_json'])})

conn.close()
