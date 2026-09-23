#!/usr/bin/env python3
"""
Test script to trigger Bastion suspension for testing.
Run this to simulate EXE tampering / integrity attacks.

SAFETY: without --force this suspends a THROWAWAY database in the temp
folder, not the shop's real one. Pass --force only when you deliberately
want to lock the install in front of you (to rehearse the unlock flow).
"""
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(__file__))

FORCE = '--force' in sys.argv[1:]
if not FORCE:
    os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()
    os.environ['AURUM_DB_PATH'] = os.path.join(os.environ['AURUM_DB_DIR'],
                                               'suspension_test.db')
    print('[SAFE] no --force: using throwaway DB, shop data untouched')

from database.db_manager import DBManager

def trigger_suspension():
    db = DBManager()

    # Simulate EXE hash mismatch (tampering detected)
    db.bastion_suspend(
        attack_type='EXE_HASH_MISMATCH',
        detail='EXE hash mismatch: expected=abc123, actual=tampered456'
    )

    # Verify suspension
    status = db.bastion_get_status()
    print(f"Suspended: {status.get('suspended')}")
    print(f"Reason: {status.get('reason')}")
    print(f"Attack Type: {status.get('attack_type')}")
    print(f"Detail: {status.get('detail')}")

if __name__ == '__main__':
    trigger_suspension()