# -*- coding: utf-8 -*-
"""
Bidirectional LAN discovery for the AurumOS Nexus network core.

Two complementary beacons share one UDP port (7273):

  * Master Sun beacon  (role='server') -- the Host Core broadcasts its
    presence AND listens for client "node" beacons on the same socket, so
    the radar can show every client station on the LAN.
  * Node beacon        (role='client') -- a network-mode client station
    announces its presence (pure broadcast, no listen) so the Host Core
    can discover and display it.

Both beacons are shop-agnostic: a client can be discovered even when shop_ids
were never manually synced (the bound server_ip / master gate is the real
boundary). `find_master_server()` is kept for the one-time bonding scan a
client runs during setup.
"""

import socket
import json
import threading
import time
import uuid


def _discovery_log(msg):
    try:
        print(f"[UDP DISCOVERY] {msg}", flush=True)
    except Exception:
        pass


class ConstellationDiscovery:
    def __init__(self, role='client', port=7273, listen_port=7272):
        self.role = str(role).lower().strip()
        self.port = int(port)
        self.listen_port = int(listen_port)
        self._running = False
        self._socket = None
        self._thread = None
        try:
            self._machine_id = str(uuid.getnode())
        except Exception:
            self._machine_id = "NODE-UNKNOWN"
        # Discovered peer registry: id -> peer dict (populated by listeners)
        self._peer_registry = {}
        self._registry_lock = threading.Lock()

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('192.168.1.1', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
        finally:
            s.close()
        return ip

    def _shop_identifiers(self):
        # These values never change while the app is open, yet the beacon loop
        # calls this every ~2.5s. Building a fresh DBManager per broadcast
        # re-opens the DB + re-runs schema/migration checks continuously,
        # which pegs disk/CPU and makes the whole app sluggish. Cache the
        # result after the first successful read (retry only if it failed).
        cached = getattr(self, '_shop_ident_cache', None)
        if cached is not None:
            return cached
        try:
            from database.db_manager import DBManager
            db = DBManager()
            shop_id = db.get_or_create_shop_id()
            biz_name = db.get_config('business_name', 'AurumOS Node')
        except Exception:
            # Don't cache the failure permanently — allow a later retry.
            return "SHOP-UNKNOWN", "AurumOS Core"
        self._shop_ident_cache = (shop_id, biz_name)
        return self._shop_ident_cache

    def _build_payload(self, identity, kind):
        shop_id, biz_name = self._shop_identifiers()
        return {
            "identity": identity,
            "kind": kind,
            "shop_id": shop_id,
            "id": self._machine_id,
            "name": biz_name,
            "ip": self._get_local_ip(),
            "port": self.listen_port,
            "timestamp": int(time.time()),
        }

    def _ingest_beacon(self, data, addr):
        try:
            payload = json.loads(data.decode('utf-8'))
        except Exception:
            return
        identity = payload.get("identity", "")
        if identity not in ("AURUM_OS_MASTER_SUN", "AURUM_OS_NODE"):
            return
        # Ignore beacons we emitted ourselves
        if payload.get("id") == self._machine_id:
            return
        peer_ip = payload.get("ip") or (addr[0] if addr else "")
        if not peer_ip or peer_ip == "127.0.0.1":
            return
        with self._registry_lock:
            self._peer_registry[payload.get("id") or peer_ip] = {
                "id": payload.get("id") or peer_ip,
                "ip": peer_ip,
                "name": payload.get("name", "AurumOS Node"),
                "shop_id": payload.get("shop_id", ""),
                "kind": payload.get("kind", "node"),
                "port": payload.get("port", 0),
                "last_seen": time.time(),
            }

    def _collect_peers(self, sock, window):
        """Drain any inbound beacons for `window` seconds into the registry."""
        sock.settimeout(window)
        cutoff = time.time() + window
        while time.time() < cutoff:
            try:
                data, addr = sock.recvfrom(2048)
                self._ingest_beacon(data, addr)
            except socket.timeout:
                break
            except Exception:
                continue

    # ── SERVER (MASTER SUN) beacon: broadcasts AND listens for nodes ────────

    def start_server_beacon(self):
        if self.role != 'server':
            _discovery_log("Aborted: Server beacon requested on a non-server node.")
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._server_loop, daemon=True, name="UDPBeaconCore")
        self._thread.start()
        _discovery_log(f"Master Beacon initialized on port {self.port}")
        return True

    def _server_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.bind(('', self.port))
        except Exception as e:
            _discovery_log(f"Beacon bind failed: {e}")
        self._socket = s

        while self._running:
            try:
                payload = self._build_payload("AURUM_OS_MASTER_SUN", "master")
                s.sendto(json.dumps(payload).encode('utf-8'), ('255.255.255.255', self.port))
            except Exception as e:
                _discovery_log(f"Broadcast transmitter error: {e}")
            # Brief listen window catches client node beacons arriving on this port
            try:
                self._collect_peers(s, 0.4)
            except Exception:
                pass
            time.sleep(2.1)

        try:
            s.close()
        except Exception:
            pass

    # ── CLIENT node beacon: announces presence (no listen needed) ──────────

    def start_client_beacon(self):
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._client_loop, daemon=True, name="UDPNodeBeacon")
        self._thread.start()
        _discovery_log(f"Node Beacon initialized on port {self.port}")
        return True

    def _client_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._socket = s
        while self._running:
            try:
                payload = self._build_payload("AURUM_OS_NODE", "node")
                s.sendto(json.dumps(payload).encode('utf-8'), ('255.255.255.255', self.port))
            except Exception as e:
                _discovery_log(f"Node broadcast error: {e}")
            time.sleep(2.5)
        try:
            s.close()
        except Exception:
            pass

    # ── Discovered peers (Host Core reads this for the radar) ───────────────

    def get_discovered_peers(self, ttl=10.0):
        """Returns peers heard within `ttl` seconds, excluding self."""
        now = time.time()
        with self._registry_lock:
            for k in [k for k, v in self._peer_registry.items()
                      if now - v.get("last_seen", 0) > ttl]:
                del self._peer_registry[k]
            return [dict(v) for v in self._peer_registry.values()]

    # ── CLIENT finder: locate the Master Sun during bonding/setup ───────────

    def find_master_server(self, timeout=4.0):
        if self.role != 'client':
            return None

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('', self.port))
            s.settimeout(float(timeout))
        except Exception as e:
            _discovery_log(f"Failed to bind UDP listener port: {e}")
            s.close()
            return None

        from database.db_manager import DBManager
        try:
            db = DBManager()
            my_shop_id = db.get_or_create_shop_id()
        except Exception:
            my_shop_id = ""

        local_ip = self._get_local_ip()
        _discovery_log(f"Scanning subnet for matching shop signature: {my_shop_id}...")
        start_time = time.time()

        while (time.time() - start_time) < timeout:
            try:
                data, addr = s.recvfrom(1024)
                payload = json.loads(data.decode('utf-8'))

                if payload.get("identity") == "AURUM_OS_MASTER_SUN":
                    # Drop our own looped broadcast
                    if payload.get("ip") == local_ip:
                        continue
                    _discovery_log(
                        f"✓ Core Master Connected: {payload['name']} found at {payload['ip']}:{payload['port']}")
                    s.close()
                    return {
                        "ip": payload["ip"],
                        "port": payload["port"],
                        "name": payload["name"],
                        "id": payload.get("id") or payload["ip"],
                        "shop_id": payload.get("shop_id")
                    }
            except socket.timeout:
                break
            except Exception:
                continue

        s.close()
        _discovery_log("Scan concluded: No external server detected inside timeout window.")
        return None

    def stop(self):
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        _discovery_log("UDP Beacon shut down cleanly.")
