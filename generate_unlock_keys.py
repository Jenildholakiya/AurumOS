#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AurumOS One-Time Unlock Key Generator
======================================
Generates BOTH Regular (login lock) and Bastion (AI suspension) unlock keys.
Each key is ONE-TIME USE only — after verification the nonce increments and
the old key stops working.

Usage:
  python generate_unlock_keys.py

The script reads the lock code from the screen and produces keys for
yesterday / today / tomorrow with the current nonce.
"""

import hashlib
import os
import sys
from datetime import datetime, timedelta

# ── Same salts used in db_manager.py ────────────────────────────
REGULAR_SALT = 'AurumOS@Jewel#2024$Prof'
BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'

# ── Nonce file (shared with the app) ───────────────────────────
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database')
NONCE_FILE = os.path.join(DB_DIR, '.unlock_nonce')


def _get_nonce():
    """Read the current nonce from disk. Returns 0 if missing/corrupt."""
    try:
        if os.path.isfile(NONCE_FILE):
            with open(NONCE_FILE, 'r') as f:
                return int(f.read().strip())
    except (ValueError, IOError, OSError):
        pass
    return 0


def _make_regular(lc, date_str, nonce):
    """12-char regular unlock key."""
    return hashlib.sha256(
        (lc + REGULAR_SALT + date_str + str(nonce)).encode('utf-8')
    ).hexdigest()[:12].upper()


def _make_bastion(lc, date_str, nonce):
    """16-char bastion unlock key."""
    return hashlib.sha256(
        (lc + BASTION_SALT + date_str + str(nonce)).encode('utf-8')
    ).hexdigest()[:16].upper()


def main():
    print()
    print('=' * 62)
    print('   AurumOS  |  One-Time Unlock Key Generator')
    print('=' * 62)
    print()

    # ── Get lock code ──────────────────────────────────────────
    lock_code = input('  Enter lock code from the locked screen : ').strip()
    if not lock_code:
        print('  ERROR: No lock code entered.'); sys.exit(1)
    lock_code = lock_code.upper()[:8]

    # ── Read nonce ─────────────────────────────────────────────
    nonce = _get_nonce()

    # ── Timezone helpers (IST) ─────────────────────────────────
    now_utc  = datetime.utcnow()
    now_ist  = now_utc + timedelta(hours=5, minutes=30)
    now_loc  = datetime.now()

    print()
    print('  ' + '-' * 58)
    print(f'  Lock Code   : {lock_code}')
    print(f'  Use Counter : {nonce}')
    print(f'  IST Now     : {now_ist.strftime("%Y-%m-%d %H:%M:%S IST")}')
    print(f'  Local Time  : {now_loc.strftime("%Y-%m-%d %H:%M:%S")}')
    print('  ' + '-' * 58)
    print()

    # ── Generate keys for 3 days × both salts ──────────────────
    for label, dt in [('Yesterday', now_ist - timedelta(days=1)),
                      ('Today    ', now_ist),
                      ('Tomorrow ', now_ist + timedelta(days=1))]:
        ds = dt.strftime('%Y-%m-%d')
        reg = _make_regular(lock_code, ds, nonce)
        bas = _make_bastion(lock_code, ds, nonce)
        print(f'  [{label}]  {ds}')
        print(f'      Regular (12-char) : {reg}')
        print(f'      Bastion (16-char) : {bas}')
        print()

    # ── Summary ────────────────────────────────────────────────
    print('  ' + '=' * 58)
    print('  Nonce (use counter) :', nonce)
    print()
    print('  RULES:')
    print('  - Regular key unlocks the LOGIN lock screen (12 chars)')
    print('  - Bastion key unlocks the AI SUSPENSION screen (16 chars)')
    print('  - Each key works ONLY ONCE')
    print('  - After use the nonce increments automatically')
    print('  - Old keys stop working immediately')
    print('  ' + '=' * 58)
    print()


if __name__ == '__main__':
    main()
