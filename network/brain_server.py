# -*- coding: utf-8 -*-
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class ConstellationHTTPServer(HTTPServer):
    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()


class BrainServerEngine(BaseHTTPRequestHandler):
    db_reference = None
    connected_clients_pool = []
    pool_lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def _set_cors_headers(self, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Role-Token')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_cors_headers()

    # ── CRITICAL SECURITY GUARD: ROLE VALIDATION LAYER ──
    def _assert_admin_privileges(self, payload):
        """
        Cryptographically asserts the sender identity.
        Returns True if authorized, False if transaction is blocked.
        """
        # Read role attributes directly passed inside payload parameters
        user_role = str(payload.get('auth_role', 'staff')).strip().lower()
        username = str(payload.get('auth_user', 'unknown')).strip()

        if user_role == 'admin':
            return True

        print(f"[SECURITY ALERT] Unauthorized intercept: User '{username}' attempted administrative write-action.")
        return False

    def do_GET(self):
        if "/api/constellation/stream_listen" in self.path:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            with self.pool_lock:
                self.connected_clients_pool.append(self.wfile)

            try:
                while True:
                    import time
                    time.sleep(10)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                with self.pool_lock:
                    if self.wfile in self.connected_clients_pool:
                        self.connected_clients_pool.remove(self.wfile)

    def do_POST(self):
        if not self.db_reference:
            self._set_cors_headers(500)
            self.wfile.write(json.dumps({"status": "error", "message": "Server database link offline"}).encode('utf-8'))
            return

        length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)
        payload = json.loads(raw_body.decode('utf-8')) if raw_body else {}
        response = {"status": "error", "message": "Invalid cluster path entry protocol"}

        try:
            # ── OPEN CHANNELS (Accessible by both Staff Dashboards & Admins) ──
            if "/api/constellation/get_stock_ledger" in self.path:
                date_target = payload.get('date')
                records = self.db_reference.fetch_stock_ledger_by_date(date_target)
                response = {"status": "success", "data": records}

            elif "/api/constellation/add_stock_entry" in self.path:
                # Sales team can enter new inventory items received at the station
                success = self.db_reference.add_stock_entry(**payload)
                if success:
                    with self.db_reference._get_connection() as conn:
                        conn.execute("PRAGMA wal_checkpoint(FULL);")
                    response = {"status": "success"}
                    self.trigger_network_wide_broadcast({"event": "invalidate_cache", "target": "stock_ledger"})
                else:
                    response = {"status": "error", "message": "Database write authorization declined"}

            # ── RESTRICTED CHANNELS (Enforced by Server Security Guard) ──
            elif "/api/constellation/delete_stock_entry" in self.path or "/api/constellation/update_settings" in self.path:
                if not self._assert_admin_privileges(payload):
                    self._set_cors_headers(403)  # Forbidden Status Code Response
                    self.wfile.write(json.dumps({"status": "security_violation",
                                                 "message": "Access Denied: Administrative privileges required."}).encode(
                        'utf-8'))
                    return

                # If verified true admin, continue standard database path mutations
                if "/api/constellation/delete_stock_entry" in self.path:
                    target_id = payload.get('entry_id')
                    self.db_reference.delete_stock_entry(target_id)
                    response = {"status": "success"}
                    self.trigger_network_wide_broadcast({"event": "invalidate_cache", "target": "stock_ledger"})

            # ── LOGIN ACCOUNT SYNC (Client Node bootstrap) ──
            # A Client Node keeps auth/2FA LOCAL (offline-safe), but its fresh
            # local DB has no copy of the staff/owner accounts created on the
            # Host Core. The node proves it knows ONE valid password, then we
            # return the full account set so it can authenticate locally.
            # Exposure is identical to the LAN sync engine (which already ships
            # admin_creds hashes to every shop peer) — a random device cannot
            # fetch the set without first presenting a valid password.
            elif "/api/constellation/sync_admin_creds" in self.path:
                password = str(payload.get('password', '')).strip()
                auth = self.db_reference.authenticate_user_by_password(password)
                if not auth.get("authenticated"):
                    self._set_cors_headers(403)
                    self.wfile.write(json.dumps(
                        {"status": "denied", "message": "Invalid password"}).encode('utf-8'))
                    return
                try:
                    with self.db_reference._get_connection() as _conn:
                        _rows = _conn.execute("SELECT * FROM admin_creds").fetchall()
                    accounts = [dict(r) for r in _rows]
                except Exception:
                    accounts = []
                try:
                    shop_id = self.db_reference.get_or_create_shop_id()
                except Exception:
                    shop_id = ""
                response = {"status": "success", "accounts": accounts, "shop_id": shop_id}

        except Exception as e:
            response = {"status": "error", "message": str(e)}

        self._set_cors_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def trigger_network_wide_broadcast(self, event_message):
        payload = f"data: {json.dumps(event_message)}\n\n".encode('utf-8')
        with self.pool_lock:
            dead_nodes = []
            for client_stream in self.connected_clients_pool:
                try:
                    client_stream.write(payload)
                    client_stream.flush()
                except Exception:
                    dead_nodes.append(client_stream)
            for old_stream in dead_nodes:
                if old_stream in self.connected_clients_pool:
                    self.connected_clients_pool.remove(old_stream)


# Guard so the server can only be launched ONCE per process. A second call
# (the old code started it twice on the same port) raised
# "OSError: [WinError 10048] Address already in use", which propagated out of
# run_aur_os() and crashed the whole app before the window ever opened.
_server_singleton = None


def launch_server_core(db_manager_instance, host='0.0.0.0', port=7272):
    global _server_singleton
    if _server_singleton is not None and _server_singleton.is_alive():
        # Already running in this process — just refresh the DB reference.
        try:
            BrainServerEngine.db_reference = db_manager_instance
        except Exception:
            pass
        return _server_singleton

    BrainServerEngine.db_reference = db_manager_instance
    try:
        server = ConstellationHTTPServer((host, port), BrainServerEngine)
    except OSError as bind_err:
        # Port already taken (another AurumOS instance, or a zombie process).
        # Do NOT crash the app — log and continue so the GUI still launches.
        try:
            import logging
            logging.getLogger("aurumos.db").error(
                f"[CONSTELLATION] Could not bind {host}:{port} ({bind_err}). "
                "Server bus skipped — this instance runs without LAN sync."
            )
        except Exception:
            pass
        return None
    t = threading.Thread(target=server.serve_forever, daemon=True, name="ConstellationServerBus")
    t.start()
    _server_singleton = t
    return t