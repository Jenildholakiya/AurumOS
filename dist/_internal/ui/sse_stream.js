/* ══════════════════════════════════════════════════════════════════════════════
   AurumOS SSE Stream — Real-Time License & Plan Synchronization
   Connects to: {SERVER_URL}/api/nexus/stream?key={LICENSE_KEY}
   Handles: status_change, plan_change, heartbeat, reconnection indicator
   ══════════════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__aurumSSE) return;
  window.__aurumSSE = true;

  var _evtSource = null;
  var _reconnectTimer = null;
  var _reconnectDelay = 1000;
  var _maxReconnectDelay = 30000;
  var _closed = false;
  var _connected = false;
  var _lastEventAt = 0;
  var _lastKnownPlan = null;
  var _lastKnownFeatures = null;

  function _log(msg) { try { console.log('[SSE] ' + msg); } catch (e) {} }
  function _err(msg) { try { console.error('[SSE] ' + msg); } catch (e) {} }

  // ── Reconnection indicator ──────────────────────────────────────────
  function _showReconnecting(show) {
    var el = document.getElementById('sse-reconnect-indicator');
    if (show) {
      if (el) return;
      el = document.createElement('div');
      el.id = 'sse-reconnect-indicator';
      el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99998;'
        + 'background:linear-gradient(135deg,#b45309,#d97706);color:#fff;'
        + 'padding:6px 20px;display:flex;align-items:center;justify-content:center;gap:8px;'
        + 'font-family:Inter,sans-serif;font-size:0.72rem;font-weight:600;'
        + 'box-shadow:0 2px 12px rgba(180,83,9,0.3);animation:subSlideDown 0.3s ease-out;';
      el.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
        + 'background:#fff;animation:sseBlink 1s ease-in-out infinite;"></span>'
        + 'Reconnecting to AurumOS server...';
      document.body.appendChild(el);
      // Inject keyframe if not exists
      if (!document.getElementById('sse-anim')) {
        var style = document.createElement('style');
        style.id = 'sse-anim';
        style.textContent = '@keyframes subSlideDown{from{transform:translateY(-100%);}to{transform:translateY(0);}}'
          + '@keyframes sseBlink{0%,100%{opacity:1;}50%{opacity:0.3;}}'
          + '@keyframes sseNotifIn{from{opacity:0;transform:translateX(-50%) translateY(20px);}to{opacity:1;transform:translateX(-50%) translateY(0);}}';
        document.head.appendChild(style);
      }
    } else {
      if (el) el.remove();
    }
  }

  // ── Plan change notification toast ──────────────────────────────────
  function _showPlanNotification(plan, features, expiresAt) {
    var existing = document.getElementById('sse-plan-notif');
    if (existing) existing.remove();

    var isUpgrade = _lastKnownPlan && _planTier(plan) > _planTier(_lastKnownPlan);
    var isDowngrade = _lastKnownPlan && _planTier(plan) < _planTier(_lastKnownPlan);
    var isRenewal = _lastKnownPlan === plan;

    var title, msg, bgColor;
    if (isDowngrade) {
      title = 'Plan Downgraded';
      msg = 'Features limited to ' + plan.toUpperCase() + ' tier. Data preserved.';
      bgColor = '#dc2626';
    } else if (isUpgrade) {
      title = 'Plan Upgraded to ' + plan.toUpperCase() + '!';
      msg = features.length + ' features now available.';
      bgColor = '#059669';
    } else if (isRenewal) {
      title = 'Subscription Renewed';
      msg = 'Your ' + plan.toUpperCase() + ' plan has been renewed.';
      bgColor = '#2563eb';
    } else {
      title = 'Plan Changed to ' + plan.toUpperCase();
      msg = features.length + ' features available.';
      bgColor = '#b45309';
    }

    if (expiresAt) {
      var d = new Date(expiresAt);
      var str = d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();
      msg += ' Expires: ' + str;
    }

    var el = document.createElement('div');
    el.id = 'sse-plan-notif';
    el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);'
      + 'background:' + bgColor + ';color:#fff;padding:12px 24px;border-radius:10px;'
      + 'font-family:Inter,sans-serif;z-index:99999;opacity:0;'
      + 'box-shadow:0 8px 32px rgba(0,0,0,0.2);max-width:420px;text-align:center;'
      + 'animation:sseNotifIn 0.3s ease-out forwards;';
    el.innerHTML = '<div style="font-size:0.82rem;font-weight:700;margin-bottom:2px;">' + title + '</div>'
      + '<div style="font-size:0.68rem;opacity:0.9;">' + msg + '</div>';
    document.body.appendChild(el);

    setTimeout(function () {
      el.style.opacity = '0';
      el.style.transform = 'translateX(-50%) translateY(20px)';
      el.style.transition = 'all 0.3s ease';
      setTimeout(function () { el.remove(); }, 300);
    }, 4000);
  }

  function _planTier(p) {
    var tiers = { 'lite': 0, 'pro': 1, 'enterprise': 2 };
    return tiers[(p || '').toLowerCase()] || 0;
  }

  // ── Fetch subscription status from server ───────────────────────────
  async function _fetchSubscriptionStatus() {
    // Try pywebview bridge first
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.subscription_sync === 'function') {
      try {
        var data = await window.pywebview.api.subscription_sync();
        if (data) return data;
      } catch (e) { _log('pywebview sync failed: ' + e); }
    }
    // Fallback: direct fetch
    try {
      var key = window.__aurumSSEKey || '';
      var res = await fetch('https://aurum-os-admin.vercel.app/api/subscription/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, machine_id: 'browser' })
      });
      return await res.json();
    } catch (e) { _err('Direct fetch failed: ' + e); }
    return null;
  }

  // ── Apply new features to subscription state ────────────────────────
  function _applyNewFeatures(data) {
    if (!data) return;
    var newPlan = (data.effective_plan || data.plan || 'lite').toLowerCase();
    var newFeatures = data.features || [];
    var expiresAt = data.subscription_expires_at || (data.subscription && data.subscription.expires_at) || null;

    // Detect change type
    var oldPlan = _lastKnownPlan || 'lite';
    var oldFeatures = _lastKnownFeatures || [];

    _lastKnownPlan = newPlan;
    _lastKnownFeatures = newFeatures.slice();

    // Show notification
    _showPlanNotification(newPlan, newFeatures, expiresAt);

    // Handle downgrade — redirect if in removed module
    if (_planTier(newPlan) < _planTier(oldPlan)) {
      _handleDowngrade(newPlan, newFeatures);
    }

    // Re-fetch full state from Python, THEN dispatch event after it resolves
    // This ensures __aurumSubHasFeature() returns correct data when sidebar checks
    var syncPromise = window.__aurumSubSync ? window.__aurumSubSync() : Promise.resolve();
    syncPromise.then(function () {
      // Dispatch event so boot.js / sidebar.js can re-apply locks
      try {
        window.dispatchEvent(new CustomEvent('subscription:features_updated', {
          detail: { plan: newPlan, features: newFeatures.slice() }
        }));
      } catch (e) {}
      // Also update sidebar locks directly
      _updateSidebarLocks(newFeatures);
      _log('Features applied: plan=' + newPlan + ' features=' + newFeatures.length);
    }).catch(function () {
      // Even if sync fails, dispatch with whatever features we have
      try {
        window.dispatchEvent(new CustomEvent('subscription:features_updated', {
          detail: { plan: newPlan, features: newFeatures.slice() }
        }));
      } catch (e) {}
      _updateSidebarLocks(newFeatures);
    });
  }

  // ── Handle downgrade — redirect from removed modules ────────────────
  function _handleDowngrade(newPlan, newFeatures) {
    var currentPage = '';
    try {
      var path = window.location.pathname.split('/').pop();
      currentPage = path.toLowerCase();
    } catch (e) {}

    // Pages that require features beyond the new plan
    var removedPages = [];
    if (newPlan === 'lite') {
      removedPages = ['tag_audit.html', 'year_close.html', 'karigar.html', 'accounting.html',
        'chart_of_accounts.html', 'ledger.html', 'reports.html', 'network_manager.html',
        'staff.html', 'analytics_dashboard.html'];
    } else if (newPlan === 'pro') {
      removedPages = ['cloud_sync.html', 'fleet_bastion.html'];
    }

    if (removedPages.indexOf(currentPage) !== -1) {
      _log('Downgrade: redirecting from ' + currentPage + ' to dashboard');
      setTimeout(function () {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.navigate) {
          window.pywebview.api.navigate('dashboard.html');
        } else {
          window.location.href = 'dashboard.html';
        }
      }, 2000);
    }
  }

  // ── Update sidebar lock states ──────────────────────────────────────
  function _updateSidebarLocks(features) {
    if (!window.AurumOS) return;
    var locked = document.querySelectorAll('.sb-item.feature-locked');
    locked.forEach(function (item) {
      var fid = item.getAttribute('data-feature');
      if (fid && features.indexOf(fid) !== -1) {
        // Feature is now available — unlock
        item.classList.remove('feature-locked');
        item.style.opacity = '';
        item.style.cursor = '';
        item.removeAttribute('title');
        var lock = item.querySelector('.sb-lock');
        if (lock) lock.remove();
        _log('Unlocked sidebar: ' + fid);
      }
    });
  }

  // ── SSE connection ──────────────────────────────────────────────────
  function _connect(serverUrl, licenseKey) {
    if (_closed) return;
    if (_evtSource) { try { _evtSource.close(); } catch (e) {} _evtSource = null; }

    var url = serverUrl.replace(/\/+$/, '') + '/api/nexus/stream?key=' + encodeURIComponent(licenseKey);
    _log('Connecting to: ' + url);

    try {
      _evtSource = new EventSource(url);
    } catch (e) {
      _log('EventSource create failed: ' + e);
      _scheduleReconnect();
      return;
    }

    _evtSource.onopen = function () {
      _log('Connected');
      _connected = true;
      _reconnectDelay = 1000;
      _showReconnecting(false);
      // Reconnect catch-up for urgent messages (server replays ~5 min)
      try { if (window.__aurumUrgentCatchUp) window.__aurumUrgentCatchUp(); } catch (e) {}
    };

    _evtSource.onmessage = function (ev) {
      _lastEventAt = Date.now();
      _handleMessage(ev.data);
    };

    _evtSource.addEventListener('status_change', function (ev) {
      _lastEventAt = Date.now();
      _handleMessage(ev.data);
    });

    _evtSource.addEventListener('plan_change', function (ev) {
      _lastEventAt = Date.now();
      _handlePlanChange(ev.data);
    });

    // ── Urgent admin messages (info/warning/critical) ───────────────────
    // Doubles can arrive (global + per-key fan-out) — delegated handler dedupes.
    _evtSource.addEventListener('urgent_message', function (event) {
      try {
        _lastEventAt = Date.now();
        var msg;
        try { msg = JSON.parse(event.data); }
        catch (e) { console.error('[MSGS] bad JSON, dropped:', event.data); return; }
        if (msg == null || msg.id == null) { console.error('[MSGS] invalid payload, dropped:', event.data); return; }
        console.log('[MSGS] event id=' + msg.id + ' priority=' + (msg.priority || 'info'));
        var sid = String(msg.id);
        if (window.__aurumUrgentSeen && window.__aurumUrgentSeen(sid)) { console.log('[MSGS] duplicate id=' + sid + ', dropped'); return; }
        if (window.__aurumUrgentSeen) window.__aurumUrgentSeen(sid, true);
        if (window.__aurumUrgentReceive) { window.__aurumUrgentReceive(msg); }
        else {
          // urgent.js not ready yet — queue, never drop
          window.__aurumUrgentPending = window.__aurumUrgentPending || [];
          if (window.__aurumUrgentPending.length < 20) window.__aurumUrgentPending.push(msg);
          console.log('[MSGS] queued id=' + sid + ' (display module loading)');
        }
      } catch (e) { console.error('[MSGS] handler error:', e); }
    });

    _evtSource.onerror = function () {
      _log('Connection error');
      _connected = false;
      try { _evtSource.close(); } catch (e) {}
      _evtSource = null;
      _showReconnecting(true);
      _scheduleReconnect();
    };
  }

  // ── Handle status_change (revoke/expire) ────────────────────────────
  function _handleMessage(raw) {
    if (!raw) return;
    var data;
    try { data = JSON.parse(raw); } catch (e) { return; }

    _log('Event received: ' + JSON.stringify(data));

    if (data.status === 'revoked' || data.status === 'disabled' || data.status === 'suspended') {
      _log('License ' + data.status + ' — locking terminal');
      if (_evtSource) { try { _evtSource.close(); } catch (e) {} _evtSource = null; }
      if (window.pywebview && window.pywebview.api && window.pywebview.api.sse_handle_revoke) {
        window.pywebview.api.sse_handle_revoke(data.status || 'revoked', data.message || '');
      } else {
        window.location.href = 'revoked.html?reason=' + encodeURIComponent(data.status || 'revoked');
      }
    } else if (data.status === 'expired' || data.status === 'subscription_expired') {
      _log('License expired — redirecting to expiry screen');
      if (_evtSource) { try { _evtSource.close(); } catch (e) {} _evtSource = null; }
      if (window.pywebview && window.pywebview.api && window.pywebview.api.sse_handle_revoke) {
        window.pywebview.api.sse_handle_revoke('expired', data.message || '');
      } else {
        window.location.href = 'expiry.html?reason=expired';
      }
    } else if (data.status === 'active' && data.previous_status && data.previous_status !== 'active') {
      _log('License reactivated via SSE — applying active state directly');
      // Trust SSE event — don't re-fetch from server (server may still say expired)
      _applyNewFeatures({
        effective_plan: 'pro',
        plan: 'pro',
        features: [],
        subscription_expires_at: null,
        status: 'active'
      });
      // Notify subscription.js
      if (window.__onSubscriptionEvent) {
        window.__onSubscriptionEvent('reactivated', { status: 'active', valid: true, effective_plan: 'pro' });
      }
    }
  }

  // ── Handle plan_change — the core real-time sync ────────────────────
  function _handlePlanChange(raw) {
    if (!raw) return;
    var data;
    try { data = JSON.parse(raw); } catch (e) { return; }

    _log('>>> PLAN_CHANGE received: ' + JSON.stringify(data));

    var newPlan = (data.plan || '').toLowerCase();
    var newFeatures = data.features || [];
    var expiresAt = data.subscription_expires_at || null;

    // If server sent features directly, apply them immediately
    if (newFeatures.length > 0) {
      _applyNewFeatures({
        effective_plan: newPlan,
        plan: newPlan,
        features: newFeatures,
        subscription_expires_at: expiresAt,
        status: 'active'
      });
    }

    // Also re-fetch full status for complete sync (ensures consistency)
    _fetchSubscriptionStatus().then(function (fullData) {
      if (fullData && fullData.features) {
        _applyNewFeatures(fullData);
      }
    }).catch(function (e) {
      _log('Re-fetch after plan_change failed: ' + e);
    });

    // Notify Python backend to sync
    if (window.pywebview && window.pywebview.api && window.pywebview.api.subscription_sync) {
      window.pywebview.api.subscription_sync().catch(function () {});
    }
  }

  // ── Reconnection with exponential backoff ───────────────────────────
  function _scheduleReconnect() {
    if (_closed || _reconnectTimer) return;
    _log('Reconnecting in ' + _reconnectDelay + 'ms');
    _reconnectTimer = setTimeout(function () {
      _reconnectTimer = null;
      if (!_closed && window.__aurumSSEUrl && window.__aurumSSEKey) {
        _connect(window.__aurumSSEUrl, window.__aurumSSEKey);
      }
    }, _reconnectDelay);
    _reconnectDelay = Math.min(_reconnectDelay * 1.5, _maxReconnectDelay);
  }

  function _close() {
    _closed = true;
    _showReconnecting(false);
    if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
    if (_evtSource) { try { _evtSource.close(); } catch (e) {} _evtSource = null; }
    _log('Closed');
  }

  // ── Public API ──────────────────────────────────────────────────────
  window.__aurumSSEConnect = function (serverUrl, licenseKey) {
    window.__aurumSSEUrl = serverUrl;
    window.__aurumSSEKey = licenseKey;
    // Persist so re-injected sse_stream.js on new pages can reconnect
    try {
      localStorage.setItem('aurum_sse_url', serverUrl);
      localStorage.setItem('aurum_sse_key', licenseKey);
    } catch (e) {}
    _closed = false;
    _connect(serverUrl, licenseKey);
  };

  window.__aurumSSEClose = _close;

  window.__aurumSSEStatus = function () {
    return { connected: _connected, plan: _lastKnownPlan, features: _lastKnownFeatures, lastEventAt: _lastEventAt };
  };

  // ── Auto-reconnect from localStorage on page load ─────────────────
  (function _autoConnect() {
    try {
      var url = localStorage.getItem('aurum_sse_url');
      var key = localStorage.getItem('aurum_sse_key');
      if (url && key && !_connected) {
        _log('Auto-connecting from localStorage');
        window.__aurumSSEConnect(url, key);
      }
    } catch (e) {}
  })();
})();
