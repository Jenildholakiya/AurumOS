# force_clear_suspension.py
# Directly clears ANY current BASTION suspension on this PC's real DB.
# No key needed -- emergency reset.
import os
os.environ['AURUM_DB_PATH'] = 'database/aurum_local.db'
from database.db_manager import DBManager

db = DBManager()

print("Before:", db.bastion_get_status())

with db._get_connection() as conn:
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_suspended','0')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('bastion_record','')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('account_locked','0')")
    conn.execute("INSERT OR REPLACE INTO app_config(key,value) VALUES('login_attempts','0')")
    conn.commit()

print("After:", db.bastion_get_status())
print("DONE. Restart AurumOS.exe.")
