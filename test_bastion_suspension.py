#!/usr/bin/env python3
"""
Test script to trigger Bastion suspension for testing.
Run this to simulate EXE tampering / integrity attacks.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

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