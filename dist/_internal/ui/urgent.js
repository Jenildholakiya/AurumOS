/* ══════════════════════════════════════════════════════════════════════
   AurumOS Urgent Messages — client-side receive + display
   Source of truth: admin dashboard (Next.js). DO NOT modify the server.
   Stream: existing EventSource (sse_stream.js) + 'aurum-urgent' CustomEvent
           forwarded by the Python daemon. Informational only — never locks
           billing/stock (only status_change revoked/expired does that).
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__aurumUrgent) return;
  window.__aurumUrgent = true;

  var seenIds = {};          // session dedupe (backend SQLite is permanent)
  var critQueue = [];        // critical modals waiting for a safe moment
  var critOpen = false;
  var lastPoll = 0;

  // ── Self-contained styles (work on every page) ────────────────────────
  (function urgentCSS() {
    try {
      if (document.getElementById('urgent-css')) return;
      var st = document.createElement('style');
      st.id = 'urgent-css';
      st.textContent =
        '.toast-host{position:fixed;right:18px;top:70px;z-index:200;display:flex;flex-direction:column;gap:10px;max-width:340px;}'
        + '.toast-pop{display:flex;gap:10px;align-items:flex-start;background:#fff;border:1px solid #e2e8f0;border-left:3px solid #2563eb;border-radius:10px;padding:12px 14px;box-shadow:0 12px 32px rgba(15,23,42,.18);cursor:pointer;animation:urgentToastIn .3s cubic-bezier(.34,1.4,.64,1);font-family:inherit;}'
        + '.toast-pop.out{opacity:0;transform:translateX(20px);transition:all .4s;}'
        + '@keyframes urgentToastIn{from{opacity:0;transform:translateX(40px);}to{opacity:1;transform:none;}}'
        + '.toast-x{margin-left:auto;color:#94a3b8;cursor:pointer;font-size:1rem;line-height:1;padding-left:8px;}'
        + '.toast-x:hover{color:#dc2626;}'
        + '.notif-ico{width:30px;height:30px;border-radius:8px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:.85rem;}'
        + '.notif-ico.warn{background:#fffbeb;border:1px solid rgba(180,83,9,.2);}'
        + '.notif-ico.due{background:#fef2f2;border:1px solid rgba(185,28,28,.18);}'
        + '.notif-ico.info{background:rgba(37,99,235,.06);border:1px solid rgba(37,99,235,.18);}'
        + '.notif-ico.ok{background:rgba(21,128,61,.07);border:1px solid rgba(21,128,61,.18);}'
        + '.notif-txt{font-size:.72rem;color:#0f172a;line-height:1.45;}'
        + '.notif-time{font-size:.6rem;color:#94a3b8;margin-top:3px;}'
        + '.urgent-modal-ov{position:fixed;inset:0;z-index:99999;background:rgba(15,23,42,.55);display:flex;align-items:center;justify-content:center;padding:20px;animation:urgentFade .25s ease;}'
        + '@keyframes urgentFade{from{opacity:0;}to{opacity:1;}}'
        + '.urgent-modal{width:420px;max-width:100%;background:#fff;border-radius:14px;border-top:5px solid #dc2626;box-shadow:0 24px 64px rgba(0,0,0,.35);padding:24px;animation:urgentPop .3s cubic-bezier(.34,1.4,.64,1);font-family:inherit;}'
        + '@keyframes urgentPop{from{opacity:0;transform:scale(.94) translateY(10px);}to{opacity:1;transform:none;}}'
        + '.urgent-modal-tag{font-size:.6rem;font-weight:800;letter-spacing:1.5px;color:#dc2626;margin-bottom:10px;}'
        + '.urgent-modal-title{font-size:1.05rem;font-weight:800;color:#0f172a;margin-bottom:10px;line-height:1.35;}'
        + '.urgent-modal-body{font-size:.82rem;color:#475569;line-height:1.6;white-space:pre-wrap;}'
        + '.urgent-modal-time{font-size:.65rem;color:#94a3b8;margin-top:12px;}'
        + '.urgent-modal-btn{margin-top:16px;width:100%;padding:11px;background:#dc2626;color:#fff;border:none;border-radius:8px;font-size:.82rem;font-weight:700;cursor:pointer;}'
        + '.urgent-modal-btn:hover{background:#b91c1c;}';
      document.head.appendChild(st);
    } catch (e) {}
  })();

  function log(m) { try { console.log('[URGENT] ' + m); } catch (e) {} }

  function api() {
    try { return (window.pywebview && window.pywebview.api) || null; }
    catch (e) { return null; }
  }

  function myKey() {
    var k = '';
    try { k = window.__aurumSSEKey || localStorage.getItem('aurum_sse_key') || ''; } catch (e) {}
    return String(k || '').trim().toUpperCase();
  }

  function normPri(p) {
    p = String(p || 'info').toLowerCase();
    return (p === 'critical' || p === 'warning') ? p : 'info';
  }

  // en-IN: "6 Sep, 14:32"
  function fmtTime(s) {
    try {
      var d = new Date(s);
      if (isNaN(d.getTime())) return '';
      return d.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
    } catch (e) { return ''; }
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text; // plain text only — never HTML
    return e;
  }

  // ── Sound (WebAudio, no assets) ─────────────────────────────────────
  function alertSound() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      [0, 0.28, 0.56].forEach(function (off) {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        o.type = 'sine'; o.frequency.value = 880;
        var t = ctx.currentTime + off;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.4, t + 0.03);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.24);
        o.start(t); o.stop(t + 0.26);
      });
    } catch (e) {}
  }

  // ── Toast host (shared with dashboard toasts when present) ──────────
  function toastHost() {
    var h = document.getElementById('toast-host');
    if (h) return h;
    h = document.createElement('div');
    h.id = 'toast-host';
    h.className = 'toast-host';
    document.body.appendChild(h);
    return h;
  }

  function toast(msg, ms, accent) {
    try {
      var t = el('div', 'toast-pop');
      if (accent) t.style.borderLeftColor = accent;
      var dots = { info: '#2563eb', warning: '#b45309', critical: '#dc2626' };
      var dot = el('div', 'notif-ico ' + (msg.priority === 'warning' ? 'warn' : msg.priority === 'critical' ? 'due' : 'info'));
      dot.innerHTML = '<span style="width:9px;height:9px;border-radius:50%;background:' + (dots[msg.priority] || dots.info) + ';"></span>';
      var box = el('div');
      box.appendChild(el('div', 'notif-txt', String(msg.title || 'AurumOS notice')));
      var tm = el('div', 'notif-time', fmtTime(msg.sent_at) + (msg.body ? '' : ''));
      box.appendChild(tm);
      var x = el('span', 'toast-x', '×');
      x.onclick = function (ev) { ev.stopPropagation(); t.remove(); };
      t.appendChild(dot); t.appendChild(box); t.appendChild(x);
      t.title = String(msg.body || '');
      t.onclick = function () { openMessage(msg); t.remove(); };
      toastHost().appendChild(t);
      setTimeout(function () { t.classList.add('out'); setTimeout(function () { t.remove(); }, 400); }, ms);
    } catch (e) {}
  }

  // ── Critical modal (blocking UI, never blocking saves) ──────────────
  function txBusy() {
    try {
      if (window.__aurumTxBusy === true) return true;
      if (document.querySelector('.bill-saving, .tx-busy, .swal2-container')) return true;
    } catch (e) {}
    return false;
  }

  function showCritical(msg) {
    if (txBusy()) { // never cover an in-progress bill save — wait for commit
      if (critQueue.indexOf(msg) === -1) critQueue.push(msg);
      setTimeout(flushCritQueue, 2000);
      return;
    }
    if (critOpen) { if (critQueue.indexOf(msg) === -1) critQueue.push(msg); return; }
    critOpen = true;
    try {
      var ov = el('div', 'urgent-modal-ov');
      ov.id = 'urgent-modal-ov';
      var box = el('div', 'urgent-modal');
      var tag = el('div', 'urgent-modal-tag', 'CRITICAL · AURUMOS');
      var title = el('div', 'urgent-modal-title', String(msg.title || 'Urgent notice'));
      var body = el('div', 'urgent-modal-body', String(msg.body || ''));
      var time = el('div', 'urgent-modal-time', fmtTime(msg.sent_at));
      var btn = el('button', 'urgent-modal-btn', 'Acknowledge');
      btn.onclick = function () {
        var a = api();
        if (a && a.urgent_mark_read) { try { a.urgent_mark_read(String(msg.id)).catch(function () {}); } catch (e) {} }
        try { ov.remove(); } catch (e) {}
        critOpen = false;
        refreshInbox();
        setTimeout(flushCritQueue, 300);
      };
      box.appendChild(tag); box.appendChild(title); box.appendChild(body); box.appendChild(time); box.appendChild(btn);
      ov.appendChild(box);
      document.body.appendChild(ov);
    } catch (e) { critOpen = false; }
  }

  function flushCritQueue() {
    if (critOpen || txBusy()) { setTimeout(flushCritQueue, 2000); return; }
    var m = critQueue.shift();
    if (m) showCritical(m);
  }
  window.__aurumUrgentFlush = flushCritQueue;

  // ── Receive (called by sse_stream.js listener + 'aurum-urgent' events) ─
  function receive(raw) {
    try {
      var msg = {
        id: Number(raw && raw.id),
        title: String((raw && raw.title) != null ? raw.title : '').slice(0, 120),
        body: String((raw && raw.body) != null ? raw.body : '').slice(0, 1000),
        priority: ['info', 'warning', 'critical'].indexOf(raw && raw.priority) !== -1 ? raw.priority : 'info',
        sent_at: (raw && (raw.sent_at || raw.created_at)) || new Date().toISOString()
      };
      if (raw && raw.key != null) msg.key = raw.key;
      if (raw && raw.broadcast != null) msg.broadcast = raw.broadcast;
      if (raw && raw.source != null) msg.source = raw.source;
      if (!Number.isFinite(msg.id) || !msg.title || !msg.body) {
        console.error('[MSGS] invalid payload, dropped:', raw); return;
      }
      var sid = String(msg.id);
      // Self-heal: a stale "seen" without a kept copy re-stores instead of dropping.
      if (seenIds[sid] && boxHas(sid)) { console.log('[MSGS] duplicate id=' + sid + ', dropped'); return; }
      if (!storeMsg(msg)) { console.log('[MSGS] duplicate id=' + sid + ', dropped'); return; }
      var a = api();
      if (a) {
        var ing = null;
        try { ing = a.urgent_ingest; } catch (e) { ing = null; }
        if (typeof ing === 'function') {
          try {
            var p = a.urgent_ingest({ id: msg.id, title: msg.title, body: msg.body, priority: msg.priority, sent_at: msg.sent_at, key: msg.key, broadcast: msg.broadcast });
            if (p && typeof p.then === 'function') {
              p.then(function (r) {
                console.log('[MSGS] stored id=' + sid + ' backend=' + (r && r.status));
                if (r && r.stored !== false) display(msg);
                refreshInbox();
              }).catch(function () { display(msg); refreshInbox(); });
              return;
            }
          } catch (e) { console.error('[MSGS] ingest error:', e); }
        }
      }
      console.log('[MSGS] stored id=' + sid + ' (local)');
      display(msg);
      refreshInbox();
    } catch (e) { console.error('[MSGS] receive error:', e); }
  }

  function display(msg) {
    if (msg.priority === 'critical') {
      alertSound();
      showCritical(msg); // no auto-dismiss; ack-only
    } else if (msg.priority === 'warning') {
      toast(msg, 15000, '#b45309');
    } else {
      toast(msg, 8000, '#2563eb');
    }
  }

  function openMessage(msg) {
    var a = api();
    if (msg.priority !== 'critical' && a && a.urgent_mark_read) {
      try { a.urgent_mark_read(String(msg.id)).catch(function () {}); } catch (e) {}
    }
    refreshInbox();
  }

  // ── Seen persistence (localStorage mirror; SQLite is the record) ─────
  function persistSeen() {
    try {
      var ids = Object.keys(seenIds).slice(-200);
      localStorage.setItem('aurum_urgent_seen', JSON.stringify(ids));
    } catch (e) {}
  }
  // ── Local inbox (works even before the backend store exists) ─────────
  // Memory mirror is the session truth (survives broken storage);
  // localStorage is the cross-restart mirror. Honest accounting: an id is
  // "seen" ONLY once it sits in the box.
  var memBox = null;
  // Tombstones: ids the user deleted never come back, even if the server
  // still lists them.
  function hload() {
    try {
      var v = JSON.parse(localStorage.getItem('aurum_urgent_hidden') || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }
  function hideId(id) {
    try {
      var h = hload(), sid = String(id), found = false;
      for (var i = 0; i < h.length; i++) {
        if (String(h[i]) === sid) { found = true; break; }
      }
      if (!found) { h.push(sid); localStorage.setItem('aurum_urgent_hidden', JSON.stringify(h.slice(-200))); }
      delete seenIds[sid];
      memBox = box().filter(function (m) { return String(m.id) !== sid; });
      lsave(memBox);
    } catch (e) {}
  }
  function isHidden(sid) {
    try {
      var h = hload();
      for (var i = 0; i < h.length; i++) {
        if (String(h[i]) === String(sid)) return true;
      }
    } catch (e) {}
    return false;
  }
  function lload() {
    try {
      var v = JSON.parse(localStorage.getItem('aurum_urgent_inbox') || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }
  function lsave(box) {
    try { localStorage.setItem('aurum_urgent_inbox', JSON.stringify((box || []).slice(0, 100))); }
    catch (e) {}
  }
  function box() {
    if (!memBox) memBox = lload();
    return memBox;
  }
  function boxHas(sid) {
    var b = box();
    for (var i = 0; i < b.length; i++) {
      if (String(b[i].id) === String(sid)) return true;
    }
    return false;
  }
  function storeMsg(msg) {
    // Returns true only when the message is newly kept.
    try {
      var sid = String(msg.id);
      if (isHidden(sid)) { seenIds[sid] = true; persistSeen(); return false; }
      if (boxHas(sid)) return false;
      box().unshift({ id: msg.id, title: msg.title, body: msg.body, priority: msg.priority, sent_at: msg.sent_at, read: 0, source: msg.source || 'live' });
      while (box().length > 100) box().pop();
      lsave(box());
      seenIds[sid] = true;
      persistSeen();
      return true;
    } catch (e) { return false; }
  }
  function lstore(msg) { return storeMsg(msg); }
  function lack(id) {
    try {
      var ch = false;
      box().forEach(function (m) { if (String(m.id) === String(id) && !m.read) { m.read = 1; ch = true; } });
      if (ch) { lsave(box()); }
    } catch (e) {}
  }
  function lackAll() {
    try {
      box().forEach(function (m) { m.read = 1; });
      lsave(box());
    } catch (e) {}
  }
  function ldelete(id) {
    try {
      hideId(id);
    } catch (e) {}
  }
  function lclearTests() {
    try {
      var gone = [];
      box().forEach(function (m) { if (m.source === 'selftest') gone.push(m.id); });
      memBox = box().filter(function (m) { return m.source !== 'selftest'; });
      lsave(memBox);
      gone.forEach(function (id) { hideId(id); });
    } catch (e) {}
  }
  function hydrateSeen() {
    try {
      var ids = JSON.parse(localStorage.getItem('aurum_urgent_seen') || '[]');
      ids.forEach(function (id) { seenIds[String(id)] = true; });
    } catch (e) {}
    try {
      lload().forEach(function (m) { seenIds[String(m.id)] = true; });
    } catch (e2) {}
    persistSeen();
    var a = api();
    if (a && a.urgent_list) {
      try {
        var p = a.urgent_list(100);
        var done = function (res) {
          ((res && res.messages) || []).forEach(function (m) { seenIds[String(m.id)] = true; });
          persistSeen();
          refreshInbox();
        };
        if (p && typeof p.then === 'function') p.then(done).catch(function () {});
      } catch (e) {}
    }
  }

  // ── Inbox bridge (dashboard bell consumes this) ───────────────────────
  // Merges backend SQLite list with the local store (works pre-rebuild).
  function mergeLists(server) {
    var map = {}, out = [];
    (box() || []).forEach(function (m) {
      var k = String(m.id);
      if (isHidden(k)) return;
      if (!map[k]) { map[k] = true; out.push({ id: m.id, title: m.title, body: m.body, priority: m.priority, sent_at: m.sent_at, read: !!m.read, source: m.source || 'live' }); }
    });
    ((server && server.messages) || []).forEach(function (m) {
      var k = String(m.id);
      if (isHidden(k)) return;
      if (!map[k]) {
        map[k] = true;
        out.push({ id: m.id, title: m.title, body: m.body, priority: m.priority, sent_at: m.sent_at, read: !!m.read });
      } else if (m.read) {
        for (var i = 0; i < out.length; i++) {
          if (String(out[i].id) === k) { out[i].read = true; break; }
        }
      }
    });
    return out;
  }
  window.__urgentInbox = {
    list: function () {
      var a = api();
      if (a && a.urgent_list) {
        try {
          var p = a.urgent_list(100);
          if (p && typeof p.then === 'function') {
            return p.then(function (res) { return { status: 'success', messages: mergeLists(res) }; })
                    .catch(function () { return { status: 'success', messages: mergeLists(null) }; });
          }
        } catch (e) {}
      }
      return Promise.resolve({ status: 'success', messages: mergeLists(null) });
    },
    ack: function (id) {
      lack(id);
      var a = api();
      if (a && a.urgent_mark_read) { try { return a.urgent_mark_read(String(id)); } catch (e) {} }
      return Promise.resolve({ status: 'offline' });
    },
    ackAll: function () {
      lackAll();
      var a = api();
      if (a && a.urgent_mark_all_read) { try { return a.urgent_mark_all_read(); } catch (e) {} }
      return Promise.resolve({ status: 'offline' });
    },
    del: function (id) {
      ldelete(id);
      var a = api();
      if (a && a.urgent_delete) { try { return a.urgent_delete(String(id)); } catch (e) {} }
      return Promise.resolve({ status: 'offline' });
    },
    clearTests: function () {
      lclearTests();
      return Promise.resolve({ status: 'success' });
    }
  };

  // ── Stream status (shown in the bell dropdown) ────────────────────────
  window.__aurumUrgentStream = function () {
    var connected = false;
    try {
      if (typeof window.__aurumSSEStatus === 'function') connected = !!window.__aurumSSEStatus().connected;
    } catch (e) {}
    var k = myKey();
    return { connected: connected, key: k ? (k.slice(0, 6) + '…' + k.slice(-4)) : '—' };
  };

  // ── Direct missed-message fetch (no backend needed) ───────────────────
  // Same filter as the server contract: all targets, or my key in target_keys.
  function directFetchMissed(viaPoll) {
    try {
      var base = '';
      try { base = window.__aurumSSEUrl || localStorage.getItem('aurum_sse_url') || ''; } catch (e) {}
      if (!base) base = 'https://aurum-os-admin.vercel.app';
      var key = myKey();
      if (!key) return Promise.resolve({ status: 'no-key', added: 0 });
      var url = String(base).replace(/\/+$/, '') + '/api/messages?key=' + encodeURIComponent(key) + '&limit=30';
      return fetch(url, { method: 'GET' }).then(function (r) {
        if (!r.ok) throw { http: r.status };
        return r.json();
      }).then(function (data) {
        var items = (data && data.messages) || [];
        var added = 0,fresh = [];
        var skippedSeen = 0, skippedTarget = 0, firstWhy = '';
        (Array.isArray(items) ? items.slice(0, 30) : []).forEach(function (m) {
          if (!m || m.id == null) return;
          var applies = String(m.target_mode || '').toLowerCase() === 'all';
          if (!applies) {
            var keys = m.target_keys || [];
            if (typeof keys === 'string') { try { keys = JSON.parse(keys); } catch (e2) { keys = [keys]; } }
            for (var i = 0; i < (keys || []).length; i++) {
              if (String(keys[i] || '').trim().toUpperCase() === key) { applies = true; break; }
            }
          }
          if (!applies) { skippedTarget++; if (!firstWhy) firstWhy = 'target id=' + m.id; return; }
          var sid = String(m.id);
          if (seenIds[sid] && boxHas(sid)) { skippedSeen++; if (!firstWhy) firstWhy = 'seen id=' + sid; return; }
          var msg = { id: m.id, title: m.title, body: m.body, priority: normPri(m.priority), sent_at: m.sent_at || m.created_at };
          if (!storeMsg(msg)) { skippedSeen++; if (!firstWhy) firstWhy = 'dup id=' + sid; return; }
          added++; fresh.push(msg);
        });
        persistSeen();
        if (added && !viaPoll) {
          // newest 3 pop; rest wait in the inbox
          fresh.slice(0, 3).forEach(function (msg, i) {
            setTimeout(function () { display(msg); }, i * 400);
          });
        }
        if (added) refreshInbox();
        try { window.__urgentLastFetch = { ok: true, at: Date.now(), added: added, total: (Array.isArray(items) ? items.length : 0), skippedSeen: skippedSeen, skippedTarget: skippedTarget, firstWhy: firstWhy, key: key }; } catch (e) {}
        return { status: 'success', added: added, total: (Array.isArray(items) ? items.length : 0), skippedSeen: skippedSeen, skippedTarget: skippedTarget, firstWhy: firstWhy, key: key };
      }).catch(function (err) {
        // Pinpoint the fault: HTTP code vs blocked-vs-offline.
        var finish = function (code) {
          try { window.__urgentLastFetch = { ok: false, at: Date.now(), added: 0, code: code }; } catch (e) {}
          return { status: code, added: 0 };
        };
        if (err && err.http) return Promise.resolve(finish('http-' + err.http));
        try {
          return fetch(url, { method: 'GET', mode: 'no-cors' }).then(
            function () { return finish('cors'); },   // host reachable, browser read blocked
            function () { return finish('offline'); } // host itself unreachable
          );
        } catch (e) { return Promise.resolve(finish('offline')); }
      });
    } catch (e) { return Promise.resolve({ status: 'offline', added: 0 }); }
  }
  window.__aurumUrgentCheck = function () { return directFetchMissed(false); };

  function refreshInbox() {
    try {
      if (typeof window.__urgentInboxRefresh === 'function') window.__urgentInboxRefresh();
    } catch (e) {}
    try {
      var a = api();
      if (a && a.urgent_list) {
        var p = a.urgent_list(1);
        if (p && typeof p.then === 'function') p.then(function () {}).catch(function () {});
      }
    } catch (e) {}
  }

  // ── Public hooks for sse_stream.js ────────────────────────────────────
  window.__aurumUrgentReceive = receive;
  window.__aurumUrgentCatchUp = function () { return directFetchMissed(true); };
  // Re-show a critical modal from the inbox (acknowledge-only clearing).
  window.__aurumUrgentShowModal = function (m) {
    if (!m) return;
    m.priority = 'critical';
    showCritical(m);
  };
  window.__aurumUrgentSeen = function (id, add) {
    var sid = String(id);
    if (add) { seenIds[sid] = true; persistSeen(); return true; }
    return !!seenIds[sid];
  };
  window.addEventListener('aurum-urgent', function (e) { if (e && e.detail) receive(e.detail); });

  // ── Polling fallback: SSE down >60s → fetch missed every 5 min ───────
  // Plus always-on background sync every 5 min: converges the inbox even
  // if a live event is lost (server replays only ~5 min).
  var lastAuto = 0;
  setInterval(function () {
    try {
      var down = true, lastAct = 0;
      if (typeof window.__aurumSSEStatus === 'function') {
        var st = window.__aurumSSEStatus();
        down = !st.connected;
      }
      var now = Date.now();
      if (!down) {
        if (now - lastAuto < 5 * 60 * 1000) return;
        lastAuto = now;
        var a2 = api();
        if (a2 && a2.urgent_fetch_missed) {
          try { a2.urgent_fetch_missed().then(function () { refreshInbox(); }).catch(function () {}); }
          catch (e) {}
        } else {
          directFetchMissed(true);
        }
        return;
      }
      if (now - lastPoll < 5 * 60 * 1000) return;
      lastPoll = now;
      var a = api();
      if (a && a.urgent_fetch_missed) {
        try { a.urgent_fetch_missed().then(function () { refreshInbox(); }).catch(function () {}); }
        catch (e) {}
      } else {
        directFetchMissed(true); // backend predates the store: fetch straight
      }
      void lastAct;
    } catch (e) {}
  }, 30000);

  // ── Init: hydrate, missed catch-up once, wire key ─────────────────────
  hydrateSeen();
  // Drain events that arrived before this module loaded (never drop)
  try {
    var pend = window.__aurumUrgentPending || [];
    window.__aurumUrgentPending = [];
    pend.slice(0, 20).forEach(function (m) { receive(m); });
    if (pend.length) console.log('[MSGS] drained ' + pend.length + ' queued event(s)');
  } catch (e) { console.error('[MSGS] drain error:', e); }
  setTimeout(function () {
    var a = api();
    if (a && a.urgent_fetch_missed) {
      try { a.urgent_fetch_missed().then(function () { refreshInbox(); }).catch(function () {}); }
      catch (e) {}
    } else {
      directFetchMissed(false);
    }
    void myKey;
  }, 2500);
})();
