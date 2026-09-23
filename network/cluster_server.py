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

import hmac
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

# How long a staff PC may keep working after it last reached the server. The
# grace exists so a router reboot does not stop a sale mid-way; past it the
# terminal has no authority to continue and says so.
LEASE_SECONDS = 300

# How long a centre stays logged in on the portal (also the default in
# hm_share.HM_SESSION_TTL, which owns the session table). The link itself is
# governed by hm_portal_tokens; a login still dies on its own clock, so a
# lost browser does not stay open for ever.
HM_SESSION_SECONDS = 12 * 3600


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


def _secrets_match(presented, expected):
    """Constant-time shared-secret comparison.

    A plain == leaks how many leading characters are right through timing,
    which turns the 64-hex-char terminal secret into a guessable one for
    anyone on the LAN willing to measure. compare_digest closes that.
    """
    try:
        a = str(presented or '')
        b = str(expected or '')
        if not a or not b:
            return False
        return hmac.compare_digest(a, b)
    except Exception:
        return False


class _ClusterHandler(BaseHTTPRequestHandler):
    db = None
    context = None          # supplies plan, features and business name
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass                # the default handler spams stderr

    def _send(self, obj, status=200, cookies=None):
        try:
            body = json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            for jar in (cookies or []):
                self.send_header('Set-Cookie', jar)
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_html(self, text, status=200):
        try:
            body = (text or '').encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
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
        if self.path.startswith('/hm/'):
            return self._hm_get()
        if self.path == '/' or self.path.startswith('/?'):
            return self._hm_index()
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

    # -- hallmark centre portal ------------------------------------------
    # The assaying centre is not a staff terminal and never enrols: it opens
    # a plain web page, either from a share link for one lot or the Kanban
    # board for everything away. Only open lots are ever visible here, and
    # only piece results, HUIDs and board columns can be written back. Money,
    # customers and the shop's own register stay unreachable.

    def _hm_index(self):
        """The root page: what this server is and where the board lives.

        Staff PCs probe bare addresses when joining, and owners paste the
        bare address into a browser. Answering with links beats answering
        with 'unknown endpoint'.
        """
        shop = ''
        try:
            if self.db:
                from network import hm_share as hms
                shop = hms.shop_name(self.db)
        except Exception:
            shop = ''
        title = (str(shop).replace('&', '&amp;').replace('<', '&lt;')
                 .replace('>', '&gt;') if shop else 'AurumOS shop server')
        self._send_html(
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,'
            ' initial-scale=1.0"><title>' + title
            + ' — centre login</title>'
            '<style>body{margin:0;min-height:100vh;display:flex;'
            'align-items:center;justify-content:center;background:#f1f5f9;'
            'font-family:"DM Sans",system-ui,sans-serif;color:#0f172a;}'
            '.box{background:#fff;border:1px solid #e2e8f0;border-radius:16px;'
            'padding:36px 40px;text-align:center;width:340px;max-width:92vw;'
            'box-shadow:0 18px 44px rgba(15,23,42,0.12);}'
            '.shop{font-size:0.72rem;color:#64748b;margin-bottom:4px;}'
            'h1{font-size:1.2rem;margin:0 0 6px;}'
            'p{font-size:0.78rem;color:#64748b;line-height:1.6;margin:0 0 18px;}'
            'input{width:100%;height:46px;text-align:center;font-size:1.05rem;'
            'letter-spacing:5px;border:1px solid #e2e8f0;border-radius:10px;'
            'outline:none;box-sizing:border-box;}'
            'input:focus{border-color:#2563eb;}'
            'button{width:100%;margin-top:12px;height:44px;border:none;'
            'border-radius:10px;background:#2563eb;color:#fff;font-weight:700;'
            'font-size:0.85rem;cursor:pointer;font-family:inherit;}'
            'button:hover{background:#1d4ed8;}'
            '#err{min-height:1.2em;font-size:0.72rem;color:#dc2626;'
            'margin-top:10px;font-weight:600;}</style></head><body>'
            '<div class="box"><div class="shop">' + title + '</div>'
            '<h1>Centre login</h1>'
            '<p>Type the login the shop gave you.</p>'
            '<input id="pin" type="password" maxlength="32" '
            'placeholder="••••" autocomplete="off">'
            '<button onclick="go()">Open my board</button>'
            '<div id="err"></div></div>'
            '<script>'
            'function go(){'
            'var pin=document.getElementById("pin").value||"";'
            'var err=document.getElementById("err");'
            'if(!pin){err.textContent="Type your login first.";return;}'
            'err.textContent="";'
            'fetch("/hm/api/plogin",{method:"POST",'
            'headers:{"Content-Type":"application/json"},'
            'body:JSON.stringify({pin:pin})}).then(function(r){return r.json();})'
            '.then(function(d){'
            'if(d&&d.status==="success"){window.location="/hm/board";return;}'
            'err.textContent=(d&&d.message)||"Could not log in.";'
            '}).catch(function(){err.textContent="No connection.";});}'
            'document.getElementById("pin").addEventListener("keydown",'
            'function(e){if(e.key==="Enter")go();});'
            '</script></body></html>')

    def _hm_board_file(self):
        """The centre board page on disk, frozen or source alike."""
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            exe_dir = os.path.dirname(os.path.abspath(_sys.executable))
            p = os.path.join(exe_dir, '_internal', 'ui', 'hm_board.html')
            if os.path.isfile(p):
                return p
            base = getattr(_sys, '_MEIPASS', '') or ''
            p2 = os.path.join(base, 'ui', 'hm_board.html') if base else ''
            return p2 if p2 and os.path.isfile(p2) else p
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(here), 'ui', 'hm_board.html')

    def _hm_session_centre(self):
        """The logged-in centre behind this request's cookie, if any."""
        try:
            raw = self.headers.get('Cookie') or ''
            jar = {}
            for part in raw.split(';'):
                if '=' in part:
                    k, _, v = part.partition('=')
                    jar[k.strip()] = v.strip()
            sid = jar.get('hm_session') or ''
            if not sid or not self.db:
                return None
            from network import hm_share as hms
            return hms.hm_session_get(self.db, sid)
        except Exception:
            return None

    def _hm_require_login(self, token):
        """The token's centre, but only with a matching login session.

        Returns (centre, token, None) or (None, '', error_dict). A portal
        link alone opens the login form; only the login opens the data.
        An empty token means the PIN-only root login: the session itself
        names the centre, and its live link is used under the hood.
        """
        from network import hm_share as hms
        if not self.db:
            return None, '', {'status': 'error',
                              'message': 'server database not ready'}
        token = str(token or '').strip()
        if not token:
            cid = self._hm_session_centre()
            if not cid:
                return None, '', {
                    'status': 'login_required', 'centre': '',
                    'message': 'Type your login first.'}
            token = hms.live_token(self.db, cid)
            if not token:
                return None, '', {
                    'status': 'denied',
                    'message': 'This portal link is no longer valid.'}
        centre = hms.portal_centre(self.db, token)
        if not centre:
            return None, '', {'status': 'denied',
                              'message': 'This portal link is no longer '
                                         'valid.'}
        if not hms.centre_has_pin(self.db, centre['id']):
            return None, '', {'status': 'no_pin',
                              'message': 'The shop has not set your login '
                                         'yet. Ask them for it.'}
        if self._hm_session_centre() != int(centre['id']):
            return None, '', {'status': 'login_required',
                              'centre': centre.get('name') or '',
                              'message': 'Log in to open your lots.'}
        return centre, token, None

    def _hm_get(self):
        from urllib.parse import urlparse, parse_qs
        from network import hm_share as hms
        try:
            parsed = urlparse(self.path)
            route = parsed.path.rstrip('/') or '/hm/board'
            if route in ('/hm', '/hm/board') or route.startswith('/hm/c/'):
                page = self._hm_board_file()
                try:
                    with open(page, 'r', encoding='utf-8') as f:
                        self._send_html(f.read())
                except Exception:
                    self._send({'status': 'error',
                                'message': 'board page missing'}, 404)
                return
            if not self.db:
                self._send({'status': 'error',
                            'message': 'server database not ready'}, 503)
                return
            if route == '/hm/api/info':
                self._send({'status': 'success', 'shop': hms.shop_name(self.db),
                            'stages': [{'id': s, 'label': hms.STAGE_LABELS[s]}
                                       for s in hms.STAGES]})
                return
            if route == '/hm/api/lots':
                self._send(hms.board_payload(self.db))
                return
            if route == '/hm/api/lot':
                lot_no = (parse_qs(parsed.query).get('lot_no') or [''])[0]
                out = hms.lot_payload(self.db, lot_no)
                self._send(out, 200 if out.get('status') == 'success' else 404)
                return
            if route == '/hm/api/clots':
                token = (parse_qs(parsed.query).get('token') or [''])[0]
                centre, token, err = self._hm_require_login(token)
                if err:
                    self._send({'status': 'success', 'logged_in': False,
                                'reason': err.get('status'),
                                'message': err.get('message'),
                                'centre': err.get('centre', '')})
                    return
                self._send(hms.portal_board(self.db, token))
                return
            if route == '/hm/api/clot':
                qs = parse_qs(parsed.query)
                token = (qs.get('token') or [''])[0]
                lot_no = (qs.get('lot_no') or [''])[0]
                centre, token, err = self._hm_require_login(token)
                if err:
                    self._send({'status': 'success', 'logged_in': False,
                                'reason': err.get('status'),
                                'message': err.get('message'),
                                'centre': err.get('centre', '')})
                    return
                out = hms.portal_lot(self.db, token, lot_no)
                code = 200 if out.get('status') == 'success' else 403
                self._send(out, code)
                return
            if route == '/hm/api/csession':
                token = (parse_qs(parsed.query).get('token') or [''])[0]
                centre, token, err = self._hm_require_login(token)
                if err:
                    self._send({'status': 'success', 'logged_in': False,
                                'reason': err.get('status'),
                                'message': err.get('message'),
                                'centre': err.get('centre', '')})
                    return
                self._send({'status': 'success', 'logged_in': True,
                            'centre': centre.get('name') or ''})
                return
            self._send({'status': 'error', 'message': 'unknown endpoint'}, 404)
        except Exception as e:
            _log(f'hm request failed: {e}')
            self._send({'status': 'error', 'message': str(e)}, 500)

    def _hm_post(self, payload):
        from network import hm_share as hms
        if self.path.startswith('/hm/api/piece'):
            return self._send(hms.set_piece(
                self.db, payload.get('lot_no'), payload.get('tag_id'),
                payload.get('result'), payload.get('huid')))
        if self.path.startswith('/hm/api/stage'):
            return self._send(hms.move_stage(
                self.db, payload.get('lot_no'), payload.get('stage')))
        if self.path.startswith('/hm/api/clogin'):
            return self._hm_login(payload)
        if self.path.startswith('/hm/api/plogin'):
            return self._hm_plogin(payload)
        if self.path.startswith('/hm/api/clogout'):
            return self._hm_logout()
        if self.path.startswith('/hm/api/cpiece'):
            centre, token, err = self._hm_require_login(payload.get('token'))
            if err:
                return self._send(err, 403)
            return self._send(hms.portal_piece(
                self.db, token, payload.get('tag_id'),
                payload.get('result'), payload.get('huid')))
        if self.path.startswith('/hm/api/cstage'):
            centre, token, err = self._hm_require_login(payload.get('token'))
            if err:
                return self._send(err, 403)
            return self._send(hms.portal_stage(
                self.db, token, payload.get('lot_no'),
                payload.get('stage')))
        self._send({'status': 'error', 'message': 'unknown endpoint'}, 404)

    def _hm_login(self, payload):
        """Trade a portal token plus the centre PIN for a login session."""
        from network import hm_share as hms
        token = str((payload or {}).get('token') or '').strip()
        pin = str((payload or {}).get('pin') or '')
        centre = hms.portal_centre(self.db, token)
        if not centre:
            return self._send({'status': 'denied',
                               'message': 'This portal link is no longer '
                                          'valid.'}, 403)
        if not hms.centre_has_pin(self.db, centre['id']):
            return self._send({'status': 'no_pin',
                               'message': 'The shop has not set your login '
                                          'yet. Ask them for it.'}, 403)
        if not hms.verify_centre_pin(self.db, centre['id'], pin):
            _log(f"portal login refused for centre {centre['id']}")
            return self._send({'status': 'denied',
                               'message': 'Wrong login. Try again.'}, 403)
        sid, max_age = hms.hm_session_create(self.db, centre['id'])
        if not sid:
            return self._send({'status': 'error',
                               'message': 'Could not open the portal.'}, 500)
        _log(f"portal login: centre {centre['id']}")
        jar = (f'hm_session={sid}; Path=/hm/; Max-Age={max_age}; '
               f'HttpOnly; SameSite=Lax')
        return self._send({'status': 'success',
                           'centre': centre.get('name') or ''},
                          cookies=[jar])

    def _hm_plogin(self, payload):
        """PIN-only login from the root page: just a PIN, nothing else.

        PINs are unique per centre, so one PIN finds exactly one dashboard.
        """
        from network import hm_share as hms
        if not self.db:
            return self._send({'status': 'error',
                               'message': 'server database not ready'}, 503)
        pin = str((payload or {}).get('pin') or '')
        if not pin:
            return self._send({'status': 'error',
                               'message': 'Type your login first.'}, 400)
        centre = hms.find_centre_by_pin(self.db, pin)
        if not centre:
            _log('portal pin login refused (no match)')
            return self._send({'status': 'denied',
                               'message': 'Wrong login. Try again.'}, 403)
        token = hms.live_token(self.db, centre['id'])
        if not token:
            token = hms.silent_token(self.db, centre['id'])
        if not token:
            return self._send({'status': 'error',
                               'message': 'Could not open the portal.'}, 500)
        sid, max_age = hms.hm_session_create(self.db, centre['id'])
        if not sid:
            return self._send({'status': 'error',
                               'message': 'Could not open the portal.'}, 500)
        _log(f"portal pin login: centre {centre['id']}")
        jar = (f'hm_session={sid}; Path=/hm/; Max-Age={max_age}; '
               f'HttpOnly; SameSite=Lax')
        return self._send({'status': 'success',
                           'centre': centre.get('name') or ''},
                          cookies=[jar])

    def _hm_logout(self):
        sid = None
        try:
            raw = self.headers.get('Cookie') or ''
            for part in raw.split(';'):
                if '=' in part:
                    k, _, v = part.partition('=')
                    if k.strip() == 'hm_session':
                        sid = v.strip()
        except Exception:
            sid = None
        if sid:
            try:
                from network import hm_share as hms
                hms.hm_session_drop(self.db, sid)
            except Exception:
                pass
        return self._send(
            {'status': 'success'},
            cookies=['hm_session=; Path=/hm/; Max-Age=0; HttpOnly'])

    # -- POST ------------------------------------------------------------

    def do_POST(self):
        if not self.db:
            self._send({'status': 'error', 'message': 'server database not ready'}, 503)
            return
        try:
            if self.path.startswith('/hm/api/'):
                return self._hm_post(self._body())
            if self.path.startswith('/aurum/enroll'):
                return self._enroll(self._body())
            if self.path.startswith('/aurum/login'):
                return self._login(self._body())
            if self.path.startswith('/aurum/sync'):
                return self._sync(self._body())
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
        # Whether this PC was already on the owner's list BEFORE this call.
        # A brand-new PC refused by the seat limit must not leave a row
        # behind: the owner would otherwise collect dead entries for PCs
        # that were never allowed in. (The limit check itself has to run
        # AFTER registering, because seat order is ranked against the
        # existing rows — an unknown PC checked first would always pass.)
        known = store.get_terminal(self.db, terminal_id)
        terminal, secret = store.register_terminal(
            self.db, terminal_id,
            hostname=str(payload.get('hostname') or '').strip(),
            ip=client_ip)

        licensed = perms.licensed_seats(self._ask('plan', 'lite'), self._ask('features', []))
        if not store.within_seat_limit(self.db, terminal_id, licensed):
            if not known:
                try:
                    store.remove_terminal(self.db, terminal_id)
                except Exception:
                    pass
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

    def _authenticate_terminal(self, payload):
        """Confirm the PC is one we know and still allow. Returns the terminal
        row, or None after having already answered with the refusal."""
        from database import cluster_store as store
        terminal_id = str(payload.get('terminal_id') or '').strip()
        terminal = store.get_terminal(self.db, terminal_id) if terminal_id else None
        if not terminal:
            self._send({'status': 'not_enrolled',
                        'message': 'This PC is not connected to the shop server.'}, 403)
            return None
        if terminal.get('status') == store.STATUS_BLOCKED:
            self._send({'status': 'blocked',
                        'message': 'This PC has been blocked by the owner.'}, 403)
            return None
        if not _secrets_match(payload.get('secret'),
                              store.get_secret(self.db, terminal_id)):
            self._send({'status': 'denied',
                        'message': 'This PC is no longer recognised. Run setup again.'}, 403)
            return None
        return terminal

    def _sync(self, payload):
        """Take a staff PC's new rows and hand back everyone else's.

        The server is a hub. A terminal never talks to another terminal: it
        sends only what it wrote and receives everything it has not seen, so a
        bill taken at one counter reaches the owner and every other counter
        through here.
        """
        from database import cluster_store as store

        terminal = self._authenticate_terminal(payload)
        if terminal is None:
            return

        device_id = str(payload.get('device_id') or '').strip()
        if not device_id:
            self._send({'status': 'error', 'message': 'device_id is required'}, 400)
            return

        accepted = {}
        failed = {}
        incoming = payload.get('changes') or {}
        if incoming:
            result = self.db.apply_incoming_rows(device_id, incoming)
            accepted = result.get('applied', {}) or {}
            failed = result.get('failed', {}) or {}

        relayed = store.changes_from_others(
            self.db, device_id, payload.get('since') or {})

        store.touch_terminal(
            self.db, terminal.get('terminal_id'),
            self.client_address[0] if self.client_address else '')

        if accepted or relayed:
            _log(f"sync with {terminal.get('hostname') or device_id}: "
                 f"took {sum(accepted.values())}, sent "
                 f"{sum(len(t) for o in relayed.values() for t in o.values())}")

        self._send({
            'status':        'success',
            'changes':       relayed,
            'accepted':      accepted,
            'failed':        failed,
            'lease_seconds': LEASE_SECONDS,
            'server_time':   time.time(),
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
        if not _secrets_match(payload.get('secret'),
                              store.get_secret(self.db, terminal_id)):
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
