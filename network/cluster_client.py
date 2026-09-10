# -*- coding: utf-8 -*-
"""The staff PC's side of the shop network.

Everything a terminal needs to find its server, join it, and sign someone in.
No page talks to this directly; it all arrives through the cluster dispatcher.

Lives under network/ so it reaches customers through the auto-updater.
"""

import json
import os
import socket
import sys
import urllib.error
import urllib.request

from network.cluster_server import CLUSTER_PORT

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
