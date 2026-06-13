# -*- coding: utf-8 -*-
"""
AurumOS Hardware DNA Check
===========================
Run this on client PC to see their hardware fingerprint components.
Send output to Professor to debug lock issues.

Usage:
  python dna_check.py
  OR double-click dna_check.py on client PC
"""
import uuid, hashlib, subprocess, sys, os
from datetime import date

def _wmic(query):
    try:
        out = subprocess.check_output(
            'wmic ' + query + ' get /value',
            shell=True, timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000
        ).decode('ascii', errors='ignore')
        vals = [
            l.split('=',1)[1].strip()
            for l in out.splitlines()
            if '=' in l and l.split('=',1)[1].strip()
            and l.split('=',1)[1].strip() not in ('','None','To Be Filled By O.E.M.')
        ]
        return vals[0] if vals else '[NOT FOUND]'
    except Exception as e:
        return f'[ERROR: {e}]'

def get_dna():
    mac      = str(uuid.getnode())
    cpu_id   = _wmic('cpu get ProcessorId')
    disk_id  = _wmic('diskdrive get SerialNumber')
    bios_id  = _wmic('bios get SerialNumber')
    board_id = _wmic('baseboard get SerialNumber')

    raw = '|'.join([mac, cpu_id, disk_id, bios_id, board_id])
    dna = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]

    print("=" * 55)
    print("  AurumOS Hardware DNA Report")
    print("=" * 55)
    print(f"  Date       : {date.today()}")
    print(f"  MAC Addr   : {mac}")
    print(f"  CPU ID     : {cpu_id}")
    print(f"  Disk SN    : {disk_id}")
    print(f"  BIOS SN    : {bios_id}")
    print(f"  Board SN   : {board_id}")
    print("-" * 55)
    print(f"  FINGERPRINT: {dna}")
    print(f"  LOCK CODE  : {dna[:8].upper()}")
    print("=" * 55)
    print()
    print("  Send FINGERPRINT to your AurumOS provider.")
    print()
    return dna

if __name__ == '__main__':
    get_dna()
    input("  Press Enter to close...")