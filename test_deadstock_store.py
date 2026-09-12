"""The dead stock report against a real database, in both modes.

Run from the project root:  python test_deadstock_store.py

test_deadstock.py covers the arithmetic. This covers the half that decides
whether an owner believes the number: that the report reads the same stock
the rest of the software does, values it at the same rate the dashboard
uses, survives a shop whose table predates half these columns, and writes an
export a person can actually open.

Runs against a throwaway database in the system temp directory. The shop's
own file is never opened.
"""

import csv
import os
import shutil
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

TMP = tempfile.mkdtemp(prefix='aurum_dead_')
os.environ['AURUM_DB_PATH'] = os.path.join(TMP, 'aurum_local.db')

from database.db_manager import DBManager                      # noqa: E402
from core import deadstock as D                                # noqa: E402


def add(db, tag, entry, gr, touch, karat='22K', category='Ring',
        tagged=1, price=0):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stock_inventory"
            "(tag_id,it_name,it_code,gr_wt,nt_wt,touch,is_tagged,entry_date,pcs)"
            " VALUES(?,?,?,?,?,?,?,?,1)",
            (tag, 'Piece ' + tag, 'NK', gr, gr, touch, tagged, entry))
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(stock_inventory)").fetchall()}
        if 'karat' in cols:
            conn.execute("UPDATE stock_inventory SET karat=? WHERE tag_id=?",
                         (karat, tag))
        if 'category' in cols:
            conn.execute("UPDATE stock_inventory SET category=? WHERE tag_id=?",
                         (category, tag))
        if 'sale_price' in cols and price:
            conn.execute("UPDATE stock_inventory SET sale_price=? WHERE tag_id=?",
                         (price, tag))
        conn.commit()


def bill(db, rate, amount=50000):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO sales_history(vch_id,customer,gold_rate,total_amount,date)"
            " VALUES(?,?,?,?,date('now'))",
            ('B-%s' % rate, 'Walk in', rate, amount))
        conn.commit()


def main():
    db = DBManager()

    head('a shop with stock of several ages')
    add(db, 'T-OLD', '2024-01-15', 32.400, 91.6, category='Necklace')
    add(db, 'T-MID', '2025-11-20', 8.200, 91.6, category='Ring', price=62000)
    add(db, 'T-NEW', '2026-09-01', 4.500, 91.6, category='Ring')
    add(db, 'BULK', '2023-05-05', 500.0, 99.5, tagged=0)
    rows = db.dead_stock_rows()
    chk('every row comes back', len(rows) == 4)
    chk('with the date it arrived',
        all(r.get('entry_date') for r in rows))
    chk('oldest first out of the database',
        rows[0]['tag_id'] == 'BULK')
    chk('and the columns added later are there',
        'karat' in rows[0] and 'sale_price' in rows[0] and 'photo' in rows[0])

    head('the rate comes from billing, as the dashboard does')
    chk('no bills yet means no rate', db.latest_gold_rate() == 0.0)
    bill(db, 68000)
    bill(db, 72000)
    chk('the newest bill sets the rate', db.latest_gold_rate() == 72000.0)

    head('the report')
    rep = D.report(db.dead_stock_rows(), db.latest_gold_rate(), 180, None, 'retail')
    tags = [r['tag_id'] for r in rep['rows']]
    chk('the old pieces are dead', 'T-OLD' in tags and 'T-MID' in tags)
    chk('the new one is not', 'T-NEW' not in tags)
    # Half a kilo of bulk would swamp the number that matters.
    chk('and the bulk row is not called a dead ring', 'BULK' not in tags)
    chk('bulk is still reported separately', rep['weight_stock']['pieces'] == 1)

    head('it values stock at the same figure the dashboard does')
    # The dashboard's expression, written out here independently: if these
    # two ever drift, one screen tells an owner their shelf is worth
    # something the other denies.
    with db._get_connection() as conn:
        dash = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(nt_wt,gr_wt,0)*COALESCE(touch,0)/100.0),0) "
            "FROM stock_inventory WHERE COALESCE(gr_wt,0)>0 AND COALESCE(touch,0)>0"
        ).fetchone()[0]
    mine = rep['all_pieces']['fine_wt'] + rep['weight_stock']['fine_wt']
    chk('the fine weight matches to the milligram',
        abs(round(dash, 3) - round(mine, 3)) < 0.001)
    dash_value = round(dash * 72000 / 10.0, 2)
    mine_value = rep['all_pieces']['metal_value'] + rep['weight_stock']['metal_value']
    chk('and so does the rupee value', abs(dash_value - round(mine_value, 2)) < 0.02)

    head('a shop whose table predates the newer columns')
    # An old install has stock_inventory without karat, category or
    # sale_price. The query asks PRAGMA rather than assuming, so it has to
    # survive that rather than failing the whole screen.
    old_tmp = tempfile.mkdtemp(prefix='aurum_dead_old_')
    try:
        import sqlite3
        old_db = os.path.join(old_tmp, 'aurum_local.db')
        conn = sqlite3.connect(old_db)
        conn.execute("CREATE TABLE stock_inventory (id INTEGER PRIMARY KEY, "
                     "it_code TEXT, it_name TEXT, tag_id TEXT UNIQUE, "
                     "gr_wt REAL, nt_wt REAL, touch REAL, pcs INTEGER, "
                     "huid TEXT, is_tagged INTEGER, entry_date DATE)")
        conn.execute("INSERT INTO stock_inventory(tag_id,it_name,gr_wt,nt_wt,"
                     "touch,pcs,is_tagged,entry_date) VALUES"
                     "('T-1','Old chain',10,10,91.6,1,1,'2024-01-01')")
        conn.commit()
        conn.close()

        os.environ['AURUM_DB_PATH'] = old_db
        DBManager._ID_CACHE = None
        old = DBManager()
        got = old.dead_stock_rows()
        chk('the query still returns rows', len(got) == 1)
        chk('and fills the missing columns with blanks',
            got[0]['category'] == '' and got[0]['karat'] == '')
        chk('the report still builds',
            D.report(got, 0, 180, None)['dead']['pieces'] == 1)
    finally:
        os.environ['AURUM_DB_PATH'] = os.path.join(TMP, 'aurum_local.db')
        shutil.rmtree(old_tmp, ignore_errors=True)

    head('a shop that has never billed, so has no rate')
    norate = D.report(db.dead_stock_rows(), 0, 180, None, 'wholesale')
    chk('the report is still produced', norate['dead']['pieces'] >= 1)
    chk('the value is honestly zero', norate['dead']['metal_value'] == 0.0)
    chk('but the fine weight is real', norate['dead']['fine_wt'] > 0)
    chk('and it says so in grams', 'g fine' in D.headline(norate))

    head('the export a shop hands to a karigar')
    import main as app                                          # noqa: E402
    api = app.AurumAPI.__new__(app.AurumAPI)
    api.db = db
    api._software_type = lambda: 'retail'
    out = api.dead_stock_export(180)
    chk('a file is written', out.get('status') == 'success')
    chk('it is where it says it is', os.path.isfile(out.get('path', '')))
    with open(out['path'], encoding='utf-8-sig') as fh:
        read = list(csv.reader(fh))
    chk('with a header a person can read', read[0][0] == 'Tag')
    chk('and one line per dead piece', len(read) - 1 == out['rows'])
    chk('carrying the age', 'Days' in read[0])
    chk('and the tag of the oldest piece first', read[1][0] == 'T-OLD')

    head('an export with nothing to export')
    quiet = api.dead_stock_export(99999)
    chk('says so rather than writing an empty file',
        quiet.get('status') == 'empty')

    print('')
    return 1 if FAIL else 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print('%d FAILED' % FAIL if code else 'ALL PASS')
    sys.exit(code)
