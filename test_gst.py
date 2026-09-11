"""Pin the GST rules used by the setup and settings screens.

Run from the project root:  python test_gst.py

A GSTIN goes on a tax invoice and into a return. The check character exists so
a typo can be caught before either happens, and the whole point of doing it
here is that it needs no network and no lookup. These tests cover the checksum
both ways, the parts read out of a valid number, and what gets stored when the
shop says it is not registered.

Nothing here touches the database, the network or a file.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core import gst                                          # noqa: E402

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


# 27AAPFU0939F1ZV is the number used as the worked example in GST
# documentation; the rest are built by asking the module for the right check
# character, which is the same arithmetic a real number is issued with.
VALID = '27AAPFU0939F1ZV'
GUJARAT = '24AAACC1206D1ZM'

print("-- the check character --")
check("worked example validates", gst.validate(VALID)['ok'], True)
check("check character is derived, not guessed", gst.checksum_char(VALID[:14]), 'V')
check("a Gujarat number validates", gst.validate(GUJARAT)['ok'], True)

# Change one character and the number must stop validating. This is the whole
# reason the check digit exists, so test it against every position.
broken = 0
for i in range(14):
    ch = VALID[i]
    swap = '0' if ch != '0' else '1'
    if i < 2 or (2 <= i < 7) or (7 <= i < 11) or i >= 11:
        candidate = VALID[:i] + swap + VALID[i + 1:]
        if not gst.validate(candidate)['ok']:
            broken += 1
check("every single-character change is rejected", broken, 14)

print("-- shape --")
check("empty asks for a number", gst.validate('')['error'], 'Enter a GSTIN.')
check_that("short number says how short",
           '14' in gst.validate('27AAPFU0939F1Z')['error'])
check_that("wrong shape is named as such",
           'shape' in gst.validate('27AAPFU0939F1Q9')['error'])
check_that("unknown state code is named",
           '49' in gst.validate('49AAPFU0939F1ZV')['error'])
check_that("typo is reported as a typo",
           'typo' in gst.validate('27AAPFU0939F1ZX')['error'])

print("-- reading a valid number apart --")
r = gst.validate(VALID)
check("state code", r['state_code'], '27')
check("state name", r['state'], 'Maharashtra')
check("PAN comes out of the GSTIN", r['pan'], 'AAPFU0939F')
check("PAN is well formed", gst.validate_pan(r['pan']), True)
check("entity character", r['entity_code'], '1')
check("Gujarat resolves", gst.validate(GUJARAT)['state'], 'Gujarat')
check("Ladakh (added 2020) is known", gst.STATE_CODES['38'], 'Ladakh')

print("-- typing tolerance --")
check("spaces and dashes are ignored", gst.normalise(' 27-aapfu 0939f1zv '), VALID)
check("lower case is accepted", gst.validate('27aapfu0939f1zv')['ok'], True)

print("-- rates --")
check("a plain number", gst.clean_rate('1.5'), 1.5)
check("a percent sign is tolerated", gst.clean_rate('1.5%'), 1.5)
check("junk falls back", gst.clean_rate('abc', 1.5), 1.5)
check("negative falls back", gst.clean_rate('-2', 1.5), 1.5)
check("over 100 falls back", gst.clean_rate('120', 1.5), 1.5)
check("blank falls back", gst.clean_rate('', 1.5), 1.5)

print("-- what gets stored --")
fields, err = gst.build({'gst_registered': '1', 'gstin': VALID, 'gst_legal_name': 'Test Jewellers'})
check("a registered shop stores no error", err, '')
check("state is filled in from the number", fields['gst_state'], 'Maharashtra')
check("PAN is filled in from the number", fields['pan'], 'AAPFU0939F')
check("legal name is kept", fields['gst_legal_name'], 'Test Jewellers')
check("jewellery rates default to 1.5 + 1.5",
      (fields['gst_cgst_rate'], fields['gst_sgst_rate']), ('1.5', '1.5'))
check("HSN defaults to 7113", fields['gst_hsn'], '7113')

fields, err = gst.build({'gst_registered': '1', 'gstin': '27AAPFU0939F1ZX'})
check_that("a bad number is refused, not stored", bool(err))

fields, err = gst.build({'gst_registered': '0', 'gstin': 'nonsense'})
check("an unregistered shop is not asked to validate", err, '')
check("and is not recorded as registered", fields['gst_registered'], '0')
check_that("but what was typed is kept, so the toggle is reversible",
           fields['gstin'] == 'NONSENSE')

fields, err = gst.build({'gst_registered': '1', 'gstin': VALID, 'gst_cgst_rate': '2.5',
                         'gst_sgst_rate': 'rubbish', 'gst_hsn': '7113abc99',
                         'gst_price_inclusive': 'yes'})
check("a typed rate is kept", fields['gst_cgst_rate'], '2.5')
check("a junk rate falls back", fields['gst_sgst_rate'], '1.5')
check("HSN keeps only digits", fields['gst_hsn'], '711399')
check("inclusive pricing is a flag", fields['gst_price_inclusive'], '1')

fields, _ = gst.build({'gst_registered': '1', 'gstin': VALID},
                      existing={'gst_legal_name': 'Kept From Before'})
check("existing values survive a partial save", fields['gst_legal_name'], 'Kept From Before')

print("-- defaults for a shop that has never opened the screen --")
d = gst.defaults()
check("not registered until said otherwise", d['gst_registered'], '0')
check("every stored key is present", sorted(d), sorted([
    'gst_registered', 'gstin', 'gst_legal_name', 'gst_state', 'gst_state_code',
    'pan', 'gst_cgst_rate', 'gst_sgst_rate', 'gst_hsn', 'gst_price_inclusive']))

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
