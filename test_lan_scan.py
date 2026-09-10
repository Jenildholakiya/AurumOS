"""Checks for the network device scan.

Run from the project root:  python test_lan_scan.py

The scan itself talks to a real network, which makes it useless as a test: the
answer changes with whatever happens to be plugged in. So the parts that decide
things are exercised with the network replaced by fixed answers, and only the
reading of `arp -a` is checked against genuine output.

What actually matters here is the labelling. The owner uses this screen to tell
a PC that has already joined from one that merely runs AurumOS from a phone on
the same wifi, and getting that wrong sends them to the wrong machine.
"""

import os
import shutil
import sys
import tempfile

REPO = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO)

FAIL = []
work = tempfile.mkdtemp(prefix='aurum_scan_')
os.makedirs(os.path.join(work, 'database'), exist_ok=True)
os.environ['AURUM_DB_PATH'] = os.path.join(work, 'database', 'aurum_local.db')


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


from database.db_manager import DBManager          # noqa: E402
from database import cluster_store as store        # noqa: E402
from network import lan_scan                       # noqa: E402

print("-- reading arp output --")
# Real output, including the rows that are not devices at all.
WINDOWS_ARP = """
Interface: 192.168.0.155 --- 0x11
  Internet Address      Physical Address      Type
  192.168.0.1           d8-0d-17-a1-20-10     dynamic
  192.168.0.21          74-56-3c-33-a4-68     dynamic
  192.168.0.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
  239.255.255.250       01-00-5e-7f-ff-fa     static
  255.255.255.255       ff-ff-ff-ff-ff-ff     static
"""
table = lan_scan.parse_arp(WINDOWS_ARP)
check("real devices are kept", sorted(table), ['192.168.0.1', '192.168.0.21'])
check("the broadcast row is not a device", '192.168.0.255' in table, False)
check("multicast rows are not devices",
      any(a.startswith('224.') or a.startswith('239.') for a in table), False)
check("hardware ids are normalised",
      table['192.168.0.1'], 'd8-0d-17-a1-20-10')

# Some Windows builds print colons instead of dashes.
check("colon-separated output reads the same",
      lan_scan.parse_arp('  10.0.0.5   AA:BB:CC:DD:EE:FF   dynamic'),
      {'10.0.0.5': 'aa-bb-cc-dd-ee-ff'})
check("nothing sensible in, nothing out", lan_scan.parse_arp('no addresses here'), {})
check("no output at all is survivable", lan_scan.parse_arp(None), {})

print("\n-- working out this PC's network --")
check("a /24 is taken around the address",
      str(lan_scan.local_network('192.168.0.155')), '192.168.0.0/24')
check("and around a different one",
      str(lan_scan.local_network('10.1.2.3')), '10.1.2.0/24')

print("\n-- labelling each device --")
db = DBManager()
db.save_setup('Dholakiya Jewellers', 'Owner', '9900000000', 'Rajkot', '360001')
# A staff PC that has already joined, last seen at .21.
store.register_terminal(db, 'TERMINAL-ONE', 'COUNTER-1', '192.168.0.21')

FAKE_NETWORK = {
    '192.168.0.155': {'aurum': True, 'business': 'Dholakiya Jewellers'},   # this PC
    '192.168.0.21':  {'aurum': True, 'business': 'Dholakiya Jewellers'},   # joined
    '192.168.0.42':  {'aurum': True, 'business': 'Dholakiya Jewellers'},   # not joined
    '192.168.0.1':   {'aurum': False},                                     # router
}

lan_scan.local_ip = lambda: '192.168.0.155'
lan_scan.arp_table = lambda: {
    '192.168.0.1':  'd8-0d-17-a1-20-10',
    '192.168.0.21': '74-56-3c-33-a4-68',
    '192.168.0.42': 'e0-d5-5e-e0-a1-4c',
    '192.168.0.77': 'fc-3f-db-03-21-61',
    '192.168.0.90': 'aa-bb-cc-dd-ee-01',
}
lan_scan._probe_aurum = lambda address, port=0: FAKE_NETWORK.get(address, {'aurum': False})
# .42 and .77 answer on the Windows file-sharing port; the router does not.
WINDOWS_MACHINES = {'192.168.0.155', '192.168.0.21', '192.168.0.42', '192.168.0.77'}
lan_scan._looks_like_a_pc = lambda address: address in WINDOWS_MACHINES
lan_scan._name_of = lambda address: {'192.168.0.21': 'COUNTER-1',
                                     '192.168.0.42': 'BACK-OFFICE'}.get(address, '')

res = lan_scan.scan(db, wake=False)
check("the scan succeeds", res['status'], 'success')
by_ip = {d['ip']: d for d in res['devices']}

check("every device is listed, this PC included", sorted(by_ip), sorted(
    ['192.168.0.1', '192.168.0.21', '192.168.0.42', '192.168.0.77',
     '192.168.0.90', '192.168.0.155']))
check("this PC is labelled as such", by_ip['192.168.0.155']['kind'], 'this_pc')
check("a PC that has joined is a staff PC", by_ip['192.168.0.21']['kind'], 'joined')
check("and carries its registry entry",
      by_ip['192.168.0.21']['terminal_id'], 'TERMINAL-ONE')
check("AurumOS that has not joined is called out separately",
      by_ip['192.168.0.42']['kind'], 'aurum')
check("and is marked as not joined", by_ip['192.168.0.42']['joined'], False)
check("anything else is just a device", by_ip['192.168.0.90']['kind'], 'device')
check("the router is a device, not a candidate",
      by_ip['192.168.0.1']['kind'], 'device')

# The question the owner actually has: which of these could become a till.
check("a Windows PC with no AurumOS is offered as a candidate",
      by_ip['192.168.0.77']['kind'], 'pc')
check("and is marked as a PC", by_ip['192.168.0.77']['is_pc'], True)
check("a phone answering nothing is not offered",
      by_ip['192.168.0.90']['is_pc'], False)
check("AurumOS already running outranks the plain-PC label",
      by_ip['192.168.0.42']['kind'], 'aurum')
check("a PC that has joined stays a staff PC",
      by_ip['192.168.0.21']['kind'], 'joined')
check("the candidates are exactly the two worth walking to",
      sorted(d['ip'] for d in res['devices'] if d['kind'] in ('pc', 'aurum')),
      ['192.168.0.42', '192.168.0.77'])
check("the router is not claimed to be AurumOS",
      by_ip['192.168.0.1']['aurum'], False)
check("hardware ids come through", by_ip['192.168.0.21']['mac'], '74-56-3c-33-a4-68')
check("names come through", by_ip['192.168.0.42']['hostname'], 'BACK-OFFICE')
check("the network is reported", res['network'], '192.168.0.0/24')

print("\n-- a network too large is refused, not swept --")
lan_scan.local_network = lambda ip=None: __import__('ipaddress').ip_network('10.0.0.0/16')
big = lan_scan.scan(db, wake=False)
check("it declines rather than trying", big['status'], 'error')

shutil.rmtree(work, ignore_errors=True)
print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
