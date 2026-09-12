"""Which metal has not moved, and what it is worth.

Run from the project root:  python test_deadstock.py

A shop will argue with this report, because it is telling an owner that
money they already spent is asleep on a shelf. So the arithmetic has to hold
up: the valuation must agree with the dashboard to the formula, an age must
not drift by a day, and loose metal must not be counted as a dead ring.

core/deadstock.py takes "today" as an argument, so the awkward dates are
tested here rather than once a year.
"""

import sys

from core import deadstock as D

FAIL = 0


def chk(label, cond):
    global FAIL
    if not cond:
        FAIL += 1
    print('  [%s] %s' % ('PASS' if cond else 'FAIL', label))


def head(t):
    print('\n-- %s --' % t)


TODAY = '2026-09-12'
RATE = 72000.0          # per ten grams


def piece(tag, entry, gr=10.0, nt=None, touch=91.6, **extra):
    row = {'tag_id': tag, 'is_tagged': 1, 'entry_date': entry,
           'gr_wt': gr, 'nt_wt': gr if nt is None else nt, 'touch': touch,
           'it_name': 'Chain', 'category': 'Chain', 'karat': '22K'}
    row.update(extra)
    return row


head('how old a piece is')
chk('a year to the day', D.age_days('2025-09-12', TODAY) == 365)
chk('yesterday', D.age_days('2026-09-11', TODAY) == 1)
chk('today is nothing', D.age_days(TODAY, TODAY) == 0)
# A date typed in the future is a typing mistake, not a piece that arrives
# next week, and it must not come back as a negative age.
chk('a future date is not negative', D.age_days('2027-01-01', TODAY) == 0)
chk('no date at all is unknown', D.age_days('', TODAY) is None)
chk('and junk is unknown', D.age_days('not a date', TODAY) is None)
# Leap day, because 2028 has one and the arithmetic must not care.
chk('a leap year is counted honestly',
    D.age_days('2028-02-29', '2028-03-01') == 1)

head('the bands a jeweller already talks in')
chk('90 days is still fresh', D.band_for(90)[0] == 'fresh')
chk('91 is slow', D.band_for(91)[0] == 'slow')
chk('half a year is stale', D.band_for(200)[0] == 'stale')
chk('a year and a day is dead', D.band_for(366)[0] == 'dead')
chk('the label reads in months', D.band_for(200)[1] == '6 to 12 months')
chk('an unknown age has no band', D.band_for(None)[0] == '')

head('what the metal is worth, to the same formula as the dashboard')
# fine = net x touch / 100;  value = fine x rate / 10
p = piece('T-1', '2025-01-01', gr=20.0, nt=18.0, touch=91.6)
chk('fine weight uses the net weight', D.fine_weight(p) == 16.488)
chk('and the value follows the ten-gram rate',
    D.metal_value(p, RATE) == round(16.488 * 72000 / 10.0, 2))
chk('gross is used when there is no net',
    D.fine_weight({'gr_wt': 10.0, 'touch': 50.0}) == 5.0)
chk('no touch means no fine weight', D.fine_weight({'gr_wt': 10.0}) == 0.0)
chk('no weight means none either', D.fine_weight({'touch': 91.6}) == 0.0)
# A shop with no rate yet must still get a report, in grams.
chk('with no rate there is no rupee value', D.metal_value(p, 0) == 0.0)

head('a price the shop set, and a price nobody set')
chk('a sale price is carried through',
    D.ticket_value({'sale_price': 45000}) == 45000.0)
# Zero is "nobody priced it", which is not the same as "worth nothing", so
# it is kept separate from the metal value rather than replacing it.
chk('an unpriced piece is zero, not a guess', D.ticket_value({}) == 0.0)

head('a finished piece and loose metal are different things')
chk('a tagged piece is a piece', D.kind_of(piece('T-9', TODAY)) == D.TAGGED)
chk('an untagged row is weight',
    D.kind_of({'tag_id': '', 'is_tagged': 0}) == D.UNTAGGED)
chk('a katti packet is weight, not a dead ring',
    D.kind_of({'tag_id': 'KATTI-0007', 'is_tagged': 0}) == D.UNTAGGED)

head('what counts as dead')
rows = D.classify([piece('T-1', '2025-06-01'), piece('T-2', '2026-09-01')], RATE, TODAY)
old, recent = rows[0], rows[1]
chk('the oldest sorts first', old['tag_id'] == 'T-1')
chk('past the threshold is dead', D.is_dead(old, 180) is True)
chk('inside it is not', D.is_dead(recent, 180) is False)
chk('a shop can set its own patience', D.is_dead(recent, 5) is True)
chk('an unknown age is never called dead',
    D.is_dead({'age_days': None}, 180) is False)

head('the report, for a retail shop')
STOCK = [
    piece('T-1', '2024-09-01', gr=32.4, touch=91.6, category='Necklace'),   # 741d
    piece('T-2', '2025-12-14', gr=8.2, touch=91.6, category='Ring'),        # 272d
    piece('T-3', '2026-01-20', gr=12.0, touch=75.0, category='Ring',
          karat='18K'),                                                     # 235d
    piece('T-5', '2026-05-20', gr=6.0, touch=91.6, category='Bangle'),      # 115d
    piece('T-4', '2026-09-01', gr=4.5, touch=91.6, category='Ring'),        # 11d
    {'tag_id': '', 'is_tagged': 0, 'entry_date': '2024-01-01',
     'gr_wt': 500.0, 'nt_wt': 500.0, 'touch': 99.5},
]
rep = D.report(STOCK, RATE, 180, TODAY, 'retail')
chk('three pieces are dead', rep['dead']['pieces'] == 3)
chk('the fresh one is not among them',
    all(r['tag_id'] != 'T-4' for r in rep['rows']))
# Half a tonne of bulk metal would swamp the number that matters.
chk('loose metal is kept out of the dead count',
    all(r['kind'] == D.TAGGED for r in rep['rows']))
chk('but it is still reported, separately',
    rep['weight_stock']['pieces'] == 1)
# 115 days is slow, not dead. Calling it dead would have the shop melting
# pieces that are still selling.
chk('a piece inside the threshold is left alone',
    all(r['tag_id'] != 'T-5' for r in rep['rows']))
chk('the oldest is on top', rep['rows'][0]['tag_id'] == 'T-1')
chk('and the report says how old that is', rep['oldest_days'] == 741)
chk('the share of stock is a percentage', 0 < rep['share_of_stock'] <= 100)
chk('it knows it has a rate', rep['has_rate'] is True)

head('every band is printed, even an empty one')
# A shop wants to see that nothing has crossed into "over a year" yet.
bands = {b['band']: b for b in rep['bands']}
chk('all four bands appear', len(rep['bands']) == 4)
chk('the fresh band holds the newest piece', bands['fresh']['pieces'] == 1)
chk('the slow band holds the one inside the threshold',
    bands['slow']['pieces'] == 1)
chk('the stale band holds two', bands['stale']['pieces'] == 2)
chk('and the dead band holds the oldest', bands['dead']['pieces'] == 1)
# The bands cover every piece exactly once, or a piece is being counted
# twice or not at all.
chk('every piece lands in exactly one band',
    sum(b['pieces'] for b in rep['bands']) == rep['all_pieces']['pieces'])
empty = D.report([piece('T-9', TODAY)], RATE, 180, TODAY)
chk('bands with nothing in them are still listed',
    len(empty['bands']) == 4 and empty['bands'][3]['pieces'] == 0)

head('where the money is')
cats = {c['name']: c for c in rep['by_category']}
chk('rings are grouped together', cats['Ring']['pieces'] == 2)
chk('and the biggest sum is first',
    rep['by_category'][0]['metal_value'] >= rep['by_category'][-1]['metal_value'])
chk('purity is grouped too', len(rep['by_purity']) == 2)
# A shop that never set up categories still gets an honest row.
plain = D.report([piece('T-1', '2024-01-01', category='')], RATE, 180, TODAY)
chk('no category set reads as Not set',
    plain['by_category'][0]['name'] == 'Not set')

head('the report for a wholesaler')
w = D.report(STOCK, RATE, 180, TODAY, 'wholesale')
chk('the same pieces are dead', w['dead']['pieces'] == rep['dead']['pieces'])
chk('and the fine weight is the same number', w['dead']['fine_wt'] == rep['dead']['fine_wt'])
# The difference is what gets said out loud, not what gets counted.
chk('but it is put in grams, not rupees', 'g fine' in D.headline(w))
chk('while retail is put in rupees', 'Rs' in D.headline(rep))

head('the sentence an owner reads first')
chk('it names the count', D.headline(rep).startswith('3 pieces'))
chk('and how long the worst has been there', 'month' in D.headline(rep))
chk('one piece is not "1 pieces"',
    D.headline(D.report([piece('T-1', '2024-01-01')], RATE, 180, TODAY))
    .startswith('1 piece,'))
clean = D.report([piece('T-9', TODAY)], RATE, 180, TODAY)
chk('a clean shop is told so plainly',
    D.headline(clean).startswith('Nothing has been sitting'))
# With no rate the report still works; it just talks in grams.
norate = D.report(STOCK, 0, 180, TODAY, 'retail')
chk('no rate falls back to grams', 'g fine' in D.headline(norate))
chk('and the value is honestly zero', norate['dead']['metal_value'] == 0.0)

head('money is written the Indian way')
chk('a lakh', D.format_rupees(285000) == '2,85,000')
chk('a crore', D.format_rupees(12500000) == '1,25,00,000')
chk('a small number is left alone', D.format_rupees(940) == '940')
chk('and it rounds to the rupee', D.format_rupees(4999.6) == '5,000')

head('an empty shop does not break the report')
e = D.report([], RATE, 180, TODAY)
chk('nothing dead', e['dead']['pieces'] == 0)
chk('nothing at all', e['all_pieces']['pieces'] == 0)
chk('no share of nothing', e['share_of_stock'] == 0.0)
chk('and still four bands', len(e['bands']) == 4)
chk('none of it is a crash', isinstance(e['rows'], list))

print('')
if FAIL:
    print('%d FAILED' % FAIL)
    sys.exit(1)
print('ALL PASS')
