"""Pin what a retail shop needs from inventory that the schema had no room for.

Run from the project root:  python test_retail_inventory.py

Stock was modelled the wholesale way: weight and purity, settled in grams. A
retail shop sells a finished piece for money, so it has to record what the
piece costs to make and what it sells for. With nowhere to put that, the till
fell back to one rate and one making charge kept in browser storage and applied
to every line, which is why a shop selling rings at 12% and chains at 8%
retyped it on every line.

These tests cover the columns that fixed it, the lookup the till uses on a
scan, and the sold-pieces query that the Sold Tags screen has called since it
was written without it ever existing.

Everything runs against a throwaway database in a temp folder. Nothing here
touches the project's own data, the network or a printer.
"""

import os
import shutil
import sys
import tempfile

REPO = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO)

FAIL = []
work = tempfile.mkdtemp(prefix='aurum_retailinv_')
os.makedirs(os.path.join(work, 'database'), exist_ok=True)
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
os.chdir(work)

from database.db_manager import DBManager                      # noqa: E402


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


def check_that(label, ok, detail=''):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        if detail:
            print(f"         {detail}")
        FAIL.append(label)


db = DBManager()

try:
    print("-- the columns a price needs --")
    import sqlite3
    conn = sqlite3.connect(os.environ['AURUM_DB_PATH'])
    stock = {r[1] for r in conn.execute("PRAGMA table_info(stock_inventory)")}
    master = {r[1] for r in conn.execute("PRAGMA table_info(product_master)")}
    conn.close()
    for col in ('category', 'karat', 'making_type', 'making_charge', 'sale_price', 'stone_value'):
        check_that(f"stock_inventory.{col}", col in stock)
    for col in ('making_type', 'making_charge', 'sale_price', 'hsn'):
        check_that(f"product_master.{col}", col in master)

    print("-- running the migration again changes nothing --")
    db.ensure_retail_pricing_columns()
    db.ensure_retail_pricing_columns()
    check_that("safe to run on every startup", True)

    print("-- a retail piece carries its own pricing --")
    db.add_stock_entry(it_code='RING22', it_name='Gold Ring', tag_id='TAGRET001',
                       pcs=1, gr_wt=5.25, nt_wt=5.10, touch=91.6, huid='HUID9',
                       category='Rings', karat='22K', making_type='per_gram',
                       making_charge=150, sale_price=0, stone_value=1200)
    it = db.get_item_by_tag('TAGRET001')
    check("category", it['category'], 'Rings')
    check("karat", it['karat'], '22K')
    check("making charge", it['making_charge'], 150.0)
    check("making type", it['making_type'], 'per_gram')
    check("stone value", it['stone_value'], 1200.0)
    check("HUID", it['huid'], 'HUID9')

    print("-- a wholesale entry passes none of that and still saves --")
    ok = db.add_stock_entry(it_code='68', it_name='Bulk', tag_id='TAGWS001',
                            pcs=1, gr_wt=10.0, nt_wt=10.0, touch=68, wastage=2)
    ws = db.get_item_by_tag('TAGWS001')
    check("saved", bool(ok), True)
    check("making defaults to nothing owed", ws['making_charge'], 0.0)
    check("and to a sane type", ws['making_type'], 'percent')

    print("-- a piece with no making charge falls back to its product --")
    db.add_product_master('RING22', 'Gold Ring', 'Rings', '91.6', 0,
                          12, 'percent', 0, '7113')
    db.add_stock_entry(it_code='RING22', it_name='Plain Ring', tag_id='TAGRET002',
                       pcs=1, gr_wt=4.0, nt_wt=4.0, touch=91.6, making_charge=0)
    fallback = db.get_item_by_tag('TAGRET002')
    check("the piece itself holds nothing", fallback['making_charge'], 0.0)
    check("so the product master answers", fallback['eff_making_charge'], 12.0)
    check("and supplies the HSN", fallback['hsn'], '7113')
    own = db.get_item_by_tag('TAGRET001')
    check("a piece that does hold one wins", own['eff_making_charge'], 150.0)

    print("-- the product master carries retail pricing --")
    p = [x for x in db.get_all_products() if x['code'] == 'RING22'][0]
    check("making charge", p['making_charge'], 12.0)
    check("making type", p['making_type'], 'percent')
    check("HSN", p['hsn'], '7113')
    print("-- and the old five-argument call still works --")
    check_that("wholesale callers are unaffected",
               bool(db.add_product_master('CHAIN', 'Chain', 'Chains', '91.6', 3)))
    c = [x for x in db.get_all_products() if x['code'] == 'CHAIN'][0]
    check("its making charge defaults", c['making_charge'], 0.0)

    print("-- updating a row only touches real columns --")
    row_id = ws['id']
    check_that("a real column updates", bool(db.update_stock_entry(
        row_id, {'making_charge': 9, 'category': 'Chains'})))
    after = db.get_item_by_tag('TAGWS001')
    check("making changed", after['making_charge'], 9.0)
    check("category changed", after['category'], 'Chains')
    check_that("a column that does not exist is ignored, not fatal",
               bool(db.update_stock_entry(row_id, {'making_charge': 11, 'not_a_column': 'x'})))
    check("the real one still applied", db.get_item_by_tag('TAGWS001')['making_charge'], 11.0)
    check("nothing usable means no update", db.update_stock_entry(row_id, {'bogus': 1}), False)

    print("-- saving through the bridge, not straight to the database --")
    # main.py filters incoming keys against an allowlist before calling the
    # database. The retail columns were missing from it, so a piece saved with
    # a making charge arrived with none and nothing anywhere said so. Going
    # through AurumAPI here is the point of this test: calling the database
    # directly would pass while the real path silently lost the values.
    try:
        import main as _main
    except ImportError:
        print("  [SKIP] main.py import unavailable (PIL not installed) — bridge tests skipped")
        _main = None

    if _main is not None:
        api = _main.AurumAPI.__new__(_main.AurumAPI)
        api.db = db
        api._window = None
        res = api.add_stock_entry({'it_code': 'RING22', 'it_name': 'Bridge Ring',
                                   'tag_id': 'TAGBRIDGE1', 'pcs': 1, 'gr_wt': 6.0,
                                   'nt_wt': 6.0, 'touch': 91.6, 'huid': 'HUB1',
                                   'category': 'Rings', 'karat': '22K',
                                   'making_type': 'per_gram', 'making_charge': 150,
                                   'sale_price': 0, 'stone_value': 300})
        check("the bridge accepted it", res.get('status'), 'success')
        bridged = db.get_item_by_tag('TAGBRIDGE1')
        check("making charge survived the allowlist", bridged['making_charge'], 150.0)
        check("and so did the type", bridged['making_type'], 'per_gram')
        check("and the category", bridged['category'], 'Rings')
        check("and the stone value", bridged['stone_value'], 300.0)

        print("-- the inventory table can read them back --")
        listed = [r for r in db.get_stock_ledger() if r['tag_id'] == 'TAGBRIDGE1']
        check_that("the row is listed", len(listed) == 1)
        if listed:
            check("category is in the query", listed[0]['category'], 'Rings')
            check("making charge is in the query", listed[0]['making_charge'], 150.0)

    print("-- an unknown tag --")
    check("returns nothing rather than raising", db.get_item_by_tag('NOPE'), None)
    check("and so does an empty one", db.get_item_by_tag(''), None)

    print("-- sold pieces --")
    # The Sold Tags screen has called this since it was written. It never
    # existed, so the page could only ever say "Failed to load".
    check_that("the method exists at all", hasattr(db, 'get_sold_tags'))
    check("no sales yet is an empty list, not a crash", db.get_sold_tags('', '', '', ''), [])
    import json as _json
    db.execute_query(
        """INSERT INTO sales_history (vch_id,customer,mobile,status,total_amount,items,date)
           VALUES (?,?,?,?,?,?,date('now'))""",
        ('R-1', 'Ramesh', '99999', 'PAID', 39387.0, _json.dumps([
            {'code': 'RING22', 'it_name': 'Gold Ring', 'tag_id': 'TAGRET001',
             'huid': 'HUID9', 'touch': 91.6, 'weight': 5.25, 'fine': 4.76,
             'making': 800, 'amount': 38240, 'category': 'Rings'}])))
    rows = db.get_sold_tags('', '', '', '')
    check("one sold line comes back", len(rows), 1)
    r = rows[0]
    check("the piece", (r['tag_id'], r['it_name'], r['huid']), ('TAGRET001', 'Gold Ring', 'HUID9'))
    check("the money", (r['making'], r['amount']), (800.0, 38240.0))
    check("the customer", (r['vch_id'], r['customer']), ('R-1', 'Ramesh'))
    check("searching by HUID finds it", len(db.get_sold_tags('', '', 'HUID9', '')), 1)
    check("searching for something else does not", len(db.get_sold_tags('', '', 'zzzz', '')), 0)
    check("filtering by category finds it", len(db.get_sold_tags('', '', '', 'Rings')), 1)
    check("filtering by a touch band finds it", len(db.get_sold_tags('', '', '', '91.x')), 1)
    check("filtering by the wrong band does not", len(db.get_sold_tags('', '', '', '75.x')), 0)
    # The screen's band options read "91.x" but carry the bare "91" as their
    # value, so the filter has to accept both or it drops every row.
    check("a bare band number works too", len(db.get_sold_tags('', '', '', '91')), 1)
    check("and the wrong bare number does not", len(db.get_sold_tags('', '', '', '75')), 0)

    print("-- how stock leaves when something is sold --")
    import json as _j

    def _wt(tag):
        with db._get_connection() as c:
            r = c.execute("SELECT gr_wt FROM stock_inventory WHERE tag_id=?", (tag,)).fetchone()
            return r[0] if r else None

    # A tagged piece leaves whole, by its tag.
    db.add_stock_entry(it_code='RING22', it_name='R', tag_id='TAGSELL0001',
                       pcs=1, gr_wt=5.0, nt_wt=5.0, touch=91.6)
    db.deduct_stock_after_sale(_j.dumps([
        {'it_code': 'RING22', 'weight': 5.0, 'tag_id': 'TAGSELL0001'}]))
    check("a tagged piece is removed by its tag", _wt('TAGSELL0001'), None)

    # Weight Terminal stock leaves by weight, against the touch, oldest first.
    for i, w in enumerate([10.0, 5.0, 8.0], 1):
        db.add_stock_entry(it_code='FIFO', it_name='Batch', tag_id=f'KATTI-T-{i}',
                           pcs=1, gr_wt=w, nt_wt=w, touch=60)
    db.deduct_stock_after_sale(_j.dumps([{'it_code': '60', 'weight': 12.0, 'tag_id': ''}]))
    check("oldest batch is used up first", _wt('KATTI-T-1'), None)
    check("the next is reduced by the remainder", _wt('KATTI-T-2'), 3.0)
    check("the newest is untouched", _wt('KATTI-T-3'), 8.0)

    # A product code that happens to read as a purity must not eat bulk gold.
    # This used to: the code was read as a touch before anything checked
    # whether it was a real product, so selling an item coded "75" deducted
    # from 75-touch bulk gold and left the item itself in stock.
    db.add_stock_entry(it_code='ALLOY75', it_name='Bulk 75', tag_id='KATTI-P-1',
                       pcs=1, gr_wt=20.0, nt_wt=20.0, touch=75)
    db.add_stock_entry(it_code='75', it_name='Design 75', tag_id='TAGCODE0075',
                       pcs=1, gr_wt=4.0, nt_wt=4.0, touch=91.6)
    db.deduct_stock_after_sale(_j.dumps([{'it_code': '75', 'weight': 4.0, 'tag_id': ''}]))
    check("unrelated 75-touch gold is left alone", _wt('KATTI-P-1'), 20.0)
    check_that("and nothing else is quietly consumed either",
               _wt('TAGCODE0075') == 4.0,
               "a tagged piece sold without its tag should be refused, not guessed at")

    # A purity with no product of that code still resolves, which is what an
    # ordinary wholesale weight line depends on.
    db.add_stock_entry(it_code='MIXED', it_name='Bulk 90', tag_id='KATTI-P-2',
                       pcs=1, gr_wt=10.0, nt_wt=10.0, touch=90)
    db.deduct_stock_after_sale(_j.dumps([{'it_code': '90', 'weight': 3.0, 'tag_id': ''}]))
    check("a weight line still deducts against its touch", _wt('KATTI-P-2'), 7.0)

    print("-- the stock take can count a shop that records no purity --")
    # A retail shop counts rings and chains, not 91.6% and 75%. The snapshot
    # filtered on touch > 0, so such a shop opened the stock take and saw
    # nothing at all, and the sign-off then discarded whatever it did count.
    db.add_stock_entry(it_code='RING22', it_name='Ring A', tag_id='TAGCOUNT1',
                       pcs=1, gr_wt=5.0, nt_wt=5.0, touch=0, category='Rings')
    db.add_stock_entry(it_code='RING22', it_name='Ring B', tag_id='TAGCOUNT2',
                       pcs=1, gr_wt=3.0, nt_wt=3.0, touch=0, category='Rings')
    db.add_stock_entry(it_code='CHAIN', it_name='Chain A', tag_id='TAGCOUNT3',
                       pcs=1, gr_wt=12.0, nt_wt=12.0, touch=0, category='Chains')
    snap = db.get_stocksync_snapshot()
    snap_rows = snap.get('data') if isinstance(snap, dict) else snap
    by_cat = {r.get('category'): r for r in (snap_rows or []) if r.get('category')}
    check_that("categories come back at all", set(by_cat) >= {'Rings', 'Chains'},
               f"got {sorted(by_cat)}")
    if 'Rings' in by_cat:
        check("two rings are added together", by_cat['Rings']['wt_live'], 8.0)
        check("and counted as two pieces", by_cat['Rings']['pc_book_close'], 2)
    if 'Chains' in by_cat:
        check("chains are their own line", by_cat['Chains']['wt_live'], 12.0)

    print("-- and a purity shop is grouped exactly as before --")
    db.add_stock_entry(it_code='68', it_name='Batch', tag_id='KATTI-SNAP-1',
                       pcs=1, gr_wt=20.0, nt_wt=20.0, touch=68)
    snap2 = db.get_stocksync_snapshot()
    rows2 = snap2.get('data') if isinstance(snap2, dict) else snap2
    by_touch = {r['touch']: r for r in (rows2 or []) if not r.get('category')}
    check_that("the 68 group is there", 68.0 in by_touch, f"got {sorted(by_touch)}")
    if 68.0 in by_touch:
        # Computed rather than hard-coded: earlier tests in this run leave
        # their own 68-touch rows behind, and a fixed number would fail for a
        # reason that has nothing to do with what is being tested.
        with db._get_connection() as _c:
            expect = round(_c.execute(
                "SELECT COALESCE(SUM(gr_wt),0) FROM stock_inventory WHERE ROUND(touch,2)=68.0"
            ).fetchone()[0], 3)
        check("its weight is every 68-touch row added up", by_touch[68.0]['wt_live'], expect)

finally:
    try:
        os.chdir(REPO)
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
