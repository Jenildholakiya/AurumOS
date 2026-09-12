"""Setup runs once, and never again on the same machine.

Run from the project root:  python test_setup_once.py

This drives the real database against a throwaway file and replays what
happened on 12 September: a shop set up the day before, the hardware reader
silent the next morning, and the software deciding the database belonged to
somebody else. It wiped the stock, the sales, the customers and the ledger,
and opened the setup wizard.

The checks here are the promises that stops it happening again: setup asked
for once, a silent reader never mistaken for a different computer, a licence
lock code that does not move, and nothing on this path that deletes a row.
"""

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

TMP = tempfile.mkdtemp(prefix='aurum_setup_')
os.environ['AURUM_DB_PATH'] = os.path.join(TMP, 'aurum_local.db')

from database.db_manager import DBManager                     # noqa: E402
from core import machine_id as M                              # noqa: E402


def fresh(probe=None):
    """A DBManager with the hardware readers replaced by a fixed answer."""
    DBManager._ID_CACHE = dict(probe) if probe is not None else {}
    DBManager._FP_CACHE = None
    DBManager._FP_PINNED = None
    db = DBManager()
    if probe is not None:
        DBManager._ID_CACHE = dict(probe)
    return db


YESTERDAY = {'machineguid': '06e336cd-fb01-44bd-8d3a-5853f416fd2b',
             'bios': 'H9Y69X2', 'baseboard': '/H9Y69X2/CNPE10095B0PIJ/',
             'mac': '251485283382915'}
# The same machine the next morning, after Windows dropped the tool that
# read four of those. Only the network card still answers.
TODAY = {'mac': '251485283382915'}
ANOTHER_PC = {'machineguid': '11111111-2222-3333-4444-555555555555',
              'mac': '777'}


def main():
    head('a machine nobody has set up yet')
    db = fresh(YESTERDAY)
    chk('opens the wizard', db.is_setup_done() is False)

    head('the shop completes setup')
    db.save_setup('DJ Jewellers', 'Owner', '9820011223', 'Rajkot', '1234',
                  license_key='AR-CDCA-QPDU-LRSK-SNH5')
    with db._get_connection() as conn:
        accounts = conn.execute("SELECT COUNT(*) FROM admin_creds").fetchone()[0]
        rows = {r['key']: r['value'] for r in conn.execute(
            "SELECT key,value FROM app_config WHERE key IN "
            "('setup_done','machine_identity','machine_fingerprint')")}
    chk('an account exists', accounts > 0)
    chk('the flag is set', rows.get('setup_done') == '1')
    chk('and the machine is recorded by name of reader',
        str(rows.get('machine_identity') or '').startswith('machineguid:'))
    lock_then = db._machine_fingerprint()[:8].upper()
    chk('a licence lock code exists', len(lock_then) == 8)

    head('the same day, opened again')
    db = fresh(YESTERDAY)
    chk('goes straight to the login', db.is_setup_done() is True)
    chk('and the lock code has not moved',
        db._machine_fingerprint()[:8].upper() == lock_then)

    head('the next morning, the reader has gone quiet')
    # This is the exact failure. wmic removed, four ids empty, and the old
    # code called it a different computer.
    db = fresh(TODAY)
    verdict = M.compare(rows.get('machine_identity'), TODAY)
    chk('the reader cannot answer', verdict == M.UNKNOWN)
    chk('which is not a different machine', verdict != M.DIFFERENT)
    chk('so the shop still goes to its login', db.is_setup_done() is True)

    head('and its books are untouched')
    with db._get_connection() as conn:
        still = conn.execute("SELECT COUNT(*) FROM admin_creds").fetchone()[0]
        biz = conn.execute(
            "SELECT value FROM app_config WHERE key='business_name'").fetchone()
    chk('the account survived', still == accounts)
    chk('and so did the shop name', biz and biz[0] == 'DJ Jewellers')
    chk('the lock code is still the one it was activated with',
        db._machine_fingerprint()[:8].upper() == lock_then)

    head('the reader comes back')
    db = fresh(YESTERDAY)
    chk('still the login', db.is_setup_done() is True)
    chk('and still the same lock code',
        db._machine_fingerprint()[:8].upper() == lock_then)

    head('the folder really is copied to another PC')
    db = fresh(ANOTHER_PC)
    # Not the wizard. The wizard would write a new shop over the one in
    # the file. The old code wiped instead, which is worse still.
    chk('it does not run the wizard over the shop that is there',
        db.is_setup_done() is True)
    chk('and it is flagged as belonging elsewhere',
        getattr(db, '_foreign', False) is True)
    # The old code deleted everything at this point. A shop that moved its
    # own PC, or whose motherboard was replaced, lost its books to it.
    with db._get_connection() as conn:
        after = conn.execute("SELECT COUNT(*) FROM admin_creds").fetchone()[0]
        biz2 = conn.execute(
            "SELECT value FROM app_config WHERE key='business_name'").fetchone()
    chk('nothing was deleted', after == accounts)
    chk('the shop name is still there', biz2 and biz2[0] == 'DJ Jewellers')

    head('no path through this code wipes anything')
    src = open(os.path.join(ROOT, 'database', 'db_manager.py'),
               encoding='utf-8', errors='replace').read()
    body = src[src.index('def is_setup_done'):src.index('def mark_setup_done')]
    chk('the setup check never calls the wipe', '_wipe_business_data' not in body)
    chk('and nothing else calls it either',
        src.count('_wipe_business_data') == 1)

    head('the readers it uses still exist on this Windows')
    DBManager._ID_CACHE = None
    real = DBManager._id_probe()
    answered = sorted(k for k, v in real.items() if v)
    chk('at least one reader answers (%s)' % (', '.join(answered) or 'none'),
        bool(answered))
    # The point of the rewrite: none of these is wmic, which is gone.
    chk('the registry value is readable', bool(real.get('machineguid')))
    chk('and it is what identity is taken from',
        M.identity(real)[1] == 'machineguid')

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
