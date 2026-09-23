import os
import sys
import tempfile
import time

sys.path.insert(0, 'E:\\AurumOS_Client')
os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()
os.environ['AURUM_DB_PATH'] = os.path.join(os.environ['AURUM_DB_DIR'], 't.db')

passed, failed = 0, 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}')


from database.db_manager import DBManager
from database import cluster_store as store
from network import cluster_server as srv
from network import hm_share as hms
from network import lan_scan

db = DBManager()

print('-- enroll refused by seat limit leaves no row --')
store.register_terminal(db, 'KNOWN-PC', hostname='known', ip='1.1.1.1')
# licensed=1: KNOWN-PC holds the only seat
check('known inside limit', store.within_seat_limit(db, 'KNOWN-PC', 1))
# simulate _enroll for a NEW pc: register, check, refuse, clean up
known = store.get_terminal(db, 'NEW-PC')
check('new pc unknown first', known is None)
terminal, secret = store.register_terminal(db, 'NEW-PC', hostname='n', ip='2.2.2.2')
allowed = store.within_seat_limit(db, 'NEW-PC', 1)
if not allowed and not known:
    store.remove_terminal(db, 'NEW-PC')
check('new pc refused', not allowed)
check('no row left behind', store.get_terminal(db, 'NEW-PC') is None)
check('known pc untouched', store.get_terminal(db, 'KNOWN-PC') is not None)

print('-- constant-time secret compare --')
check('match', srv._secrets_match('abc123', 'abc123'))
check('mismatch', not srv._secrets_match('abc123', 'abc124'))
check('empty presented refuses', not srv._secrets_match('', 'abc123'))
check('empty expected refuses', not srv._secrets_match('abc123', ''))

print('-- offline-safe local ip --')
ip = lan_scan.local_ip()
check('returns something routable or loopback', bool(ip))
check('adapter enumeration works', bool(lan_scan._adapter_ip()) or ip == '127.0.0.1')

print('-- hm sessions persist --')
cid = db.hm_save_centre({'name': 'Test Centre'})
sid, max_age = hms.hm_session_create(db, cid)
check('session created', bool(sid) and max_age > 0)
check('session resolves', hms.hm_session_get(db, sid) == cid)
hms.hm_session_drop(db, sid)
check('logout drops', hms.hm_session_get(db, sid) is None)

print('-- portal token expiry --')
out = hms.portal_issue(db, cid, email='centre@example.com')
check('issue works', out.get('status') == 'success')
tok = out.get('token')
check('token live', hms.portal_centre(db, tok) is not None)
check('live_token finds it', hms.live_token(db, cid) == tok)
# force-expire it
with db._get_connection() as conn:
    conn.execute("UPDATE hm_portal_tokens SET expires_at=? WHERE token=?",
                 (time.time() - 1, tok))
    conn.commit()
check('expired token denied', hms.portal_centre(db, tok) is None)
check('expired token not live', hms.live_token(db, cid) == '')

print('-- sync settled accounting --')
res = db.apply_incoming_rows('PEER-X', {'sales_history': []})
check('empty changes ok', res.get('status') == 'success')
check('failed key present', 'failed' in res)

print('-- settle partition --')
from network.cluster_client import partition_settled
mine = {'sales_history': [{'sync_version': 1}, {'sync_version': 2}],
        'clients_master': [{'sync_version': 1}]}
confirmed, short = partition_settled(
    mine, {'sales_history': 2, 'clients_master': 1}, {})
check('full accept confirms all', set(confirmed) == {'sales_history', 'clients_master'}
      and not short)
confirmed, short = partition_settled(
    mine, {'sales_history': 1, 'clients_master': 1}, {})
check('short table held back', set(confirmed) == {'clients_master'}
      and 'sales_history' in short)
confirmed, short = partition_settled(
    mine, {'sales_history': 2, 'clients_master': 1}, {'clients_master': 1})
check('failed table held back', set(confirmed) == {'sales_history'}
      and 'clients_master' in short)

print('-- no hardcoded licence defaults --')
import re as _re
live = open('main.py', encoding='utf-8').read()
sub = open('subscription_manager.py', encoding='utf-8').read()
check('no plan pro default in main',
      "data.get('plan', 'pro')" not in live)
check('no fabricated-year default in main',
      'remaining_days\', SUBSCRIPTION_DAYS' not in live
      and 'remaining_days", SUBSCRIPTION_DAYS' not in live)
check('no bare plan=pro in subscription manager',
      not _re.search(r"^\s*plan\s*=\s*'pro'\s*$", sub, _re.M))
check('no bare remaining=SUBSCRIPTION_DAYS in subscription manager',
      not _re.search(r"^\s*remaining\s*=\s*SUBSCRIPTION_DAYS\s*$", sub, _re.M))

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
