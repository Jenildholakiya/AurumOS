# -*- coding: utf-8 -*-
"""
AurumOS LAN Sync Engine -- Multi-PC, Shop-Scoped
==================================================
Keeps ANY NUMBER of independent local AurumOS databases (one per PC,
same shop LAN) in near-real-time agreement. No shared file, no fixed
IP list, no single point of failure -- every PC works perfectly
offline at all times, and discovers its shopmates automatically.

How it works
------------
1. Every shop has ONE shop_id (e.g. "SHOP-7F3A2C"), shared by every PC
   that belongs to that shop (see get_or_create_shop_id / set_shop_id
   in db_manager.py). Different shops always have different shop_ids,
   even if their PCs happen to sit on the same WiFi router.

2. DISCOVERY (UDP broadcast, not a fixed IP):
   Every PC broadcasts a small "I'm here" packet on the LAN every few
   seconds, containing its shop_id, device_id, and which TCP port its
   sync server is listening on. Every PC also LISTENS for these
   broadcasts. A broadcast is only accepted if shop_id matches exactly
   -- a different shop's broadcast is silently ignored, so two shops
   on the same network never see each other's data.

   This works for 2 PCs or 20 PCs with zero config changes -- nobody
   has to type in IP addresses, and adding a new PC to a shop just
   means it starts broadcasting and gets discovered automatically.

3. PEER LIST (with staleness):
   Discovered peers are kept in memory with a "last seen" timestamp.
   If a PC hasn't been heard from in a while (it's turned off, or left
   the network), it quietly drops out of the peer list -- no manual
   cleanup needed, and a returning PC rejoins automatically the next
   time it broadcasts.

4. SYNC (unchanged mechanics, now looped over every live peer):
   Same HTTP exchange protocol as before (ping + exchange), but the
   background loop now iterates over the CURRENT peer list each cycle
   instead of one hardcoded IP. Each peer is tried independently --
   one peer being offline never affects syncing with the others.

Zero third-party dependencies (socket, threading, json, http.server
from the standard library only) -- no packaging changes needed.
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


SYNC_PORT = 58901          # TCP port for the actual data exchange (ping/exchange)
DISCOVERY_PORT = 58902     # UDP port for "I'm here" broadcasts
DISCOVERY_INTERVAL = 4     # seconds between broadcasts
SYNC_INTERVAL_SECONDS = 12 # how often we attempt a sync with each known peer
CONNECT_TIMEOUT = 3        # seconds -- fail fast if a peer is unreachable
PEER_STALE_AFTER = 30      # seconds -- if not heard from in this long, drop it


class _SyncRequestHandler(BaseHTTPRequestHandler):
    """Handles incoming sync requests FROM another PC in the same shop."""

    db_manager = None

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        try:
            body = json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        try:
            if self.path.startswith('/aurum-sync/ping'):
                dev_id = self.db_manager.get_or_create_device_id() if self.db_manager else ''
                shop_id = self.db_manager.get_or_create_shop_id() if self.db_manager else ''
                self._send_json({'status': 'success', 'device_id': dev_id, 'shop_id': shop_id})
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

            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            payload = json.loads(raw.decode('utf-8') or '{}')

            peer_device_id = payload.get('device_id', '')
            peer_shop_id = payload.get('shop_id', '')
            my_shop_id = self.db_manager.get_or_create_shop_id()

            if peer_shop_id != my_shop_id:
                _log(f"REJECTED exchange from different shop_id={peer_shop_id} (mine={my_shop_id})")
                self._send_json({'status': 'error', 'message': 'shop_id mismatch'}, 403)
                return

            client_ip = self.client_address[0] if self.client_address else '?'
            _log(f"Incoming sync request from {client_ip} (device {peer_device_id})")

            since_versions = payload.get('since_versions', {})
            incoming_changes = payload.get('changes', {})

            if peer_device_id and incoming_changes:
                recv_summary = ', '.join(f"{len(v)} {k}" for k, v in incoming_changes.items())
                _log(f"Applying from {client_ip}: {recv_summary}")
                self.db_manager.apply_incoming_rows(peer_device_id, incoming_changes)
            else:
                _log(f"Nothing to apply from {client_ip} this request")

            my_changes = self.db_manager.get_changes_since(peer_device_id, since_versions)
            reply_changes = my_changes.get('changes', {})
            if reply_changes:
                send_summary = ', '.join(f"{len(v)} {k}" for k, v in reply_changes.items())
                _log(f"Replying to {client_ip} with: {send_summary}")
            else:
                _log(f"Nothing new to send back to {client_ip}")

            try:
                self.db_manager.detect_stock_conflicts()
            except Exception:
                pass

            self._send_json(my_changes)
        except Exception as e:
            _log(f"do_POST error: {e}\n{traceback.format_exc()}")
            self._send_json({'status': 'error', 'message': str(e)}, 500)


class SyncEngine:
    """
    Owns four background pieces:
      1. The sync HTTP server (accepts incoming exchange requests)
      2. The UDP broadcaster (announces "I'm here" to the LAN)
      3. The UDP listener (discovers other PCs in the same shop)
      4. The sync poller (syncs with every currently-known peer)

    Call SyncEngine(db_manager).start() once at app startup. Works for
    any number of PCs in the shop -- 2, 5, or 20 -- with zero per-PC
    configuration beyond sharing the same shop_id.
    """

    def __init__(self, db_manager, port=SYNC_PORT, discovery_port=DISCOVERY_PORT,
                 interval_seconds=SYNC_INTERVAL_SECONDS):
        self.db = db_manager
        self.port = port
        self.discovery_port = discovery_port
        self.interval = interval_seconds

        self._server = None
        self._server_thread = None
        self._broadcast_thread = None
        self._listen_thread = None
        self._poll_thread = None
        self._stop_flag = threading.Event()

        self._peers = {}
        self._peers_lock = threading.Lock()

        self.last_sync_ok = False
        self.last_sync_at = None
        self.last_error = ''

    def _start_server(self):
        try:
            handler_cls = _SyncRequestHandler
            handler_cls.db_manager = self.db
            self._server = HTTPServer(('0.0.0.0', self.port), handler_cls)
            self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._server_thread.start()
            _log(f"Server listening on 0.0.0.0:{self.port}")
        except OSError as e:
            _log(f"Could not start sync server: {e}")
        except Exception as e:
            _log(f"Unexpected server start error: {e}")

    def _broadcast_loop(self):
        shop_id = self.db.get_or_create_shop_id()
        device_id = self.db.get_or_create_device_id()
        msg = json.dumps({
            'aurumos_sync': True,
            'shop_id': shop_id,
            'device_id': device_id,
            'port': self.port,
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

    def _listen_loop(self):
        my_shop_id = self.db.get_or_create_shop_id()
        my_device_id = self.db.get_or_create_device_id()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.discovery_port))
        except OSError as e:
            _log(f"Could not bind discovery listener: {e} -- discovery disabled")
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
                peer_shop_id = info.get('shop_id', '')
                peer_device_id = info.get('device_id', '')

                if peer_device_id == my_device_id:
                    continue
                if peer_shop_id != my_shop_id:
                    continue

                with self._peers_lock:
                    is_new = peer_device_id not in self._peers
                    self._peers[peer_device_id] = {
                        'ip': peer_ip,
                        'port': info.get('port', self.port),
                        'last_seen': time.time(),
                    }
                if is_new:
                    _log(f"Discovered new shopmate PC at {peer_ip}:{info.get('port', self.port)} (device {peer_device_id})")

            except socket.timeout:
                continue
            except Exception as e:
                _log(f"Discovery listen error: {e}")

        try:
            sock.close()
        except Exception:
            pass

    def _get_live_peers(self):
        """Returns current (ip, port) pairs (one per known device_id),
        dropping any device not heard from recently."""
        now = time.time()
        with self._peers_lock:
            stale = [dev_id for dev_id, info in self._peers.items()
                     if now - info['last_seen'] > PEER_STALE_AFTER]
            for dev_id in stale:
                _log(f"Peer device {dev_id} went stale (not seen in {PEER_STALE_AFTER}s) -- removing")
                del self._peers[dev_id]
            return [(info['ip'], info.get('port', self.port)) for info in self._peers.values()]

    def _sync_with_peer(self, peer_ip, peer_port):
        import urllib.request
        try:
            my_id = self.db.get_or_create_device_id()
            my_shop_id = self.db.get_or_create_shop_id()

            ping_url = f"http://{peer_ip}:{peer_port}/aurum-sync/ping"
            with urllib.request.urlopen(ping_url, timeout=CONNECT_TIMEOUT) as resp:
                ping_data = json.loads(resp.read().decode('utf-8'))
            peer_device_id = ping_data.get('device_id', '')
            peer_shop_id = ping_data.get('shop_id', '')

            if not peer_device_id:
                return False
            if peer_shop_id != my_shop_id:
                _log(f"Skipping {peer_ip} -- shop_id mismatch at sync time")
                return False

            _log(f"Reached peer {peer_ip} (device {peer_device_id}) -- starting exchange")

            since_versions = self.db.get_my_sync_versions(peer_device_id)
            my_outgoing = self.db.get_changes_since(peer_device_id, {})
            outgoing_changes = my_outgoing.get('changes', {})

            if outgoing_changes:
                sent_summary = ', '.join(f"{len(v)} {k}" for k, v in outgoing_changes.items())
                _log(f"Sending to {peer_ip}: {sent_summary}")
            else:
                _log(f"Nothing new to send to {peer_ip} this cycle")

            body = json.dumps({
                'device_id': my_id,
                'shop_id': my_shop_id,
                'since_versions': since_versions,
                'changes': outgoing_changes,
            }).encode('utf-8')

            exchange_url = f"http://{peer_ip}:{peer_port}/aurum-sync/exchange"
            req = urllib.request.Request(
                exchange_url, data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
                reply = json.loads(resp.read().decode('utf-8'))

            incoming = reply.get('changes', {})
            if incoming:
                recv_summary = ', '.join(f"{len(v)} {k}" for k, v in incoming.items())
                _log(f"Receiving from {peer_ip}: {recv_summary}")
                self.db.apply_incoming_rows(peer_device_id, incoming)
                self.db.detect_stock_conflicts()
            else:
                _log(f"Nothing new received from {peer_ip} this cycle")

            _log(f"Sync cycle with {peer_ip} completed successfully")
            return True

        except Exception as e:
            _log(f"Could not sync with {peer_ip}: {e}")
            return False

    def _poll_loop(self):
        while not self._stop_flag.is_set():
            try:
                peers = self._get_live_peers()
                if not peers:
                    self.last_sync_ok = False
                    self.last_error = 'No shopmate PCs discovered yet'
                else:
                    any_ok = False
                    for peer_ip, peer_port in peers:
                        ok = self._sync_with_peer(peer_ip, peer_port)
                        any_ok = any_ok or ok
                    self.last_sync_ok = any_ok
                    self.last_sync_at = time.time()
                    self.last_error = '' if any_ok else 'All known peers unreachable this cycle'
            except Exception as e:
                _log(f"poll loop error: {e}")
            self._stop_flag.wait(self.interval)

    def start(self):
        shop_id = self.db.get_or_create_shop_id()
        device_id = self.db.get_or_create_device_id()
        _log(f"Starting. shop_id={shop_id} device_id={device_id}")

        self._start_server()

        self._broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._broadcast_thread.start()

        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        _log(f"Engine fully started -- broadcasting + listening on shop {shop_id}")

    def stop(self):
        self._stop_flag.set()
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass

    def status(self):
        with self._peers_lock:
            peer_count = len(self._peers)
            peer_ips = [f"{info['ip']}:{info.get('port', self.port)}" for info in self._peers.values()]
        return {
            'shop_id': self.db.get_or_create_shop_id(),
            'device_id': self.db.get_or_create_device_id(),
            'peers_discovered': peer_count,
            'peer_ips': peer_ips,
            'last_sync_ok': self.last_sync_ok,
            'last_sync_at': self.last_sync_at,
            'last_error': self.last_error,
        }