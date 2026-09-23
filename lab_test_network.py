"""Live test network: a real shop server + a real staff join, on this PC.

Run from the project root:  python lab_test_network.py

What it does, with nothing faked:
  1. Starts a REAL shop server (the exact server code the main PC runs)
     on 127.0.0.1:58971, with its own isolated database. THAT is the server.
  2. Creates a SECOND, separate database acting as the staff PC.
  3. Runs the REAL staff flow against it, step by step, the same functions
     the setup and login screens call:
       verify (see the shop name) -> join (enrol) -> owner assigns the
       staffer -> staff signs in -> first sync -> block/unblock.
  4. Leaves the server RUNNING at the end so you can poke it yourself
     (open http://127.0.0.1:58971/aurum/hello in a browser — you will see
     the shop answering). Press Enter in this window to shut it down.

Nothing touches your real shop: every file lives under a temp folder, the
live port 58903 is never used, and your real config.json is never read.
"""

import os
import shutil
import sys
import tempfile
import time

REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO_ROOT)

PORT = 58971          # not the live port, so a running AurumOS is undisturbed
FAIL = []


def banner(n, text):
    print(f"\n=== {n}. {text} ===")


def ok(label, cond, detail=''):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    if detail:
        print(f"       {detail}")
    if not cond:
        FAIL.append(label)


work = tempfile.mkdtemp(prefix='aurum_lab_')
server_db_path = os.path.join(work, 'server', 'aurum_local.db')
staff_db_path = os.path.join(work, 'staff', 'aurum_local.db')
os.makedirs(os.path.dirname(server_db_path), exist_ok=True)
os.makedirs(os.path.dirname(staff_db_path), exist_ok=True)

os.environ['AURUM_DB_PATH'] = server_db_path
from database.db_manager import DBManager
from database import cluster_store as store
from network import cluster_server, cluster_client as client

# The "staff PC" keeps its own config file, like a real second computer.
lab_config = os.path.join(work, 'staff_config.json')
client._config_path = lambda: lab_config

print("Setting up the test network (all files under a temp folder)…")
server_db = DBManager()
server_db.complete_initial_setup('Lab Jewellers', 'owner', 'ownerpass123')
server_db.add_staff_user('arjun', 'staffpass99', ['billing'])

os.environ['AURUM_DB_PATH'] = staff_db_path
staff_db = DBManager()   # bare: a staff PC is never "set up", it only joins

cluster_server.start_server(server_db, context={
    'business_name': lambda: 'Lab Jewellers',
    'version':       lambda: 'lab',
    'plan':          lambda: 'enterprise',
    'features':      lambda: [],
    'software_type': lambda: 'wholesale',
}, host='127.0.0.1', port=PORT)
time.sleep(0.3)

print(f"\n***** THE SERVER is 'Lab Jewellers' on 127.0.0.1:{PORT} *****")

banner(1, "STAFF SEES THE SERVER (what Check does in setup)")
found = client.verify_server('127.0.0.1', PORT)
ok("server answers", found.get('status') == 'success', str(found))
ok("…and names its shop, so staff know it is the right PC",
   found.get('business') == 'Lab Jewellers', found.get('business'))

banner(2, "STAFF JOINS (what Complete Setup does)")
joined = client.join_server(staff_db, '127.0.0.1', PORT)
ok("join succeeds", joined.get('status') == 'success', str(joined))
terms = store.list_terminals(server_db)
ok("owner now sees 1 PC", len(terms) == 1, str(len(terms)))
tid = (terms[0].get('terminal_id') if terms else '')
ok("it waits to be assigned", terms[0].get('status') if terms else None,
   str(terms[0]) if terms else 'none')
ok("this PC now thinks of itself as a terminal",
   client.is_terminal(), str(client.local_role()))

banner(3, "OWNER SETS UP THE PC (Computers on this shop -> Set up)")
assigned = store.assign_terminal(server_db, tid, username='arjun',
                                 bundles=['billing'],
                                 status=store.STATUS_APPROVED)
ok("assigned to arjun", assigned.get('assigned_user'), str(assigned))

banner(4, "STAFF SIGNS IN (the login screen)")
login = client.login('staffpass99')
ok("sign-in succeeds", login.get('status') == 'success', str(login))
ok("signed in as arjun (staff, never owner)", login.get('username'),
   str(login))
bad = client.login('wrong-password')
ok("a wrong password is refused", bad.get('status') != 'success',
   f"{bad.get('status')}: {bad.get('message')}")

banner(5, "FIRST SYNC (bills/stock start flowing)")
sync = client.sync_once(staff_db)
ok("exchange succeeds", sync.get('status') == 'success', str(sync))
fresh = store.get_terminal(server_db, tid)
ok("server just heard from it — the badge would read Online",
   fresh.get('last_ip') == '127.0.0.1', str(fresh.get('last_ip')))

banner(6, "OWNER DISCONNECTS, THEN RECONNECTS")
store.assign_terminal(server_db, tid, username=None, bundles=None,
                      status=store.STATUS_BLOCKED)
ok("blocked PC cannot sign in",
   client.login('staffpass99').get('status') != 'success')
store.assign_terminal(server_db, tid, status=store.STATUS_APPROVED)
ok("re-connected PC signs in again",
   client.login('staffpass99').get('status') == 'success')

print("\n" + ("ALL STEPS PASSED" if not FAIL
      else f"{len(FAIL)} STEP(S) FAILED: {FAIL}"))
print(f"""
The test network is STILL RUNNING — try it yourself:
  * Browser:  http://127.0.0.1:{PORT}/aurum/hello   (the server answering)
  * The staff config + databases are under:
      {work}
Press Enter in this window to shut the server down and delete everything.""")
try:
    input()
except (EOFError, KeyboardInterrupt):
    pass
finally:
    try:
        cluster_server.stop_server()
    except Exception:
        pass
    shutil.rmtree(work, ignore_errors=True)
    print("Lab shut down, temp files removed.")
    sys.exit(1 if FAIL else 0)
