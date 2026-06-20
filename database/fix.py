import sqlite3
db_path = "aurum_local.db"
conn = sqlite3.connect(db_path)
conn.execute("UPDATE app_config SET value='0' WHERE key='bastion_suspended'")
conn.execute("UPDATE app_config SET value='0' WHERE key='account_locked'")
conn.commit()
conn.close()
print("Suspension cleared.")