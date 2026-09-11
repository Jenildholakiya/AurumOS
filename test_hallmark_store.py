"""The hallmarking register against a real database.

Run from the project root:  python test_hallmark_store.py

test_hallmark.py covers the arithmetic. This covers the half that decides
what a shop can legally sell: that a piece away at the centre is not offered
for sending twice, that a piece which passed comes back saleable, that one
which failed does not, and that cancelling a lot releases its pieces rather
than stranding them.

Runs against a throwaway database in the system temp directory. The shop's
own file is never opened.
"""

import os
import shutil
import sqlite3
import sys
import tempfile

FAIL = 0


def chk(label, cond):
    global FAIL
    if not cond:
        FAIL += 1
    print('  [%s] %s' % ('PASS' if cond else 'FAIL', label))


def head(t):
    print('\n-- %s --' % t)


ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix='aurum_hm_')
os.environ['AURUM_DB_PATH'] = os.path.join(TMP, 'aurum_local.db')

from database.db_manager import DBManager                      # noqa: E402
from core import hallmark as H                                 # noqa: E402


def stock(db, tag, name, gr_wt, karat, huid=''):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stock_inventory"
            "(tag_id,it_name,it_code,gr_wt,nt_wt,touch,huid,is_tagged,entry_date)"
            " VALUES(?,?,?,?,?,?,?,1,'2026-09-01')",
            (tag, name, 'NK', gr_wt, gr_wt, 91.6, huid))
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(stock_inventory)").fetchall()]
        if 'karat' in cols:
            conn.execute("UPDATE stock_inventory SET karat=? WHERE tag_id=?",
                         (karat, tag))
        conn.commit()


def tags_pending(db):
    return sorted(r['tag_id'] for r in db.hm_pending_stock())


def main():
    db = DBManager()
    db.ensure_hallmark_tables()

    head('a shop with four untagged-for-hallmark pieces')
    for t, n, w in (('T-1', 'Chain', 8.0), ('T-2', 'Ring', 4.0),
                    ('T-3', 'Bangle', 12.0), ('T-4', 'Kada', 20.0)):
        stock(db, t, n, w, '22K')
    stock(db, 'T-9', 'Already marked', 6.0, '22K', huid='ZZ1234')
    chk('all four are waiting to go', tags_pending(db) == ['T-1', 'T-2', 'T-3', 'T-4'])
    chk('a piece that already carries a number is not offered',
        'T-9' not in tags_pending(db))

    head('a centre to send them to')
    cid = db.hm_save_centre({'name': 'Rajkot AHC', 'code': 'AHC-0912',
                             'per_piece': 45, 'min_charge': 200,
                             'turnaround_days': 7})
    chk('saved and given an id', cid > 0)
    centres = db.hm_centres()
    chk('and it reads back', centres and centres[0]['name'] == 'Rajkot AHC')
    chk('with its own rate', centres[0]['per_piece'] == 45.0)
    cid2 = db.hm_save_centre({'id': cid, 'name': 'Rajkot AHC',
                              'per_piece': 50, 'min_charge': 200})
    chk('editing keeps the same id', cid2 == cid)
    chk('and changes the rate', db.hm_centres()[0]['per_piece'] == 50.0)

    head('pack a lot')
    lot_no = H.next_lot_no(db.hm_lot_numbers(), '2026-09-11')
    charge = H.charge_for(2, per_piece=50, minimum=200)
    made = db.hm_create_lot(lot_no, {'id': cid, 'name': 'Rajkot AHC'},
                            ['T-1', 'T-2'], '2026-09-11', '2026-09-18',
                            charge, 'first lot')
    chk('the lot is created', made['status'] == 'success')
    chk('with both pieces in it', made['pieces'] == 2)
    chk('numbered for the year', made['lot_no'] == 'HM-2026-0001')

    head('a piece at the centre is not offered for sending again')
    # Offering it twice would put the same piece in two lots, and the shop
    # would go looking for jewellery that is already out of the building.
    chk('only the two left behind are waiting',
        tags_pending(db) == ['T-3', 'T-4'])

    head('what was sent is frozen at the time of sending')
    lot = db.hm_lot(made['lot_id'])
    chk('the lot reads back', lot is not None)
    chk('with its pieces', len(lot['items']) == 2)
    chk('and the weight as it was', lot['items'][0]['gr_wt'] == 8.0)
    with db._get_connection() as conn:
        conn.execute("UPDATE stock_inventory SET gr_wt=99 WHERE tag_id='T-1'")
        conn.commit()
    lot = db.hm_lot(made['lot_id'])
    chk('a later edit to stock does not rewrite what went',
        lot['items'][0]['gr_wt'] == 8.0)

    head('book the lot back in: one passed, one failed')
    got = db.hm_receive(made['lot_id'],
                        [{'tag_id': 'T-1', 'result': 'pass', 'huid': 'A1B2C3'},
                         {'tag_id': 'T-2', 'result': 'fail',
                          'purity_found': 900, 'note': 'under 916'}],
                        '2026-09-17')
    chk('the receipt is accepted', got['status'] == 'success')
    chk('one piece was marked', got['marked'] == 1)
    chk('one failed', got['failed'] == 1)
    chk('and nothing is still out', got['still_out'] == 0)

    head('a passed piece is saleable, a failed one is not')
    with db._get_connection() as conn:
        h1 = conn.execute("SELECT huid FROM stock_inventory WHERE tag_id='T-1'"
                          ).fetchone()[0]
        h2 = conn.execute("SELECT huid FROM stock_inventory WHERE tag_id='T-2'"
                          ).fetchone()[0]
    chk('the number is written onto the stock record', h1 == 'A1B2C3')
    chk('the failed piece carries none', (h2 or '') == '')
    # This is the point of the whole register. A piece that failed its assay
    # must come back into the queue, not quietly become sellable stock.
    chk('and the failed piece is waiting to go again',
        'T-2' in tags_pending(db))
    chk('while the passed one is finished with', 'T-1' not in tags_pending(db))

    head('the lot is closed')
    lot = db.hm_lot(made['lot_id'])
    chk('marked received', lot['status'] == 'received')
    chk('on the day it came back', lot['received_date'] == '2026-09-17')
    chk('and the state agrees', H.lot_state(lot, '2026-09-20') == H.STATE_DONE)

    head('a part receipt leaves the rest out')
    lot2_no = H.next_lot_no(db.hm_lot_numbers(), '2026-09-20')
    made2 = db.hm_create_lot(lot2_no, {'id': cid, 'name': 'Rajkot AHC'},
                             ['T-3', 'T-4'], '2026-09-20', '2026-09-27',
                             H.charge_for(2, 50, 200))
    part = db.hm_receive(made2['lot_id'],
                         [{'tag_id': 'T-3', 'result': 'pass', 'huid': 'B2C3D4'}],
                         '2026-09-26')
    chk('one piece is still at the centre', part['still_out'] == 1)
    lot2 = db.hm_lot(made2['lot_id'])
    chk('so the lot is not closed', lot2['status'] == 'part')
    chk('and it has no received date', (lot2['received_date'] or '') == '')
    chk('the piece still out is not offered for sending',
        'T-4' not in tags_pending(db))

    head('numbers already in use')
    used = db.hm_huids_in_use()
    chk('a number on stock counts', 'A1B2C3' in used)
    chk('so does one on another lot', 'B2C3D4' in used)
    chk('and check_receipt refuses to reuse it',
        H.check_receipt({'result': 'pass', 'huid': 'A1B2C3'},
                        seen=used)[0] is False)

    head('cancelling a lot releases its pieces')
    lot3_no = H.next_lot_no(db.hm_lot_numbers(), '2026-10-01')
    made3 = db.hm_create_lot(lot3_no, {'id': cid, 'name': 'Rajkot AHC'},
                             ['T-2'], '2026-10-01', '2026-10-08',
                             H.charge_for(1, 50, 200))
    chk('the piece goes out', 'T-2' not in tags_pending(db))
    db.hm_cancel_lot(made3['lot_id'])
    # Nothing is deleted, so the register still shows the lot was packed,
    # but the jewellery is back in the shop and must be offered again.
    chk('cancelling brings it back', 'T-2' in tags_pending(db))
    chk('and the lot is still on the register',
        db.hm_lot(made3['lot_id'])['status'] == 'cancelled')
    chk('a received lot cannot be cancelled away',
        (db.hm_cancel_lot(made['lot_id']),
         db.hm_lot(made['lot_id'])['status'])[1] == 'received')

    head('looking a piece up for a BIS question')
    by_huid = db.hm_find('A1B2C3')
    chk('found by its number', by_huid and by_huid[0]['tag_id'] == 'T-1')
    chk('with the lot it travelled in', by_huid[0]['lot_no'] == 'HM-2026-0001')
    chk('and the centre that marked it',
        by_huid[0]['centre_name'] == 'Rajkot AHC')
    chk('found by tag too', bool(db.hm_find('T-1')))
    chk('part of a number is enough', bool(db.hm_find('a1b2')))
    chk('and nothing matches nothing', db.hm_find('ZZZZZZ') == [])

    head('the panel over the whole register')
    d = H.dashboard(db.hm_pending_stock(), db.hm_lots(), '2026-09-28')
    chk('it builds', isinstance(d, dict))
    chk('with every lot on it', len(d['lots']) == 3)
    chk('one piece is still at the centre', d['out']['pieces'] == 1)
    chk('one failed its assay', d['failed']['pieces'] == 1)
    chk('and the unsaleable count is stock waiting plus stock away',
        d['saleable_gap'] == d['waiting']['pieces'] + d['out']['pieces'])

    print('')
    return 1 if FAIL else 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    finally:
        try:
            shutil.rmtree(TMP, ignore_errors=True)
        except Exception:
            pass
    if code:
        print('%d FAILED' % FAIL)
    else:
        print('ALL PASS')
    sys.exit(code)
