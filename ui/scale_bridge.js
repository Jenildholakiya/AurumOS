/* AurumOS Scale Bridge — ES5 only, works in Edge WebView EXE */
'use strict';

var ScaleBridge = {
    _port: null,
    _baud: 9600,
    _connected: false,
    _weightCb: null,
    _errorCb: null,
    LS_PORT: 'aurum_scale_port',
    LS_BAUD: 'aurum_scale_baud',

    init: function(weightCb, errorCb) {
        this._weightCb = weightCb || null;
        this._errorCb  = errorCb  || null;
        var self = this;
        window.__onScale = function(d) {
            if (!d) return;
            if (d.error) {
                self._connected = false;
                if (self._errorCb) self._errorCb(d.error);
                return;
            }
            if (self._weightCb) self._weightCb(d.weight, !!d.stable);
        };
    },

    loadSaved: function() {
        var port = null, baud = 9600;
        try { port = localStorage.getItem(this.LS_PORT); } catch(e){}
        try { baud = parseInt(localStorage.getItem(this.LS_BAUD)) || 9600; } catch(e){}
        return { port: port, baud: baud };
    },

    _savePort: function(p) { try { localStorage.setItem(this.LS_PORT, p); } catch(e){} },
    _saveBaud: function(b) { try { localStorage.setItem(this.LS_BAUD, String(b)); } catch(e){} },

    scanPorts: function(cb) {
        var saved = this.loadSaved();
        if (!window.pywebview || !window.pywebview.api) { cb([]); return; }
        window.pywebview.api.scale_get_ports().then(function(ports) {
            ports = ports || [];
            if (saved.port) {
                ports.sort(function(a, b) {
                    if (a.port === saved.port) return -1;
                    if (b.port === saved.port) return  1;
                    return 0;
                });
            }
            cb(ports);
        }).catch(function() { cb([]); });
    },

    connect: function(port, baud, onDone) {
        var self = this;
        if (!window.pywebview || !window.pywebview.api) {
            if (onDone) onDone(false, 'App bridge not ready. Please wait.');
            return;
        }
        self._port = port;
        self._baud = parseInt(baud) || 9600;
        window.pywebview.api.scale_connect(self._port, self._baud).then(function(r) {
            if (r && r.status === 'success') {
                self._connected = true;
                self._savePort(self._port);
                self._saveBaud(self._baud);
                if (onDone) onDone(true, null);
            } else {
                self._connected = false;
                var msg = (r && r.message) || 'Connection failed';
                if (onDone) onDone(false, msg);
            }
        }).catch(function(e) {
            self._connected = false;
            if (onDone) onDone(false, String(e));
        });
    },

    disconnect: function() {
        this._connected = false;
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.scale_disconnect().catch(function(){});
        }
    },

    getConnected: function() { return this._connected; },

    autoConnect: function(onDone) {
        var saved = this.loadSaved();
        if (saved.port) {
            this.connect(saved.port, saved.baud, onDone);
        } else {
            if (onDone) onDone(false, 'No saved port');
        }
    }
};