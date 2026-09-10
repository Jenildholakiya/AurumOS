"""End-to-end check of a staff PC joining the shop server and signing in.

Run from the project root:  python test_cluster_join.py

A real server is started on a real port and driven over real HTTP, because the
parts most likely to break here are the ones a mock would hide: a wrong address
has to fail in a way the person who typed it can act on, and a password has to
be checked on the owner's PC rather than on the terminal.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath('.'))

FAIL = []
PORT = 58971          # not the live port, so a running AurumOS is undisturbed


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


work = tempfile.mkdtemp(prefix='aurum_cluster_')
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
os.makedirs(os.path.dirname(os.environ['AURUM_DB_PATH']), exist_ok=True)

from database.db_manager import DBManager
from database import cluster_store as store
from network import cluster_server, cluster_client as client

# Keep the test off the project's real config.json.
_cfg = os.path.join(work, 'config.json')
client._config_path = lambda: _cfg

db = DBManager()
db.complete_initial_setup('Dholakiya Jewellers', 'owner', 'ownerpass123')
db.add_staff_user('meena', 'staffpass99', ['billing'])

PLAN = {'value': 'enterprise', 'features': []}
cluster_server.start_server(db, context={
    'business_name': lambda: 'Dholakiya Jewellers',
    'version':       lambda: '1.1.5',
    'plan':          lambda: PLAN['value'],
    'features':      lambda: PLAN['features'],
    'software_type': lambda: 'wholesale',
}, host='127.0.0.1', port=PORT)

print("-- typing an address into setup --")
bad = client.verify_server('127.0.0.1', 59999)
check("a port with nothing on it fails", bad['status'], 'error')
check_that("and says the main PC may be off or firewalled",
           'firewall' in bad['message'] or 'not running' in bad['message'],
           bad['message'])

unreachable = client.verify_server('10.255.255.1', PORT)
check("an unreachable address fails", unreachable['status'], 'error')

blank = client.verify_server('')
check("a blank address is caught before any network call", blank['status'], 'error')

good = client.verify_server('127.0.0.1', PORT)
check("the real server verifies", good['status'], 'success')
check("and names the shop, so the right PC is recognisable",
      good['business'], 'Dholakiya Jewellers')

print("\n-- joining --")
joined = client.join_server(db, '127.0.0.1', PORT)
check("the PC joins", joined['status'], 'success')
check("it is remembered as a terminal", client.local_role(), client.ROLE_TERMINAL)
saved = client.load_config()
check_that("the server address is stored", saved.get('server_ip') == '127.0.0.1')
check_that("a per-PC secret was issued", len(saved.get('terminal_secret', '')) > 32)

listed = store.list_terminals(db)
check("the owner now sees one PC", len(listed), 1)
check("waiting to be assigned", listed[0]['status'], 'pending')
check_that("and the secret is never handed out in listings",
           'secret' not in listed[0])

rejoin = client.join_server(db, '127.0.0.1', PORT)
check("re-running setup does not create a second entry",
      len(store.list_terminals(db)), 1)
check("and still succeeds", rejoin['status'], 'success')

print("\n-- signing in --")
wrong = client.login('not-the-password')
check("a wrong password is refused", wrong['status'], 'denied')
check_that("and says how many tries are left", 'remaining' in wrong['message'],
           wrong['message'])

ok = client.login('staffpass99')
check("the staff password works", ok['status'], 'success')
check("signed in as that staff member", ok['username'], 'meena')
check("as staff, never as owner", ok['role'], 'staff')

owner_login = client.login('ownerpass123')
check("the owner password on a terminal still only grants staff",
      owner_login['role'], 'staff')

print("\n-- what the terminal may open --")
store.assign_terminal(db, saved['terminal_id'], username='meena',
                      bundles=['billing'], status=store.STATUS_APPROVED)
assigned = client.login('staffpass99')
check("the assigned module is returned", assigned['bundles'], ['billing'])
check("billing is the wholesale bill screen", assigned['pages'],
      ['billing.html', 'tag_audit.html'])

store.assign_terminal(db, saved['terminal_id'], username='meena',
                      bundles=['billing', 'inventory'])
PLAN['value'] = 'pro'
downgraded = client.login('staffpass99')
check("a module outside the plan is dropped at login",
      downgraded['bundles'], ['billing'])
PLAN['value'] = 'enterprise'

print("\n-- the owner can shut a PC out --")
store.assign_terminal(db, saved['terminal_id'], username=None, bundles=None,
                      status=store.STATUS_BLOCKED)
shut_out = client.login('staffpass99')
check("a blocked PC cannot sign in even with the right password",
      shut_out['status'], 'blocked')
store.assign_terminal(db, saved['terminal_id'], username=None, bundles=None,
                      status=store.STATUS_APPROVED)

def probe(path, payload):
    """Post straight at the server, reading the body of a refusal too.

    The client wraps refusals into a friendly message; these checks want the
    raw verdict, so they unwrap it the same way the client does.
    """
    import json as _json
    import urllib.error
    try:
        return client._post('127.0.0.1', PORT, path, payload, 5)
    except urllib.error.HTTPError as denied:
        return _json.loads(denied.read().decode('utf-8') or '{}')


print("\n-- seat limits --")
# Pretend to be a different PC, so this runs the same path a real second
# computer would rather than a hand-built request.
real_terminal_id = client.terminal_id
client.terminal_id = lambda _db=None: 'SECOND-PC'

PLAN['value'] = 'pro'          # one connecting PC
second = client.join_server(db, '127.0.0.1', PORT)
check("a second PC is refused on a one-seat plan", second['status'], 'seat_limit')
check_that("and is told how to fix it",
           'plan' in second['message'] or 'Remove' in second['message'],
           second['message'])

PLAN['value'] = 'enterprise'   # four connecting PCs
third = client.join_server(db, '127.0.0.1', PORT)
check("the same PC is accepted once the plan allows it", third['status'], 'success')
client.terminal_id = real_terminal_id

print("\n-- an unknown PC gets nowhere --")
stranger = probe('/aurum/login', {'terminal_id': 'NEVER-SEEN', 'secret': 'x',
                                  'password': 'staffpass99'})
check("a PC that never enrolled cannot sign in", stranger['status'], 'not_enrolled')

forged = probe('/aurum/login', {'terminal_id': saved['terminal_id'],
                                'secret': 'forged', 'password': 'staffpass99'})
check("a wrong secret is refused even with the right password",
      forged['status'], 'denied')

print("\n-- the dispatcher the screens actually call --")
from core.cluster_api import dispatch


class FakeSub:
    def get_state_json(self):
        return {'effective_plan': PLAN['value'], 'features': PLAN['features']}


class FakeApi:
    """Just enough of AurumAPI for the dispatcher to run against."""
    def __init__(self, database):
        self.db = database
        self.sub = FakeSub()
        self._session_role = None
        self._session_username = None
        self._session_bundles = []

    def get_local_ip(self):
        return '127.0.0.1'

    def _audit(self, *a, **k):
        pass


api = FakeApi(db)

check("an unknown action is refused, not crashed",
      dispatch(api, 'no_such_action', {})['status'], 'unknown_action')

check("identity reports this PC as a terminal",
      dispatch(api, 'identity', {})['role'], client.ROLE_TERMINAL)

check("verify_server works through the dispatcher",
      dispatch(api, 'verify_server', {'ip': '127.0.0.1', 'port': PORT})['status'],
      'success')

check("a bad address still fails politely through the dispatcher",
      dispatch(api, 'verify_server', {'ip': '127.0.0.1', 'port': 59999})['status'],
      'error')

signed_in = dispatch(api, 'login', {'password': 'staffpass99'})
check("login works through the dispatcher", signed_in['status'], 'success')
check("and the session is recorded as staff", api._session_role, 'staff')
check("with the signed-in name", api._session_username, 'meena')

check("a terminal cannot manage other PCs",
      dispatch(api, 'list_terminals', {})['status'], 'not_server')

# Become the server again to check the owner-side actions.
client.save_config({'cluster_role': client.ROLE_SERVER})
owner_view = dispatch(api, 'list_terminals', {})
check("the owner can list connected PCs", owner_view['status'], 'success')
check("and sees both of them", len(owner_view['terminals']), 2)
check("with the licensed seat count", owner_view['licensed'], 4)

PLAN['value'] = 'pro'
over = dispatch(api, 'assign_terminal', {'terminal_id': saved['terminal_id'],
                                         'username': 'meena',
                                         'bundles': ['billing', 'inventory']})
check("a module the plan does not include is refused", over['status'], 'plan_limit')

fine = dispatch(api, 'assign_terminal', {'terminal_id': saved['terminal_id'],
                                         'username': 'meena',
                                         'bundles': ['billing']})
check("an allowed module is accepted", fine['status'], 'success')
check("and is stored against the PC", fine['terminal']['bundles'], ['billing'])

cluster_server.stop_server()
shutil.rmtree(work, ignore_errors=True)
print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
