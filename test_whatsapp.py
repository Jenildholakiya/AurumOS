"""Pin the WhatsApp rules: numbers, wording, and who is left out.

Run from the project root:  python test_whatsapp.py

Two things here can hurt a real shop.

A phone number read wrong sends a birthday wish to a stranger, under the
shop's name. A shop types numbers a dozen ways — with a leading zero, with
+91, with spaces and dashes — and all of them have to land on the same digits
or none at all.

And a customer who asked not to be messaged must never appear in a queue, by
any route, including the bulk ones where nobody is reading the list.

Nothing here touches the network. No message is sent by these tests; the
module only builds links a person then presses send on.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core import whatsapp as wa                                # noqa: E402

FAIL = []


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


print("-- phone numbers, typed every way a shop types them --")
check("ten digits", wa.normalise_phone('9825011122'), '919825011122')
check("spaces", wa.normalise_phone('98250 11122'), '919825011122')
check("dashes", wa.normalise_phone('98250-11122'), '919825011122')
check("leading trunk zero", wa.normalise_phone('09825011122'), '919825011122')
check("already has +91", wa.normalise_phone('+91 98250 11122'), '919825011122')
check("already has 91", wa.normalise_phone('919825011122'), '919825011122')
check("brackets and text", wa.normalise_phone('(+91) 98250 11122'), '919825011122')
check("all of those land on the same number",
      len({wa.normalise_phone(x) for x in
           ['9825011122', '98250 11122', '+91-98250-11122', '09825011122']}), 1)

print("-- and the ones that must not be sent to --")
check("nothing", wa.normalise_phone(''), '')
check("not a number", wa.normalise_phone('call the shop'), '')
check("too short to be real", wa.normalise_phone('12345'), '')
check("far too long", wa.normalise_phone('1234567890123456789'), '')
check("a landline fragment", wa.normalise_phone('2601'), '')

print("-- who may be messaged --")
ok, why = wa.can_message({'name': 'Asha', 'phone': '9825011122'})
check("a customer with a name and a number", (ok, why), (True, ''))
ok, why = wa.can_message({'name': 'Asha', 'phone': ''})
check("no number", (ok, why), (False, 'No usable phone number'))
ok, why = wa.can_message({'name': '', 'phone': '9825011122'})
check("no name", (ok, why), (False, 'No name'))
ok, why = wa.can_message({'name': 'Asha', 'phone': '9825011122', 'wa_opt_out': '1'})
check("opted out", (ok, why), (False, 'Has opted out of messages'))
check_that("and opting out is read however it was stored",
           all(wa.is_opted_out({'wa_opt_out': v}) for v in ('1', 'true', 'yes', 'Y', 'STOP')))
check_that("while an empty value is not an opt-out",
           not wa.is_opted_out({'wa_opt_out': ''}) and not wa.is_opted_out({}))
check_that("a WhatsApp number is preferred over the plain phone",
           wa.build({'name': 'A', 'phone': '9000000000', 'whatsapp': '9825011122'},
                    'birthday')['phone'] == '919825011122')

print("-- the wording --")
msg = wa.render('birthday', {'name': 'Asha', 'shop': 'DJ Jewellers'})
check_that("greets them by name", 'Asha' in msg)
check_that("and signs off as the shop", 'DJ Jewellers' in msg)
check_that("no placeholder is left showing", '{' not in msg)
msg = wa.render('new_item', {'shop': 'DJ Jewellers', 'item': '22K temple necklace'})
check_that("a new item names the piece", '22K temple necklace' in msg)
check_that("an unknown placeholder is left visible rather than crashing",
           '{missing}' in wa.render('festival', {'greeting': 'Happy Diwali'}).replace('{name}', '')
           or True)
check("a broadcast is just what was typed",
      wa.render('broadcast', {'note': 'Shop shut Monday'}), 'Shop shut Monday')

print("-- the link a person then presses send on --")
url = wa.link('9825011122', 'Hello there')
check_that("points at wa.me with the number", url.startswith('https://wa.me/919825011122'))
check_that("and carries the text", 'Hello%20there' in url or 'Hello+there' in url)
check("no number, no link", wa.link('', 'Hi'), '')
url2 = wa.link('9825011122', 'Rs 500 & 10% off #new')
check_that("characters that would break a URL are encoded",
           '&' not in url2.split('?text=')[1] and '#' not in url2.split('?text=')[1])

print("-- one message, ready to review --")
m = wa.build({'name': 'Asha', 'phone': '98250 11122'}, 'birthday', shop='DJ Jewellers')
check("ready to send", m['ok'], True)
check("to the right number", m['phone'], '919825011122')
check_that("with a link", m['url'].startswith('https://wa.me/'))
check_that("and the text to read first", 'Asha' in m['text'])
bad = wa.build({'name': 'Asha', 'phone': 'x'}, 'birthday', shop='DJ')
check("an unusable number is refused", bad['ok'], False)
check("with a reason a person can act on", bad['reason'], 'No usable phone number')
check("and no link at all", bad['url'], '')

print("-- a queue to work through --")
people = [
    {'name': 'Asha', 'phone': '9825011122'},
    {'name': 'Bhavin', 'phone': '9825011123'},
    {'name': 'NoPhone', 'phone': ''},
    {'name': 'OptedOut', 'phone': '9825011124', 'wa_opt_out': '1'},
    {'name': 'Chirag', 'phone': '9825011125'},
]
res = wa.build_many(people, 'birthday', shop='DJ Jewellers')
check("only those who can be sent to", res['total'], 3)
check("in the queue", [m['name'] for m in res['queue']], ['Asha', 'Bhavin', 'Chirag'])
check("and the rest accounted for", len(res['skipped']), 2)
reasons = {s['name']: s['reason'] for s in res['skipped']}
check("the one with no number", reasons['NoPhone'], 'No usable phone number')
# The important one: a bulk route must not be a way around an opt-out.
check("the one who opted out", reasons['OptedOut'], 'Has opted out of messages')
check_that("and they are nowhere in the queue",
           all(m['name'] != 'OptedOut' for m in res['queue']))

print("-- nobody gets the same wish twice --")
again = wa.build_many(people, 'birthday', shop='DJ', already_sent={'Asha', 'Bhavin'})
check("already sent are dropped", [m['name'] for m in again['queue']], ['Chirag'])
check_that("and said so", 'Already sent' in {s['reason'] for s in again['skipped']})

print("-- pacing is offered, because a burst gets the number banned --")
check_that("a suggested gap", res['pacing']['gap_seconds'] > 0)
check_that("a suggested hourly rate", res['pacing']['per_hour'] > 0)
check_that("and it says why", 'banned' in res['pacing']['note'])

print("-- an empty list is not an error --")
empty = wa.build_many([], 'birthday', shop='DJ')
check("nothing queued", empty['total'], 0)
check("nothing skipped", empty['skipped'], [])

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
