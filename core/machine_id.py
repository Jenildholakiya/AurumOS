# -*- coding: utf-8 -*-
"""
Which machine is this, and has the shop already been set up on it.

Everything here is a pure function over plain values. The hardware is read
elsewhere and handed in, so the decision that locks a shop out of its own
books can be tested against the awkward cases instead of a real PC.

Why this exists, precisely.

The old code built one fingerprint by hashing five hardware ids together and
demanded an exact match on every launch. Four of those ids came from `wmic`,
which Microsoft has removed from current Windows. The day it disappeared the
hash changed, the app decided the database belonged to a different computer,
wiped the shop's stock, sales, customers and ledger, and showed the setup
wizard again. Same machine, same folder, same install.

Two rules follow from that, and they are the whole design:

**A probe that cannot answer is not evidence.** If the source that recorded
this machine's identity is unavailable now, the answer is "unknown", never
"different". Silence is not proof of theft.

**Identity is recorded, not recomputed.** The first source that answers is
stored with its name, and later runs re-check that same source. A hash over
whatever happened to be readable drifts the moment one reader goes quiet.

Nothing here deletes anything. A machine that really is the wrong one gets a
screen saying so, not an empty database.
"""

import hashlib
import re

# Best first. MachineGuid is a registry value written at install and read
# without spawning anything, so it survives the loss of every command-line
# tool Microsoft chooses to retire next.
SOURCE_ORDER = ('machineguid', 'csproduct', 'baseboard', 'bios', 'volume', 'mac')

# Values a BIOS reports when the field was never filled in. They identify a
# model, not a machine, so treating them as identity would make every PC of
# that model look like the same one.
JUNK = {
    '', 'none', 'null', 'n/a', 'na', 'unknown', 'default string',
    'to be filled by o.e.m.', 'to be filled by o.e.m', 'system serial number',
    'not specified', 'not applicable', '0', '00000000',
    'ffffffff-ffff-ffff-ffff-ffffffffffff',
    '00000000-0000-0000-0000-000000000000',
}

TAG_RE = re.compile(r'^([a-z]+):([0-9a-f]{16})$')


def clean(value):
    """A hardware id, or '' if what came back identifies nothing."""
    s = str(value or '').strip()
    if s.lower() in JUNK:
        return ''
    # Dashes and case differ between readers for the same value.
    return re.sub(r'[\s\-]', '', s).lower()


def digest(source, value):
    """A short, stable hash. Salted with the source name so the same serial
    read two ways cannot be mistaken for a match across sources."""
    v = clean(value)
    if not v:
        return ''
    return hashlib.sha256((source + '|' + v).encode('utf-8')).hexdigest()[:16]


def tag(source, value):
    """'machineguid:1a2b3c...' — the name matters as much as the number,
    because a later run has to re-check the same source, not any source."""
    d = digest(source, value)
    return (source + ':' + d) if d else ''


def read_tag(tagged):
    """('machineguid', '1a2b...') or ('', '') for anything unrecognised."""
    m = TAG_RE.match(str(tagged or '').strip().lower())
    return (m.group(1), m.group(2)) if m else ('', '')


def is_legacy(stored):
    """The old style: 24 hex characters and no source name.

    It cannot be re-checked, because nothing records what it was made from.
    An install carrying one is adopted rather than challenged.
    """
    s = str(stored or '').strip().lower()
    return bool(s) and not TAG_RE.match(s) and bool(re.match(r'^[0-9a-f]{16,64}$', s))


def identity(probe):
    """(tagged id, source) from the best source that actually answered."""
    for source in SOURCE_ORDER:
        t = tag(source, (probe or {}).get(source))
        if t:
            return t, source
    return '', ''


SAME = 'same'
DIFFERENT = 'different'
UNKNOWN = 'unknown'


def compare(stored, probe):
    """Is this the machine the identity was recorded on?

    Returns UNKNOWN whenever the question cannot honestly be answered: no
    identity recorded, an old-style one that cannot be re-checked, or a
    source that has stopped answering. Only a source that answers and
    answers differently returns DIFFERENT.
    """
    stored = str(stored or '').strip().lower()
    if not stored:
        return UNKNOWN
    if is_legacy(stored):
        return UNKNOWN
    source, want = read_tag(stored)
    if not source:
        return UNKNOWN
    got = digest(source, (probe or {}).get(source))
    if not got:
        # The reader that recorded this machine has gone quiet. That is a
        # statement about Windows, not about whose computer this is.
        return UNKNOWN
    return SAME if got == want else DIFFERENT


SETUP = 'setup'
LOGIN = 'login'
FOREIGN = 'foreign'


def decide(facts):
    """Which screen opens, and why.

    facts:
      has_accounts  — is there anyone who can log in
      setup_flag    — did a setup run to completion
      verdict       — compare() above

    There is deliberately no outcome that destroys data. A machine that
    really is the wrong one is told so and left alone.
    """
    f = facts or {}
    accounts = bool(f.get('has_accounts'))
    flag = bool(f.get('setup_flag'))
    verdict = f.get('verdict') or UNKNOWN

    if not accounts:
        # Nobody can log in, so a wizard is the only way forward. This is
        # also the half-finished case: a flag written but the account lost.
        return {'screen': SETUP,
                'reason': 'no account has been created on this machine yet',
                'adopt': False}

    if verdict == DIFFERENT:
        return {'screen': FOREIGN,
                'reason': 'this database was set up on another machine',
                'adopt': False}

    if not flag:
        # Accounts exist but the flag never landed, usually a setup that
        # crashed after writing the account. Let them in and stamp it.
        return {'screen': LOGIN,
                'reason': 'an account exists, so setup did run',
                'adopt': True}

    return {'screen': LOGIN,
            'reason': ('recognised' if verdict == SAME
                       else 'cannot read the hardware, and an account exists'),
            'adopt': verdict != SAME}


def rank(source):
    """Lower is better. An unknown source ranks below every known one."""
    try:
        return SOURCE_ORDER.index(source)
    except ValueError:
        return len(SOURCE_ORDER)


def should_restamp(stored, probe):
    """Whether to write a fresh identity over what is recorded.

    Never over a foreign machine, or a copied database would simply adopt
    whatever computer it was opened on. Otherwise the rule is: only ever
    trade up.

    Trading up matters because a shop first stamped from its network card is
    one card swap away from looking like a different computer. The moment a
    steadier reader answers, that is the one worth keeping.

    Trading down matters more. Replacing a registry value with a MAC address
    because the registry was briefly unreadable would pin the shop to that
    card, which is the same bug this module exists to prevent, one level
    down.
    """
    verdict = compare(stored, probe)
    if verdict == DIFFERENT:
        return False

    fresh, source = identity(probe)
    if not fresh:
        return False

    held, _ = read_tag(stored)
    if not held:
        # Nothing recorded, or the old style that cannot be re-checked.
        return True
    return rank(source) < rank(held)
