import os, tempfile, importlib.util, hashlib
from datetime import datetime

tmpdir = tempfile.mkdtemp()
os.environ['AURUM_DB_PATH'] = os.path.join(tmpdir, 'test.db')

spec = importlib.util.spec_from_file_location("db_manager", "database/db_manager.py")
db_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db_manager)
db = db_manager.DBManager()

print("BEFORE:", db.bastion_get_status())

db.bastion_suspend('exe_tamper', 'QA test')

print("AFTER:", db.bastion_get_status())
print("Login while suspended:", db.authenticate_user('owner', 'anything'))

fp = db._machine_fingerprint()[:8].upper()
key = hashlib.sha256((fp + 'BASTION@AurumOS#Jenil$2024!Admin' + datetime.now().strftime('%Y-%m-%d')).encode()).hexdigest()[:16].upper()
print("Unlock:", db.bastion_clear(key, fp))
print("AFTER CLEAR:", db.bastion_get_status())