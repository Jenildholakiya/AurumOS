import sys
sys.path.insert(0, '.')
from database.db_manager import DBManager
db = DBManager()

# Test get_inventory_stats
stats = db.get_inventory_stats()
print('=== get_inventory_stats ===')
for k, v in stats.items():
    print(f'  {k}: {v}')

# Test get_analytics_payload
analytics = db.get_analytics_payload()
print()
print('=== get_analytics_payload ===')
print(f'  status: {analytics.get("status")}')
print(f'  bins count: {len(analytics.get("bins", []))}')
for b in analytics.get('bins', []):
    print(f'    {b["bin_id"]}: {b["weight"]}g, items: {len(b["items"])}')

# Test get_touch_ledger_details
ledger = db.get_touch_ledger_details('ALL', 'weight', '', '')
print()
print('=== get_touch_ledger_details (ALL, weight) ===')
opn = sum(r['gr_wt'] for r in ledger if r['txn_type'] == 'OPENING')
inw = sum(r['gr_wt'] for r in ledger if r['txn_type'] == 'IN')
out = sum(r['gr_wt'] for r in ledger if r['txn_type'] == 'OUT')
print(f'  OPENING: {opn}')
print(f'  IN: {inw}')
print(f'  OUT: {out}')
print(f'  CLOSING: {opn + inw - out}')