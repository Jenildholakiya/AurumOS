"""Does a bill taken on a staff PC actually reach the shop?

Run from the project root:  python test_shop_sync.py

Three databases and a real server on a real socket: the owner's PC and two
staff PCs. This is the question the whole multi-PC feature exists to answer,
and until now nothing tested it, because there was nothing to test.

The shop server is a hub. A staff PC never talks to another staff PC, so what
has to be proved is that a bill written at one counter reaches the owner and
then the other counter, that a PC is never sent its own rows back, and that
running the exchange twice changes nothing.
"""

import json
import os
import shutil
import sys
import tempfile

REPO = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO)

FAIL = []
PORT = 58973          # not the shop port, so a running AurumOS is undisturbed
work = tempfile.mkdtemp(prefix='aurum_shopsync_')


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


def make_pc(name):
    """A PC with its own database and its own config file."""
    folder = os.path.join(work, name, 'database')
    os.makedirs(folder, exist_ok=True)
    os.environ['AURUM_DB_PATH'] = os.path.join(folder, 'aurum_local.db')
    from database.db_manager import DBManager
    db = DBManager()
    db.save_setup('Dholakiya Jewellers', 'Owner', '9900000000', 'Rajkot', '360001')
    return db, os.path.join(work, name, 'config.json')


from database.db_manager import DBManager          # noqa: E402
from database import cluster_store as store        # noqa: E402
from network import cluster_server                 # noqa: E402
from network import cluster_client as client       # noqa: E402

owner_db, owner_cfg = make_pc('owner')
counter1_db, counter1_cfg = make_pc('counter1')
counter2_db, counter2_cfg = make_pc('counter2')
owner_db.add_staff_user('meena', 'staffpass99', ['billing'])

# One process is playing three computers, so the config file has to be swapped
# each time we act as a different one. On real hardware each PC simply has its
# own.
def acting_as(cfg_path):
    client._config_path = lambda: cfg_path


PLAN = {'value': 'enterprise'}
cluster_server.start_server(owner_db, context={
    'business_name': lambda: 'Dholakiya Jewellers',
    'version':       lambda: '1.1.5',
    'plan':          lambda: PLAN['value'],
    'features':      lambda: [],
    'software_type': lambda: 'wholesale',
}, host='127.0.0.1', port=PORT)


def bill(db, vch_id, customer, amount):
    """Write a sale the way the application does, stamped for sync."""
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO sales_history (vch_id, customer, status, total_amount, items) "
            "VALUES (?,?,?,?,?)",
            (vch_id, customer, 'PAID', amount, json.dumps([])))
        conn.commit()
    db.stamp_row_for_sync('sales_history', vch_id, pk_col='vch_id')


def bills(db):
    with db._get_connection() as conn:
        return {r['vch_id']: r['customer'] for r in conn.execute(
            "SELECT vch_id, customer FROM sales_history").fetchall()}


print("-- two staff PCs join the shop --")
real_id = client.terminal_id
for cfg, tag, db in ((counter1_cfg, 'COUNTER-1', counter1_db),
                     (counter2_cfg, 'COUNTER-2', counter2_db)):
    acting_as(cfg)
    client.terminal_id = lambda _db=None, t=tag: t
    joined = client.join_server(db, '127.0.0.1', PORT)
    check(f'{tag} joins', joined.get('status'), 'success')
client.terminal_id = real_id
check("the owner sees both", len(store.list_terminals(owner_db)), 2)

print("\n-- a bill taken at counter 1 reaches the owner --")
bill(counter1_db, 'VCH-101', 'Walk-in at counter 1', 12500.0)
acting_as(counter1_cfg)
first = client.sync_once(counter1_db)
check("counter 1 syncs", first.get('status'), 'success')
check("and sent one row", first.get('sent'), 1)
check_that("the owner now holds that bill", 'VCH-101' in bills(owner_db),
           str(bills(owner_db)))
check("with the customer intact", bills(owner_db).get('VCH-101'),
      'Walk-in at counter 1')

print("\n-- and reaches the other counter, through the owner --")
acting_as(counter2_cfg)
second = client.sync_once(counter2_db)
check("counter 2 syncs", second.get('status'), 'success')
check_that("counter 2 received counter 1's bill", 'VCH-101' in bills(counter2_db),
           str(bills(counter2_db)))
check("counter 2 sent nothing of its own", second.get('sent'), 0)

print("\n-- the owner's own work reaches the counters --")
bill(owner_db, 'VCH-900', 'Owner sale', 4200.0)
acting_as(counter1_cfg)
client.sync_once(counter1_db)
check_that("counter 1 received the owner's bill", 'VCH-900' in bills(counter1_db),
           str(bills(counter1_db)))

print("\n-- nothing is echoed back to where it came from --")
acting_as(counter1_cfg)
again = client.sync_once(counter1_db)
check("counter 1 has nothing left to send", again.get('sent'), 0)
check("and is sent nothing it already has", again.get('received'), 0)
check("its own bill is not duplicated",
      len([v for v in bills(counter1_db) if v == 'VCH-101']), 1)

def settle(rounds=2):
    """Sync every counter until the shop has stopped changing.

    Two rounds because a bill has two hops to make: counter to owner, then
    owner to the other counter.
    """
    for _ in range(rounds):
        for cfg, db in ((counter1_cfg, counter1_db), (counter2_cfg, counter2_db)):
            acting_as(cfg)
            client.sync_once(db)


print("\n-- once everyone has caught up, running it again changes nothing --")
settle()
before = (bills(owner_db), bills(counter1_db), bills(counter2_db))
check_that("every PC agrees before we start",
           before[0] == before[1] == before[2], str(before))
settle()
after = (bills(owner_db), bills(counter1_db), bills(counter2_db))
check("no PC gained or lost a bill", after, before)

print("\n-- both counters billing at once --")
bill(counter1_db, 'VCH-102', 'Counter 1 second sale', 800.0)
bill(counter2_db, 'VCH-201', 'Counter 2 sale', 6400.0)
settle()

everywhere = ['VCH-101', 'VCH-102', 'VCH-201', 'VCH-900']
check("the owner holds every bill", sorted(bills(owner_db)), everywhere)
check("counter 1 holds every bill", sorted(bills(counter1_db)), everywhere)
check("counter 2 holds every bill", sorted(bills(counter2_db)), everywhere)

print("\n-- a PC that is not enrolled cannot push anything in --")
acting_as(counter1_cfg)
saved = client.load_config()
client.save_config({'terminal_secret': 'forged'})
refused = client.sync_once(counter1_db)
check("a wrong secret is refused", refused.get('status'), 'denied')
client.save_config({'terminal_secret': saved.get('terminal_secret')})

print("\n-- a blocked PC stops syncing --")
tid = client.load_config().get('terminal_id')
store.assign_terminal(owner_db, tid, username=None, bundles=None,
                      status=store.STATUS_BLOCKED)
blocked = client.sync_once(counter1_db)
check("a blocked PC is turned away", blocked.get('status'), 'blocked')
store.assign_terminal(owner_db, tid, username=None, bundles=None,
                      status=store.STATUS_APPROVED)

print("\n-- how long a PC may keep working after the server goes quiet --")
state = client.sync_state()
check_that("a PC that has just synced holds a valid lease", state['lease_valid'],
           str(state))
check("the lease is the server's, not a local guess", state['lease_seconds'],
      float(cluster_server.LEASE_SECONDS))
# Wind the clock back past the lease without waiting for it.
client._last_contact = client._last_contact - (cluster_server.LEASE_SECONDS + 30)
check_that("a PC out of contact past the lease loses it",
           not client.sync_state()['lease_valid'], str(client.sync_state()))

cluster_server.stop_server()
os.chdir(REPO)
shutil.rmtree(work, ignore_errors=True)
print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
