# -*- coding: utf-8 -*-
"""See every device on the shop's network, not just the ones that joined.

The Connected Computers list only knows about PCs that have already been
through setup. This finds everything else on the same network, so the owner can
see the staff PC sitting there before it has joined, read its address off the
screen, and tell whether it is running AurumOS at all.

How it looks around, and why:

  * A UDP datagram is sent to every address on the local network. Nothing is
    listening and no reply is wanted; the point is that Windows has to resolve
    each address to a hardware address first, which fills the ARP table. That
    takes milliseconds for a whole network, where running ping against 254
    addresses would mean 254 processes.

  * The ARP table is then read back. Anything that answered is a real device.

  * Each device is asked whether it is running AurumOS, on the shop port, and
    whether it is a Windows machine at all, on the file-sharing port. Two
    connections to two well-known ports, and nothing else.

That second question is what makes the list worth reading. Sixteen addresses
tell an owner nothing; knowing which five are computers that could run a till,
and which one already is, tells them where to walk. No wider port scanning and
no attempt to fingerprint anything beyond that.

Lives under network/ so it reaches customers through the auto-updater.
"""

import concurrent.futures
import ipaddress
import re
import socket
import subprocess
import sys
import time

from network.cluster_server import CLUSTER_PORT

# A shop network is a /24 in practice. Anything larger is refused rather than
# swept, so a machine on an unusual network cannot sit here for minutes.
MAX_HOSTS = 512
ARP_SETTLE_SECONDS = 1.2
PROBE_TIMEOUT = 0.6
PC_PROBE_TIMEOUT = 0.4
# Whole-pass budget for asking the network for device names. gethostbyaddr
# cannot be given a timeout, so the pass is bounded rather than each call.
NAME_DEADLINE = 2.5

_MAC = re.compile(
    r'(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})')

# Broadcast and multicast entries are the network talking to itself, not
# devices anyone can connect a till to.
_NOT_A_DEVICE = ('ff-ff-ff-ff-ff-ff', '00-00-00-00-00-00')
_MULTICAST_PREFIXES = ('01-00-5e', '33-33-', '01-80-c2')


def _log(msg):
    try:
        import logging
        logging.getLogger('aurumos.cluster').info(f'[SCAN] {msg}')
    except Exception:
        pass


def _no_window():
    """Keep console windows from flashing over the app on Windows."""
    if sys.platform == 'win32':
        return {'creationflags': 0x08000000}
    return {}


def _adapter_ip():
    """This PC's own LAN address, without dialing out.

    getaddrinfo on the hostname lists every IPv4 the PC holds. Link-local
    (169.254.x) and loopback are skipped — neither reaches the shop — and
    private ranges sort first, so a PC with several adapters reports the
    shop one rather than a virtual machine's.
    """
    cands = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                        socket.AF_INET):
            ip = (info[4][0] if info[4] else '')
            if not ip or ip.startswith('127.') or ip.startswith('169.254.'):
                continue
            if ip not in cands:
                cands.append(ip)
    except Exception:
        pass

    def _score(ip):
        if ip.startswith('192.168.'):
            return 0
        if ip.startswith('10.'):
            return 1
        if ip.startswith('172.'):
            try:
                if 16 <= int(ip.split('.')[1]) <= 31:
                    return 2
            except Exception:
                pass
        return 3

    cands.sort(key=_score)
    return cands[0] if cands else ''


def local_ip():
    """This PC's LAN address, online or off.

    The fast path asks the OS which interface it would use (no traffic is
    sent). Offline that raises, and the old code then claimed 127.0.0.1 —
    so scan()/find_servers() swept 127.0.0.0/24 and found nothing while the
    shop LAN sat one adapter away. Now it falls back to enumerating the
    PC's own adapters instead of lying.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.connect(('8.8.8.8', 80))
            ip = sock.getsockname()[0]
            if ip and not str(ip).startswith('127.'):
                return ip
        except Exception:
            pass
        return _adapter_ip() or '127.0.0.1'
    finally:
        try:
            sock.close()
        except Exception:
            pass


def local_network(ip=None):
    """The address range to look at, as a /24 around this PC.

    Reading the real mask would mean parsing ipconfig in whatever language
    Windows is set to. A shop is on a /24; if it is not, the worst case is
    that a device on a neighbouring range is missed, which is better than
    guessing wrong from text that may not even be in English.
    """
    ip = ip or local_ip()
    try:
        return ipaddress.ip_network(f'{ip}/24', strict=False)
    except Exception:
        return None


def _wake_arp(network, skip):
    """Touch every address so Windows resolves it, filling the ARP table."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sent = 0
    try:
        for host in network.hosts():
            address = str(host)
            if address == skip:
                continue
            try:
                # Port 9 is discard. Nothing replies and nothing needs to.
                sock.sendto(b'\x00', (address, 9))
                sent += 1
            except Exception:
                continue
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return sent


def parse_arp(raw):
    """Pull address and hardware-address pairs out of `arp -a` output.

    Split from the command so it can be tested against real output without a
    network, and because the surrounding text is localised: the column headings
    and the word for "dynamic" change with the Windows display language, while
    the two things being read do not.
    """
    found = {}
    for match in _MAC.finditer(raw or ''):
        address, mac = match.group(1), match.group(2).lower().replace(':', '-')
        if mac in _NOT_A_DEVICE or mac.startswith(_MULTICAST_PREFIXES):
            continue
        if address.endswith('.255') or address.startswith('224.'):
            continue
        found[address] = mac
    return found


def arp_table():
    """Address to hardware-address, as Windows currently sees it."""
    try:
        done = subprocess.run(['arp', '-a'], capture_output=True, text=True,
                              timeout=20, **_no_window())
        return parse_arp(done.stdout or '')
    except Exception as e:
        _log(f'could not read the arp table: {e}')
        return {}


def _probe_aurum(address, port=CLUSTER_PORT):
    """Is AurumOS answering here? One connection, one known port."""
    import json
    import urllib.request
    try:
        url = f'http://{address}:{int(port)}/aurum/hello'
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as response:
            data = json.loads(response.read().decode('utf-8') or '{}')
        if data.get('aurumos'):
            return {'aurum': True, 'business': data.get('business', ''),
                    'shop_id': data.get('shop_id', ''),
                    'version': data.get('version', '')}
    except Exception:
        pass
    return {'aurum': False}


def _looks_like_a_pc(address):
    """Is this a Windows machine, as opposed to a phone or a printer?

    Windows keeps file sharing listening on 445, and 139 before that. Almost
    nothing else on a shop network does: phones listen on nothing, printers
    answer on their own printing and web ports, routers on their admin page.

    This is the difference between a list of sixteen addresses and a list that
    says which two machines could actually run the till. One extra connection
    to one well-known port, and nothing else is probed.
    """
    for port in (445, 139):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(PC_PROBE_TIMEOUT)
        try:
            if sock.connect_ex((address, port)) == 0:
                return True
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
    return False


def _name_of(address):
    """The device's name, if the network will say.

    No socket.setdefaulttimeout here, tempting as it is: that setting is
    process-wide, so calling it from a pool of threads would race with itself
    and quietly change the timeout of every other socket the application has
    open, including the licence check. gethostbyaddr takes no timeout of its
    own, so the whole naming pass is bounded by a deadline instead and
    stragglers are simply left unnamed.
    """
    try:
        return socket.gethostbyaddr(address)[0].split('.')[0]
    except Exception:
        return ''


def scan(db=None, wake=True, port=CLUSTER_PORT):
    """Every device on the shop network, with what each one is.

    `wake` sends the datagrams that fill the ARP table. Turning it off reads
    only what Windows already knows, which is instant but misses anything the
    PC has not spoken to recently.
    """
    started = time.time()
    me = local_ip()
    network = local_network(me)
    if network is None:
        return {'status': 'error',
                'message': "Could not work out this PC's network."}
    if network.num_addresses > MAX_HOSTS:
        return {'status': 'error',
                'message': 'This network is too large to list device by device.'}

    if wake:
        _wake_arp(network, me)
        time.sleep(ARP_SETTLE_SECONDS)

    table = arp_table()
    # Only devices on this PC's own network; the ARP table can hold others.
    addresses = sorted(
        (a for a in table if ipaddress.ip_address(a) in network),
        key=lambda a: tuple(int(p) for p in a.split('.')))
    if me not in addresses:
        addresses.insert(0, me)

    known = {}
    if db is not None:
        try:
            from database import cluster_store as store
            for row in store.list_terminals(db):
                if row.get('last_ip'):
                    known[row['last_ip']] = row
        except Exception:
            pass

    # Two passes rather than one. Asking whether AurumOS is there has its own
    # timeout and always finishes; asking the network for a name does not, so
    # it gets a deadline and anything still outstanding is left unnamed.
    def look(address):
        return _probe_aurum(address, port), _looks_like_a_pc(address)

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        probes = {pool.submit(look, a): a for a in addresses}
        aurum, is_pc = {}, {}
        for future in concurrent.futures.as_completed(probes, timeout=None):
            found, pc = future.result()
            aurum[probes[future]] = found
            is_pc[probes[future]] = pc

    names = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=32)
    try:
        lookups = {pool.submit(_name_of, a): a for a in addresses}
        done, _ = concurrent.futures.wait(lookups, timeout=NAME_DEADLINE)
        for future in done:
            try:
                names[lookups[future]] = future.result()
            except Exception:
                pass
    finally:
        pool.shutdown(wait=False)

    devices = []
    for address in addresses:
        found = aurum.get(address, {'aurum': False})
        name = names.get(address, '')
        terminal = known.get(address)
        if address == me:
            kind = 'this_pc'
        elif terminal:
            kind = 'joined'
        elif found.get('aurum'):
            kind = 'aurum'
        elif is_pc.get(address):
            # A Windows machine with no AurumOS on it. This is the row the
            # owner is looking for when they ask which computer they could
            # turn into a till.
            kind = 'pc'
        else:
            kind = 'device'
        devices.append({
            'ip':          address,
            'mac':         table.get(address, ''),
            'hostname':    name or (terminal or {}).get('hostname', ''),
            'kind':        kind,
            'aurum':       bool(found.get('aurum')),
            'business':    found.get('business', ''),
            'terminal_id': (terminal or {}).get('terminal_id', ''),
            'joined':      bool(terminal),
            'is_pc':       bool(is_pc.get(address)),
        })

    _log(f'{len(devices)} device(s) on {network} in {time.time() - started:.1f}s')
    return {
        'status':  'success',
        'network': str(network),
        'this_pc': me,
        'devices': devices,
        'took':    round(time.time() - started, 1),
    }


# How long to wait for one address when hunting servers. Short on purpose:
# empty addresses refuse fast on a LAN; only firewalled hosts burn the full
# budget, and 64 of them are asked at once.
FIND_TIMEOUT = 0.5
FIND_WORKERS = 64


def find_servers(port=CLUSTER_PORT):
    """Shop servers answering on this network right now.

    Lighter than scan(): no ARP wake, no names, no PC probing — just "who
    here runs AurumOS as a server". Built for a lost terminal: it dials a
    dead address and its owner cannot be expected to type the right one, so
    it asks the network instead and picks its shop from the answers.
    """
    import json
    import urllib.request

    started = time.time()
    me = local_ip()
    network = local_network(me)
    if network is None:
        return {'status': 'error',
                'message': "Could not work out this PC's network."}
    if network.num_addresses > MAX_HOSTS:
        return {'status': 'error',
                'message': 'This network is too large to search address by address.'}

    targets = [str(host) for host in network.hosts() if str(host) != me]

    def check(address):
        try:
            url = f'http://{address}:{int(port)}/aurum/hello'
            with urllib.request.urlopen(url, timeout=FIND_TIMEOUT) as response:
                data = json.loads(response.read().decode('utf-8') or '{}')
            if data.get('aurumos'):
                return {'ip':       address,
                        'business': data.get('business', ''),
                        'version':  data.get('version', '')}
        except Exception:
            pass
        return None

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=FIND_WORKERS) as pool:
        for result in pool.map(check, targets):
            if result:
                found.append(result)
    found.sort(key=lambda s: s['ip'])

    _log(f'{len(found)} server(s) on {network} in {time.time() - started:.1f}s')
    return {
        'status':  'success',
        'network': str(network),
        'this_pc': me,
        'servers': found,
        'took':    round(time.time() - started, 1),
    }
