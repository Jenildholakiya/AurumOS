/*
 * AurumOS Scale Widget  —  scale_widget.js
 * ─────────────────────────────────────────────────────────────────
 * Include in any page:  <script src="scale_widget.js"></script>
 *
 * Provides:
 *   ScaleWidget.init()           — call once on page load
 *   ScaleWidget.getWeight()      — returns current weight (number)
 *   ScaleWidget.isConnected()    — returns boolean
 *   ScaleWidget.openPopup()      — show scale management popup
 *   ScaleWidget.onWeight(cb)     — register weight callback
 *
 * Injects:
 *   #sw-btn   — compact status button (embed anywhere)
 *   #sw-popup — full scale popup (singleton, appended to body)
 *   #sw-mini  — mini weight display for modal embedding
 *
 * Usage in modal:
 *   ScaleWidget.renderMini('container-id')  — renders mini widget in element
 *   ScaleWidget.applyToField('field-id')    — auto-fills field on stable weight
 */

var ScaleWidget = (function() {
    'use strict';

    // ── STATE ─────────────────────────────────────────────────────
    var _connected   = false;
    var _port        = '';
    var _baud        = 1200;
    var _lastWeight  = null;
    var _stable      = false;
    var _callbacks   = [];
    var _miniTargets = {};    // { containerId: fieldId }
    var _inited      = false;

    // ── CSS (injected once) ───────────────────────────────────────
    var CSS = [
        /* Scale button in topbar */
        '.sw-btn{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 12px;',
        'border-radius:6px;border:1px solid var(--r,rgba(14,12,9,.08));',
        'background:transparent;cursor:pointer;font-size:.68rem;font-weight:600;',
        'color:var(--ink3,#5c5a52);transition:all .15s;font-family:var(--sans,"DM Sans",sans-serif);}',
        '.sw-btn:hover{background:var(--c2,#f2efe7);}',
        '.sw-btn.connected{border-color:rgba(21,128,61,.3);background:rgba(21,128,61,.07);color:#15803d;}',
        '.sw-btn.connecting{opacity:.7;pointer-events:none;}',
        '.sw-dot{width:7px;height:7px;border-radius:50%;background:#ccc;flex-shrink:0;transition:background .3s;}',
        '.sw-btn.connected .sw-dot{background:#15803d;animation:sw-pulse 2s infinite;}',
        '.sw-btn.connecting .sw-dot{background:var(--am,#b45309);animation:sw-pulse .6s infinite;}',
        '@keyframes sw-pulse{0%,100%{opacity:1}50%{opacity:.35}}',

        /* Popup overlay */
        '.sw-overlay{display:none;position:fixed;inset:0;background:rgba(14,12,9,.5);',
        'z-index:9990;align-items:center;justify-content:center;}',
        '.sw-overlay.open{display:flex;}',
        '.sw-popup{background:#fff;border-radius:14px;width:460px;max-width:96vw;',
        'box-shadow:0 12px 48px rgba(14,12,9,.2);overflow:hidden;font-family:var(--sans,"DM Sans",sans-serif);}',

        /* Popup header */
        '.sw-ph{display:flex;align-items:center;justify-content:space-between;',
        'padding:16px 20px;border-bottom:1px solid rgba(14,12,9,.08);background:#faf8f3;}',
        '.sw-ph-l{display:flex;align-items:center;gap:10px;}',
        '.sw-logo{width:32px;height:32px;background:#0e0c09;border-radius:7px;display:flex;',
        'align-items:center;justify-content:center;font-size:.8rem;}',
        '.sw-ph-title{font-family:var(--serif,"DM Serif Display",Georgia,serif);font-size:1rem;color:#2c2a24;}',
        '.sw-ph-sub{font-size:.68rem;color:#7a7268;margin-top:2px;}',
        '.sw-close{width:28px;height:28px;border-radius:6px;border:1px solid rgba(14,12,9,.08);',
        'background:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;',
        'color:#7a7268;font-size:.82rem;transition:all .13s;}',
        '.sw-close:hover{background:#fef2f2;color:#b91c1c;}',

        /* Status ring */
        '.sw-status{padding:18px 20px;border-bottom:1px solid rgba(14,12,9,.08);',
        'display:flex;align-items:center;gap:14px;}',
        '.sw-ring{width:48px;height:48px;border-radius:50%;border:3px solid #e5e5e5;',
        'display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all .3s;}',
        '.sw-ring.ok{border-color:#15803d;background:rgba(21,128,61,.07);}',
        '.sw-ring.err{border-color:#b91c1c;background:rgba(185,28,28,.07);}',
        '.sw-ring.busy{border-color:#b45309;background:rgba(180,83,9,.07);',
        'animation:sw-spin .8s linear infinite;}',
        '@keyframes sw-spin{to{transform:rotate(360deg)}}',
        '.sw-st-title{font-size:.82rem;font-weight:700;color:#2c2a24;}',
        '.sw-st-sub{font-size:.7rem;color:#7a7268;margin-top:3px;}',

        /* Live weight display */
        '.sw-wt-disp{margin:0 20px 16px;background:#faf8f3;border:1px solid rgba(14,12,9,.08);',
        'border-radius:10px;padding:14px 18px;display:flex;align-items:center;',
        'justify-content:space-between;}',
        '.sw-wt-val{font-family:var(--mono,"DM Mono",monospace);font-size:2rem;',
        'font-weight:800;color:#0e0c09;letter-spacing:-1px;transition:color .3s;}',
        '.sw-wt-val.stable{color:#15803d;}',
        '.sw-wt-unit{font-size:.76rem;color:#7a7268;margin-left:4px;}',
        '.sw-wt-badge{font-size:.6rem;font-weight:700;padding:3px 9px;border-radius:20px;',
        'background:#f0f0f0;color:#aaa;border:1px solid #e5e5e5;}',
        '.sw-wt-badge.stable{background:rgba(21,128,61,.07);color:#15803d;',
        'border-color:rgba(21,128,61,.22);}',

        /* Port selector */
        '.sw-cfg{padding:0 20px 16px;display:flex;flex-direction:column;gap:10px;}',
        '.sw-row{display:flex;gap:8px;align-items:center;}',
        '.sw-lbl{font-size:.6rem;font-weight:600;text-transform:uppercase;',
        'letter-spacing:1px;color:#7a7268;margin-bottom:5px;}',
        '.sw-sel,.sw-inp{height:36px;background:#faf8f3;border:1px solid rgba(14,12,9,.08);',
        'border-radius:7px;font-size:.8rem;padding:0 11px;color:#0e0c09;outline:none;',
        'font-family:var(--mono,"DM Mono",monospace);}',
        '.sw-sel{cursor:pointer;}',
        '.sw-port-sel{flex:1;}',
        '.sw-baud-sel{width:120px;}',
        '.sw-scan-btn{height:36px;padding:0 13px;border:1px solid rgba(14,12,9,.08);',
        'border-radius:7px;background:#faf8f3;font-size:.72rem;cursor:pointer;white-space:nowrap;',
        'transition:background .15s;}',
        '.sw-scan-btn:hover{background:#f2efe7;}',

        /* Connect button */
        '.sw-conn-btn{width:100%;height:40px;border:none;border-radius:9px;font-size:.82rem;',
        'font-weight:700;cursor:pointer;transition:all .2s;display:flex;align-items:center;',
        'justify-content:center;gap:8px;margin:0 20px 16px;width:calc(100% - 40px);}',
        '.sw-conn-btn.connect{background:#0e0c09;color:#fff;}',
        '.sw-conn-btn.connect:hover{background:#a87d1e;}',
        '.sw-conn-btn.disconnect{background:rgba(185,28,28,.07);color:#b91c1c;',
        'border:1px solid rgba(185,28,28,.2);}',
        '.sw-conn-btn.disconnect:hover{background:#b91c1c;color:#fff;}',
        '.sw-conn-btn:disabled{opacity:.5;pointer-events:none;}',

        /* Mini widget (for modals) */
        '.sw-mini{display:inline-flex;align-items:center;gap:8px;',
        'background:#faf8f3;border:1px solid rgba(14,12,9,.08);border-radius:8px;',
        'padding:7px 12px;cursor:pointer;transition:all .18s;}',
        '.sw-mini:hover{border-color:rgba(168,125,30,.22);background:rgba(168,125,30,.06);}',
        '.sw-mini.has-weight{border-color:rgba(21,128,61,.25);background:rgba(21,128,61,.06);}',
        '.sw-mini-dot{width:7px;height:7px;border-radius:50%;background:#ccc;flex-shrink:0;}',
        '.sw-mini.connected .sw-mini-dot{background:#15803d;animation:sw-pulse 2s infinite;}',
        '.sw-mini-val{font-family:var(--mono,"DM Mono",monospace);font-size:.88rem;',
        'font-weight:800;color:#0e0c09;}',
        '.sw-mini.has-weight .sw-mini-val{color:#15803d;}',
        '.sw-mini-lbl{font-size:.6rem;color:#7a7268;}',
        '.sw-mini-arrow{font-size:.7rem;color:#7a7268;margin-left:2px;}',
    ].join('');

    function _injectCSS() {
        if (document.getElementById('sw-css')) return;
        var s = document.createElement('style');
        s.id  = 'sw-css';
        s.textContent = CSS;
        document.head.appendChild(s);
    }

    // ── POPUP HTML ────────────────────────────────────────────────
    function _buildPopup() {
        if (document.getElementById('sw-popup')) return;
        var div = document.createElement('div');
        div.className = 'sw-overlay';
        div.id        = 'sw-popup';
        div.innerHTML =
            '<div class="sw-popup">'
            // Header
            + '<div class="sw-ph">'
            + '<div class="sw-ph-l">'
            + '<div class="sw-logo">&#9878;</div>'
            + '<div><div class="sw-ph-title">Scale Connection</div>'
            + '<div class="sw-ph-sub" id="sw-ph-sub">Connect once &mdash; stays connected</div></div>'
            + '</div>'
            + '<div class="sw-close" onclick="ScaleWidget.closePopup()">&#x2715;</div>'
            + '</div>'
            // Status
            + '<div class="sw-status">'
            + '<div class="sw-ring" id="sw-ring"><span id="sw-ring-ico">&#9675;</span></div>'
            + '<div><div class="sw-st-title" id="sw-st-title">Not Connected</div>'
            + '<div class="sw-st-sub" id="sw-st-sub">Select a COM port and connect</div></div>'
            + '</div>'
            // Live weight
            + '<div class="sw-wt-disp">'
            + '<div><div style="font-size:.6rem;letter-spacing:2px;text-transform:uppercase;color:#7a7268;margin-bottom:4px;">Live Weight</div>'
            + '<div style="display:flex;align-items:baseline;gap:4px;">'
            + '<div class="sw-wt-val" id="sw-wt-val">— &nbsp;</div>'
            + '<span class="sw-wt-unit">g</span>'
            + '</div></div>'
            + '<span class="sw-wt-badge" id="sw-wt-badge">WAITING</span>'
            + '</div>'
            // Config
            + '<div class="sw-cfg">'
            + '<div>'
            + '<div class="sw-lbl">COM Port</div>'
            + '<div class="sw-row">'
            + '<select class="sw-sel sw-port-sel" id="sw-port-sel"><option value="">Select port...</option></select>'
            + '<button class="sw-scan-btn" onclick="ScaleWidget._scanPorts()">&#8635; Scan</button>'
            + '</div>'
            + '</div>'
            + '<div>'
            + '<div class="sw-lbl">Baud Rate (auto-detected)</div>'
            + '<select class="sw-sel sw-baud-sel" id="sw-baud-sel">'
            + '<option value="1200">1200</option>'
            + '<option value="2400">2400</option>'
            + '<option value="4800">4800</option>'
            + '<option value="9600" selected>9600</option>'
            + '<option value="19200">19200</option>'
            + '</select>'
            + '</div>'
            + '</div>'
            // Connect button
            + '<button class="sw-conn-btn connect" id="sw-conn-btn" onclick="ScaleWidget._toggleConnect()">&#9654; Connect Scale</button>'
            + '</div>';
        document.body.appendChild(div);
        div.addEventListener('click', function(e) { if (e.target === div) _self.closePopup(); });
    }

    // ── STATUS UPDATE ─────────────────────────────────────────────
    function _updateStatus() {
        var ring  = document.getElementById('sw-ring');
        var ico   = document.getElementById('sw-ring-ico');
        var title = document.getElementById('sw-st-title');
        var sub   = document.getElementById('sw-st-sub');
        var btn   = document.getElementById('sw-conn-btn');
        var phsub = document.getElementById('sw-ph-sub');

        if (!ring) return;

        if (_connected) {
            ring.className  = 'sw-ring ok';
            ico.innerHTML   = '&#10003;';
            title.innerText = 'Connected — ' + _port;
            sub.innerText   = 'Scale is live. Baud: ' + _baud + '. Stays connected across pages.';
            if (btn) { btn.className = 'sw-conn-btn disconnect'; btn.innerHTML = '&#9632; Disconnect'; }
            if (phsub) phsub.innerText = 'Live \u2014 ' + _port + ' @ ' + _baud;
        } else {
            ring.className  = 'sw-ring';
            ico.innerHTML   = '&#9675;';
            title.innerText = 'Not Connected';
            sub.innerText   = 'Select a COM port and click Connect';
            if (btn) { btn.className = 'sw-conn-btn connect'; btn.innerHTML = '&#9654; Connect Scale'; }
            if (phsub) phsub.innerText = 'Connect once \u2014 stays connected permanently';
        }

        // Update topbar button
        var tbBtn = document.getElementById('sw-tb-btn');
        if (tbBtn) {
            tbBtn.className = 'sw-btn' + (_connected ? ' connected' : '');
            tbBtn.innerHTML = '<span class="sw-dot"></span>'
                + (_connected ? ('Scale \u2014 ' + _port) : 'Scale');
        }

        // Update all mini widgets
        var minis = document.querySelectorAll('.sw-mini');
        for (var i = 0; i < minis.length; i++) {
            minis[i].className = 'sw-mini' + (_connected ? ' connected' : '');
        }
    }

    function _updateWeight(wt, stable) {
        _lastWeight = wt;
        _stable     = stable;

        var val   = document.getElementById('sw-wt-val');
        var badge = document.getElementById('sw-wt-badge');
        if (val) {
            val.innerText  = wt ? wt.toFixed(3) : '\u2014\u00a0';
            val.className  = 'sw-wt-val' + (stable && wt ? ' stable' : '');
        }
        if (badge) {
            badge.innerText  = stable ? 'STABLE' : 'UNSTABLE';
            badge.className  = 'sw-wt-badge' + (stable && wt ? ' stable' : '');
        }

        // Update all mini widgets
        var minis = document.querySelectorAll('.sw-mini');
        for (var i = 0; i < minis.length; i++) {
            var dot = minis[i].querySelector('.sw-mini-dot');
            var val2 = minis[i].querySelector('.sw-mini-val');
            if (dot) dot.className = 'sw-mini-dot' + (stable ? ' stable' : '');
            if (val2) val2.innerText = wt ? wt.toFixed(3) + 'g' : '\u2014';
            minis[i].className = 'sw-mini' + (_connected ? ' connected' : '') + (stable && wt ? ' has-weight' : '');
        }

        // Auto-fill registered fields
        if (stable && wt) {
            var keys = Object.keys(_miniTargets);
            for (var j = 0; j < keys.length; j++) {
                var fieldId = _miniTargets[keys[j]];
                var el = document.getElementById(fieldId);
                if (el && document.activeElement !== el) {
                    el.value = wt.toFixed(3);
                    // Trigger oninput for any auto-calc
                    if (el.oninput) el.oninput();
                    if (el.dispatchEvent) {
                        try { el.dispatchEvent(new Event('input')); } catch(e) {}
                    }
                }
            }
        }

        // Fire callbacks
        for (var k = 0; k < _callbacks.length; k++) {
            try { _callbacks[k](wt, stable); } catch(e) {}
        }
    }

    // ── GLOBAL SCALE RECEIVER ─────────────────────────────────────
    // pywebview broadcasts to window.__onScale on every weight reading
    window.__onScale = function(data) {
        if (!data) return;
        if (data.error) {
            _connected = false;
            _updateStatus();
            return;
        }
        var wt     = parseFloat(data.weight) || 0;
        var stable = data.stable === true || data.stable === 'true';
        _connected = true;
        _updateWeight(wt, stable);
    };

    window.__onScaleConnected = function(data) {
        if (!data) return;
        _connected = true;
        _port      = data.port || '';
        _baud      = data.baud || 1200;
        _updateStatus();
    };

    // ── PUBLIC API ────────────────────────────────────────────────
    var _self = {

        init: function() {
            if (_inited) return;
            _inited = true;
            _injectCSS();
            _buildPopup();

            // Check if scale already connected (from auto-connect on startup)
            if (window.pywebview && window.pywebview.api) {
                window.pywebview.api.scale_is_connected().then(function(r) {
                    if (r && r.connected) {
                        _connected = true;
                        _port      = r.port || '';
                        _baud      = r.baud || 1200;
                        _updateStatus();
                    }
                    _self._scanPorts();
                    _self._setPortSelFromSaved();
                }).catch(function() {
                    _self._scanPorts();
                });
            }
        },

        _scanPorts: function() {
            if (!window.pywebview || !window.pywebview.api) return;
            window.pywebview.api.scale_get_ports().then(function(ports) {
                var sel = document.getElementById('sw-port-sel');
                if (!sel) return;
                var saved = sel.value;
                sel.innerHTML = '<option value="">Select port...</option>';
                for (var i = 0; i < ports.length; i++) {
                    var opt = document.createElement('option');
                    opt.value       = ports[i].port;
                    opt.textContent = ports[i].port + ' \u2014 ' + (ports[i].desc || '').slice(0, 30);
                    sel.appendChild(opt);
                }
                if (saved) sel.value = saved;
            }).catch(function() {});
        },

        _setPortSelFromSaved: function() {
            if (!window.pywebview || !window.pywebview.api) return;
            window.pywebview.api.scale_get_saved_config().then(function(cfg) {
                var sel  = document.getElementById('sw-port-sel');
                var bsel = document.getElementById('sw-baud-sel');
                if (sel && cfg.port)  sel.value  = cfg.port;
                if (bsel && cfg.baud) bsel.value = String(cfg.baud);
                _port = cfg.port || '';
                _baud = cfg.baud || 1200;
            }).catch(function() {});
        },

        _toggleConnect: function() {
            if (_connected) {
                _self.disconnect();
            } else {
                _self.connect();
            }
        },

        connect: function() {
            var sel  = document.getElementById('sw-port-sel');
            var bsel = document.getElementById('sw-baud-sel');
            var btn  = document.getElementById('sw-conn-btn');
            var port = sel ? sel.value : _port;
            var baud = bsel ? parseInt(bsel.value) : _baud;

            if (!port) {
                var sub = document.getElementById('sw-st-sub');
                if (sub) sub.innerText = 'Please select a COM port first';
                return;
            }

            var ring  = document.getElementById('sw-ring');
            var title = document.getElementById('sw-st-title');
            var sub2  = document.getElementById('sw-st-sub');
            if (ring)  ring.className  = 'sw-ring busy';
            if (title) title.innerText = 'Connecting...';
            if (sub2)  sub2.innerText  = 'Auto-detecting baud rate...';
            if (btn)   { btn.disabled = true; btn.innerHTML = '&#8987; Connecting...'; }

            if (!window.pywebview || !window.pywebview.api) return;
            window.pywebview.api.scale_connect(port, baud).then(function(r) {
                if (btn) btn.disabled = false;
                if (r && r.status === 'success') {
                    _connected = true;
                    _port      = r.port || port;
                    _baud      = r.baud || baud;
                    _updateStatus();
                } else {
                    var ring2 = document.getElementById('sw-ring');
                    var t2    = document.getElementById('sw-st-title');
                    var s2    = document.getElementById('sw-st-sub');
                    if (ring2) ring2.className = 'sw-ring err';
                    if (t2)    t2.innerText    = 'Connection Failed';
                    if (s2)    s2.innerText    = (r && r.message) ? r.message : 'Could not connect. Check port.';
                    if (btn)   { btn.className = 'sw-conn-btn connect'; btn.innerHTML = '&#9654; Connect Scale'; }
                }
            }).catch(function(e) {
                if (btn) { btn.disabled = false; btn.innerHTML = '&#9654; Connect Scale'; }
                var sub3 = document.getElementById('sw-st-sub');
                if (sub3) sub3.innerText = 'Error: ' + String(e);
            });
        },

        disconnect: function() {
            if (!window.pywebview || !window.pywebview.api) return;
            window.pywebview.api.scale_disconnect().then(function() {
                _connected  = false;
                _lastWeight = null;
                _stable     = false;
                _updateStatus();
                var val   = document.getElementById('sw-wt-val');
                var badge = document.getElementById('sw-wt-badge');
                if (val)   { val.innerText = '\u2014\u00a0'; val.className = 'sw-wt-val'; }
                if (badge) { badge.innerText = 'WAITING'; badge.className = 'sw-wt-badge'; }
            }).catch(function() {});
        },

        openPopup: function() {
            _self._scanPorts();
            _self._setPortSelFromSaved();
            var popup = document.getElementById('sw-popup');
            if (popup) popup.className = 'sw-overlay open';
        },

        closePopup: function() {
            var popup = document.getElementById('sw-popup');
            if (popup) popup.className = 'sw-overlay';
        },

        // Internal bridge — called by pages that have their own __onScale handlers
        _onScaleRaw: function(d) {
            if (!d) return;
            if (d.error) { _connected = false; _updateStatus(); return; }
            var wt     = parseFloat(d.weight) || 0;
            var stable = d.stable === true || d.stable === 'true';
            _connected = true;
            _updateWeight(wt, stable);
        },

        getWeight: function()    { return _lastWeight; },
        isConnected: function()  { return _connected; },
        isStable: function()     { return _stable; },

        onWeight: function(cb) {
            if (typeof cb === 'function') _callbacks.push(cb);
        },

        /*
         * renderBtn(containerId)
         * Renders the compact topbar button into given element
         */
        renderBtn: function(containerId) {
            var el = document.getElementById(containerId);
            if (!el) return;
            var btn = document.createElement('button');
            btn.id        = 'sw-tb-btn';
            btn.className = 'sw-btn' + (_connected ? ' connected' : '');
            btn.innerHTML = '<span class="sw-dot"></span>Scale';
            btn.onclick   = function() { _self.openPopup(); };
            el.appendChild(btn);
        },

        /*
         * renderMini(containerId, autoFillFieldId?)
         * Renders compact weight display for embedding in modals
         * autoFillFieldId: if provided, fills that input on stable weight
         */
        renderMini: function(containerId, autoFillFieldId) {
            var el = document.getElementById(containerId);
            if (!el) return;
            var wt  = _lastWeight ? _lastWeight.toFixed(3) + 'g' : '\u2014';
            var div = document.createElement('div');
            div.className = 'sw-mini' + (_connected ? ' connected' : '') + (_stable && _lastWeight ? ' has-weight' : '');
            div.title     = 'Click to manage scale connection';
            div.innerHTML =
                '<span class="sw-mini-dot"></span>'
                + '<span class="sw-mini-val">' + wt + '</span>'
                + '<span class="sw-mini-lbl">g</span>'
                + '<span class="sw-mini-arrow">&#8599;</span>';
            div.onclick = function() { _self.openPopup(); };
            el.appendChild(div);

            if (autoFillFieldId) {
                _miniTargets[containerId] = autoFillFieldId;
            }
        },

        /*
         * applyToField(fieldId)
         * On next stable reading, fills the given input field.
         * Returns immediately; fills asynchronously.
         */
        applyToField: function(fieldId) {
            // If already have stable weight, apply immediately
            if (_stable && _lastWeight) {
                var el = document.getElementById(fieldId);
                if (el) {
                    el.value = _lastWeight.toFixed(3);
                    if (el.oninput) el.oninput();
                    try { el.dispatchEvent(new Event('input')); } catch(e) {}
                    return;
                }
            }
            // Otherwise wait for next stable reading
            var once = function(wt, stable) {
                if (stable && wt) {
                    var el2 = document.getElementById(fieldId);
                    if (el2) {
                        el2.value = wt.toFixed(3);
                        if (el2.oninput) el2.oninput();
                        try { el2.dispatchEvent(new Event('input')); } catch(e) {}
                    }
                    // Remove self from callbacks
                    var idx = _callbacks.indexOf(once);
                    if (idx >= 0) _callbacks.splice(idx, 1);
                }
            };
            _callbacks.push(once);
        }
    };

    return _self;
})();