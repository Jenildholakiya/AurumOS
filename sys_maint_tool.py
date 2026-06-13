# unlock_keygen.py  — run this on your developer PC only
import hashlib, datetime

SECRET_SALT = 'AurumOS@Jewel#2024$Prof'

lock_code = input("Client lock code (8 chars from screen): ").strip().upper()

# Generate for today AND tomorrow (so you can send in advance)
for label, d in [
    ("TODAY    ", datetime.date.today()),
    ("TOMORROW ", datetime.date.today() + datetime.timedelta(days=1)),
]:
    key = hashlib.sha256(
        (lock_code + SECRET_SALT + d.strftime('%Y-%m-%d')).encode()
    ).hexdigest()[:12].upper()
    print(f"{label} ({d})  ->  Unlock Key: {key}")