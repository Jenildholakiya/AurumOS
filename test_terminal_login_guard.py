"""A staff PC must never burn the local login lockout.

Run from the project root:  python test_terminal_login_guard.py

Background: a terminal holds no local users (it joins; it is never set up),
so every LOCAL password check there fails. Three failures used to trigger a
15-minute lockout that hides the password box entirely -- on a terminal there
is no admin around to unlock it, so the PC looked permanently dead ("cannot
type anything"). The login page now waits for this PC's role before choosing
its auth path, and _do_login_check refuses the local path on terminals
without touching the attempt budget. This pins that second half down.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO_ROOT)

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


work = tempfile.mkdtemp(prefix='aurum_termguard_')
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
os.makedirs(os.path.dirname(os.environ['AURUM_DB_PATH']), exist_ok=True)

from database.db_manager import DBManager
from network import cluster_client as client
from network.lan_scan import local_ip
from core import cluster_api
from main import AurumAPI

db = DBManager()
db.complete_initial_setup('Guard Jewellers', 'owner', 'ownerpass123')

REAL_IS_TERMINAL = client.is_terminal


def make_stub():
    return SimpleNamespace(db=db, _login_attempts=0, _lockout_until=None,
                           _MAX_ATTEMPTS=3, _LOCKOUT_SECONDS=900)


def attempt(stub, password):
    return AurumAPI._do_login_check(stub, password, datetime.now(),
                                    datetime, timedelta)


print("-- on a terminal: the local path is refused, never counted --")
client.is_terminal = lambda: True
try:
    stub = make_stub()
    r1 = attempt(stub, 'whatever-staff-typed')
    check("wrong password on a terminal is refused as not_terminal",
          r1['status'], 'not_terminal')
    check("the attempt is not counted", stub._login_attempts, 0)

    attempt(stub, 'wrong-again')
    r3 = attempt(stub, 'wrong-a-third-time')
    check("three failures still refuse as not_terminal (never locks)",
          r3['status'], 'not_terminal')
    check("the budget is still untouched", stub._login_attempts, 0)
    check("no lockout timer is armed", stub._lockout_until, None)
finally:
    client.is_terminal = REAL_IS_TERMINAL

print("-- on the main PC: the local path still works --")
client.is_terminal = lambda: False
try:
    stub2 = make_stub()
    r4 = attempt(stub2, 'wrongpass')
    check("a wrong password still fails locally", r4['status'], 'error')
    check("and the attempt is counted", stub2._login_attempts, 1)
finally:
    client.is_terminal = REAL_IS_TERMINAL

print("-- setup refuses the PC's own address (self-join is impossible) --")
own = local_ip()
r5 = client.verify_server(own)
check("own address is refused", r5['status'], 'error')
check("and the message names the mistake",
      'own address' in (r5['message'] or ''), True)
r6 = client.verify_server('127.0.0.1')
check("loopback still passes the guard (same-machine dev/test path)",
      'own address' in (r6.get('message') or ''), False)
r7 = client.join_server(db, own)
check("joining with the own address fails before any network call",
      r7['status'], 'error')
check("the attempt is still not counted", make_stub()._login_attempts, 0)

print("-- a lost terminal can find its servers without typing --")
found = cluster_api.dispatch(object(), 'find_servers', {})
check("the search succeeds", found.get('status'), 'success')
check("it returns a server list", isinstance(found.get('servers'), list), True)
check("every answer carries what the picker shows",
      all('ip' in s and 'business' in s for s in found.get('servers', [])), True)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    sys.exit(1)
print("ALL CHECKS PASSED")
