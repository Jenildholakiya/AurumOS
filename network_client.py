# -*- coding: utf-8 -*-
"""
network_client — Printer bridge client (LAN).

Legacy thin client referenced by main.py's network-printing and
bridge-discovery paths (print_tag_network / bridge_discover / bridge_ping).
It is provided as a safe module so the imports in main.py always resolve.
When no bridge server is configured it degrades gracefully to local
behaviour instead of raising ImportError.
"""

import os
import json


def _config_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'config.json')


def _load_config():
    try:
        p = _config_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_config(cfg):
    try:
        p = _config_path()
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def discover_server(timeout=3.0):
    """No legacy bridge server in this build — report none found."""
    return None


def get_bridge_client():
    """Return None when running locally (no bridge configured)."""
    return None


class BridgeClient:
    """Minimal bridge client. Degrades to local when no server is set."""

    def __init__(self, cfg=None):
        self.cfg = cfg or _load_config()
        self._base = self.cfg.get('server_ip', '')

    def ping(self):
        return False

    def print_tag(self, tags):
        # Without a real bridge server the job cannot be routed.
        # Callers (print_tag_network) catch failures and fall back to local.
        return {'status': 'error', 'message': 'No bridge server configured'}
