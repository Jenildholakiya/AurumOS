"""Drive the real AurumAPI class, not a stand-in.

Run from the project root:  python test_app_integration.py

The other suites test the pieces. This one imports main.py itself, builds the
real API object, starts the real shop server from it, joins it over a real
socket, signs in, and checks the permission guard on the genuine class. That
covers the wiring the unit tests cannot see: a method removed by hand, an
import left dangling, a guard that does not survive contact with the real
class.

AurumAPI.__init__ is deliberately skipped. It starts the security monitor,
contacts the licensing server and registers this machine with the live admin
dashboard, none of which belongs in a test run. Everything this exercises is
attached by hand instead, so the code under test is real while the side
effects are not.
"""

import os
import shutil
import sys
import tempfile

REPO = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO)

FAIL = []
work = tempfile.mkdtemp(prefix='aurum_app_')


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


# Keep the database, logs and config this creates away from the project.
os.makedirs(os.path.join(work, 'database'), exist_ok=True)
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')
os.chdir(work)

print("-- main.py still imports after the networking removal --")
import main                                             # noqa: E402
from database.db_manager import DBManager               # noqa: E402
from network import cluster_client as client            # noqa: E402
from network import cluster_server                      # noqa: E402
from core import permissions as P                       # noqa: E402

check_that("the module imports", hasattr(main, 'AurumAPI'))
check_that("nothing still references the deleted sync engine",
           not hasattr(main, 'SyncEngine'))
for gone in ('bridge_ping', 'bridge_discover', 'provision_nexus_identity', 'join_shop'):
    check_that(f"{gone} is gone", not hasattr(main.AurumAPI, gone))
for kept in ('cluster', 'get_sync_status', 'print_tag_network', 'print_tag_local'):
    check_that(f"{kept} survives", hasattr(main.AurumAPI, kept))

# Keep the test off the project's real config.json.
client._config_path = lambda: os.path.join(work, 'config.json')


class FakeSub:
    plan = 'enterprise'
    features = []

    def get_state_json(self):
        return {'effective_plan': self.plan, 'features': self.features}


db = DBManager()
# save_setup, because that is the path the setup screen actually uses. An
# earlier version of this test used complete_initial_setup, a legacy helper
# nothing in the application calls, and the shop name came back as the generic
# product name. The test was wrong, but it showed the lookup had only one place
# to look, so it now has two.
db.save_setup('Dholakiya Jewellers', 'Jenil', '9900000000', 'Rajkot', '360001')
db.add_staff_user('meena', 'staffpass99', ['billing'])

check("the shop name is readable straight after setup",
      db.get_business_name(), 'Dholakiya Jewellers')

with db._get_connection() as _c:
    _c.execute("DELETE FROM app_config WHERE key='business_name'")
    _c.execute("INSERT OR REPLACE INTO business_profile (id, biz_name) VALUES (1,?)",
               ('Dholakiya Jewellers',))
    _c.commit()
check("and still readable from an install that kept it elsewhere",
      db.get_business_name(), 'Dholakiya Jewellers')
db.set_config('business_name', 'Dholakiya Jewellers')

api = main.AurumAPI.__new__(main.AurumAPI)       # real class, no heavy __init__
api.db = db
api.sub = FakeSub()
api._session_role = None
api._session_username = None
api._session_bundles = []
api._window = None

print("\n-- the real object starts the real server --")
api._start_cluster()
check_that("the shop server is listening", cluster_server.is_running())

ident = api.cluster('identity')
check("identity answers through the real dispatcher", ident.get('status'), 'success')
check("this PC is the server", ident.get('role'), 'server')
check_that("and knows its own address", bool(ident.get('ip')), str(ident))

check("an unknown action is handled, not thrown",
      api.cluster('nonsense').get('status'), 'unknown_action')

print("\n-- a staff PC joins it over a real socket --")
found = client.verify_server('127.0.0.1', cluster_server.CLUSTER_PORT)
check("the address verifies", found.get('status'), 'success')
check("and names the shop", found.get('business'), 'Dholakiya Jewellers')

joined = client.join_server(db, '127.0.0.1', cluster_server.CLUSTER_PORT)
check("the PC joins", joined.get('status'), 'success')

signed = client.login('staffpass99')
check("the staff password is accepted by the server", signed.get('status'), 'success')
check("as staff", signed.get('role'), 'staff')
check("landing on the billing screen", signed.get('pages', [None])[0], 'billing.html')

print("\n-- the guard on the genuine class --")
installed = P.install_guard(api)
check_that(f"the guard wrapped the real methods ({installed})", installed > 190)

# pywebview's own view of the API, taken from its source.
import inspect                                          # noqa: E402


def bridge_view(obj):
    seen = {}
    for name in dir(obj):
        if name.startswith('_'):
            continue
        try:
            attr = getattr(obj, name)
        except Exception:
            continue
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            seen[name] = inspect.getfullargspec(attr).args[1:]
    return seen


view = bridge_view(api)
check_that(f"all {len(view)} methods still reach the page", len(view) > 190)
check("generate_bill still declares its argument",
      view.get('generate_bill'), ['bill_data'])
check("add_staff still declares all three",
      view.get('add_staff'), ['username', 'password', 'permissions'])

api._session_role = 'admin'
check_that("the owner can still read the staff list",
           isinstance(api.get_all_staff(), list), str(api.get_all_staff())[:80])

api._session_role = 'staff'
api._session_bundles = ['billing']
denied = api.get_all_staff()
check_that("a billing terminal cannot read the staff list",
           isinstance(denied, dict) and denied.get('denied') is True, str(denied)[:120])
check_that("nor change settings",
           isinstance(api.update_settings({}), dict) and
           api.update_settings({}).get('denied') is True)
check_that("but may still read its own categories",
           isinstance(api.get_categories(), list), str(api.get_categories())[:80])
check("and the dispatcher stays reachable while signed in as staff",
      api.cluster('identity').get('status'), 'success')

print("\n-- the app's own background work is not blocked by a staff session --")
check_that("a licence check still runs",
           not isinstance(api.check_license_revoked(network=False), dict),
           str(api.check_license_revoked(network=False))[:80])

cluster_server.stop_server()
os.chdir(REPO)
shutil.rmtree(work, ignore_errors=True)
print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
