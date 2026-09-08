/* ══════════════════════════════════════════════════════════════════════════════
   AurumOS Subscription — Client-side enforcement
   Handles: startup check, periodic sync (30 min), feature gating, UI overlays
   365-day subscription timeline
   ══════════════════════════════════════════════════════════════════════════════ */
(function () {
  if (window.__aurumSub) return;
  window.__aurumSub = true;

  // ── Lite features (same as Python LITE_FEATURES) ────────────────────
  var LITE_FEATURES = [
    'local_mode', 'billing_retail', 'stock_entry', 'product_master',
    'client_ledger', 'staff_login_lockout', 'tag_printing_local',
    'scale_weighing', 'sales_report_basic', 'bastion_core'
  ];

  // ── Enterprise-only features (must match Python ENTERPRISE_ONLY_FEATURES) ──
  // The admin server may still list these under Pro — strip them client-side
  // so the plan tier stays authoritative.
  var ENTERPRISE_ONLY_FEATURES = [
    'lan_multi_pc', 'cloud_sync', 'fleet_bastion', 'customer_loyalty', 'bastion_ai',
    'nexus_management', 'bridge_server', 'custom_db_location',
    'priority_support', 'api_integration'
  ];

  function _enforcePlanTier(plan, features) {
    var list = features || [];
    if (typeof list === 'string') list = list.split(',');
    if (String(plan || 'lite').toLowerCase() === 'enterprise') return list.slice();
    return list.filter(function (f) { return ENTERPRISE_ONLY_FEATURES.indexOf(f) === -1; });
  }

  // ── State ────────────────────────────────────────────────────────────
  var _state = {
    valid: false,
    status: 'unknown',
    effective_plan: 'lite',
    features: [],
    subscription: { status: 'unknown', expires_at: null, remaining_days: 0,
                    grace_remaining_days: 0, renewal_amount: 0 },
    business: '', owner: '', plan: 'lite', is_trial: false, trial_end_ms: null,
    last_sync: 0
  };
  var _syncTimer = null;
  var _bootstrapped = false;
  var _pollTimer = null;
  var _pollInflight = false;

  // ── INSTANT LOAD from localStorage (set by Python on plan change) ──
  // This ensures feature gating works on page load BEFORE any API call
  // NOTE: status-based redirects (expired/revoked) are handled by startupCheck()
  // + _renderUI(), NOT from localStorage, to avoid stale data causing wrong redirects.
  try {
    var cached = localStorage.getItem('aurum_sub_state');
    if (cached) {
      var parsed = JSON.parse(cached);
      if (parsed) {
        _applyState(parsed);
      }
    }
  } catch (e) {}

  function _log(m) { try { console.log('[SUB] ' + m); } catch (e) {} }
  function _err(m) { try { console.error('[SUB] ' + m); } catch (e) {} }

  // ── API bridge ───────────────────────────────────────────────────────
  function _api() {
    return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  }

  function _call(method) {
    var api = _api();
    if (!api || typeof api[method] !== 'function') return Promise.resolve(null);
    try {
      var result = api[method]();
      if (result && typeof result.then === 'function') {
        return result.catch(function (e) {
          _err('API call ' + method + ' failed: ' + e);
          return null;
        });
      }
      return Promise.resolve(result || null);
    } catch (e) {
      _err('API call ' + method + ' threw: ' + e);
      return Promise.resolve(null);
    }
  }

  // ── Startup check ────────────────────────────────────────────────────
  var _startupInProgress = false;
  function startupCheck() {
    if (_startupInProgress) return Promise.resolve(_state);
    _startupInProgress = true;
    return _call('subscription_startup_check').then(function (data) {
      _startupInProgress = false;
      if (data) {
        _applyState(data);
        _log('Startup: status=' + _state.status + ' plan=' + _state.effective_plan
             + ' features=' + _state.features.length);
        _renderUI();
        _startPeriodicSync();
        _startFilePolling();
        _startReactivationPolling();
        _fireEvent('features_updated');
        return _state;
      }
      return _state;
    }).catch(function (e) {
      _startupInProgress = false;
      _err('Startup check failed: ' + e);
      return _state;
    });
  }

  // ── Periodic sync (30 min) ───────────────────────────────────────────
  function _startPeriodicSync() {
    if (_syncTimer) clearInterval(_syncTimer);
    _syncTimer = setInterval(function () {
      _call('subscription_sync').then(function (data) {
        if (data) {
          var oldPlan = _state.effective_plan;
          _applyState(data);
          if (oldPlan !== _state.effective_plan) {
            _log('Plan changed: ' + oldPlan + ' -> ' + _state.effective_plan);
            _fireEvent('plan_changed');
          }
          _renderUI();
          _fireEvent('features_updated');
        }
      }).catch(function () {});
    }, 30 * 60 * 1000); // 30 minutes
    _log('Periodic sync started (30 min)');
  }

  // ── File-polling for auto plan changes (every 5s) ──────────────────
  // Checks if the state file timestamp changed (meaning plan changed server-side).
  // If changed, fetches full state and applies instantly — no page refresh needed.
  var _pollTimer = null;
  var _lastKnownTs = 0;

  function _startFilePolling() {
    if (_pollTimer) clearInterval(_pollTimer);
    _lastKnownTs = 0;
    _pollTimer = setInterval(function () {
      _call('subscription_check_update').then(function (result) {
        if (!result || !result.ts) return;
        if (_lastKnownTs === 0) {
          _lastKnownTs = result.ts;
          return;
        }
        if (result.ts !== _lastKnownTs) {
          _lastKnownTs = result.ts;
          _log('State file changed (ts=' + result.ts + ') — fetching new state');
          _call('subscription_startup_check').then(function (data) {
            if (data && data.features) {
              var oldPlan = _state.effective_plan;
              _applyState(data);
              if (oldPlan !== _state.effective_plan) {
                _log('Plan changed via poll: ' + oldPlan + ' -> ' + _state.effective_plan);
                _fireEvent('plan_changed');
              }
              _renderUI();
              _fireEvent('features_updated');
              _log('Features updated via poll: plan=' + _state.effective_plan + ' features=' + _state.features.length);
            }
          }).catch(function () {});
        }
      }).catch(function () {});
    }, 5000); // 5 seconds
    _log('File polling started (5s)');
  }

  // ── Reactivation polling (8s) — calls Python subscription_poll_check ──
  function _startReactivationPolling() {
    if (_pollTimer) clearInterval(_pollTimer);
    _pollTimer = setInterval(function () {
      if (_pollInflight) return;
      if (_state.status !== 'expired' && _state.status !== 'revoked' && _state.valid) {
        // Already active — skip poll
        return;
      }
      var wasActive = _state.valid && (_state.status === 'active' || _state.status === 'grace');
      _pollInflight = true;
      _call('subscription_poll_check').then(function (data) {
        _pollInflight = false;
        if (data && (data.status === 'active' || data.status === 'grace') && data.valid && !wasActive) {
          _log('Reactivation detected via poll');
          _applyState(data);
          _showReactivationToast(data.effective_plan);
          _navigateAwayFromRevoke();
          _renderUI();
          _fireEvent('features_updated');
        }
      }).catch(function () { _pollInflight = false; });
    }, 8000);
    _log('Reactivation polling started (8s)');
  }

  // ── Reactivation toast ───────────────────────────────────────────────
  function _showReactivationToast(plan) {
    var existing = document.getElementById('sub-reactivation-toast');
    if (existing) existing.remove();
    var toast = document.createElement('div');
    toast.id = 'sub-reactivation-toast';
    toast.style.cssText = 'position:fixed;top:24px;right:24px;z-index:100003;'
      + 'background:linear-gradient(135deg,#059669,#10b981);color:#fff;'
      + 'padding:14px 24px;border-radius:10px;max-width:380px;'
      + 'box-shadow:0 8px 32px rgba(5,150,105,0.3);font-family:var(--sans);'
      + 'animation:subSlideDown 0.3s ease-out;display:flex;align-items:center;gap:10px;';
    toast.innerHTML = ''
      + '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5">'
      + '  <path d="M20 6L9 17l-5-5"/>'
      + '</svg>'
      + '<div>'
      + '  <div style="font-weight:700;font-size:0.82rem;margin-bottom:2px;">License Reactivated</div>'
      + '  <div style="font-size:0.68rem;opacity:0.9;">Welcome back! Your ' + (plan || 'pro').toUpperCase() + ' plan is active.</div>'
      + '</div>';
    document.body.appendChild(toast);
    setTimeout(function () {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(20px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(function () { toast.remove(); }, 300);
    }, 5000);
  }

  // ── Navigate away from revoked/expiry.html on reactivation ────────────
  function _navigateAwayFromRevoke() {
    try {
      var page = location.pathname.replace(/.*[\\/]/, '').toLowerCase().trim();
      if (page === 'revoked.html' || page === 'expiry.html') {
        _log('Reactivated on ' + page + ' — navigating to login');
        if (window.pywebview && window.pywebview.api && window.pywebview.api.navigate) {
          window.pywebview.api.navigate('login.html');
        } else {
          window.location.href = 'login.html';
        }
      }
    } catch (e) {}
  }

  function syncNow() {
    return _call('subscription_sync').then(function (data) {
      if (data) { _applyState(data); _renderUI(); }
      return _state;
    });
  }

  // ── Feature gating ───────────────────────────────────────────────────
  function hasFeature(featureId) {
    // Block ALL features when expired/revoked/invalid — should be on revoked.html
    if (_state.status === 'expired' || _state.status === 'revoked' || !_state.valid) {
      return false;
    }
    // Grace period: only lite features allowed
    if (_state.status === 'grace') {
      return LITE_FEATURES.indexOf(featureId) !== -1;
    }
    return _state.features.indexOf(featureId) !== -1;
  }

  function checkFeature(featureId) {
    if (hasFeature(featureId)) return { allowed: true };
    var required = 'pro';
    var proFeatures = [
      'karigar_vouchers','touch_groups','full_accounts',
      'tag_audit','stock_med_reports','tsc_network_printing','multi_staff',
      'analytics_dashboard','year_close','bastion_enhanced'
    ];
    var entFeatures = ENTERPRISE_ONLY_FEATURES;
    if (proFeatures.indexOf(featureId) !== -1) required = 'pro';
    if (entFeatures.indexOf(featureId) !== -1) required = 'enterprise';
    return { allowed: false, required_plan: required, plan: _state.effective_plan };
  }

  function requireFeature(featureId, silent) {
    var check = checkFeature(featureId);
    if (check.allowed) return true;
    if (!silent) showUpgradeMessage(featureId, check.required_plan);
    return false;
  }

  // ── UI: Grace warning banner ─────────────────────────────────────────
  function _showGraceBanner() {
    var existing = document.getElementById('sub-grace-banner');
    if (existing) existing.remove();
    if (_state.status !== 'grace') return;
    var days = (_state.subscription && _state.subscription.grace_remaining_days) || 0;
    var banner = document.createElement('div');
    banner.id = 'sub-grace-banner';
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:100000;'
      + 'background:linear-gradient(135deg,#b45309,#d97706);color:#fff;'
      + 'padding:10px 24px;display:flex;align-items:center;justify-content:space-between;'
      + 'font-family:var(--sans);box-shadow:0 4px 16px rgba(180,83,9,0.3);';
    banner.innerHTML = ''
      + '<div style="display:flex;align-items:center;gap:10px;">'
      + '  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2">'
      + '    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
      + '    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>'
      + '  </svg>'
      + '  <span style="font-weight:600;font-size:0.82rem;">'
      + '    Subscription expired \u2014 <strong>' + days + ' grace day' + (days !== 1 ? 's' : '') + ' left. Only Lite features available.</strong>'
      + '  </span>'
      + '</div>'
      + '<button onclick="window.__aurumSubRenew()" style="background:#fff;color:#b45309;'
      + '  border:none;padding:7px 18px;border-radius:6px;font-weight:700;font-size:0.78rem;'
      + '  cursor:pointer;font-family:var(--sans);">Renew Now</button>';
    document.body.appendChild(banner);
  }

  // ── Grace enforcement: force features to LITE ────────────────────────
  function _applyGraceDegradation() {
    if (_state.status !== 'grace') return;
    _state.features = LITE_FEATURES.slice();
    _state.effective_plan = 'lite';
    try {
      localStorage.setItem('aurum_sub_state', JSON.stringify(_state));
    } catch (e) {}
    _log('Grace degradation: features forced to Lite (' + _state.features.length + ')');
  }

  // ── Redirect to revoked/expiry.html for expired/revoked ─────────────
  function _redirectToRevokeScreen() {
    var shouldRedirect = _state.status === 'expired' || _state.status === 'revoked' || !_state.valid;
    if (!shouldRedirect) return;

    var target = _state.status === 'expired' ? 'expiry.html' : 'revoked.html';

    // Don't redirect if already on the CORRECT page
    try {
      var page = location.pathname.replace(/.*[\\/]/, '').toLowerCase().trim();
      if (page === target || page === 'login.html' || page === 'setup.html') return;
    } catch (e) {}

    var target = _state.status === 'expired' ? 'expiry.html' : 'revoked.html';
    _log('Redirecting to ' + target + ' (status=' + _state.status + ', valid=' + _state.valid + ')');
    if (window.pywebview && window.pywebview.api && window.pywebview.api.navigate) {
      window.pywebview.api.navigate(target);
    } else {
      window.location.href = target;
    }
  }

  // ── UI: Subscription badge (top-right corner) ────────────────────────
  function _renderBadge() {
    var badge = document.getElementById('sub-status-badge');
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'sub-status-badge';
      document.body.appendChild(badge);
    }
    if (_state.status === 'unknown' || (_state.status === 'active' && _state.valid)) {
      badge.style.display = 'none';
      return;
    }
    // Hide badge on revoked/expiry.html (they have their own UI)
    try {
      var page = location.pathname.replace(/.*[\\/]/, '').toLowerCase().trim();
      if (page === 'revoked.html' || page === 'expiry.html') { badge.style.display = 'none'; return; }
    } catch (e) {}

    var color = '#d97706';
    var label = _state.effective_plan.toUpperCase();
    if (_state.status === 'grace') { color = '#b45309'; label += ' (Grace)'; }
    else if (_state.status === 'expired' || _state.status === 'revoked' || !_state.valid) {
      color = '#b91c1c'; label = 'EXPIRED';
    }
    badge.style.cssText = 'position:fixed;bottom:16px;left:16px;z-index:99999;'
      + 'background:' + color + ';color:#fff;padding:5px 12px;border-radius:6px;'
      + 'font-size:0.62rem;font-weight:700;font-family:var(--sans);letter-spacing:0.5px;'
      + 'box-shadow:0 2px 8px rgba(0,0,0,0.15);cursor:default;display:flex;align-items:center;gap:6px;';
    badge.innerHTML = '<span style="width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,0.7);"></span>'
      + label;
    badge.style.display = 'flex';
  }

  // ── UI: Remaining days tooltip ────────────────────────────────────────
  function getRemainingText() {
    var sub = _state.subscription || {};
    var days = sub.remaining_days || 0;
    if (days > 0) return days + ' day' + (days !== 1 ? 's' : '') + ' remaining';
    if (_state.status === 'grace') {
      var g = sub.grace_remaining_days || 0;
      return g + ' day' + (g !== 1 ? 's' : '') + ' grace remaining';
    }
    return '';
  }

  function getExpiryText() {
    var sub = _state.subscription || {};
    if (!sub.expires_at) return '';
    return new Date(sub.expires_at).toLocaleDateString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric'
    }).toUpperCase();
  }

  function getRenewalText() {
    var sub = _state.subscription || {};
    var amt = sub.renewal_amount || 0;
    return amt ? '\u20B9' + Number(amt).toLocaleString('en-IN') : '';
  }

  // ── Render all UI elements + enforce status ──────────────────────────
  function _renderUI() {
    if (_state.status === 'grace') {
      _applyGraceDegradation();
      _showGraceBanner();
    } else if (_state.status === 'expired' || _state.status === 'revoked' || !_state.valid) {
      _redirectToRevokeScreen();
    }

    _renderBadge();
  }

  // ── Upgrade message ──────────────────────────────────────────────────
  function showUpgradeMessage(featureId, requiredPlan) {
    var msg = 'This feature requires the ' + requiredPlan.toUpperCase() + ' plan.';
    var existing = document.getElementById('sub-upgrade-toast');
    if (existing) existing.remove();
    var toast = document.createElement('div');
    toast.id = 'sub-upgrade-toast';
    toast.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:100002;'
      + 'background:var(--ink);color:#fff;padding:18px 28px;border-radius:12px;'
      + 'box-shadow:0 12px 40px rgba(14,12,9,0.3);text-align:center;max-width:360px;'
      + 'animation:subModalIn 0.2s ease-out;';
    toast.innerHTML = ''
      + '<div style="font-family:var(--serif);font-size:1rem;margin-bottom:8px;">Upgrade Required</div>'
      + '<div style="font-size:0.82rem;color:rgba(255,255,255,0.7);margin-bottom:16px;">' + msg + '</div>'
      + '<button onclick="this.closest(\'#sub-upgrade-toast\').remove()" '
      + '  style="background:var(--gold);color:#fff;border:none;padding:8px 24px;border-radius:6px;'
      + '  font-weight:700;font-size:0.78rem;cursor:pointer;font-family:var(--sans);">OK</button>';
    document.body.appendChild(toast);
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 4000);
  }

  // ── Renew button handler ─────────────────────────────────────────────
  window.__aurumSubRenew = function () {
    // Try to open admin URL or show contact info
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_admin_renewal) {
        window.pywebview.api.open_admin_renewal();
      } else {
        window.open('https://aurum-os-admin.vercel.app', '_blank');
      }
    } catch (e) {
      window.open('https://aurum-os-admin.vercel.app', '_blank');
    }
  };

  // ── SSE event handler (called from Python) ───────────────────────────
  window.__onSubscriptionEvent = function (event, data) {
    _log('Event: ' + event + ' data: ' + JSON.stringify(data));
    if (event === 'plan_changed' || event === 'plan_change') {
      // Apply state directly from Python data — no pywebview callback needed
      if (data && data.features) {
        _applyState(data);
        _log('Plan applied: ' + _state.effective_plan + ' features=' + _state.features.length);
        _renderUI();
        try {
          window.dispatchEvent(new CustomEvent('subscription:features_updated', {
            detail: { plan: _state.effective_plan, features: _state.features.slice() }
          }));
        } catch (e) {}
      } else {
        // Fallback: re-fetch from Python if no data provided
        syncNow().catch(function (e) { _err('Sync after plan_change failed: ' + e); });
      }
    } else if (event === 'revoked') {
      _state.valid = false;
      _state.status = 'revoked';
      _state.effective_plan = 'lite';
      _state.features = [];
      _applyState(_state);
      _redirectToRevokeScreen();
    } else if (event === 'expired') {
      _state.valid = false;
      _state.status = 'expired';
      _state.effective_plan = 'lite';
      _state.features = [];
      _applyState(_state);
      _redirectToRevokeScreen();
    } else if (event === 'status_changed' || event === 'sync_source') {
      // sync_source: just log, don't call back into pywebview
      if (data && data.source) {
        _log('Sync source: ' + data.source + (data.hours_old ? ' (' + data.hours_old.toFixed(1) + 'h old)' : ''));
      }
    } else if (event === 'reactivated') {
      _log('Reactivation event received');
      if (data && data.features) {
        _applyState(data);
        _showReactivationToast(data.effective_plan);
        _navigateAwayFromRevoke();
        _renderUI();
        _fireEvent('features_updated');
      } else {
        syncNow().then(function () {
          if (_state.status === 'active' || _state.status === 'grace') {
            _showReactivationToast(_state.effective_plan);
            _navigateAwayFromRevoke();
          }
          _renderUI();
        });
      }
    }
  };

  // ── Internal helpers ─────────────────────────────────────────────────
  function _applyState(data) {
    _state.valid = data.valid || false;
    _state.status = data.status || 'unknown';
    _state.effective_plan = data.effective_plan || 'lite';
    _state.features = _enforcePlanTier(data.effective_plan || data.plan, data.features);
    _state.business = data.business || '';
    _state.owner = data.owner || '';
    _state.plan = data.plan || 'lite';
    _state.is_trial = data.is_trial || false;
    _state.trial_end_ms = data.trial_end_ms || null;
    _state.subscription = data.subscription || _state.subscription;
    _state.last_sync = Date.now();

    // ── Status-based feature override ──
    if (_state.status === 'grace') {
      // Grace period: degrade to Lite features only
      _state.features = LITE_FEATURES.slice();
      _state.effective_plan = 'lite';
    } else if (_state.status === 'expired' || _state.status === 'revoked' || !_state.valid) {
      // Expired/revoked: no features at all
      _state.features = [];
      _state.effective_plan = 'lite';
    }

    // Persist to localStorage so next page load has correct features instantly
    try {
      _state._ts = Date.now();
      localStorage.setItem('aurum_sub_state', JSON.stringify(_state));
    } catch (e) {}
  }

  function _fireEvent(name) {
    try {
      window.dispatchEvent(new CustomEvent('subscription:' + name, { detail: _state }));
    } catch (e) {}
  }

  // ── CSS animation injection ──────────────────────────────────────────
  (function () {
    var style = document.createElement('style');
    style.textContent = '@keyframes subModalIn{from{opacity:0;transform:scale(0.95) translateY(10px);}to{opacity:1;transform:none;}}';
    document.head.appendChild(style);
  })();

  // ── Public API ───────────────────────────────────────────────────────
  window.__aurumSubState = function () { return JSON.parse(JSON.stringify(_state)); };
  window.__aurumSubHasFeature = hasFeature;
  window.__aurumSubCheckFeature = checkFeature;
  window.__aurumSubRequireFeature = requireFeature;
  window.__aurumSubSync = syncNow;
  window.__aurumSubGetRemaining = getRemainingText;
  window.__aurumSubGetExpiry = getExpiryText;
  window.__aurumSubGetRenewal = getRenewalText;

  // ── Auto-init if called from Python ──────────────────────────────────
  window.__aurumSubInit = function (key) {
    if (_bootstrapped) return;
    _bootstrapped = true;
    _log('Auto-init (key=' + (key ? key.substring(0, 10) + '...' : 'none') + ')');
    startupCheck();
  };

  // ── Bootstrap: wait for pywebview then auto-init ─────────────────────
  function _bootstrap() {
    if (_bootstrapped) return;
    if (window.pywebview && window.pywebview.api &&
        typeof window.pywebview.api.subscription_startup_check === 'function') {
      _bootstrapped = true;
      _log('Bootstrap: pywebview ready, running startup check');
      startupCheck();
    }
  }

  window.addEventListener('pywebviewready', _bootstrap);
  // Also try polling in case pywebviewready already fired
  var pollCount = 0;
  var poll = setInterval(function () {
    if (_bootstrapped || pollCount > 50) { clearInterval(poll); return; }
    pollCount++;
    _bootstrap();
  }, 200);
})();
