"""Whether this is the same machine, and whether setup has to run again.

Run from the project root:  python test_machine_id.py

This is the code that decided, on 11 September, that a shop's own computer
belonged to somebody else. It wiped the stock, the sales, the customers and
the ledger, and showed the setup wizard. The machine had not changed. What
changed was that Windows removed `wmic`, so four of the five hardware ids it
hashed came back empty and the hash came out different.

Every case below is written against that: a reader going quiet must never
look like a different computer, and no outcome may destroy data.
"""

import sys

from core import machine_id as M

FAIL = 0


def chk(label, cond):
    global FAIL
    if not cond:
        FAIL += 1
    print('  [%s] %s' % ('PASS' if cond else 'FAIL', label))


def head(t):
    print('\n-- %s --' % t)


# The machine this was written on, as its readers report it.
REAL = {
    'machineguid': '06e336cd-fb01-44bd-8d3a-5853f416fd2b',
    'csproduct':   '4C4C4544-0039-5910-8036-C8C04F395832',
    'bios':        'H9Y69X2',
    'baseboard':   '/H9Y69X2/CNPE10095B0PIJ/',
    'volume':      '7A4A0D43',
    'mac':         '251485283382915',
}

head('a hardware id is written differently by different readers')
chk('case does not matter', M.digest('bios', 'H9Y69X2') == M.digest('bios', 'h9y69x2'))
chk('nor do dashes', M.digest('csproduct', '4C4C-4544') == M.digest('csproduct', '4c4c4544'))
chk('nor surrounding space', M.digest('bios', '  H9Y69X2 ') == M.digest('bios', 'H9Y69X2'))
# The same serial read as two different things must not look like a match,
# or a board serial could be mistaken for a BIOS serial.
chk('but the source does', M.digest('bios', 'X1') != M.digest('baseboard', 'X1'))

head('what a BIOS says when nobody filled the field in')
for junk in ('To Be Filled By O.E.M.', 'Default string', 'System Serial Number',
             '00000000-0000-0000-0000-000000000000', 'none', '', '   '):
    chk('%-40r identifies nothing' % junk, M.digest('bios', junk) == '')
# Otherwise every PC of that model would answer to the same identity.
chk('two machines with the same blank field are not the same machine',
    M.identity({'bios': 'Default string'})[0] == '')

head('identity comes from the best reader that answers')
chk('the registry value wins when it is there',
    M.identity(REAL)[1] == 'machineguid')
chk('and the tag names it', M.identity(REAL)[0].startswith('machineguid:'))
no_reg = dict(REAL); no_reg['machineguid'] = ''
chk('without it, the next one down', M.identity(no_reg)[1] == 'csproduct')
chk('with nothing readable there is no identity',
    M.identity({'bios': '', 'mac': ''})[0] == '')
chk('and none at all is not a crash', M.identity(None)[0] == '')

head('the same machine, recognised')
ident, _ = M.identity(REAL)
chk('recognises itself', M.compare(ident, REAL) == M.SAME)
# A shop swaps a disk or a network card. The registry value does not move,
# so the machine is still the machine.
swapped = dict(REAL, mac='999', volume='DEADBEEF')
chk('a new disk or network card changes nothing',
    M.compare(ident, swapped) == M.SAME)

head('a genuinely different machine')
other = dict(REAL, machineguid='11111111-2222-3333-4444-555555555555')
chk('is reported as different', M.compare(ident, other) == M.DIFFERENT)

head('a reader that has gone quiet is not evidence of anything')
# This is the whole bug. wmic vanished, the reader returned nothing, and the
# old code read that silence as "different computer" and wiped the books.
gone = dict(REAL, machineguid='')
chk('silence is unknown, never different', M.compare(ident, gone) == M.UNKNOWN)
chk('and it is certainly not a match', M.compare(ident, gone) != M.SAME)
chk('every reader silent is still only unknown',
    M.compare(ident, {}) == M.UNKNOWN)
chk('nothing recorded yet is unknown', M.compare('', REAL) == M.UNKNOWN)
chk('and garbage in the field is unknown',
    M.compare('not a real tag', REAL) == M.UNKNOWN)

head('an install carrying the old style of fingerprint')
# 24 hex characters with no record of what made them. It cannot be
# re-checked, so it is adopted rather than used to lock anyone out.
chk('is recognised as the old style', M.is_legacy('14b9dc83e5f1a2b3c4d5e6f7') is True)
chk('a tagged one is not', M.is_legacy('machineguid:0123456789abcdef') is False)
chk('and it never reads as a different machine',
    M.compare('14b9dc83e5f1a2b3c4d5e6f7', REAL) == M.UNKNOWN)

head('which screen opens')
chk('a fresh machine gets the wizard',
    M.decide({'has_accounts': False, 'setup_flag': False,
              'verdict': M.UNKNOWN})['screen'] == M.SETUP)
chk('a set-up machine goes to the login',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.SAME})['screen'] == M.LOGIN)

# The case that cost a shop its books. An account exists, setup ran, and the
# hardware reader is silent. That is a login, not a wizard.
chk('a silent reader still goes to the login',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.UNKNOWN})['screen'] == M.LOGIN)
chk('and never to the wizard',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.UNKNOWN})['screen'] != M.SETUP)

chk('a genuinely foreign database is told so',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.DIFFERENT})['screen'] == M.FOREIGN)
# Not the wizard, because the wizard would write over the shop it found.
chk('and is not offered the wizard',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.DIFFERENT})['screen'] != M.SETUP)

head('no outcome destroys anything')
seen = set()
for accounts in (True, False):
    for flag in (True, False):
        for verdict in (M.SAME, M.DIFFERENT, M.UNKNOWN):
            seen.add(M.decide({'has_accounts': accounts, 'setup_flag': flag,
                               'verdict': verdict})['screen'])
chk('there are only three outcomes', seen == {M.SETUP, M.LOGIN, M.FOREIGN})
chk('and none of them is a wipe', 'wipe' not in seen)

head('a setup that crashed halfway')
# The account was written and the flag was not. Sending them back through
# the wizard would overwrite the shop they already have.
r = M.decide({'has_accounts': True, 'setup_flag': False, 'verdict': M.UNKNOWN})
chk('an account without a flag still logs in', r['screen'] == M.LOGIN)
chk('and the identity is stamped on the way through', r['adopt'] is True)
# The other half: a flag with no account is a wizard, because nobody can
# log in to a machine with no accounts on it.
chk('a flag without an account runs the wizard',
    M.decide({'has_accounts': False, 'setup_flag': True,
              'verdict': M.SAME})['screen'] == M.SETUP)

head('when to write a fresh identity')
chk('nothing recorded, so record something',
    M.should_restamp('', REAL) is True)
chk('the old style is replaced', M.should_restamp('14b9dc83e5f1a2b3c4d5e6f7', REAL) is True)
# Only ever trade up. Replacing a registry value with a MAC address
# because the registry was briefly unreadable would pin the shop to a
# network card, and the next card swap would read as a different computer:
# the same bug again, one level down.
chk('a weak identity is traded up when a better reader answers',
    M.should_restamp(M.tag('mac', REAL['mac']), REAL) is True)
chk('a good one is never traded down for a worse one',
    M.should_restamp(M.tag('machineguid', REAL['machineguid']), gone) is False)
chk('nor churned when it is already the best available',
    M.should_restamp(M.tag('machineguid', REAL['machineguid']), REAL) is False)
# A copied database must not simply adopt whichever computer opens it.
chk('and a foreign machine is never stamped over',
    M.should_restamp(M.tag('machineguid', REAL['machineguid']), other) is False)
chk('and the machine is still let in while that reader is quiet',
    M.compare(M.tag('machineguid', REAL['machineguid']), gone) == M.UNKNOWN)
# A machine that has just been recognised keeps what it has, so a good
# identity is never churned.
chk('a recognised machine is left alone', M.should_restamp(ident, REAL) is False)
chk('nor is anything stamped when nothing can be read',
    M.should_restamp('', {}) is False)

head('the day it actually broke, replayed')
# 11 September: set up while wmic answered. 12 September: wmic gone.
yesterday = {'machineguid': REAL['machineguid'], 'bios': 'H9Y69X2',
             'baseboard': '/H9Y69X2/CNPE10095B0PIJ/', 'mac': '251485283382915'}
today = {'machineguid': '', 'csproduct': '', 'bios': '', 'baseboard': '',
         'volume': '', 'mac': '251485283382915'}
stamped, _ = M.identity(yesterday)
chk('the shop was stamped from the registry', stamped.startswith('machineguid:'))
chk('the next morning the reader is silent',
    M.compare(stamped, today) == M.UNKNOWN)
chk('so the shop opens at its login screen',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.compare(stamped, today)})['screen'] == M.LOGIN)
chk('and its books are not touched',
    M.decide({'has_accounts': True, 'setup_flag': True,
              'verdict': M.compare(stamped, today)})['screen'] != M.FOREIGN)

print('')
if FAIL:
    print('%d FAILED' % FAIL)
    sys.exit(1)
print('ALL PASS')
