"""
AurumOS SSE Listener — Real-Time License & Plan Synchronization
Receives events from: GET /api/nexus/stream?key={LICENSE_KEY}
Handles: plan_change, status_change, connected, heartbeat
Runs in daemon thread — never blocks UI, auto-reconnects forever.
"""
import json
import time
import threading
import urllib.request
import urllib.error
import ssl
import sys
import os

_LOG = lambda m: print(f'[SSE-PY] {m}', flush=True)
_ERR = lambda m: print(f'[SSE-PY ERR] {m}', flush=True)


class LicenseEventListener:
    """Python-based SSE listener for real-time license events."""

    def __init__(self, license_key, server_url,
                 on_plan_change=None, on_status_change=None, on_connected=None,
                 on_urgent_message=None):
        self.license_key = license_key
        self.server_url = server_url.rstrip('/')
        self.on_plan_change = on_plan_change
        self.on_status_change = on_status_change
        self.on_connected = on_connected
        self.on_urgent_message = on_urgent_message
        self.running = False
        self.thread = None
        self._lock = threading.Lock()
        self._reconnect_delay = 5
        self._max_reconnect_delay = 30
        self._last_event_id = None
        self.last_activity = 0.0  # epoch of last byte/event (for poll fallback)

    def seconds_since_activity(self):
        if not self.last_activity:
            return 1e9
        return time.time() - self.last_activity

    def start(self):
        """Start listening in background daemon thread."""
        with self._lock:
            if self.running:
                _LOG('Already running')
                return
            self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        _LOG(f'Started listener for key={self.license_key[:10]}...')

    def stop(self):
        """Stop listening."""
        with self._lock:
            self.running = False
        _LOG('Stopped')

    def is_running(self):
        return self.running

    def _listen_loop(self):
        """Main loop with auto-reconnect — never exits while running=True."""
        url = f'{self.server_url}/api/nexus/stream?key={self.license_key}'

        while self.running:
            try:
                self._connect_and_listen(url)
            except Exception as e:
                _ERR(f'Connection error: {e}')

            if self.running:
                _LOG(f'Reconnecting in {self._reconnect_delay}s...')
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 1.5, self._max_reconnect_delay
                )

    def _connect_and_listen(self, url):
        """Open SSE connection and process events line by line."""
        # Build request with optional Last-Event-ID header
        headers = {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'User-Agent': 'AurumOS/Client-PY'
        }
        if self._last_event_id:
            headers['Last-Event-ID'] = str(self._last_event_id)

        req = urllib.request.Request(url, headers=headers)

        # Create SSL context that works on all platforms
        ctx = ssl.create_default_context()

        try:
            resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        except urllib.error.URLError as e:
            _ERR(f'HTTP error: {e}')
            return
        except Exception as e:
            _ERR(f'Connect failed: {e}')
            return

        _LOG('Connected to server')
        self._reconnect_delay = 5  # Reset backoff on success
        self.last_activity = time.time()

        if self.on_connected:
            try:
                self.on_connected()
            except Exception as e:
                _ERR(f'on_connected callback error: {e}')

        # Parse SSE stream — line by line
        current_event = ''
        current_data = ''
        current_id = None

        try:
            for raw_line in resp:
                if not self.running:
                    break

                line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')

                # Any byte = stream alive (drives the poll-fallback watchdog)
                self.last_activity = time.time()

                # Empty line = dispatch event
                if line == '':
                    if current_data:
                        self._dispatch_event(current_event, current_data, current_id)
                    current_event = ''
                    current_data = ''
                    current_id = None
                    continue

                # Comment line (heartbeat)
                if line.startswith(':'):
                    continue

                # Parse SSE field
                if line.startswith('event:'):
                    current_event = line[6:].strip()
                elif line.startswith('data:'):
                    data_part = line[5:].strip()
                    if current_data:
                        current_data += '\n' + data_part
                    else:
                        current_data = data_part
                elif line.startswith('id:'):
                    try:
                        current_id = int(line[3:].strip())
                        self._last_event_id = current_id
                    except ValueError:
                        pass
                elif line.startswith('retry:'):
                    try:
                        ms = int(line[6:].strip())
                        self._reconnect_delay = max(5, ms // 1000)
                    except ValueError:
                        pass

        except (urllib.error.URLError, ConnectionError, OSError) as e:
            _ERR(f'Stream interrupted: {e}')
        except Exception as e:
            _ERR(f'Stream error: {e}')
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _dispatch_event(self, event_type, data_str, event_id):
        """Dispatch a parsed SSE event to the appropriate handler."""
        _LOG(f'Event: {event_type or "message"} data={data_str[:200]}')

        data = None
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            _ERR(f'Invalid JSON: {data_str[:100]}')
            return

        if event_type == 'connected':
            _LOG(f'Server connected: {data}')
            if self.on_connected:
                try:
                    self.on_connected(data)
                except Exception as e:
                    _ERR(f'on_connected error: {e}')

        elif event_type == 'plan_change':
            _LOG(f'PLAN CHANGE: plan={data.get("plan")} features={len(data.get("features", []))}')
            # Save to local cache
            self._save_cache('plan_change', data)
            # Apply features
            if self.on_plan_change:
                try:
                    self.on_plan_change(data)
                except Exception as e:
                    _ERR(f'on_plan_change error: {e}')

        elif event_type == 'status_change':
            status = data.get('status', 'unknown')
            _LOG(f'STATUS CHANGE: {status}')
            # Save to local cache
            self._save_cache('status_change', data)
            # Handle revoke/expire
            if self.on_status_change:
                try:
                    self.on_status_change(data)
                except Exception as e:
                    _ERR(f'on_status_change error: {e}')

        elif event_type == 'urgent_message':
            _LOG(f'URGENT MESSAGE id={data.get("id")} priority={data.get("priority")}')
            if self.on_urgent_message:
                try:
                    self.on_urgent_message(data)
                except Exception as e:
                    _ERR(f'on_urgent_message error: {e}')

        else:
            _LOG(f'Unknown event type: {event_type}')

    def _save_cache(self, event_type, data):
        """Save event to local cache for offline resilience."""
        try:
            base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
            cache_path = os.path.join(base, 'database', '.sse_cache')
            cache = {}
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r') as f:
                        cache = json.load(f)
                except Exception:
                    pass
            cache[event_type] = {
                'data': data,
                'timestamp': time.time()
            }
            with open(cache_path, 'w') as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            _ERR(f'Cache save error: {e}')

    def load_cached_event(self, event_type):
        """Load a cached event (for offline use)."""
        try:
            base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath('.')
            cache_path = os.path.join(base, 'database', '.sse_cache')
            if os.path.exists(cache_path):
                with open(cache_path, 'r') as f:
                    cache = json.load(f)
                entry = cache.get(event_type)
                if entry:
                    age = time.time() - entry.get('timestamp', 0)
                    if age < 3600:  # 1 hour max
                        return entry.get('data')
        except Exception:
            pass
        return None
