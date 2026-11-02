"""The hallmarking rules: grades, numbers, charges and where a lot stands.

Run from the project root:  python test_hallmark.py

These are the calculations a shop will argue with you about at the counter,
so every one of them is checkable on its own. No database, no clock, no
network: "today" is passed in, which is how the awkward dates get tested at
all rather than once a year.
"""

import sys

from core import hallmark as H

FAIL = 0


def chk(label, cond):
    global FAIL
    if not cond:
        FAIL += 1
    print('  [%s] %s' % ('PASS' if cond else 'FAIL', label))


def head(t):
    print('\n-- %s --' % t)


head('a karat is written a dozen ways behind the counter')
chk("'22K' is 22", H.karat_of('22K') == 22)
chk('so is "22 kt"', H.karat_of('22 kt') == 22)
chk('so is the number on its own', H.karat_of(22) == 22)
chk('so is 22.0 off a database', H.karat_of('22.0') == 22)
chk('and nonsense is nothing, not a guess', H.karat_of('rose gold') == 0)

head('purity is what BIS will strike, not what the shop fancies')
chk('22K is 916 fine', H.fineness('22K') == 916)
chk('18K is 750', H.fineness(18) == 750)
chk('and the mark reads 22K916', H.grade_mark('22K') == '22K916')
chk('21K is not a grade', H.is_markable(21) is False)
chk('and it would have to be sold as 20K', H.nearest_markable(21) == 20)
# Rounding up would mean claiming purity the metal does not have, which is
# the one direction the law does not forgive.
chk('never rounded up to 22K', H.nearest_markable(21) != 22)
chk('below every grade there is nothing to claim', H.nearest_markable(5) == 0)

head('the six-character number struck on the piece')
chk('six letters and digits is fine', H.validate_huid('A1B2C3')[0] is True)
chk('lower case is just typing', H.normalise_huid('a1b2c3') == 'A1B2C3')
chk('spaces and dashes are just typing',
    H.normalise_huid(' a1-b2 c3 ') == 'A1B2C3')
chk('five characters is not', H.validate_huid('A1B2C')[0] is False)
chk('and it says how many there were', 'five' in H.validate_huid('A1B2C')[1]
    or '5' in H.validate_huid('A1B2C')[1])
chk('punctuation is not', H.validate_huid('A1B2C!')[0] is False)
chk('nothing typed is not', H.validate_huid('')[0] is False)

head('numbers read off a loupe get mistyped')
# O against 0 and I against 1 are the whole reason a shop rings the centre a
# week later. This warns; it does not refuse, because a real HUID may hold
# any of them.
chk('O and 0 are flagged', 'O' in H.huid_warning('AO0B12'))
chk('so are I and 1', H.huid_warning('AI1B23') != '')
chk('a clean number says nothing', H.huid_warning('AB2C34') == '')
chk('and the warning never blocks the save',
    H.validate_huid('AO0B12')[0] is True)

head('two pieces cannot share a number')
rows = [{'huid': 'A1B2C3'}, {'huid': 'a1b2c3'}, {'huid': 'ZZ9999'}]
chk('the same number typed twice is caught',
    H.duplicate_huids(rows) == ['A1B2C3'])
chk('case does not hide it', 'A1B2C3' in H.duplicate_huids(rows))
chk('blanks are not duplicates of each other',
    H.duplicate_huids([{'huid': ''}, {'huid': ''}]) == [])

head('what the centre charges')
c = H.charge_for(10)
chk('ten pieces at the per-piece rate', c['by_piece'] == 450.0)
chk('the minimum does not apply', c['minimum_applied'] is False)
chk('tax on top', c['gst'] == 81.0)
chk('and a total to pay', c['total'] == 531.0)

small = H.charge_for(2)
# This is where shops lose money: two pieces and ten pieces cost the same.
chk('two pieces fall under the minimum', small['minimum_applied'] is True)
chk('so the base is the minimum, not the count', small['base'] == 200.0)
chk('sending nothing costs nothing', H.charge_for(0)['total'] == 0.0)
chk('a centre may charge its own rate',
    H.charge_for(10, per_piece=60)['by_piece'] == 600.0)

head('so the panel can say how many more would ride free')
chk('two sent, two more cost nothing', H.worth_sending(2) == 2)
chk('at the minimum, none', H.worth_sending(5) == 0)
chk('past it, none', H.worth_sending(12) == 0)

head('where a lot stands')
LOT = {'sent_date': '2026-09-01', 'expected_date': '2026-09-08'}
chk('out, before it is due', H.lot_state(LOT, '2026-09-05') == H.STATE_OUT)
chk('due, on the day', H.lot_state(LOT, '2026-09-08') == H.STATE_DUE)
chk('late, after it', H.lot_state(LOT, '2026-09-09') == H.STATE_LATE)
back = dict(LOT, received_date='2026-09-07')
chk('received beats every other state',
    H.lot_state(back, '2026-09-30') == H.STATE_DONE)
chk('a cancelled lot is not late for ever',
    H.lot_state(dict(LOT, status='cancelled'), '2026-12-31') == H.STATE_VOID)
chk('with no expected date, a week is assumed',
    H.lot_state({'sent_date': '2026-09-01'}, '2026-09-09') == H.STATE_LATE)
chk('and the due date says so', H.due_date('2026-09-01') == '2026-09-08')

head('how long it has been away')
chk('counted to today while it is out',
    H.days_out('2026-09-01', '2026-09-11') == 10)
chk('and stops on the day it came back',
    H.days_out('2026-09-01', '2026-09-30', received_date='2026-09-05') == 4)
chk('never negative', H.days_out('2026-09-20', '2026-09-01') == 0)

head('what may go in a lot')
ok_item = {'tag_id': 'T-1', 'gr_wt': 8.2, 'karat': '22K', 'huid': ''}
chk('a plain unmarked piece may go', H.can_send(ok_item)[0] is True)
chk('one already marked may not',
    H.can_send(dict(ok_item, huid='A1B2C3'))[0] is False)
chk('and it says why',
    'Already' in H.can_send(dict(ok_item, huid='A1B2C3'))[1])
chk('a piece with no weight may not',
    H.can_send(dict(ok_item, gr_wt=0))[0] is False)
chk('a piece with no tag may not',
    H.can_send(dict(ok_item, tag_id=''))[0] is False)
chk('an unmarkable purity may not',
    H.can_send(dict(ok_item, karat='21K'))[0] is False)
chk('and it names the grade it would have to go as',
    '20K' in H.can_send(dict(ok_item, karat='21K'))[1])

head('what comes back')
chk('a pass with a number is fine',
    H.check_receipt({'result': 'pass', 'huid': 'A1B2C3'})[0] is True)
chk('a pass without one is not',
    H.check_receipt({'result': 'pass', 'huid': ''})[0] is False)
# A failed piece was never marked. Recording a number against one would put
# an unhallmarked piece into saleable stock, which is the whole risk here.
chk('a fail must carry no number',
    H.check_receipt({'result': 'fail', 'huid': 'A1B2C3'})[0] is False)
chk('a plain fail is fine',
    H.check_receipt({'result': 'fail', 'huid': ''})[0] is True)
chk('a number already used on another piece is refused',
    H.check_receipt({'result': 'pass', 'huid': 'A1B2C3'},
                    seen={'A1B2C3'})[0] is False)
chk('and no verdict at all is refused',
    H.check_receipt({'result': '', 'huid': ''})[0] is False)

head('lot numbers count within the year')
chk('the first of a year', H.next_lot_no([], '2026-04-02') == 'HM-2026-0001')
chk('then the next',
    H.next_lot_no(['HM-2026-0001', 'HM-2026-0002'], '2026-04-02')
    == 'HM-2026-0003')
chk('last year does not carry over',
    H.next_lot_no(['HM-2025-0042'], '2026-01-01') == 'HM-2026-0001')
chk('a gap does not reuse a number',
    H.next_lot_no(['HM-2026-0001', 'HM-2026-0009'], '2026-06-06')
    == 'HM-2026-0010')

head('the summary across one lot')
items = [{'gr_wt': 8.0, 'result': 'pass', 'huid': 'A1B2C3'},
         {'gr_wt': 4.5, 'result': 'fail', 'huid': ''},
         {'gr_wt': 2.5, 'result': 'pending', 'huid': ''}]
s = H.summarise(items)
chk('every piece counted', s['pieces'] == 3)
chk('weights added', s['gross_wt'] == 15.0)
chk('passed, failed and still out are separate',
    (s['passed'], s['failed'], s['pending']) == (1, 1, 1))
chk('and only one carries a number', s['marked'] == 1)

head('the panel across the top')
pending = [{'tag_id': 'T-9', 'gr_wt': 6.0, 'karat': '22K'},
           {'tag_id': 'T-8', 'gr_wt': 4.0, 'karat': '22K'}]
lots = [
    {'id': 1, 'lot_no': 'HM-2026-0001', 'sent_date': '2026-09-01',
     'expected_date': '2026-09-08',
     'items': [{'gr_wt': 8.0, 'result': 'pending'}]},
    {'id': 2, 'lot_no': 'HM-2026-0002', 'sent_date': '2026-09-09',
     'expected_date': '2026-09-16',
     'items': [{'gr_wt': 5.0, 'result': 'pending'}]},
    {'id': 3, 'lot_no': 'HM-2026-0003', 'sent_date': '2026-08-01',
     'expected_date': '2026-08-08', 'received_date': '2026-08-07',
     'items': [{'gr_wt': 3.0, 'result': 'pass', 'huid': 'A1B2C3'},
               {'gr_wt': 1.0, 'result': 'fail'}]},
]
d = H.dashboard(pending, lots, '2026-09-11')
chk('stock waiting to go is counted', d['waiting']['pieces'] == 2)
chk('with its weight', d['waiting']['gross_wt'] == 10.0)
chk('two lots are still at the centre', d['out']['lots'] == 2)
chk('one of them is late', d['late']['lots'] == 1)
chk('a received lot is not counted as out', d['out']['pieces'] == 2)
chk('pieces that came back marked', d['marked'] == 1)
chk('and pieces that failed', d['failed']['pieces'] == 1)
# The number the owner actually wants: stock bought and paid for that
# cannot legally be sold this morning.
chk('what cannot be sold yet is stock waiting plus stock away',
    d['saleable_gap'] == 4)

head('a lot half received is only half away')
# Counting the whole lot as still out sends someone looking for jewellery
# that is already back on the shelf.
half = [{'id': 4, 'lot_no': 'HM-2026-0004', 'sent_date': '2026-09-05',
         'expected_date': '2026-09-12',
         'items': [{'gr_wt': 7.0, 'result': 'pass', 'huid': 'C3D4E5'},
                   {'gr_wt': 3.0, 'result': 'pending'}]}]
p = H.dashboard([], half, '2026-09-11')
chk('only the piece still there is counted', p['out']['pieces'] == 1)
chk('and only its weight', p['out']['gross_wt'] == 3.0)
chk('the lot still shows both pieces', p['lots'][0]['pieces'] == 2)

head('the worst lot is first, because that is the one to ring about')
chk('late before merely out', d['lots'][0]['lot_no'] == 'HM-2026-0001')
chk('and received last', d['lots'][-1]['lot_no'] == 'HM-2026-0003')
chk('each carries how long it has been away', d['lots'][0]['days_out'] == 10)
chk('and the state it is in', d['lots'][0]['state'] == H.STATE_LATE)

head('an empty shop does not crash the panel')
e = H.dashboard([], [], '2026-09-11')
chk('nothing waiting', e['waiting']['pieces'] == 0)
chk('nothing out', e['out']['lots'] == 0)
chk('no lots', e['lots'] == [])
chk('and nothing unsaleable', e['saleable_gap'] == 0)

print('')
if FAIL:
    print('%d FAILED' % FAIL)
    sys.exit(1)
print('ALL PASS')
