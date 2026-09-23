"""Joining must flip the PC into a syncing terminal at once, no restart.

Run from the project root:  python test_join_flips_role.py

Background: the app boots as a server. After Client Node setup joined the
shop, nothing flipped that until a restart -- the PC kept answering as a
server while never syncing as a terminal, so the main PC listed it as
offline minutes after joining even though it was switched on. cluster join
now stops the server and starts the sync loop on success. This pins that.
"""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO_ROOT)

FAIL = []
PORT = 58972          # not the live port, so a running AurumOS is undisturbed


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


work = tempfile.mkdtemp(prefix='aurum_joinflip_')
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
os.makedirs(os.path.dirname(os.environ['AURUM_DB_PATH']), exist_ok=True)

from database.db_manager import DBManager
from core import cluster_api
from network import cluster_client as client
from network import cluster_server

# Keep the test off the project's real config.json.
client._config_path = lambda: os.path.join(work, 'config.json')

db = DBManager()
db.complete_initial_setup('Flip Jewellers', 'owner', 'ownerpass123')

# The accidental server this PC has run since boot, as on a fresh install.
cluster_server.start_server(db, context={
    'business_name': lambda: 'Flip Jewellers',
    'version':       lambda: '1.1.7',
    'plan':          lambda: 'enterprise',
    'features':      lambda: [],
    'software_type': lambda: 'wholesale',
}, host='127.0.0.1', port=PORT)
check("the boot-time server is running before join",
      cluster_server.is_running(), True)


class FakeApi:
    db = db


print("-- joining flips the role immediately --")
try:
    res = cluster_api.dispatch(FakeApi(), 'join',
                               {'ip': '127.0.0.1', 'port': PORT})
    check("join succeeds", res.get('status'), 'success')
    check("the PC is a terminal now",
          client.is_terminal(), True)
    check("it stopped answering as a server",
          cluster_server.is_running(), False)
    alive = (client._sync_thread is not None
             and client._sync_thread.is_alive())
    check("the sync loop is running (first exchange in ~3s)", alive, True)
finally:
    try:
        client.stop_sync()
    except Exception:
        pass
    try:
        cluster_server.stop_server()
    except Exception:
        pass
    shutil.rmtree(work, ignore_errors=True)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    sys.exit(1)
print("ALL CHECKS PASSED")
