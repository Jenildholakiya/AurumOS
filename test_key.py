import hashlib as _hl
from datetime import datetime as _dt, timedelta as _td

lock_code = 'FA20DBA9'
REGULAR_SALT = 'AurumOS@Jewel#2024$Prof'

# Current time in IST, UTC, local
now_ist = _dt.utcnow() + _td(hours=5, minutes=30)
now_utc = _dt.utcnow()
now_loc = _dt.now()

regular_dates = []
for base in [now_ist, now_utc, now_loc]:
    for delta in [0, -1, 1]:
        ds = (base + _td(days=delta)).strftime('%Y-%m-%d')
        if ds not in regular_dates:
            regular_dates.append(ds)

print('Regular dates:', regular_dates)

# Test with a sample key
entered = 'C0F7ABCDEF12'  # 12 chars
print(f'Entered length: {len(entered)}')

for date_str in regular_dates:
    candidate = _hl.sha256(
        (lock_code + REGULAR_SALT + date_str).encode('utf-8')
    ).hexdigest()[:12].upper()
    print(f'  {date_str}: {candidate} == {entered} ? {candidate == entered}')