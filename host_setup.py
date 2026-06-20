# host_setup.py
# Run this ONCE on the FIRST PC (the Host) of this shop.
import os
os.environ['AURUM_DB_PATH'] = 'database/aurum_local.db'
from database.db_manager import DBManager

db = DBManager()
shop_id = db.get_or_create_shop_id()

with open('shop_id.txt', 'w') as f:
    f.write(shop_id)

print("=" * 50)
print("HOST SETUP COMPLETE")
print("Shop ID:", shop_id)
print("=" * 50)
print("Copy the file 'shop_id.txt' (created next to this script)")
print("to the SAME folder on the Client PC, then run client_setup.py there.")
