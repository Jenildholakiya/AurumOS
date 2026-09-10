# -*- coding: utf-8 -*-
"""The owner PC's side of the shop network.

Three endpoints, which is all the joining flow needs:

  GET  /aurum/hello    Who am I. Unauthenticated, because a staff PC has to be
                       able to ask "is there an AurumOS server at this address"
                       before it has any credentials at all. It answers with
                       the shop name so the person typing the address can see
                       they reached the right PC, and nothing else.

  POST /aurum/enroll   A staff PC announces itself. It appears on the owner's
                       network page from this moment. Enrolling on its own
                       grants nothing: the seat limit is checked here, and a
                       password is still required to get in.

  POST /aurum/login    Verify a staff password and say what that person may
                       open. The check happens here, on the owner's PC, not on
                       the terminal, so a staff PC that cannot reach the server
                       cannot let anyone in.

Lives under network/ so it reaches customers through the auto-updater.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 58901 and 58902 belong to the sync engine this work replaces. Sitting
# clear of both means the new server and the old one can run side by side
# during the changeover instead of one silently failing to bind.
CLUSTER_PORT = 58903

# A wrong password costs a growing pause, so guessing over the LAN is slow.
# Keyed by terminal, kept in memory only.
_ATTEMPTS = {}
_ATTEMPTS_LOCK = threading.Lock()
_MAX_ATTEMPTS = 5
_ATTEMPT_WINDOW = 300.0


def _log(msg):
    try:
        import logging
        logging.getLogger('aurumos.cluster').info(f'[CLUSTER] {msg}')
    except Exception:
        pass


def _throttle_state(terminal_id):
    now = time.time()
    with _ATTEMPTS_LOCK:
        tries, first = _ATTEMPTS.get(terminal_id, (0, now))
        if now - first > _ATTEMPT_WINDOW:
            tries, first = 0, now
        return tries, first


def _record_failure(terminal_id):
    now = time.time()
    with _ATTEMPTS_LOCK:
        tries, first = _ATTEMPTS.get(terminal_id, (0, now))
        if now - first > _ATTEMPT_WINDOW:
            tries, first = 0, now
        _ATTEMPTS[terminal_id] = (tries + 1, first)


def _clear_failures(terminal_id):
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(terminal_id, None)


class _ClusterHandler(BaseHTTPRequestHandler):
    db = None
    context = None          # supplies plan, features and business name
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass                # the default handler spams stderr

    def _send(self, obj, status=200):
        try:
            body = json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _body(self):
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            return json.loads(raw.decode('utf-8') or '{}')
        except Exception:
            return {}

    def _ask(self, key, default=''):
        """Read one value from the host application, never crashing if the
        app is mid-startup and has not wired everything up yet."""
        try:
            provider = (self.context or {}).get(key)
            return provider() if callable(provider) else default
        except Exception:
            return default

    # -- GET -------------------------------------------------------------

    def do_GET(self):
        if not self.path.startswith('/aurum/hello'):
            self._send({'status': 'error', 'message': 'unknown endpoint'}, 404)
            return
        try:
            self._send({
                'status':      'success',
                'aurumos':     True,
                'shop_id':     self.db.get_or_create_shop_id() if self.db else '',
                'business':    self._ask('business_name'),
                'version':     self._ask('version'),
                'plan':        self._ask('plan', 'lite'),
                'accepting':   bool(self.db),
            })
        except Exception as e:
            self._send({'status': 'error', 'message': str(e)}, 500)

    # -- POST ------------------------------------------------------------

    def do_POST(self):
        if not self.db:
            self._send({'status': 'error', 'message': 'server database not ready'}, 503)
            return
        try:
            if self.path.startswith('/aurum/enroll'):
                return self._enroll(self._body())
            if self.path.startswith('/aurum/login'):
                return self._login(self._body())
            self._send({'status': 'error', 'message': 'unknown endpoint'}, 404)
        except Exception as e:
            _log(f'request failed: {e}')
            self._send({'status': 'error', 'message': str(e)}, 500)

    def _enroll(self, payload):
        from database import cluster_store as store
        from core import permissions as perms

        terminal_id = str(payload.get('terminal_id') or '').strip()
        if not terminal_id:
            self._send({'status': 'error', 'message': 'terminal_id is required'}, 400)
            return

        client_ip = self.client_address[0] if self.client_address else ''
        terminal, secret = store.register_terminal(
            self.db, terminal_id,
            hostname=str(payload.get('hostname') or '').strip(),
            ip=client_ip)

        licensed = perms.licensed_seats(self._ask('plan', 'lite'), self._ask('features', []))
        if not store.within_seat_limit(self.db, terminal_id, licensed):
            _log(f'{terminal_id} refused: seat limit {licensed} reached')
            self._send({
                'status':   'seat_limit',
                'licensed': licensed,
                'message':  (f'This licence connects {licensed} PC'
                             f'{"" if licensed == 1 else "s"}. '
                             'Remove another PC on the owner computer, or add a connection '
                             'to your plan.'),
            }, 403)
            return

        if terminal.get('status') == store.STATUS_BLOCKED:
            self._send({'status': 'blocked',
                        'message': 'This PC has been blocked by the owner.'}, 403)
            return

        _log(f'{terminal_id} enrolled from {client_ip} as {terminal.get("status")}')
        self._send({
            'status':      'success',
            'terminal':    terminal,
            'secret':      secret,
            'shop_id':     self.db.get_or_create_shop_id(),
            'business':    self._ask('business_name'),
            'server_plan': self._ask('plan', 'lite'),
        })

    def _login(self, payload):
        from database import cluster_store as store
        from core import permissions as perms

        terminal_id = str(payload.get('terminal_id') or '').strip()
        password = str(payload.get('password') or '')

        terminal = store.get_terminal(self.db, terminal_id) if terminal_id else None
        if not terminal:
            self._send({'status': 'not_enrolled',
                        'message': 'This PC is not connected to the shop server.'}, 403)
            return
        if terminal.get('status') == store.STATUS_BLOCKED:
            self._send({'status': 'blocked',
                        'message': 'This PC has been blocked by the owner.'}, 403)
            return
        if str(payload.get('secret') or '') != store.get_secret(self.db, terminal_id):
            self._send({'status': 'denied',
                        'message': 'This PC is no longer recognised. Run setup again.'}, 403)
            return

        tries, first = _throttle_state(terminal_id)
        if tries >= _MAX_ATTEMPTS:
            wait = int(_ATTEMPT_WINDOW - (time.time() - first))
            self._send({'status': 'locked', 'retry_after': max(wait, 1),
                        'message': f'Too many wrong passwords. Try again in {max(wait, 1)} seconds.'}, 429)
            return

        auth = self.db.authenticate_user_by_password(password)
        if not auth.get('authenticated'):
            _record_failure(terminal_id)
            left = max(_MAX_ATTEMPTS - (tries + 1), 0)
            self._send({'status': 'denied', 'attempts_left': left,
                        'message': f'Wrong password. {left} attempt'
                                   f'{"" if left == 1 else "s"} remaining.'}, 403)
            return

        _clear_failures(terminal_id)
        store.touch_terminal(self.db, terminal_id,
                             self.client_address[0] if self.client_address else '')

        plan = self._ask('plan', 'lite')
        username = auth.get('username', '')

        # A terminal always logs in as staff, even when the password belongs to
        # the owner. The owner's own PC is the server; letting a staff PC hold
        # an owner session would put the whole application on the shop floor.
        assigned = terminal.get('assigned_user') or ''
        if assigned and assigned.strip().lower() != username.strip().lower():
            self._send({'status': 'wrong_user',
                        'message': f'This PC is set up for {assigned}.'}, 403)
            return

        bundles = terminal.get('bundles') or perms.bundles_for_plan(plan)
        bundles = [b for b in bundles if b in perms.bundles_for_plan(plan)]

        _log(f'{terminal_id} signed in as {username} with {bundles}')
        self._send({
            'status':   'success',
            'role':     'staff',
            'username': username,
            'bundles':  bundles,
            'pages':    perms.pages_for(bundles, self._ask('software_type', 'wholesale')),
            'business': self._ask('business_name'),
        })


class _ShopServer(ThreadingHTTPServer):
    """The shop server, refusing to share its port.

    socketserver turns SO_REUSEADDR on by default. On Unix that only allows
    reuse of a port left in TIME_WAIT, which is what everyone wants. On Windows
    it means something else entirely: a second process can bind a port another
    process is actively listening on, both succeed, and connections are handed
    to one or the other at random.

    That is worse than a crash. Two copies of AurumOS on one PC, or a stray one
    left running, would each believe they were the shop server while half the
    staff PCs talked to the wrong one. Turning it off on Windows makes the
    second bind fail, which start_server already reports.
    """
    daemon_threads = True
    allow_reuse_address = (sys.platform != 'win32')


_server = None
_thread = None


def start_server(db, context=None, host='0.0.0.0', port=None):
    """Start the shop server. Safe to call twice; the second call refreshes
    the database handle rather than trying to bind the port again."""
    global _server, _thread
    # AURUM_CLUSTER_PORT lets a test, or a second copy on one machine, listen
    # somewhere other than the shop port instead of fighting over it.
    if port is None:
        try:
            port = int(os.environ.get('AURUM_CLUSTER_PORT') or CLUSTER_PORT)
        except ValueError:
            port = CLUSTER_PORT
    _ClusterHandler.db = db
    _ClusterHandler.context = context or {}
    if _server is not None:
        return _server
    try:
        _server = _ShopServer((host, port), _ClusterHandler)
        _thread = threading.Thread(target=_server.serve_forever, daemon=True,
                                   name='AurumClusterServer')
        _thread.start()
        _log(f'listening on {host}:{port}')
        return _server
    except OSError as e:
        # Another copy of the app, or a stale process, already holds the port.
        # Never take the whole application down over it.
        _log(f'could not bind {host}:{port}: {e}')
        _server = None
        return None


def stop_server():
    global _server, _thread
    try:
        if _server:
            _server.shutdown()
            _server.server_close()
            _log('stopped')
    except Exception:
        pass
    _server, _thread = None, None


def is_running():
    return _server is not None
