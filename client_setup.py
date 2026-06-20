# client_setup.py
# Run this on every OTHER PC (Client) that should join the Host's shop.
# Requires shop_id.txt copied here from the Host PC first.
import os

if not os.path.exists('shop_id.txt'):
    print("ERROR: shop_id.txt not found.")
    print("Copy shop_id.txt from the Host PC into this same folder, then run again.")
    raise SystemExit(1)

with open('shop_id.txt', 'r') as f:
    shop_id = f.read().strip()

if not shop_id:
    print("ERROR: shop_id.txt is empty.")
    raise SystemExit(1)

os.environ['AURUM_DB_PATH'] = 'database/aurum_local.db'
from database.db_manager import DBManager

db = DBManager()
result = db.set_shop_id(shop_id)

print("=" * 50)
print("CLIENT SETUP COMPLETE")
print("Result:", result)
print("=" * 50)
print("Now restart AurumOS.exe on this PC.")
