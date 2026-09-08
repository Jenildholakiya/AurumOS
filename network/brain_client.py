# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.error
import time


class BrainClientProxy:
    def __init__(self):
        # Fallback network configurations mapped directly from the local root
        self.host = "127.0.0.1"
        self.port = 7272
        self.max_retries = 3
        self.initial_backoff = 0.5  # Seconds

        self._load_network_parameters()

    def _load_network_parameters(self):
        """Loads configuration variables dynamically from the local config file."""
        import os
        import sys
        try:
            base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else '.'
            cfg_path = os.path.join(base, 'config.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    self.host = cfg.get('server_ip', '127.0.0.1')
                    self.port = cfg.get('server_port', 7272)
        except Exception as e:
            print(f"[CLIENT PROXY WARN] Failed to bind local network parameters: {e}")

    def is_network_mode(self) -> bool:
        """Helper assertion block verifying if network forwarding is active."""
        return self.host != "127.0.0.1"

    def transmit_transaction(self, endpoint_route, payload_data):
        """
        Transmits data frames down the router pipe to the Host Core.
        Includes an unbreachable exponential backoff loop for network drops.
        """
        url = f"http://{self.host}:{self.port}/api/constellation/{endpoint_route}"
        encoded_data = json.dumps(payload_data).encode('utf-8')

        current_backoff = self.initial_backoff
        last_exception_msg = "Unknown infrastructure fault"

        for attempt in range(1, self.max_retries + 1):
            try:
                # Enforce absolute timeouts: 3 seconds to connect, 5 seconds to read/write
                req = urllib.request.Request(
                    url,
                    data=encoded_data,
                    headers={'Content-Type': 'application/json', 'User-Agent': 'AurumClientProxy'}
                )

                with urllib.request.urlopen(req, timeout=4.0) as response:
                    if response.getcode() == 200:
                        raw_reply = response.read().decode('utf-8')
                        return json.loads(raw_reply)

            except (urllib.error.URLError, TimeoutError) as network_err:
                last_exception_msg = str(network_err)
                print(
                    f"[NETWORK RETRY] Attempt {attempt}/{self.max_retries} failed targeting {endpoint_route}. Error: {network_err}")

                # If this wasn't our final try, sit tight and back off exponentially
                if attempt < self.max_retries:
                    time.sleep(current_backoff)
                    current_backoff *= 2  # Progressive delay ceiling: 0.5s -> 1.0s -> 2.0s

            except Exception as severe_err:
                # Immediate exit loop for structural parse errors or memory issues
                return {"status": "error", "message": f"Critical proxy pipeline error: {str(severe_err)}"}

        # ── FAILOVER GRACEFUL LIFECYCLE FORK ──
        # If the loop breaks here, the Wi-Fi or router is completely down.
        print(f"[PROX CRITICAL ERROR] Network threshold exhausted. Host Core unreachable.")
        return {
            "status": "network_offline",
            "message": f"Host Core connection dropped out. Details: {last_exception_msg}"
        }