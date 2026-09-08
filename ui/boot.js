/* AurumOS boot shim — guarantees the pywebview API bridge is READY before any
 * page runs its data-loading code. Fixes the "blank page until reload" race:
 * pywebview injects window.pywebview.api asynchronously (empty {} first, then
 * populated + a 'pywebviewready' event). Pages that call the API from
 * window.onload / DOMContentLoaded used to fire too early and silently fail.
 *
 * This script MUST be the first <script> in <head>. It changes NO page logic —
 * it only delays each page's existing load handlers until the API is live.
 */
(function () {
  if (window.__aurumBoot) return;
  window.__aurumBoot = true;

  function apiLive() {
    return !!(window.pywebview && window.pywebview.api &&
              Object.keys(window.pywebview.api).length > 0);
  }

  // Public helper: run cb once the API is actually populated (or after a
  // generous safety timeout so a page never hangs forever).
  window.apiReady = function (cb) {
    if (typeof cb !== 'function') return;
    if (apiLive()) { try { cb(); } catch (e) { console.error(e); } return; }
    var fired = false;
    function go() {
      if (fired) return; fired = true;
      try { cb(); } catch (e) { console.error(e); }
    }
    window.addEventListener('pywebviewready', go, { once: true });
    var n = 0;
    var timer = setInterval(function () {
      if (apiLive()) { clearInterval(timer); go(); }
      else if (++n > 150) { clearInterval(timer); go(); } // ~15s hard stop
    }, 100);
  };

  // ── Shared live gold rate (/10g): any billing page publishes the moment
  // the rate is typed; the dashboard subscribes. Survives navigation via
  // localStorage. {v: number, t: epoch ms, src: 'pos'|'billing'}
  window.AurumOS = window.AurumOS || {};
  window.AurumOS.setLiveRate = function (v, src) {
    try {
      v = parseFloat(String(v).replace(/,/g, ''));
      if (!(v > 0)) return;
      localStorage.setItem('aurum_live_rate', JSON.stringify({ v: v, t: Date.now(), src: src || '' }));
    } catch (e) {}
  };
  window.AurumOS.getLiveRate = function () {
    try {
      var o = JSON.parse(localStorage.getItem('aurum_live_rate') || 'null');
      if (o && parseFloat(o.v) > 0) return { v: parseFloat(o.v), t: o.t || 0, src: o.src || '' };
    } catch (e) {}
    return null;
  };

  // ── Auto-defer window.onload (16+ pages use this) ─────────────────────
  var _onload = null;
  try {
    Object.defineProperty(window, 'onload', {
      configurable: true,
      get: function () { return _onload; },
      set: function (fn) { _onload = fn; }
    });
  } catch (e) { /* some engines forbid this — onload pages then run normally */ }

  window.addEventListener('load', function () {
    window.apiReady(function () {
      if (typeof _onload === 'function') {
        try { _onload(); } catch (e) { console.error(e); }
      }
    });
  });

  // ── Auto-defer DOMContentLoaded handlers (15+ pages use this) ─────────
  // Wrap ONLY the 'DOMContentLoaded' listener type; every other event passes
  // through untouched, so nothing else in the app is affected.
  var _docAdd = document.addEventListener.bind(document);
  document.addEventListener = function (type, listener, opts) {
    if (type === 'DOMContentLoaded' && typeof listener === 'function') {
      var wrapped = function (ev) {
        window.apiReady(function () {
          try { listener(ev); } catch (e) { console.error(e); }
        });
      };
      return _docAdd(type, wrapped, opts);
    }
    return _docAdd(type, listener, opts);
  };

  // Convenience event some pages can listen for directly.
  window.addEventListener('pywebviewready', function () {
    try { window.dispatchEvent(new CustomEvent('apiready')); } catch (e) {}
  });

  // ── WRAP pywebview.api.navigate to enforce feature gate ──────────
  window.addEventListener('apiready', function () {
    try {
      var api = window.pywebview && window.pywebview.api;
      if (api && typeof api.navigate === 'function' && !api._origNavigate) {
        api._origNavigate = api.navigate.bind(api);
        api.navigate = function (page) {
          var pageLower = String(page).toLowerCase().trim();
          var _PAGE_GATE = {
            'karigar.html':'karigar_vouchers','staff.html':'multi_staff',
            'chart_of_accounts.html':'full_accounts','accounting.html':'full_accounts',
            'ledger.html':'full_accounts','reports.html':'stock_med_reports',
            'network_manager.html':'lan_multi_pc'
          };
          var fid = _PAGE_GATE[pageLower];
          if (fid && window.AurumOS && typeof window.AurumOS.hasFeature === 'function') {
            if (!window.AurumOS.hasFeature(fid)) {
              if (window.AurumOS.showUpgradeModal) window.AurumOS.showUpgradeModal(fid);
              return;
            }
          }
          return api._origNavigate(page);
        };
      }
    } catch (e) {}
  });

  // ── Feature update listener — unlock/lock sidebar on plan change ────
  window.addEventListener('subscription:features_updated', function (e) {
    try {
      var detail = e.detail || {};
      var features = detail.features || [];
      var plan = detail.plan || 'lite';
      // Unlock sidebar items that are now available
      var locked = document.querySelectorAll('.sb-item.feature-locked');
      locked.forEach(function (item) {
        var fid = item.getAttribute('data-feature');
        if (fid && features.indexOf(fid) !== -1) {
          item.classList.remove('feature-locked');
          item.style.opacity = '';
          item.style.cursor = '';
          item.removeAttribute('title');
          var page = item.getAttribute('data-page') || '';
          item.onclick = function() { if(window.sideNavigate) sideNavigate(page); };
          var lock = item.querySelector('.sb-lock');
          if (lock) lock.remove();
          console.log('[AurumOS] Unlocked: ' + fid);
        }
      });
      // Lock sidebar items that are no longer available
      var all = document.querySelectorAll('.sb-item[data-feature]');
      all.forEach(function (item) {
        var fid = item.getAttribute('data-feature');
        if (!fid) return;
        if (features.indexOf(fid) === -1 && !item.classList.contains('feature-locked')) {
          item.classList.add('feature-locked');
          item.style.opacity = '0.4';
          item.style.cursor = 'not-allowed';
          item.title = 'Upgrade to unlock';
          item.onclick = function(ev) {
            ev.preventDefault();
            ev.stopPropagation();
            if(window.AurumOS && window.AurumOS.showUpgradeModal) window.AurumOS.showUpgradeModal(fid);
          };
          if (!item.querySelector('.sb-lock')) {
            var lock = document.createElement('span');
            lock.className = 'sb-lock';
            lock.innerHTML = '&#128274;';
            lock.style.cssText = 'font-size:0.6rem;margin-left:auto;opacity:0.5;';
            item.appendChild(lock);
          }
          console.log('[AurumOS] Locked: ' + fid);
        }
      });
      console.log('[AurumOS] Plan: ' + plan + ' features: ' + features.length);
    } catch (e) { console.error('[AurumOS] Feature update error:', e); }
  });

  // ── Auto-inject subscription.js on every page ────────────────────────
  // Loads subscription module so feature gating and UI overlays work everywhere.
  window.addEventListener('apiready', function () {
    if (window.__aurumSub) return; // already loaded
    try {
      var s = document.createElement('script');
      s.src = 'subscription.js';
      s.onload = function () {
        if (window.__aurumSubInit && !window.__aurumSub) {
          window.__aurumSubInit();
        }
      };
      document.head.appendChild(s);
    } catch (e) { /* ignore */ }

    // Auto-inject SSE stream on every page (plan changes apply everywhere)
    if (!window.__aurumSSELoaded) {
      window.__aurumSSELoaded = true;
      try {
        var sse = document.createElement('script');
        sse.src = 'sse_stream.js';
        sse.onload = function () {
          // sse_stream.js auto-connects from localStorage on load
          // Also try stored creds as backup
          if (window.__aurumSSEConnect) {
            var url = localStorage.getItem('aurum_sse_url');
            var key = localStorage.getItem('aurum_sse_key');
            if (url && key) {
              window.__aurumSSEConnect(url, key);
            }
          }
        };
        document.head.appendChild(sse);
      } catch (e) { /* ignore */ }
    }

    // Auto-inject urgent-message module on every page (toasts/modal work
    // everywhere, not just the dashboard — inbox lives on the dashboard).
    if (!window.__aurumUrgent && !window.__aurumUrgentLoading) {
      window.__aurumUrgentLoading = true;
      try {
        var urg = document.createElement('script');
        urg.src = 'urgent.js';
        urg.onload = function () { window.__aurumUrgentLoading = false; };
        urg.onerror = function () { window.__aurumUrgentLoading = false; };
        document.head.appendChild(urg);
      } catch (e) { window.__aurumUrgentLoading = false; }
    }
  });

  // ── Feature Gate Utility ─────────────────────────────────────────────
  window.AurumOS = window.AurumOS || {};
  window.AurumOS.hasFeature = function(featureId) {
    if (window.__aurumSubHasFeature) return window.__aurumSubHasFeature(featureId);
    return true;
  };
  window.AurumOS.requireFeature = function(featureId, redirectPage) {
    if (window.__aurumSubRequireFeature) {
      var allowed = window.__aurumSubRequireFeature(featureId);
      if (!allowed && redirectPage) window.location.href = redirectPage;
      return allowed;
    }
    return true;
  };
  window.AurumOS.getPlan = function() {
    if (window.__aurumSubState) {
      var s = window.__aurumSubState();
      return s.effective_plan || s.plan || 'lite';
    }
    return 'lite';
  };
  window.AurumOS.getFeatures = function() {
    if (window.__aurumSubState) {
      var s = window.__aurumSubState();
      return s.features || [];
    }
    return [];
  };
  window.AurumOS.isPro = function() { return ['pro','enterprise'].indexOf(window.AurumOS.getPlan()) !== -1; };
  window.AurumOS.isEnterprise = function() { return window.AurumOS.getPlan() === 'enterprise'; };

  // ── PAGE-LEVEL FEATURE GATE ────────────────────────────────────────
  // Maps HTML page filenames to required feature IDs. If the user's plan
  // does not include the feature, they are redirected to dashboard.html
  // immediately — this is the BACKEND-level enforcement that cannot be
  // bypassed by direct URL navigation.
  var _PAGE_FEATURE_GATE = {
    'karigar.html':             'karigar_vouchers',
    'staff.html':               'multi_staff',
    'chart_of_accounts.html':   'full_accounts',
    'accounting.html':          'full_accounts',
    'ledger.html':              'full_accounts',
    'reports.html':             'stock_med_reports',
    'network_manager.html':     'lan_multi_pc'
  };

  // ── SYNCHRONOUS page gate — runs BEFORE any page renders ──────────
  (function _syncPageGate() {
    try {
      var page = location.pathname.replace(/.*[\\/]/, '').toLowerCase().trim();
      var requiredFeature = _PAGE_FEATURE_GATE[page];
      if (!requiredFeature) return;
      var raw = localStorage.getItem('aurum_sub_state');
      if (!raw) return;
      var st = JSON.parse(raw);
      var feats = st.features || [];
      if (feats.indexOf(requiredFeature) === -1) {
        window.location.href = 'dashboard.html';
      }
    } catch (e) {}
  })();

  function _enforcePageFeatureGate() {
    try {
      var page = location.pathname.replace(/.*[\\/]/, '').toLowerCase().trim();
      if (!page || page === 'dashboard.html' || page === 'login.html' ||
          page === 'setup.html' || page === 'revoked.html' || page === 'expiry.html' || page === 'settings.html') return;
      var requiredFeature = _PAGE_FEATURE_GATE[page];
      if (!requiredFeature) return;
      // Synchronous check via localStorage (persists after first load)
      try {
        var raw = localStorage.getItem('aurum_sub_state');
        if (raw) {
          var st = JSON.parse(raw);
          var feats = st.features || [];
          if (feats.indexOf(requiredFeature) === -1) {
            console.warn('[AurumOS] Page "' + page + '" requires "' + requiredFeature + '" (localStorage) — redirecting');
            window.location.href = 'dashboard.html';
            return;
          }
        }
      } catch (e) {}
      // Fallback: check via subscription API
      if (window.AurumOS && typeof window.AurumOS.hasFeature === 'function') {
        if (!window.AurumOS.hasFeature(requiredFeature)) {
          console.warn('[AurumOS] Page "' + page + '" requires "' + requiredFeature + '" — redirecting to dashboard');
          window.location.href = 'dashboard.html';
        }
      }
    } catch (e) { console.error('[AurumOS] Page gate error:', e); }
  }

  // Run the page gate after subscription state is loaded
  window.addEventListener('subscription:features_updated', function () {
    _enforcePageFeatureGate();
  });
  // Also run on delays as fallback (in case event fires before listener is ready)
  setTimeout(_enforcePageFeatureGate, 1000);
  setTimeout(_enforcePageFeatureGate, 3000);
  // Run on DOMContentLoaded immediately — catches fast navigation
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTimeout(_enforcePageFeatureGate, 500);
    });
  } else {
    setTimeout(_enforcePageFeatureGate, 500);
  }

  // Feature name map for human-readable display
  var _FEATURE_NAMES = {
    'lan_multi_pc':'LAN Multi PC','karigar_vouchers':'Karigar Vouchers','touch_groups':'Touch Groups',
    'full_accounts':'Full Accounts','tag_audit':'Tag Audit','stock_med_reports':'Stock Med Reports',
    'tsc_network_printing':'TSC Network Printing','multi_staff':'Multi Staff',
    'analytics_dashboard':'Analytics Dashboard','year_close':'Year Close','bastion_enhanced':'Bastion Enhanced',
    'cloud_sync':'Cloud Sync','fleet_bastion':'Fleet Bastion','customer_loyalty':'Customer Loyalty',
    'bastion_ai':'Bastion AI','nexus_management':'Nexus Management','bridge_server':'Bridge Server',
    'custom_db_location':'Custom DB Location','priority_support':'Priority Support','api_integration':'API Integration'
  };
  var _FEATURE_PLAN = {
    'lan_multi_pc':'enterprise','karigar_vouchers':'pro','touch_groups':'pro','full_accounts':'pro',
    'tag_audit':'pro','stock_med_reports':'pro','tsc_network_printing':'pro','multi_staff':'pro',
    'analytics_dashboard':'pro','year_close':'pro','bastion_enhanced':'pro',
    'cloud_sync':'enterprise','fleet_bastion':'enterprise','customer_loyalty':'enterprise',
    'bastion_ai':'enterprise','nexus_management':'enterprise','bridge_server':'enterprise',
    'custom_db_location':'enterprise','priority_support':'enterprise','api_integration':'enterprise'
  };

  // Show upgrade modal when locked feature is clicked
  window.AurumOS.showUpgradeModal = function(featureId) {
    var existing = document.getElementById('aurum-upgrade-modal');
    if (existing) existing.remove();
    var name = _FEATURE_NAMES[featureId] || featureId;
    var required = _FEATURE_PLAN[featureId] || 'pro';
    var current = window.AurumOS.getPlan();
    var planData = {
      pro:       { price: '\u20B97,000',  onboarding: '\u20B935,000', emoji: '\uD83E\uDD48', accent: '#2563eb' },
      enterprise:{ price: '\u20B915,000', onboarding: '\u20B975,000', emoji: '\uD83E\uDD47', accent: '#b45309' }
    };
    var pd = planData[required] || planData.pro;
    var overlay = document.createElement('div');
    overlay.id = 'aurum-upgrade-modal';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(15,23,42,0.45);'
      + 'backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML = ''
      + '<style>@keyframes subModalIn{from{opacity:0;transform:scale(0.96) translateY(8px);}to{opacity:1;transform:none;}}</style>'
      + '<div style="background:#fff;border-radius:14px;max-width:380px;width:90%;overflow:hidden;'
      + 'box-shadow:0 24px 64px rgba(15,23,42,0.18);animation:subModalIn 0.25s ease-out;">'

      // Header
      + '  <div style="background:' + pd.accent + ';padding:22px 20px;text-align:center;position:relative;">'
      + '    <div style="font-size:1.8rem;margin-bottom:6px;">' + pd.emoji + '</div>'
      + '    <div style="font-size:1rem;font-weight:700;color:#fff;letter-spacing:-0.3px;">Feature Locked</div>'
      + '    <div style="font-size:0.65rem;color:rgba(255,255,255,0.7);margin-top:4px;">Upgrade to unlock ' + name + '</div>'
      + '  </div>'

      // Body
      + '  <div style="padding:20px;">'

      // Current plan
      + '    <div style="display:flex;align-items:center;justify-content:space-between;background:#f8fafb;border-radius:8px;padding:10px 14px;margin-bottom:10px;">'
      + '      <div>'
      + '        <div style="font-size:0.5rem;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#94a3b8;">Current Plan</div>'
      + '        <div style="font-size:0.82rem;font-weight:700;color:#0f172a;text-transform:uppercase;margin-top:2px;">' + current + '</div>'
      + '      </div>'
      + '      <div style="font-size:0.55rem;color:#64748b;text-align:right;">Requires<br><strong style="color:' + pd.accent + ';">' + required.toUpperCase() + '</strong></div>'
      + '    </div>'

      // Pricing breakdown
      + '    <div style="border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin-bottom:14px;">'
      + '      <div style="font-size:0.5rem;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#64748b;margin-bottom:8px;">' + required.toUpperCase() + ' Plan Pricing</div>'

      + '      <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #f1f5f9;">'
      + '        <span style="font-size:0.7rem;color:#64748b;">Subscription (year)</span>'
      + '        <span style="font-size:0.78rem;font-weight:700;color:#0f172a;">' + pd.price + '</span>'
      + '      </div>'

      + '      <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #f1f5f9;">'
      + '        <span style="font-size:0.7rem;color:#64748b;">Onboarding Fee (one-time)</span>'
      + '        <span style="font-size:0.78rem;font-weight:700;color:#0f172a;">' + pd.onboarding + '</span>'
      + '      </div>'

      + '      <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0 2px;">'
      + '        <span style="font-size:0.65rem;font-weight:600;color:#0f172a;">Total to Start</span>'
      + '        <span style="font-size:0.88rem;font-weight:700;color:' + pd.accent + ';">' + pd.price + ' + ' + pd.onboarding + '</span>'
      + '      </div>'
      + '    </div>'

      // Buttons
      + '    <div style="display:flex;gap:8px;">'
      + '      <button onclick="document.getElementById(\'aurum-upgrade-modal\').remove()" '
      + '        style="flex:1;height:36px;border-radius:8px;border:1px solid #e2e8f0;'
      + '        background:#f8fafb;color:#64748b;font-size:0.72rem;font-weight:600;cursor:pointer;font-family:DM Sans,sans-serif;">Close</button>'
      + '      <button onclick="AurumOS._openWhatsApp(\'' + required + '\',\'' + name + '\')" '
      + '        style="flex:1;height:36px;border-radius:8px;border:none;'
      + '        background:' + pd.accent + ';color:#fff;font-size:0.72rem;font-weight:600;cursor:pointer;font-family:DM Sans,sans-serif;">Contact to Upgrade</button>'
      + '    </div>'

      + '  </div>'
      + '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
  };

  window.AurumOS._openWhatsApp = function(plan, feature) {
    var biz = 'User';
    try { biz = window.AurumOS.getPlan() ? (window.__aurumSubState().business || 'User') : 'User'; } catch(e) {}
    var msg = 'Hi, I want to upgrade to ' + plan.toUpperCase() + ' plan.\n\n'
      + 'Business: ' + biz + '\n'
      + 'Feature needed: ' + feature + '\n'
      + 'Please share the upgrade payment link.';
    var url = 'https://wa.me/917041126244?text=' + encodeURIComponent(msg);
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_url) {
      window.pywebview.api.open_url(url);
    } else { window.open(url, '_blank'); }
  };

  // Lock a DOM element — dims it and shows upgrade modal on click
  window.AurumOS.lockFeature = function(elementId, featureId, tooltipText) {
    var el = document.getElementById(elementId);
    if (!el) return;
    if (!window.AurumOS.hasFeature(featureId)) {
      el.style.opacity = '0.4';
      el.style.position = 'relative';
      el.style.cursor = 'not-allowed';
      el.title = tooltipText || (_FEATURE_NAMES[featureId] || featureId) + ' — requires ' + (_FEATURE_PLAN[featureId] || 'pro').toUpperCase() + ' plan';
      var badge = document.createElement('div');
      badge.style.cssText = 'position:absolute;top:4px;right:4px;background:#dc2626;color:#fff;'
        + 'font-size:0.48rem;padding:2px 6px;border-radius:3px;font-weight:700;z-index:10;'
        + 'font-family:Inter,sans-serif;letter-spacing:0.5px;';
      badge.innerText = (_FEATURE_PLAN[featureId] || 'PRO').toUpperCase();
      el.style.overflow = 'visible';
      el.appendChild(badge);
      el.addEventListener('click', function(e) {
        e.preventDefault(); e.stopPropagation();
        window.AurumOS.showUpgradeModal(featureId);
      }, true);
    }
  };

  // Lock sidebar button — dims, shows lock icon, modal on click
  window.AurumOS.lockSidebarBtn = function(btnSelector, featureId) {
    var btns = document.querySelectorAll(btnSelector);
    btns.forEach(function(btn) {
      if (!window.AurumOS.hasFeature(featureId)) {
        btn.style.opacity = '0.4';
        btn.style.cursor = 'not-allowed';
        btn.title = (_FEATURE_NAMES[featureId] || featureId) + ' — requires ' + (_FEATURE_PLAN[featureId] || 'pro').toUpperCase() + ' plan';
        btn.classList.add('feature-locked');
        btn.setAttribute('data-feature', featureId);
        btn.addEventListener('click', function(e) {
          e.preventDefault(); e.stopPropagation();
          window.AurumOS.showUpgradeModal(featureId);
        }, true);
      }
    });
  };
})();

/* ════════════════════════════════════════════════════════════════════
   GLOBAL ALERT / CONFIRM / TOAST SYSTEM (Blue/Offwhite Theme)
   ════════════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function(){
  var _css = document.createElement('style');
  _css.textContent = [
    '@keyframes aurFadeIn{from{opacity:0}to{opacity:1}}',
    '@keyframes aurLift{from{opacity:0;transform:translateY(16px) scale(.97)}to{opacity:1;transform:none}}',
    '#__aurum-alert-overlay{display:none;position:fixed;inset:0;z-index:999999;background:rgba(15,23,42,0.45);backdrop-filter:blur(4px);align-items:center;justify-content:center;animation:aurFadeIn .2s ease}',
    '#__aurum-alert-overlay.open{display:flex}',
    '#__aurum-alert-box{background:#fff;border-radius:14px;width:400px;max-width:90vw;overflow:hidden;box-shadow:0 20px 60px rgba(15,23,42,0.18),0 4px 16px rgba(15,23,42,0.08);animation:aurLift .3s cubic-bezier(.16,1,.3,1)}',
    '#__aurum-alert-head{padding:20px 24px 16px;display:flex;align-items:flex-start;gap:14px}',
    '#__aurum-alert-icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1.1rem;flex-shrink:0}',
    '.__aurum-alert-info #__aurum-alert-icon{background:rgba(37,99,235,0.08);color:#2563eb}',
    '.__aurum-alert-error #__aurum-alert-icon{background:rgba(220,38,38,0.08);color:#dc2626}',
    '.__aurum-alert-warn #__aurum-alert-icon{background:rgba(217,119,6,0.08);color:#d97706}',
    '.__aurum-alert-success #__aurum-alert-icon{background:rgba(22,163,74,0.08);color:#16a34a}',
    '#__aurum-alert-title{font-family:"DM Sans",system-ui,sans-serif;font-size:0.88rem;font-weight:700;color:#0f172a;line-height:1.3}',
    '#__aurum-alert-body{font-family:"DM Sans",system-ui,sans-serif;font-size:0.78rem;color:#64748b;line-height:1.6;padding:0 24px 20px}',
    '#__aurum-alert-foot{padding:0 24px 20px;display:flex;justify-content:flex-end}',
    '#__aurum-alert-ok{height:36px;padding:0 22px;border-radius:8px;border:none;background:#2563eb;color:#fff;font-family:"DM Sans",system-ui,sans-serif;font-size:0.75rem;font-weight:600;cursor:pointer;transition:all .15s}',
    '#__aurum-alert-ok:hover{background:#1d4ed8;box-shadow:0 4px 12px rgba(37,99,235,0.3);transform:translateY(-1px)}',
    '#__aurum-confirm-cancel{height:36px;padding:0 18px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;color:#64748b;font-family:"DM Sans",system-ui,sans-serif;font-size:0.75rem;font-weight:600;cursor:pointer;transition:all .15s}',
    '#__aurum-confirm-cancel:hover{border-color:#94a3b8;color:#0f172a}',
    '#__aurum-confirm-ok{height:36px;padding:0 18px;border-radius:8px;border:none;background:#dc2626;color:#fff;font-family:"DM Sans",system-ui,sans-serif;font-size:0.75rem;font-weight:600;cursor:pointer;transition:all .15s;margin-left:8px}',
    '#__aurum-confirm-ok:hover{background:#b91c1c;box-shadow:0 4px 12px rgba(220,38,38,0.3);transform:translateY(-1px)}',
    '#__aurum-confirm-ok.aurum-green{background:#16a34a}',
    '#__aurum-confirm-ok.aurum-green:hover{background:#15803d;box-shadow:0 4px 12px rgba(22,163,74,0.3)}',
    '#__aurum-toast{position:fixed;top:24px;right:24px;z-index:999998;display:flex;align-items:center;gap:10px;padding:12px 20px;border-radius:10px;background:#fff;border:1px solid #e2e8f0;box-shadow:0 8px 30px rgba(15,23,42,0.1);font-family:"DM Sans",system-ui,sans-serif;font-size:0.76rem;font-weight:500;color:#0f172a;transform:translateX(calc(100% + 30px));transition:transform .35s cubic-bezier(.16,1,.3,1),opacity .3s;max-width:400px;opacity:0}',
    '#__aurum-toast.show{transform:translateX(0);opacity:1}',
    '#__aurum-toast .aurum-toast-icon{width:28px;height:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:0.85rem;flex-shrink:0}',
    '#__aurum-toast.aurum-t-ok .aurum-toast-icon{background:rgba(22,163,74,0.08);color:#16a34a}',
    '#__aurum-toast.aurum-t-err .aurum-toast-icon{background:rgba(220,38,38,0.08);color:#dc2626}',
    '#__aurum-toast.aurum-t-warn .aurum-toast-icon{background:rgba(217,119,6,0.08);color:#d97706}',
    '#__aurum-toast.aurum-t-info .aurum-toast-icon{background:rgba(37,99,235,0.08);color:#2563eb}',
    '#__aurum-toast.aurum-t-ok{border-color:rgba(22,163,74,0.2)}',
    '#__aurum-toast.aurum-t-err{border-color:rgba(220,38,38,0.2)}',
    '#__aurum-toast.aurum-t-warn{border-color:rgba(217,119,6,0.2)}',
    '#__aurum-toast.aurum-t-info{border-color:rgba(37,99,235,0.2)}',
    '.modal-overlay{background:rgba(15,23,42,0.45)!important;backdrop-filter:blur(4px)!important;animation:aurFadeIn .2s ease}',
    '.overlay,.modal-bg,.modal-backdrop,.dlg-overlay,.modal-wrap,.ov,.loading-overlay,.prog-overlay,.tp-overlay{background:rgba(15,23,42,0.45)!important;backdrop-filter:blur(4px)!important;animation:aurFadeIn .2s ease}',
    '.modal-overlay.open,.modal-overlay.show,.modal-overlay.active{display:flex!important}',
    '.overlay.open,.overlay.active{display:flex!important}',
    '.modal-bg.open,.modal-bg.active{display:flex!important}',
    '.modal-backdrop.open,.modal-backdrop.active{display:flex!important}',
    '.dlg-overlay.open,.dlg-overlay.active{display:flex!important}',
    '.modal-wrap.open,.modal-wrap.active{display:flex!important}',
    '.ov.open,.ov.active{display:flex!important}',
    '.loading-overlay.open,.loading-overlay.active{display:flex!important}',
    '.prog-overlay.open,.prog-overlay.active{display:flex!important}',
    '.tp-overlay.open,.tp-overlay.active{display:flex!important}',
    '.toast,.copy-toast{background:#fff!important;color:#0f172a!important;border:1px solid #e2e8f0!important;border-radius:10px!important;box-shadow:0 8px 30px rgba(15,23,42,0.1)!important;font-family:"DM Sans",system-ui,sans-serif!important}',
    '.t-ok{background:#fff!important;color:#0f172a!important;border:1px solid rgba(22,163,74,0.2)!important}',
    '.t-err{background:#fff!important;color:#dc2626!important;border:1px solid rgba(220,38,38,0.2)!important}',
    '.toast.success{border-color:rgba(22,163,74,0.2)!important}',
    '.toast.error{border-color:rgba(220,38,38,0.2)!important}',
    '.toast-ok{background:#fff!important;color:#0f172a!important;border:1px solid rgba(22,163,74,0.2)!important;border-radius:10px!important}',
    '.toast-err{background:#fff!important;color:#dc2626!important;border:1px solid rgba(220,38,38,0.2)!important;border-radius:10px!important}',
    '@media (prefers-reduced-motion: reduce){#__aurum-alert-overlay,#__aurum-alert-box,#__aurum-toast{animation:none!important;transition:none!important}}'
  ].join('');
  document.head.appendChild(_css);

  var ov = document.createElement('div');
  ov.id = '__aurum-alert-overlay';
  ov.innerHTML = '<div id="__aurum-alert-box" class="__aurum-alert-info">'
    + '<div id="__aurum-alert-head"><div id="__aurum-alert-icon">!</div><div id="__aurum-alert-title"></div></div>'
    + '<div id="__aurum-alert-body"></div>'
    + '<div id="__aurum-alert-foot"><button id="__aurum-alert-ok">OK</button></div>'
    + '</div>';
  document.body.appendChild(ov);

  var _alertResolve = null;
  var _alertBox = document.getElementById('__aurum-alert-box');
  var _alertTitle = document.getElementById('__aurum-alert-title');
  var _alertBody = document.getElementById('__aurum-alert-body');
  var _alertIcon = document.getElementById('__aurum-alert-icon');
  var _alertOk = document.getElementById('__aurum-alert-ok');

  function _closeAlert() {
    ov.classList.remove('open');
    if (_alertResolve) { var r = _alertResolve; _alertResolve = null; r(); }
  }
  _alertOk.addEventListener('click', _closeAlert);
  ov.addEventListener('click', function(e) { if (e.target === ov) _closeAlert(); });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && ov.classList.contains('open')) { e.preventDefault(); _closeAlert(); }
  });

  var _icons = { info: '\u2139\uFE0F', error: '\u274C', warn: '\u26A0\uFE0F', success: '\u2705' };

  window.alert = function(msg, type) {
    type = type || 'info';
    _alertBox.className = '__aurum-alert-' + type;
    _alertTitle.textContent = type === 'error' ? 'Error' : type === 'warn' ? 'Warning' : type === 'success' ? 'Success' : 'Notice';
    _alertBody.textContent = String(msg == null ? '' : msg);
    _alertIcon.textContent = _icons[type] || _icons.info;
    ov.classList.add('open');
    _alertOk.focus();
    return new Promise(function(resolve) { _alertResolve = resolve; });
  };
  window.aurumAlert = function(title, msg, type) {
    type = type || 'info';
    _alertBox.className = '__aurum-alert-' + type;
    _alertTitle.textContent = String(title || 'Notice');
    _alertBody.textContent = String(msg == null ? '' : msg);
    _alertIcon.textContent = _icons[type] || _icons.info;
    ov.classList.add('open');
    _alertOk.focus();
    return new Promise(function(resolve) { _alertResolve = resolve; });
  };

  var cov = document.createElement('div');
  cov.id = '__aurum-alert-overlay';
  cov.style.zIndex = '1000000';
  cov.innerHTML = '<div id="__aurum-alert-box" class="__aurum-alert-warn">'
    + '<div id="__aurum-alert-head"><div id="__aurum-alert-icon">\u26A0\uFE0F</div><div id="__aurum-alert-title">Confirm</div></div>'
    + '<div id="__aurum-alert-body"></div>'
    + '<div id="__aurum-alert-foot">'
    + '<button id="__aurum-confirm-cancel">Cancel</button>'
    + '<button id="__aurum-confirm-ok">Confirm</button>'
    + '</div></div>';
  document.body.appendChild(cov);

  var _confirmResolve = null;
  var _confirmBody = cov.querySelector('#__aurum-alert-body');
  var _confirmOkBtn = cov.querySelector('#__aurum-confirm-ok');
  var _confirmCancelBtn = cov.querySelector('#__aurum-confirm-cancel');
  var _confirmTitle = cov.querySelector('#__aurum-alert-title');
  var _confirmIcon = cov.querySelector('#__aurum-alert-icon');
  var _confirmBox = cov.querySelector('#__aurum-alert-box');

  function _closeConfirm(val) {
    cov.classList.remove('open');
    if (_confirmResolve) { var r = _confirmResolve; _confirmResolve = null; r(val); }
  }
  _confirmOkBtn.addEventListener('click', function() { _closeConfirm(true); });
  _confirmCancelBtn.addEventListener('click', function() { _closeConfirm(false); });
  cov.addEventListener('click', function(e) { if (e.target === cov) _closeConfirm(false); });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && cov.classList.contains('open')) { e.preventDefault(); _closeConfirm(false); }
    if (e.key === 'Enter' && cov.classList.contains('open')) { e.preventDefault(); _closeConfirm(true); }
  });

  window.aurumConfirm = function(msg, opts) {
    opts = opts || {};
    _confirmBody.textContent = String(msg == null ? '' : msg);
    _confirmTitle.textContent = opts.title || 'Confirm';
    _confirmIcon.textContent = opts.icon || _icons.warn;
    _confirmOkBtn.textContent = opts.confirmText || 'Confirm';
    _confirmOkBtn.className = opts.type === 'success' ? 'aurum-green' : '';
    _confirmBox.className = opts.type === 'success' ? '__aurum-alert-success' : opts.type === 'info' ? '__aurum-alert-info' : '__aurum-alert-warn';
    cov.classList.add('open');
    _confirmCancelBtn.focus();
    return new Promise(function(resolve) { _confirmResolve = resolve; });
  };

  var tEl = document.createElement('div');
  tEl.id = '__aurum-toast';
  tEl.innerHTML = '<span class="aurum-toast-icon"></span><span class="aurum-toast-msg"></span>';
  document.body.appendChild(tEl);
  var _toastTimer = null;
  var _tIcons = { ok: '\u2705', err: '\u274C', warn: '\u26A0\uFE0F', info: '\u2139\uFE0F', success: '\u2705', error: '\u274C' };

  window.showToast = function(msg, type, dur) {
    type = type || 'ok';
    dur = dur || 3000;
    if (_toastTimer) clearTimeout(_toastTimer);
    tEl.className = 'aurum-t-' + (type === 'success' ? 'ok' : type === 'error' ? 'err' : type);
    tEl.querySelector('.aurum-toast-icon').textContent = _tIcons[type] || _tIcons.info;
    tEl.querySelector('.aurum-toast-msg').textContent = String(msg == null ? '' : msg);
    tEl.classList.add('show');
    _toastTimer = setTimeout(function() { tEl.classList.remove('show'); }, dur);
  };
});
