"""Dev staff PC: connect a simulated staff computer to a real server.

Run from the project root, examples:

  python scripts/dev/dev_connect_staff.py
      Connects to the live server on this network (defaults to the
      current PC's address, 10.88.253.95:58903). You will be asked for
      the staff username + password. The PC appears in the server admin
      dashboard (Computers on this shop) as DEV-STAFF-PC.

  python scripts/dev/dev_connect_staff.py --server 10.177.43.20 --user arjun
      Same, against another main PC.

  python scripts/dev/dev_connect_staff.py --selftest
      No live server needed: spins up a throwaway server + staff user on
      127.0.0.1:58972 and runs the whole flow non-interactively. Deletes
      everything afterwards. This is the CI check for this file.

What it proves, using the REAL staff-side code (the same functions the
setup and login screens call):
  verify (the server names its shop) -> join (enrol, shows as pending in
  the dashboard) -> owner assigns the user in the dashboard -> staff
  signs in -> first sync (server marks it just-seen: badge reads Online).

The dev PC keeps its own config + database under a temp folder, exactly
like a second computer would. When you are done testing, press Remove on
DEV-STAFF-PC in the dashboard (frees its seat) and delete the temp folder
printed at the end.
"""

import argparse
import getpass
import os
import shutil
import sys
import tempfile
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

FAIL = []
SELFTEST_PORT = 58972


def banner(n, text):
    print(f"\n=== {n}. {text} ===")


def ok(label, cond, detail=''):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    if detail:
        print(f"       {detail}")
    if not cond:
        FAIL.append(label)
    return cond


def ask(prompt, default=''):
    try:
        suffix = f" [{default}]" if default else ''
        val = input(f"{prompt}{suffix}: ").strip()
        return val or default
    except EOFError:
        if default:
            print(f"(no input — using {default})")
            return default
        raise SystemExit("Need interactive input; pass --user/--password or --selftest.")


def main():
    ap = argparse.ArgumentParser(description='Dev staff PC against a real server.')
    ap.add_argument('--server', default='10.88.253.95',
                    help='Main PC address (default: this PC)')
    ap.add_argument('--port', type=int, default=58903,
                    help='Shop server port (default: live port)')
    ap.add_argument('--user', default='',
                    help='Staff username (prompted if missing)')
    ap.add_argument('--password', default='',
                    help='Staff password (prompted securely if missing)')
    ap.add_argument('--hostname', default='DEV-STAFF-PC',
                    help='Name shown in the admin dashboard')
    ap.add_argument('--selftest', action='store_true',
                    help='Throwaway local server; no prompts, cleans up.')
    args = ap.parse_args()

    from database.db_manager import DBManager
    from database import cluster_store as store
    from network import cluster_server, cluster_client as client

    server = args.server
    port = args.port
    dev_server = None
    cleanup_dirs = []

    if args.selftest:
        # Throwaway shop that exists only for this run.
        lab = tempfile.mkdtemp(prefix='aurum_devselftest_')
        cleanup_dirs.append(lab)
        os.environ['AURUM_DB_PATH'] = os.path.join(lab, 'server.db')
        lab_db = DBManager()
        lab_db.complete_initial_setup('Selftest Shop', 'owner', 'ownerpass123')
        lab_db.add_staff_user('teststaff', 'testpass99', ['billing'])
        cluster_server.start_server(lab_db, context={
            'business_name': lambda: 'Selftest Shop',
            'version':       lambda: 'dev',
            'plan':          lambda: 'enterprise',
            'features':      lambda: [],
            'software_type': lambda: 'wholesale',
        }, host='127.0.0.1', port=SELFTEST_PORT)
        time.sleep(0.3)
        dev_server = lab_db
        server, port = '127.0.0.1', SELFTEST_PORT
        args.user = 'teststaff'
        args.password = 'testpass99'
        args.hostname = 'DEV-SELFTEST-PC'

    # The dev PC's own corner: separate config + database, like real hardware.
    home = tempfile.mkdtemp(prefix='aurum_devstaff_')
    if args.selftest:
        cleanup_dirs.append(home)
    os.environ['AURUM_DB_PATH'] = os.path.join(home, 'staff.db')
    staff_db = DBManager()
    client._config_path = lambda: os.path.join(home, 'staff_config.json')

    # Show up under our own name in the dashboard (join reads hostname()).
    real_hostname = client.hostname
    client.hostname = lambda: args.hostname

    try:
        print(f"\n***** THE SERVER should be at {server}:{port} *****")

        banner(1, "STAFF SEES THE SERVER")
        found = client.verify_server(server, port)
        if not ok("server answers", found.get('status') == 'success',
                  str(found)):
            print("\nIs the main PC switched on with AurumOS open?")
            return 1
        print(f"***** THAT IS THE SERVER: '{found.get('business')}' "
              f"at {found.get('ip')}:{found.get('port')} *****")

        banner(2, "STAFF JOINS (shows as pending in the admin dashboard)")
        joined = client.join_server(staff_db, server, port)
        if not ok("join succeeds", joined.get('status') == 'success',
                  str(joined)):
            return 1
        print("  Open the admin dashboard now: Computers on this shop lists "
              f"'{args.hostname}' waiting to be set up.")

        if args.selftest:
            tid = joined['terminal'].get('terminal_id', '')
            store.assign_terminal(dev_server, tid, username=args.user,
                                  bundles=['billing'],
                                  status=store.STATUS_APPROVED)
            print("  (selftest: owner assignment done automatically)")
        else:
            user = args.user or ask("Owner: set the PC up in the dashboard "
                                    "(assign user + what they can open), then "
                                    "type the staff username here", "")
            args.user = user
            try:
                input("Press Enter when the dashboard setup is saved…")
            except EOFError:
                print("(continuing without the pause)")

        banner(3, "STAFF SIGNS IN THROUGH THIS PC")
        password = args.password
        if not password:
            try:
                password = getpass.getpass("Staff password: ")
            except (EOFError, KeyboardInterrupt):
                raise SystemExit("Need a password; pass --password.")
        login = client.login(password)
        if not ok("sign-in succeeds", login.get('status') == 'success',
                  str(login)):
            print("\nWrong password, wrong user for this PC, or a blocked "
                  "PC — the dashboard row says which.")
            return 1
        ok("signed in as staff (never owner)", login.get('username'),
           str(login))

        banner(4, "FIRST SYNC (server marks it just-seen: Online)")
        sync = client.sync_once(staff_db)
        ok("exchange succeeds", sync.get('status') == 'success', str(sync))

        print("\n" + ("ALL STEPS PASSED" if not FAIL
              else f"{len(FAIL)} STEP(S) FAILED: {FAIL}"))
        print(f"""
The dev staff PC lives here (its own config + database, like hardware):
  {home}
Dashboard: '{args.hostname}' is connected. When finished testing, press
Remove on it in the dashboard (frees its seat), then delete that folder.""")
        return 1 if FAIL else 0
    finally:
        client.hostname = real_hostname
        try:
            cluster_server.stop_server()
        except Exception:
            pass
        for d in cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
