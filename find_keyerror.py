with open('E:/AurumOS_Client/database/db_manager.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
for i in range(1865, 2055):
    line = lines[i].rstrip()
    stripped = line.strip()
    if "['" in stripped and '.get(' not in stripped:
        print(f'{i+1}: {line}')