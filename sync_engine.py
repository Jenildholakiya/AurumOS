# -*- coding: utf-8 -*-
"""
AurumOS LAN Sync Engine -- Multi-PC, Shop-Scoped  (FIXED v2)
=============================================================

Fixes in this version
---------------------
1. Bidirectional discovery — PC2 discovers PC1 even if PC2 started first.
   Every PC that discovers a NEW peer immediately triggers a sync attempt
   instead of waiting for the next poll interval. Polling also retries
   previously-unreachable peers every cycle (exponential backoff removed).

2. USB-transfer shop_id isolation — caller passes `fresh_install=True`
   when the DB was just received via USB/copy. SyncEngine.__init__ accepts
   this flag and calls db.reset_shop_id() before broadcasting, so the
   copied DB gets a BRAND-NEW shop_id and never accidentally joins the
   donor shop's sync group.

3. Duplicate is_feature_enabled removed from db_manager — the correct
   fail-open version (defaults True) is the only one that should exist.
   That fix is in db_manager.py (see patch below).  SyncEngine itself
   no longer calls is_feature_enabled at all — LAN sync is always on.
"""

import json
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import logging as _logging
_sync_logger = _logging.getLogger('aurumos.sync')


def _log(msg):
    try:
        _sync_logger.info(f"[SYNC] {msg}")
    except Exception:
        pass


SYNC_PORT          = 58901   # TCP  — actual data exchange
DISCOVERY_PORT     = 58902   # UDP  — "I'm here" broadcasts
DISCOVERY_INTERVAL = 4       # s    — how often we broadcast
SYNC_INTERVAL      = 12      # s    — how often we poll known peers
CONNECT_TIMEOUT    = 3       # s    — fail fast on unreachable peers
PEER_STALE_AFTER   = 30      # s    — drop peer if silent this long


# ---------------------------------------------------------------------------
# HTTP SERVER  (incoming sync requests)
# ---------------------------------------------------------------------------

class _SyncRequestHandler(BaseHTTPRequestHandler):
    db_manager = None

    def log_message(self, fmt, *args):
        pass   # silence default HTTP log spam

    def _send_json(self, obj, status=200):
        try:
            body = json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',  'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        try:
            if self.path.startswith('/aurum-sync/ping'):
                dev_id  = self.db_manager.get_or_create_device_id()  if self.db_manager else ''
                shop_id = self.db_manager.get_or_create_shop_id()    if self.db_manager else ''
                self._send_json({'status': 'success',
                                 'device_id': dev_id,
                                 'shop_id':   shop_id})
                return
            self._send_json({'status': 'error', 'message': 'unknown endpoint'}, 404)
        except Exception as e:
            self._send_json({'status': 'error', 'message': str(e)}, 500)

    def do_POST(self):
        try:
            if not self.path.startswith('/aurum-sync/exchange'):
                self._send_json({'status': 'error', 'message': 'unknown endpoint'}, 404)
                return
            if not self.db_manager:
                self._send_json({'status': 'error', 'message': 'db not ready'}, 503)
                return

            length  = int(self.headers.get('Content-Length', 0) or 0)
            raw     = self.rfile.read(length) if length else b'{}'
            payload = json.loads(raw.decode('utf-8') or '{}')

            peer_device_id = payload.get('device_id', '')
            peer_shop_id   = payload.get('shop_id',   '')
            my_shop_id     = self.db_manager.get_or_create_shop_id()

            if peer_shop_id != my_shop_id:
                _log(f"REJECTED — shop mismatch: peer={peer_shop_id} mine={my_shop_id}")
                self._send_json({'status': 'error', 'message': 'shop_id mismatch'}, 403)
                return

            client_ip      = self.client_address[0] if self.client_address else '?'
            since_versions = payload.get('since_versions', {})
            incoming       = payload.get('changes', {})

            if peer_device_id and incoming:
                _log(f"Applying from {client_ip}: "
                     + ', '.join(f"{len(v)} {k}" for k, v in incoming.items()))
                self.db_manager.apply_incoming_rows(peer_device_id, incoming)
            else:
                _log(f"Nothing to apply from {client_ip}")

            my_changes = self.db_manager.get_changes_since(peer_device_id, since_versions)
            reply      = my_changes.get('changes', {})
            if reply:
                _log(f"Replying to {client_ip}: "
                     + ', '.join(f"{len(v)} {k}" for k, v in reply.items()))

            try:
                self.db_manager.detect_stock_conflicts()
            except Exception:
                pass

            self._send_json(my_changes)

        except Exception as e:
            _log(f"do_POST error: {e}\n{traceback.format_exc()}")
            self._send_json({'status': 'error', 'message': str(e)}, 500)


# ---------------------------------------------------------------------------
# SYNC ENGINE
# ---------------------------------------------------------------------------

class SyncEngine:
    """
    Four background threads:
      1. HTTP server     — accepts incoming exchange requests
      2. UDP broadcaster — announces this PC on the LAN every 4 s
      3. UDP listener    — discovers peers; triggers immediate sync on new find
      4. Poll loop       — re-syncs with every known peer every 12 s

    Constructor
    -----------
    SyncEngine(db_manager, fresh_install=False)

    Set fresh_install=True when the DB file just arrived via USB/copy.
    This generates a new shop_id so the copy never accidentally joins
    the donor shop's sync group.
    """

    def __init__(self, db_manager,
                 port=SYNC_PORT,
                 discovery_port=DISCOVERY_PORT,
                 interval_seconds=SYNC_INTERVAL,
                 fresh_install=False):

        self.db               = db_manager
        self.port             = port
        self.discovery_port   = discovery_port
        self.interval         = interval_seconds
        self._fresh_install   = fresh_install

        self._server          = None
        self._server_thread   = None
        self._broadcast_thread = None
        self._listen_thread   = None
        self._poll_thread     = None
        self._stop_flag       = threading.Event()

        # {device_id: {'ip': str, 'port': int, 'last_seen': float}}
        self._peers      = {}
        self._peers_lock = threading.Lock()

        # Set of device_ids we have already triggered an immediate sync for
        # (so we don't hammer a new peer with 10 instant syncs in a row).
        self._greeted    = set()

        self.last_sync_ok = False
        self.last_sync_at = None
        self.last_error   = ''

    # -----------------------------------------------------------------------
    # PUBLIC — start / stop / status
    # -----------------------------------------------------------------------

    def start(self):
        # USB-copy guard: assign a fresh shop_id before we start broadcasting
        if self._fresh_install:
            self._reset_shop_id()

        shop_id   = self.db.get_or_create_shop_id()
        device_id = self.db.get_or_create_device_id()
        _log(f"Starting. shop_id={shop_id} device_id={device_id} "
             f"fresh_install={self._fresh_install}")

        self._start_server()

        self._broadcast_thread = threading.Thread(
            target=self._broadcast_loop, daemon=True, name='SyncBroadcast')
        self._broadcast_thread.start()

        self._listen_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name='SyncListen')
        self._listen_thread.start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name='SyncPoll')
        self._poll_thread.start()

        _log(f"Engine started — broadcasting on shop {shop_id}")

    def stop(self):
        self._stop_flag.set()
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass

    def status(self):
        with self._peers_lock:
            count    = len(self._peers)
            peer_ips = [f"{i['ip']}:{i.get('port', self.port)}"
                        for i in self._peers.values()]
        return {
            'shop_id':          self.db.get_or_create_shop_id(),
            'device_id':        self.db.get_or_create_device_id(),
            'peers_discovered': count,
            'peer_ips':         peer_ips,
            'last_sync_ok':     self.last_sync_ok,
            'last_sync_at':     self.last_sync_at,
            'last_error':       self.last_error,
        }

    # -----------------------------------------------------------------------
    # USB-COPY GUARD
    # -----------------------------------------------------------------------

    def _reset_shop_id(self):
        """
        Called when fresh_install=True (USB copy detected).
        Generates a BRAND-NEW shop_id and clears device_id so this
        PC starts completely fresh — it will never join the donor's
        sync group by accident.
        """
        import uuid as _uuid
        try:
            new_shop_id = 'SHOP-' + _uuid.uuid4().hex[:8].upper()
            with self.db._get_connection() as conn:
                # New shop identity
                conn.execute(
                    "INSERT OR REPLACE INTO app_config(key,value) VALUES('shop_id',?)",
                    (new_shop_id,)
                )
                # New device identity (fresh PC, fresh device_id)
                new_dev_id = 'DEV-' + _uuid.uuid4().hex[:12].upper()
                conn.execute(
                    "INSERT OR REPLACE INTO device_registry(id,device_id,device_name) VALUES(1,?,'')",
                    (new_dev_id,)
                )
                # Wipe sync state so nothing leaks from the donor DB
                conn.execute("DELETE FROM sync_state")
                conn.execute("DELETE FROM sync_conflicts")
                conn.commit()
            _log(f"USB-copy guard: new shop_id={new_shop_id} device_id={new_dev_id}")
        except Exception as e:
            _log(f"_reset_shop_id error: {e}")

    # -----------------------------------------------------------------------
    # HTTP SERVER
    # -----------------------------------------------------------------------

    def _start_server(self):
        try:
            _SyncRequestHandler.db_manager = self.db
            self._server = HTTPServer(('0.0.0.0', self.port), _SyncRequestHandler)
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name='SyncHTTP')
            self._server_thread.start()
            _log(f"Server listening on 0.0.0.0:{self.port}")
        except OSError as e:
            _log(f"Could not start sync server: {e}")
        except Exception as e:
            _log(f"Unexpected server start error: {e}")

    # -----------------------------------------------------------------------
    # UDP BROADCAST  ("I'm here")
    # -----------------------------------------------------------------------

    def _broadcast_loop(self):
        shop_id   = self.db.get_or_create_shop_id()
        device_id = self.db.get_or_create_device_id()
        msg = json.dumps({
            'aurumos_sync': True,
            'shop_id':      shop_id,
            'device_id':    device_id,
            'port':         self.port,
        }).encode('utf-8')

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while not self._stop_flag.is_set():
            try:
                sock.sendto(msg, ('255.255.255.255', self.discovery_port))
            except Exception as e:
                _log(f"Broadcast failed: {e}")
            self._stop_flag.wait(DISCOVERY_INTERVAL)

        try:
            sock.close()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # UDP LISTENER  (discover peers)
    # -----------------------------------------------------------------------

    def _listen_loop(self):
        my_shop_id   = self.db.get_or_create_shop_id()
        my_device_id = self.db.get_or_create_device_id()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.discovery_port))
        except OSError as e:
            _log(f"Could not bind discovery listener: {e}")
            return
        sock.settimeout(1.0)

        while not self._stop_flag.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                peer_ip = addr[0]
                try:
                    info = json.loads(data.decode('utf-8'))
                except Exception:
                    continue

                if not info.get('aurumos_sync'):
                    continue

                peer_shop_id   = info.get('shop_id',   '')
                peer_device_id = info.get('device_id', '')

                # Ignore ourselves
                if peer_device_id == my_device_id:
                    continue
                # Ignore different shops
                if peer_shop_id != my_shop_id:
                    continue

                peer_port = info.get('port', self.port)

                with self._peers_lock:
                    is_new = peer_device_id not in self._peers
                    self._peers[peer_device_id] = {
                        'ip':        peer_ip,
                        'port':      peer_port,
                        'last_seen': time.time(),
                    }

                if is_new:
                    _log(f"Discovered NEW shopmate at {peer_ip}:{peer_port} "
                         f"(device {peer_device_id})")
                    # ── FIX: immediate sync on discovery ──────────────────
                    # Don't wait for the next 12-second poll cycle.
                    # Fire an instant sync in the background so both PCs
                    # are in sync within seconds of finding each other,
                    # regardless of which one started first.
                    if peer_device_id not in self._greeted:
                        self._greeted.add(peer_device_id)
                        threading.Thread(
                            target=self._instant_sync,
                            args=(peer_ip, peer_port, peer_device_id),
                            daemon=True,
                            name=f'SyncGreet-{peer_device_id[:8]}'
                        ).start()

            except socket.timeout:
                continue
            except Exception as e:
                _log(f"Discovery listen error: {e}")

        try:
            sock.close()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # IMMEDIATE SYNC  (triggered the moment a new peer is discovered)
    # -----------------------------------------------------------------------

    def _instant_sync(self, peer_ip, peer_port, peer_device_id):
        """
        Runs in its own thread as soon as a new peer is spotted.
        Retries up to 5 times with a short back-off in case the peer's
        HTTP server hasn't bound its port yet (e.g. it just started).
        """
        for attempt in range(1, 6):
            if self._stop_flag.is_set():
                return
            _log(f"Greeting {peer_ip} attempt {attempt}/5 ...")
            ok = self._sync_with_peer(peer_ip, peer_port)
            if ok:
                _log(f"Greeting sync with {peer_ip} succeeded on attempt {attempt}")
                self.last_sync_ok = True
                self.last_sync_at = time.time()
                self.last_error   = ''
                return
            # Short pause before retry — give the remote HTTP server time to bind
            time.sleep(2 * attempt)

        _log(f"Greeting sync with {peer_ip} failed after 5 attempts — "
             f"will retry in regular poll cycle")
        # Remove from greeted set so the poll loop can try again normally
        self._greeted.discard(peer_device_id)

    # -----------------------------------------------------------------------
    # POLL LOOP  (periodic re-sync with all known peers)
    # -----------------------------------------------------------------------

    def _get_live_peers(self):
        """Return (ip, port) for currently-known, non-stale peers."""
        now = time.time()
        with self._peers_lock:
            stale = [dev for dev, info in self._peers.items()
                     if now - info['last_seen'] > PEER_STALE_AFTER]
            for dev in stale:
                _log(f"Peer {dev} stale — removing")
                del self._peers[dev]
                self._greeted.discard(dev)
            return [(dev, info['ip'], info.get('port', self.port))
                    for dev, info in self._peers.items()]

    def _poll_loop(self):
        while not self._stop_flag.is_set():
            try:
                peers = self._get_live_peers()
                if not peers:
                    self.last_sync_ok = False
                    self.last_error   = 'No shopmate PCs discovered yet'
                else:
                    any_ok = False
                    for dev_id, peer_ip, peer_port in peers:
                        ok = self._sync_with_peer(peer_ip, peer_port)
                        any_ok = any_ok or ok
                        if ok:
                            # Re-add to greeted so we don't fire another
                            # instant-sync for this peer unnecessarily
                            self._greeted.add(dev_id)
                    self.last_sync_ok = any_ok
                    self.last_sync_at = time.time()
                    self.last_error   = (
                        '' if any_ok else 'All known peers unreachable this cycle'
                    )
            except Exception as e:
                _log(f"Poll loop error: {e}")
            self._stop_flag.wait(self.interval)

    # -----------------------------------------------------------------------
    # CORE SYNC  (one full exchange with one peer)
    # -----------------------------------------------------------------------

    def _sync_with_peer(self, peer_ip, peer_port):
        import urllib.request
        try:
            my_id      = self.db.get_or_create_device_id()
            my_shop_id = self.db.get_or_create_shop_id()

            # 1. Ping
            ping_url = f"http://{peer_ip}:{peer_port}/aurum-sync/ping"
            with urllib.request.urlopen(ping_url, timeout=CONNECT_TIMEOUT) as resp:
                ping_data = json.loads(resp.read().decode('utf-8'))

            peer_device_id = ping_data.get('device_id', '')
            peer_shop_id   = ping_data.get('shop_id',   '')

            if not peer_device_id:
                return False
            if peer_shop_id != my_shop_id:
                _log(f"Skipping {peer_ip} — shop mismatch at sync time "
                     f"(peer={peer_shop_id} mine={my_shop_id})")
                return False

            # 2. Build outgoing payload
            since_versions  = self.db.get_my_sync_versions(peer_device_id)
            my_outgoing     = self.db.get_changes_since(peer_device_id, {})
            outgoing_changes = my_outgoing.get('changes', {})

            if outgoing_changes:
                _log(f"Sending to {peer_ip}: "
                     + ', '.join(f"{len(v)} {k}" for k, v in outgoing_changes.items()))

            body = json.dumps({
                'device_id':      my_id,
                'shop_id':        my_shop_id,
                'since_versions': since_versions,
                'changes':        outgoing_changes,
            }).encode('utf-8')

            # 3. Exchange
            exchange_url = f"http://{peer_ip}:{peer_port}/aurum-sync/exchange"
            req = urllib.request.Request(
                exchange_url, data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
                reply = json.loads(resp.read().decode('utf-8'))

            # 4. Apply what peer sent back
            incoming = reply.get('changes', {})
            if incoming:
                _log(f"Receiving from {peer_ip}: "
                     + ', '.join(f"{len(v)} {k}" for k, v in incoming.items()))
                self.db.apply_incoming_rows(peer_device_id, incoming)
                self.db.detect_stock_conflicts()

            _log(f"Sync with {peer_ip} OK")
            return True

        except Exception as e:
            _log(f"Could not sync with {peer_ip}: {e}")
            return False