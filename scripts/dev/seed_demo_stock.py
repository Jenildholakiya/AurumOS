# -*- coding: utf-8 -*-
"""Seed a few demo tagged pieces + one katti batch into the workspace DB.

Idempotent: skips tag_ids / voucher numbers that already exist.
Run:  python seed_demo_stock.py
"""
import os
import sys

# scripts/dev/ -> project root, so `database` imports resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from database.db_manager import DBManager

# Demo pieces start with blank HUIDs: they are genuinely waiting, so a
# fresh install shows the hallmark flow honestly instead of reading bin
# labels and almost-HUIDs as hallmarked.
TAGGED = [
    dict(it_code='RING22', it_name='Classic Gold Ring', tag_id='DEMO-TAG-001',
         gr_wt=6.250, nt_wt=5.800, touch=91.6, wastage=0.5, huid='',
         category='Rings', karat='22K', making_type='per_gram',
         making_charge=450.0, pcs=1),
    dict(it_code='CHAIN22', it_name='Link Gold Chain', tag_id='DEMO-TAG-002',
         gr_wt=12.400, nt_wt=12.100, touch=91.6, wastage=0.5, huid='',
         category='Chains', karat='22K', making_type='per_gram',
         making_charge=380.0, pcs=1),
    dict(it_code='BANGL22', it_name='Kada Bangle', tag_id='DEMO-TAG-003',
         gr_wt=18.750, nt_wt=18.200, touch=91.6, wastage=0.6, huid='',
         category='Bangles', karat='22K', making_type='per_gram',
         making_charge=350.0, pcs=1),
    dict(it_code='PEND18', it_name='Rose Pendant', tag_id='DEMO-TAG-004',
         gr_wt=3.120, nt_wt=2.900, touch=75.0, wastage=0.4, huid='',
         category='Pendants', karat='18K', making_type='fixed',
         making_charge=1200.0, pcs=1),
    dict(it_code='EAR22', it_name='Jhumka Earrings', tag_id='DEMO-TAG-005',
         gr_wt=7.800, nt_wt=7.100, touch=91.6, wastage=0.5, huid='',
         category='Earrings', karat='22K', making_type='per_gram',
         making_charge=500.0, pcs=2),
]

KATTI_VCH = '9001'
KATTI_ITEMS = [
    dict(it_code='60', name='60 Touch Loose', touch=60.0, weight=25.500,
         packets=4, box='B-101'),
    dict(it_code='68', name='68 Touch Loose', touch=68.0, weight=18.250,
         packets=3, box='B-101'),
    dict(it_code='75', name='75 Touch Loose', touch=75.0, weight=10.000,
         packets=2, box='B-102'),
]

# Bin Viewer lists stock whose HUID field holds the bin code, so a few
# pieces live in B-001 itself.
BIN_PIECES = [
    dict(it_code='RING22', it_name='Bin Ring A', tag_id='DEMO-TAG-006',
         gr_wt=4.800, nt_wt=4.500, touch=91.6, wastage=0.5, huid='B-001',
         category='Rings', karat='22K', making_type='per_gram',
         making_charge=420.0, pcs=1),
    dict(it_code='PEND22', it_name='Bin Pendant B', tag_id='DEMO-TAG-007',
         gr_wt=2.600, nt_wt=2.400, touch=91.6, wastage=0.4, huid='B-001',
         category='Pendants', karat='22K', making_type='per_gram',
         making_charge=460.0, pcs=1),
    dict(it_code='EAR18', it_name='Bin Studs C', tag_id='DEMO-TAG-008',
         gr_wt=1.900, nt_wt=1.750, touch=75.0, wastage=0.3, huid='B-001',
         category='Earrings', karat='18K', making_type='fixed',
         making_charge=800.0, pcs=2),
]


def main():
    db = DBManager()
    with db._get_connection() as conn:
        have_tags = {r[0] for r in conn.execute(
            "SELECT tag_id FROM stock_inventory").fetchall()}
        have_vch = conn.execute(
            "SELECT vch_id FROM katti_vouchers WHERE vch_id=?",
            (KATTI_VCH,)).fetchone()

    added_tags = 0
    for item in TAGGED + BIN_PIECES:
        if item['tag_id'] in have_tags:
            print(f"  skip {item['tag_id']} (already in stock)")
            continue
        if db.add_stock_entry(**item):
            with db._get_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM stock_inventory WHERE tag_id=?",
                    (item['tag_id'],)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE stock_inventory SET is_tagged=1 WHERE id=?",
                        (row[0],))
                    conn.commit()
            added_tags += 1
            print(f"  + tagged {item['tag_id']} {item['it_name']} "
                  f"{item['nt_wt']}g @{item['touch']}")
        else:
            print(f"  FAILED {item['tag_id']}")

    if have_vch:
        print(f"  skip katti {KATTI_VCH} (already posted)")
    else:
        total_wt = sum(i['weight'] for i in KATTI_ITEMS)
        total_pk = sum(i['packets'] for i in KATTI_ITEMS)
        if db.save_katti_batch(KATTI_VCH, total_wt, total_pk,
                               note='Demo batch', items=KATTI_ITEMS,
                               box_id='B-101'):
            print(f"  + katti {KATTI_VCH}: {total_pk} packets, "
                  f"{total_wt:.3f}g")
        else:
            print(f"  FAILED katti {KATTI_VCH}")

    print(f"done: {added_tags} tagged pieces added")


if __name__ == '__main__':
    main()
