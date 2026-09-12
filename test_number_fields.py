"""No stepper arrows on number fields, anywhere in the software.

Run from the project root:  python test_number_fields.py

A jeweller types a weight or an amount. Nobody nudges 32.400 grams one
click at a time, and the little arrows sit on top of the last digit where
they get clicked by accident.

Seventeen screens had each written the rule for themselves and six had not,
which is how the arrows survived. The rule now lives in boot.js, the one
file every screen already loads for the bridge to work, so a new screen
cannot be built that quietly gets them back. This checks that it is still
there, and that no page with a number field has been left out.
"""

import os
import re
import sys

ROOT = os.path.abspath(os.path.dirname(__file__))
UI = os.path.join(ROOT, 'ui')

FAIL = 0


def chk(label, cond):
    global FAIL
    if not cond:
        FAIL += 1
    print('  [%s] %s' % ('PASS' if cond else 'FAIL', label))


def head(t):
    print('\n-- %s --' % t)


def read(name):
    with open(os.path.join(UI, name), encoding='utf-8', errors='replace') as f:
        return f.read()


def has_rule(text):
    """Does this file suppress the steppers itself."""
    return bool(re.search(r'spin-button', text))


BOOT_TAG = re.compile(r'<script[^>]+src=[\'"]boot\.js', re.I)


def loads_boot(text):
    """A script tag, not a comment that happens to name the file."""
    return bool(BOOT_TAG.search(text))


def main():
    pages = sorted(f for f in os.listdir(UI) if f.endswith('.html'))

    head('the rule lives where every screen already looks')
    boot = read('boot.js')
    chk('boot.js hides the outer stepper',
        'outer-spin-button' in boot)
    chk('and the inner one', 'inner-spin-button' in boot)
    chk('with appearance turned off', 'appearance:none' in boot.replace(' ', ''))
    chk('and it is injected into the page',
        'aurum-no-steppers' in boot and 'appendChild' in boot)
    # Prefixed on purpose. The unprefixed property on the input itself would
    # change how Chromium draws the field, not just its buttons.
    chk('Firefox is covered with the prefixed property only',
        '-moz-appearance:textfield' in boot.replace(' ', ''))
    chk('and the unprefixed one is not applied to the field',
        not re.search(r'input\[type=number\]\{appearance', boot.replace(' ', '')))

    head('every screen with a number field is covered')
    uncovered = []
    for name in pages:
        text = read(name)
        if 'type="number"' not in text and "type='number'" not in text:
            continue
        # Covered either by loading boot.js, or by carrying the rule itself.
        if loads_boot(text) or has_rule(text):
            continue
        uncovered.append(name)
    chk('no page is left showing arrows (%s)'
        % (', '.join(uncovered) or 'none'), not uncovered)

    head('the one screen that does not load boot.js')
    # bastion_keygen.html is standalone, so it carries the rule itself
    # rather than being the single screen that still has steppers.
    lone = [n for n in pages
            if ('type="number"' in read(n)) and not loads_boot(read(n))]
    for name in lone:
        chk('%s carries the rule itself' % name, has_rule(read(name)))
    if not lone:
        print('  [PASS] every screen with a number field loads boot.js')

    head('typing is not affected')
    # Hiding the buttons must not touch the field's type, its step, or its
    # bounds, or a weight would stop validating.
    sample = read('hallmark.html')
    chk('number fields are still number fields',
        'type="number"' in sample)
    chk('and still carry their step and bounds',
        re.search(r'type="number"[^>]*step=', sample) is not None)
    chk('nothing turned them into text boxes',
        'type="text" id="ct-rate"' not in sample)

    print('')
    return 1 if FAIL else 0


if __name__ == '__main__':
    code = main()
    print('%d FAILED' % FAIL if code else 'ALL PASS')
    sys.exit(code)
