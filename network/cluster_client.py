# -*- coding: utf-8 -*-
"""The staff PC's side of the shop network.

Everything a terminal needs to find its server, join it, and sign someone in.
No page talks to this directly; it all arrives through the cluster dispatcher.

Lives under network/ so it reaches customers through the auto-updater.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from network.cluster_server import CLUSTER_PORT
from network.lan_scan import local_ip, _no_window

# Long enough to cross a slow shop router, short enough that a wrong address
# fails while the person who typed it is still looking at the screen.
VERIFY_TIMEOUT = 4.0
CALL_TIMEOUT = 8.0

ROLE_SERVER = 'server'
ROLE_TERMINAL = 'terminal'


def _log(msg):
    try:
        import logging
        logging.getLogger('aurumos.cluster').info(f'[CLUSTER] {msg}')
    except Exception:
        pass


# -- config.json, shared with the rest of the app ------------------------

def _config_path():
    base = (os.path.dirname(sys.executable)
            if getattr(sys, 'frozen', False) else os.path.abspath('.'))
    return os.path.join(base, 'config.json')


def load_config():
    try:
        path = _config_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
    except Exception as e:
        _log(f'could not read config: {e}')
    return {}


def save_config(patch):
    """Merge keys into config.json without disturbing the rest of it."""
    data = load_config()
    data.update(patch or {})
    try:
        with open(_config_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        _log(f'could not write config: {e}')
        return False


def local_role():
    cfg = load_config()
    if str(cfg.get('cluster_role') or '').strip().lower() == ROLE_TERMINAL:
        return ROLE_TERMINAL
    return ROLE_SERVER


def is_terminal():
    return local_role() == ROLE_TERMINAL


# The shop's plan, as the main PC reports it. A staff PC holds no licence key,
# so without this it would report Lite on every page load and the screens it
# may open would change whenever its cached state was refreshed.
_SHOP_PLAN_TTL = 60
_shop_plan_cache = {'plan': None, 'at': 0.0}


def shop_plan():
    """The main PC's plan ('lite' / 'pro' / 'enterprise'), or None if never known.

    Asked at most once a minute. When the main PC cannot be reached, the last
    plan it reported is used, so a router hiccup does not downgrade the till.
    """
    now = time.time()
    if _shop_plan_cache['plan'] and now - _shop_plan_cache['at'] < _SHOP_PLAN_TTL:
        return _shop_plan_cache['plan']
    cfg = load_config()
    ip = str(cfg.get('server_ip') or '').strip()
    plan = None
    if ip:
        try:
            data = _get(ip, int(cfg.get('server_port') or CLUSTER_PORT),
                        '/aurum/hello', 1.5)   # on the page-load path: keep it short
            plan = str(data.get('plan') or '').strip().lower() or None
        except Exception:
            plan = None
    if plan:
        if cfg.get('shop_plan') != plan:
            save_config({'shop_plan': plan})
    else:
        plan = str(cfg.get('shop_plan') or '').strip().lower() or None
    _shop_plan_cache.update(plan=plan, at=now)
    return plan


def terminal_id(db=None):
    """This PC's stable identity.

    The security code already derives a hardware fingerprint, so reuse it: a
    terminal keeps the same identity across restarts, IP changes and reinstalls,
    which is what stops one PC appearing on the owner's list three times.
    """
    if db is not None:
        try:
            fingerprint = db._machine_fingerprint()
            if fingerprint:
                return str(fingerprint)[:32].upper()
        except Exception:
            pass
    try:
        import uuid
        return f'NODE-{uuid.getnode():012X}'
    except Exception:
        return 'NODE-UNKNOWN'


def hostname():
    try:
        return socket.gethostname()
    except Exception:
        return ''


def _parse_ipconfig_addresses(text):
    """Every IPv4 address ipconfig attributes to this PC's own adapters.

    Pure function of the command output, so tests can pin it down without
    touching the network. The token ``IPv4`` is identical in every Windows
    display language ("IPv4 Address", "IPv4-Adresse", "Adresse IPv4", …),
    while subnet-mask and default-gateway lines never contain it — so only
    the PC's own addresses match, never masks or the gateway (which may be
    the main PC itself).
    """
    found = set()
    for m in re.finditer(r'IPv4[^\n:]*:\s*(\d{1,3}(?:\.\d{1,3}){3})',
                         text or ''):
        found.add(m.group(1).strip())
    return found


def _adapter_addresses():
    """All IPv4 addresses across all of this PC's adapters.

    ``local_ip()`` returns only the single best-scoring address. On a PC
    with several adapters (Wi-Fi + Ethernet + VPN/virtual), the address on
    the shop network may not be that pick — and then typing it into Client
    Node setup slipped past the own-address guard and probed the PC
    itself, failing with a baffling "firewall is blocking" message.
    """
    try:
        done = subprocess.run(['ipconfig'], capture_output=True, text=True,
                              timeout=20, **_no_window())
        return _parse_ipconfig_addresses(done.stdout or '')
    except Exception:
        return set()


def _norm_ip(value):
    """Canonical form for comparing addresses typed by hand.

    ' 10.88.253.184 ' and '010.088.253.184' are the same PC; plain
    lower-cased strings would call them different addresses.
    """
    text = str(value or '').strip().lower()
    try:
        parts = text.split('.')
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            text = '.'.join(str(int(p)) for p in parts)
        import ipaddress
        return ipaddress.ip_address(text).compressed
    except Exception:
        return text


def _own_addresses():
    """Every address or name that means this PC itself.

    Fresh installs boot as a server, so typing this PC's own address into
    Client Node setup would "verify" against itself and bond the PC to
    itself. From then on every sign-in dials its own address and dies with
    "no answer", while the real main PC lists it as offline -- exactly the
    state that looks like a broken connection rather than a wrong address.

    Loopbacks are deliberately NOT refused: same-machine testing and tools
    reach a server over 127.0.0.1, and no screen ever presents a loopback
    address as "the main PC's address", so it is not a setup footgun.
    """
    found = set()
    try:
        found.add(str(local_ip()).strip())
    except Exception:
        pass
    try:
        found.update(_adapter_addresses())
    except Exception:
        pass
    try:
        name = hostname()
        if name:
            found.add(name.strip())
            for info in socket.getaddrinfo(name, None):
                ip = (info[4][0] if info[4] else '')
                if ip:
                    found.add(str(ip).strip())
    except Exception:
        pass
    refused = set()
    for entry in found:
        low = (entry or '').strip().lower()
        if not low:
            continue
        if low in ('localhost', '::1') or low.startswith('127.'):
            continue
        if low.startswith('fe80:'):
            continue                # link-local moves between networks
        refused.add(low)
        try:
            refused.add(_norm_ip(low))
        except Exception:
            pass
    return refused


# -- talking to the server ----------------------------------------------

def _url(ip, port, path):
    return f'http://{str(ip).strip()}:{int(port)}{path}'


def _get(ip, port, path, timeout):
    request = urllib.request.Request(_url(ip, port, path), method='GET')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8') or '{}')


def _post(ip, port, path, payload, timeout):
    body = json.dumps(payload or {}).encode('utf-8')
    request = urllib.request.Request(
        _url(ip, port, path), data=body,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8') or '{}')


def _friendly_failure(error, ip):
    """Turn a socket error into something a shop owner can act on."""
    reason = getattr(error, 'reason', error)
    text = str(reason).lower()
    if isinstance(error, urllib.error.HTTPError):
        try:
            body = json.loads(error.read().decode('utf-8') or '{}')
            if body.get('message'):
                return body
        except Exception:
            pass
        return {'status': 'error',
                'message': f'The PC at {ip} refused the connection.'}
    if 'timed out' in text or isinstance(reason, socket.timeout):
        return {'status': 'error',
                'message': f'No answer from {ip}. Check the address, and that '
                           f'the main PC is switched on and running AurumOS.'}
    if 'refused' in text:
        return {'status': 'error',
                'message': f'{ip} answered but is not running AurumOS, or its '
                           f'firewall is blocking the connection.'}
    if 'unreachable' in text or 'getaddrinfo' in text or 'not known' in text:
        return {'status': 'error',
                'message': f'{ip} could not be reached. Check both PCs are on '
                           f'the same network.'}
    return {'status': 'error', 'message': f'Could not reach {ip}: {reason}'}


def verify_server(ip, port=CLUSTER_PORT):
    """Is there an AurumOS server at this address?

    Called while the person is still typing the address into setup, so it says
    which shop answered. Seeing the right shop name is how they know they typed
    the right PC rather than merely a reachable one.
    """
    ip = str(ip or '').strip()
    if not ip:
        return {'status': 'error', 'message': 'Enter the main PC address first.'}
    if _norm_ip(ip) in _own_addresses():
        return {'status': 'error',
                'message': "That's this computer's own address. Enter the main PC's "
                           "address instead — you'll find it at the top of "
                           "Connected Computers on the main PC. Testing the host "
                           "and a client on this one PC? Enter 127.0.0.1."}
    try:
        data = _get(ip, port, '/aurum/hello', VERIFY_TIMEOUT)
    except Exception as e:
        return _friendly_failure(e, ip)

    if not data.get('aurumos'):
        return {'status': 'error',
                'message': f'Something answered at {ip}, but it is not AurumOS.'}
    if not data.get('accepting'):
        return {'status': 'error',
                'message': 'That PC is still starting up. Try again in a moment.'}
    return {
        'status':   'success',
        'ip':       ip,
        'port':     int(port),
        'business': data.get('business') or 'AurumOS',
        'shop_id':  data.get('shop_id', ''),
        'plan':     data.get('plan', ''),
        'version':  data.get('version', ''),
    }


def join_server(db, ip, port=CLUSTER_PORT):
    """Verify the address, enrol this PC, and remember it as a terminal."""
    found = verify_server(ip, port)
    if found.get('status') != 'success':
        return found

    tid = terminal_id(db)
    try:
        reply = _post(ip, port, '/aurum/enroll',
                      {'terminal_id': tid, 'hostname': hostname()}, CALL_TIMEOUT)
    except Exception as e:
        return _friendly_failure(e, ip)

    if reply.get('status') != 'success':
        return reply       # seat limit or blocked, already carrying a message

    save_config({
        'cluster_role':   ROLE_TERMINAL,
        'server_ip':      found['ip'],
        'server_port':    found['port'],
        'terminal_id':    tid,
        'terminal_secret': reply.get('secret', ''),
        'shop_business':  found['business'],
        # Kept for the older keys the rest of the app still reads.
        'mode':           'network',
        'is_server':      False,
    })
    _log(f'joined {found["business"]} at {found["ip"]}')
    return {
        'status':   'success',
        'business': found['business'],
        'ip':       found['ip'],
        'terminal': reply.get('terminal', {}),
    }


def leave_server():
    save_config({
        'cluster_role': ROLE_SERVER,
        'server_ip': '', 'terminal_id': '', 'terminal_secret': '',
        'mode': 'local', 'is_server': True,
    })
    return {'status': 'success'}


def login(password):
    """Ask the server to check a staff password.

    Deliberately server-side. The terminal never decides for itself whether a
    password is right, which is what makes "no server, no access" true rather
    than merely a screen a determined person could get past.
    """
    cfg = load_config()
    ip = str(cfg.get('server_ip') or '').strip()
    port = int(cfg.get('server_port') or CLUSTER_PORT)
    if not ip:
        return {'status': 'error',
                'message': 'This PC is not connected to a shop server yet.'}
    try:
        return _post(ip, port, '/aurum/login', {
            'terminal_id': cfg.get('terminal_id', ''),
            'secret':      cfg.get('terminal_secret', ''),
            'password':    password,
        }, CALL_TIMEOUT)
    except Exception as e:
        return _friendly_failure(e, ip)


def server_online():
    """Cheap reachability check for the offline overlay."""
    cfg = load_config()
    ip = str(cfg.get('server_ip') or '').strip()
    if not ip:
        return False
    try:
        return bool(_get(ip, int(cfg.get('server_port') or CLUSTER_PORT),
                         '/aurum/hello', VERIFY_TIMEOUT).get('aurumos'))
    except Exception:
        return False


# ── Keeping a staff PC's data with the shop ──────────────────────────────
# A terminal holds its own database so a router hiccup does not stop a sale.
# That only works if what it writes reaches the owner, which is what this does:
# push what this PC wrote, take back everything it has not seen, on a timer.
#
# The exchange is one request. Two would leave a window where a terminal had
# sent its bills but not yet received the stock they consumed.

SYNC_INTERVAL = 20.0          # seconds between exchanges while all is well
SYNC_RETRY = 5.0              # after a failure, try again sooner
_sync_thread = None
_sync_stop = None

# Last time the server answered, and the lease it granted. The application
# reads these to decide whether this PC may still be used.
_last_contact = 0.0
_lease_seconds = 300.0
_last_error = ''


def sync_state():
    """Whether this PC is still entitled to work, and how stale it is."""
    age = time.time() - _last_contact if _last_contact else None
    return {
        'ever_synced':  bool(_last_contact),
        'seconds_ago':  round(age, 1) if age is not None else None,
        'lease_valid':  bool(_last_contact) and age is not None and age < _lease_seconds,
        'lease_seconds': _lease_seconds,
        'last_error':   _last_error,
    }


def partition_settled(mine, accepted, failed):
    """Split pushed tables into confirmed vs short of the server's answer.

    A table is confirmed only when the server settled every row it was
    sent (accepted counts inserts, dedups and clash resolutions — every
    deterministic outcome). Anything short keeps its old outbound cursor
    and is resent next exchange.
    Returns (confirmed, short) where short maps table -> 'got/sent ...'.
    """
    confirmed, short = {}, {}
    for table, rows in (mine or {}).items():
        if not rows:
            continue
        sent = len(rows)
        got = int((accepted or {}).get(table, 0) or 0)
        if (failed or {}).get(table):
            short[table] = f'{got}/{sent} settled, {failed[table]} failed'
        elif got < sent:
            short[table] = f'{got}/{sent} settled'
        else:
            confirmed[table] = rows
    return confirmed, short


def sync_once(db):
    """One exchange with the shop server.

    Own rows first, then whatever the server holds from every other PC. The
    outbound mark is only advanced once the server confirms, so a push that
    dies halfway is repeated rather than skipped.
    """
    global _last_contact, _lease_seconds, _last_error
    from database import cluster_store as store

    cfg = load_config()
    ip = str(cfg.get('server_ip') or '').strip()
    if not ip:
        return {'status': 'error', 'message': 'This PC is not connected to a shop server.'}

    try:
        mine = db.get_changes_since('', store.outbound_cursor(db)).get('changes', {})
    except Exception as e:
        return {'status': 'error', 'message': f'Could not read local changes: {e}'}

    try:
        reply = _post(ip, int(cfg.get('server_port') or CLUSTER_PORT), '/aurum/sync', {
            'terminal_id': cfg.get('terminal_id', ''),
            'secret':      cfg.get('terminal_secret', ''),
            'device_id':   db.get_or_create_device_id(),
            'changes':     mine,
            'since':       store.cursors_by_origin(db),
        }, CALL_TIMEOUT)
    except Exception as e:
        _last_error = str(getattr(e, 'reason', e))
        return _friendly_failure(e, ip)

    if reply.get('status') != 'success':
        _last_error = reply.get('message', '')
        return reply

    # Only advance the outbound cursor past rows the server actually
    # settled (inserted, deduped, or clash-resolved with a record). A table
    # with failed rows keeps its old cursor, so the next exchange resends
    # exactly the remainder instead of silently dropping it.
    confirmed, short = {}, {}
    if mine:
        accepted = reply.get('accepted') or {}
        failed = reply.get('failed') or {}
        confirmed, short = partition_settled(mine, accepted, failed)
        if confirmed:
            store.record_outbound(db, confirmed)

    # Inbound applies whatever the outcome of our own push was: relayed
    # rows are already settled on the server, and holding them back a
    # cycle only because our upload fell short would stall the whole shop.
    received = 0
    for origin, tables in (reply.get('changes') or {}).items():
        if not tables:
            continue
        db.apply_incoming_rows(origin, tables)
        received += sum(len(rows) for rows in tables.values())

    _last_contact = time.time()
    _lease_seconds = float(reply.get('lease_seconds') or _lease_seconds)
    _last_error = ''

    if short:
        _log(f'sync: server settled only part — will resend: {short}')
        return {'status': 'partial', 'sent': sum(len(r) for r in confirmed.values()),
                'received': received, 'pending': short,
                'message': 'Shop server kept only part of the push; the rest retries automatically.'}

    sent = sum(len(rows) for rows in mine.values())
    if sent or received:
        _log(f'sync: sent {sent}, received {received}')
    return {'status': 'success', 'sent': sent, 'received': received,
            'lease_seconds': _lease_seconds}


def start_sync(db):
    """Run the exchange on a timer, for as long as this PC is a terminal."""
    global _sync_thread, _sync_stop
    import threading
    if _sync_thread is not None and _sync_thread.is_alive():
        return False
    if not is_terminal():
        return False

    _sync_stop = threading.Event()

    def loop():
        # A moment's grace so the window is up before the first exchange,
        # which on a fresh PC pulls the whole shop down.
        _sync_stop.wait(3.0)
        while not _sync_stop.is_set():
            wait = SYNC_INTERVAL
            try:
                if sync_once(db).get('status') != 'success':
                    wait = SYNC_RETRY
            except Exception as e:
                _log(f'sync loop error: {e}')
                wait = SYNC_RETRY
            _sync_stop.wait(wait)

    _sync_thread = threading.Thread(target=loop, daemon=True, name='AurumShopSync')
    _sync_thread.start()
    _log(f'syncing with the shop every {SYNC_INTERVAL:.0f}s')
    return True


def stop_sync():
    global _sync_thread
    if _sync_stop is not None:
        _sync_stop.set()
    _sync_thread = None
