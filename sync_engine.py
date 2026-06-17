# -*- coding: utf-8 -*-
"""
AurumOS LAN Sync Engine
========================
Keeps two independent local AurumOS databases (one per PC, same shop
LAN) in near-real-time agreement, with NO shared file, NO network
drive, and NO single point of failure -- each PC works perfectly
offline at all times.

How it works
------------
1. Each PC's DBManager has a permanent, random device_id (see
   get_or_create_device_id in db_manager.py) -- separate from the
   hardware security fingerprint used for licensing.
2. This module runs a tiny stdlib-only HTTP server on a fixed local
   port, exposing two endpoints:
     GET  /aurum-sync/ping              -> {"device_id": "..."}
     POST /aurum-sync/exchange          -> pull/push changed rows
3. A background thread polls the OTHER pc's fixed LAN IP every
   SYNC_INTERVAL_SECONDS. If reachable: exchange changes both ways,
   merge them in via DBManager.apply_incoming_rows, then run
   DBManager.detect_stock_conflicts(). If unreachable: stay silent,
   retry next cycle. Never blocks the UI, never raises to the caller.

This file has ZERO third-party dependencies (uses only http.server,
threading, json, socket from the standard library) so it doesn't
change the app's packaging requirements.
"""

import json
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer


SYNC_PORT = 58901  # fixed local port, both PCs use the same one
SYNC_INTERVAL_SECONDS = 12  # within the 10-15s window requested
CONNECT_TIMEOUT = 3  # seconds -- fail fast if the other PC is off/unreachable

import logging as _logging
# Child logger 'aurumos.sync' -- propagates up to the ROOT logger that
# main.py's _init_log() configures with filename=logs\aurumos.log, so
# every line below lands in that same file automatically (no separate
# log file to manage, no extra setup needed here).
_sync_logger = _logging.getLogger('aurumos.sync')


def _log(msg):
    try:
        _sync_logger.info(f"[SYNC] {msg}")
    except Exception:
        pass


class _SyncRequestHandler(BaseHTTPRequestHandler):
    """Handles incoming sync requests FROM the other PC."""

    # Reference to the live DBManager instance, set by SyncEngine.start()
    db_manager = None

    def log_message(self, fmt, *args):
        pass  # silence default request logging -- avoid noisy console spam

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
                self._send_json({'status': 'success', 'device_id': dev_id})
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
            # Peer tells us its "since" cursor for OUR changes, and sends
            # us its own new rows in the same request (single round trip).
            since_versions = payload.get('since_versions', {})
            incoming_changes = payload.get('changes', {})

            # 1) Apply what the peer just sent us
            if peer_device_id and incoming_changes:
                self.db_manager.apply_incoming_rows(peer_device_id, incoming_changes)

            # 2) Reply with whatever WE have that the peer doesn't yet
            my_changes = self.db_manager.get_changes_since(peer_device_id, since_versions)

            # 3) Re-check for stock conflicts now that data merged
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
    Owns the local sync server + the background poller thread.
    Call SyncEngine(db_manager, peer_ip).start() once at app startup.
    Completely silent and non-blocking if the peer is unreachable.
    """

    def __init__(self, db_manager, peer_ip, port=SYNC_PORT,
                 interval_seconds=SYNC_INTERVAL_SECONDS):
        self.db = db_manager
        self.peer_ip = (peer_ip or '').strip()
        self.port = port
        self.interval = interval_seconds
        self._server = None
        self._server_thread = None
        self._poll_thread = None
        self._stop_flag = threading.Event()
        self.last_sync_ok = False
        self.last_sync_at = None
        self.last_error = ''

    # ── SERVER SIDE (accepts incoming sync from the other PC) ──────────
    def _start_server(self):
        try:
            handler_cls = _SyncRequestHandler
            handler_cls.db_manager = self.db
            self._server = HTTPServer(('0.0.0.0', self.port), handler_cls)
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._server_thread.start()
            _log(f"Server listening on 0.0.0.0:{self.port}")
        except OSError as e:
            # Port already in use, etc. -- log and continue; client-side
            # polling still works even if the server side fails to bind.
            _log(f"Could not start sync server: {e}")
        except Exception as e:
            _log(f"Unexpected server start error: {e}")

    # ── CLIENT SIDE (reaches out to the other PC) ───────────────────────
    def _try_sync_once(self):
        if not self.peer_ip:
            return
        import urllib.request

        try:
            my_id = self.db.get_or_create_device_id()

            # Ping first -- cheap, fast-fail if the peer PC is off/unreachable.
            ping_url = f"http://{self.peer_ip}:{self.port}/aurum-sync/ping"
            with urllib.request.urlopen(ping_url, timeout=CONNECT_TIMEOUT) as resp:
                ping_data = json.loads(resp.read().decode('utf-8'))
            peer_device_id = ping_data.get('device_id', '')
            if not peer_device_id:
                return

            # How far have I already pulled FROM this specific peer?
            # (per-table last_version, stored in sync_state on apply)
            since_versions = self.db.get_my_sync_versions(peer_device_id)

            # My own rows (originated on THIS device) -- always sent in
            # full each cycle. Safe because apply_incoming_rows on the
            # receiving side is idempotent (skips rows it already has
            # via device_id+local_id), so resending costs a little
            # bandwidth but never duplicates data.
            my_outgoing = self.db.get_changes_since(peer_device_id, {})

            body = json.dumps({
                'device_id': my_id,
                'since_versions': since_versions,
                'changes': my_outgoing.get('changes', {}),
            }).encode('utf-8')

            exchange_url = f"http://{self.peer_ip}:{self.port}/aurum-sync/exchange"
            req = urllib.request.Request(
                exchange_url, data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
                reply = json.loads(resp.read().decode('utf-8'))

            # Apply what the peer sent back (their changes we don't have yet)
            incoming = reply.get('changes', {})
            if incoming:
                self.db.apply_incoming_rows(peer_device_id, incoming)
                self.db.detect_stock_conflicts()

            self.last_sync_ok = True
            self.last_sync_at = time.time()
            self.last_error = ''

        except Exception as e:
            # Peer offline / unreachable / LAN down -- completely silent,
            # exactly as required: never blocks, never errors to the UI.
            self.last_sync_ok = False
            self.last_error = str(e)

    def _poll_loop(self):
        while not self._stop_flag.is_set():
            try:
                self._try_sync_once()
            except Exception as e:
                _log(f"poll loop error: {e}")
            self._stop_flag.wait(self.interval)

    # ── LIFECYCLE ────────────────────────────────────────────────────
    def start(self):
        self._start_server()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        _log(f"Engine started. Peer={self.peer_ip}:{self.port} interval={self.interval}s")

    def stop(self):
        self._stop_flag.set()
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass

    def status(self):
        return {
            'peer_ip': self.peer_ip,
            'last_sync_ok': self.last_sync_ok,
            'last_sync_at': self.last_sync_at,
            'last_error': self.last_error,
        }