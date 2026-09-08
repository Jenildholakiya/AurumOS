/**
 * AurumOS Sidebar — v8 (Role-Based Staff Access Matrix)
 * Wholesale (AU-) and Retail (AR-) dual mode.
 * Staff access: reads allowed pages from get_staff_session() and
 * redirects immediately if current page is not permitted.
 */

// ── NAV DEFINITIONS ──────────────────────────────────────────────────────────

var NAV_ADMIN = [
    { id:'dashboard', label:'Dashboard',    icon:'grid',      page:'dashboard.html',       section:'Main'    },
    { id:'inventory', label:'Inventory',    icon:'box',       page:'inventory.html',       section:'Main'    },
    { id:'karigar',   label:'Karigar',      icon:'orders',    page:'karigar.html',         section:'Main'    },
    { id:'billing',   label:'Create Bill',  icon:'file-text', page:'billing.html',         section:'Main'    },
    { id:'history',   label:'History',      icon:'clock',     page:'history.html',         section:'Main'    },
    { id:'vouchers',  label:'Voucher History', icon:'receipt', page:'voucher_history.html', section:'Main'    },
    { id:'accounting',label:'Accounting',   icon:'book',      page:'accounting.html',      section:'Finance' },
    { id:'cashbank',  label:'Cash & Bank',  icon:'wallet',    page:'cash_bank.html',       section:'Finance' },
    { id:'coa',       label:'Chart of Accounts', icon:'list',  page:'chart_of_accounts.html', section:'Finance' },
    { id:'ledger',    label:'Ledger',       icon:'book',      page:'ledger.html',          section:'Finance' },
    { id:'party',     label:'Party',        icon:'users',     page:'party.html',           section:'Finance' },
    { id:'staff',     label:'Staff',        icon:'user',      page:'staff.html',           section:'Finance' },
    { id:'network',   label:'Network',      icon:'wifi',      page:'network_manager.html', section:'System'  },
];

var NAV_RETAIL = [
    { id:'dashboard', label:'Dashboard',    icon:'grid',      page:'dashboard.html',       section:'Main'     },
    { id:'gold-rate', label:'Gold Rate',    icon:'trending',  page:'gold_rate.html',       section:'Main'     },
    { id:'billing',   label:'POS / Billing',icon:'pos',       page:'pos_billing.html',     section:'Main'     },
    { id:'inventory', label:'Inventory',    icon:'box',       page:'inventory.html',       section:'Main'     },
    { id:'karigar',   label:'Karigar',      icon:'orders',    page:'karigar.html',         section:'Business' },
    { id:'customers', label:'Customers',    icon:'users',     page:'customer_account.html',section:'Business' },
    { id:'old-gold',  label:'Old Gold',     icon:'old-gold',  page:'old_gold.html',        section:'Business' },
    { id:'reports',   label:'Reports',      icon:'bar-chart', page:'reports.html',         section:'Business' },
    { id:'staff',     label:'Staff',        icon:'user',      page:'staff.html',           section:'System'   },
    { id:'settings',  label:'Settings',     icon:'settings',  page:'settings.html',        section:'System'   },
];

// Page meta — updated with all sibling pages for complete cluster routing maps
var PAGE_META = {
    'dashboard.html':       { id:'dashboard', label:'Dashboard',    icon:'grid',      section:'Main'    },
    'billing.html':         { id:'billing',   label:'Create Bill',  icon:'file-text', section:'Main'    },
    'tag_audit.html':       { id:'tag-audit', label:'Tag Audit',    icon:'tag',       section:'Main'    },
    'pos_billing.html':     { id:'billing',   label:'POS / Billing',icon:'pos',       section:'Main'    },
    'inventory.html':       { id:'inventory', label:'Inventory',    icon:'box',       section:'Main'    },
    'karigar.html':         { id:'karigar',   label:'Karigar',      icon:'orders',    section:'Main'    },
    'katti.html':           { id:'katti',     label:'Katti Batches', icon:'box',       section:'Main'    },
    'uchak_inward.html':    { id:'uchak',     label:'Uchak Inward',  icon:'box',       section:'Main'    },
    'stock_ledger.html':    { id:'stock',     label:'Stock Ledger', icon:'box',       section:'Main'    },
    'history.html':         { id:'history',   label:'History',      icon:'clock',     section:'Main'    },
    'voucher_history.html': { id:'vouchers',  label:'Voucher History', icon:'receipt', section:'Main'    },
    'party.html':           { id:'party',     label:'Party',        icon:'users',     section:'Finance' },
    'accounting.html':      { id:'accounting',label:'Accounting',   icon:'book',      section:'Finance' },
    'cash_bank.html':       { id:'cashbank',  label:'Cash & Bank',  icon:'wallet',    section:'Finance' },
    'chart_of_accounts.html':{id:'coa',       label:'Chart of Accounts', icon:'list',  section:'Finance' },
    'ledger.html':          { id:'ledger',    label:'Ledger',       icon:'book',      section:'Finance' },
    'gold_rate.html':       { id:'gold-rate', label:'Gold Rate',    icon:'trending',  section:'Main'    },
    'customers.html':       { id:'customers', label:'Customers',    icon:'users',     section:'Business'},
    'old_gold.html':        { id:'old-gold',  label:'Old Gold',     icon:'old-gold',  section:'Business'},
    'reports.html':         { id:'reports',   label:'Reports',      icon:'bar-chart', section:'Business'},
    'staff.html':           { id:'staff',     label:'Staff',        icon:'user',      section:'System'  },
    'network_manager.html': { id:'network',   label:'Network',      icon:'wifi',      section:'System'  },
    'settings.html':        { id:'settings',  label:'Settings',     icon:'settings',  section:'System'  },
};

// ── SVG ICONS ────────────────────────────────────────────────────────────────

var ICONS = {
    'grid':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
    'box':       '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
    'file-text': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>',
    'clock':     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    'users':     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    'book':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    'wifi':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>',
    'settings':  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    'user':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    'trending':  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
    'pos':       '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/><line x1="7" y1="8" x2="7.01" y2="8"/><line x1="12" y1="8" x2="17" y2="8"/><line x1="7" y1="12" x2="7.01" y2="12"/><line x1="12" y1="12" x2="17" y2="12"/></svg>',
    'old-gold':  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><polyline points="23 20 23 14 17 14"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>',
    'bar-chart': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>',
    'orders':    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>',
    'receipt':   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1z"/><line x1="8" y1="7" x2="16" y2="7"/><line x1="8" y1="11" x2="16" y2="11"/><line x1="8" y1="15" x2="13" y2="15"/></svg>',
    'wallet':    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5z"/><path d="M16 12h.01"/><path d="M3 8h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H3"/></svg>',
    'list':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
    'tag':       '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
};

// ── LOCAL STORAGE HELPERS ────────────────────────────────────────────────────

function _cacheGet(key, fallback) {
    try { var v = localStorage.getItem(key); return (v !== null && v !== '') ? v : fallback; }
    catch(e) { return fallback; }
}
function _cacheSet(key, value) {
    try { localStorage.setItem(key, String(value)); } catch(e) {}
}

// ── STATE READERS ────────────────────────────────────────────────────────────

function _getRole() {
    try {
        var u = (location.search.match(/[?&]role=([^&]+)/) || [])[1] || '';
        var l = _cacheGet('aurum_user_role', '');
        var r = u || l || 'admin';
        if (u) _cacheSet('aurum_user_role', u);
        return r;
    } catch(e) { return 'admin'; }
}

function _getUsername() {
    try {
        var u = (location.search.match(/[?&]user=([^&]+)/) || [])[1] || '';
        var l = _cacheGet('aurum_active_user', '');
        var r = u || l || '';
        if (u) _cacheSet('aurum_active_user', u);
        return r;
    } catch(e) { return ''; }
}

function _getSoftwareType() {
    try {
        var u = (location.search.match(/[?&]type=([^&]+)/) || [])[1] || '';
        if (u === 'retail' || u === 'wholesale') { _cacheSet('aurum_software_type', u); return u; }
        var c = _cacheGet('aurum_software_type', '');
        return (c === 'retail' || c === 'wholesale') ? c : 'wholesale';
    } catch(e) { return 'wholesale'; }
}

function _currentPage() {
    try { return location.pathname.replace(/.*[\\/]/, '').toLowerCase().trim() || 'dashboard.html'; }
    catch(e) { return 'dashboard.html'; }
}

function _initials(name) {
    return (name || 'OS').split(' ')
        .filter(function(w){ return w.length > 0; })
        .map(function(w){ return w[0]; })
        .join('').substring(0, 2).toUpperCase() || 'OS';
}

// ── NAVIGATION HELPERS ───────────────────────────────────────────────────────

function sideNavigate(page) {
    // ── BLOCK: feature-gated pages on lite plan ──
    var featureId = _FEATURE_MAP[page];
    if (featureId && typeof window.AurumOS !== 'undefined' && window.AurumOS.hasFeature) {
        if (!window.AurumOS.hasFeature(featureId)) {
            if (window.AurumOS.showUpgradeModal) window.AurumOS.showUpgradeModal(featureId);
            return;
        }
    }
    var role = _getRole();
    var type = _getSoftwareType();
    var user = _getUsername();
    try {
        var base = location.href.replace(/[^\/\\]*(\?.*)?$/, '');
        location.href = base + page + '?role=' + role + '&type=' + type + (user ? '&user=' + user : '');
    } catch(e) {
        try { if (window.pywebview && window.pywebview.api) window.pywebview.api.navigate(page); }
        catch(e2) {}
    }
}

function toggleSidebar() {
    var c = document.body.classList.toggle('sidebar-collapsed');
    _cacheSet('sb_collapsed', c ? '1' : '0');
}

// ── FEATURE GATE MAP ────────────────────────────────────────────────────────
// Maps sidebar page → required feature ID. If the feature is not in the
// current plan, the item gets dimmed and pointer-events:none.
var _FEATURE_MAP = {
    'karigar.html':        'karigar_vouchers',
    'analytics_dashboard': 'analytics_dashboard',
    'staff.html':          'multi_staff',
    'chart_of_accounts.html': 'full_accounts',
    'accounting.html':     'full_accounts',
    'ledger.html':         'full_accounts',
    'reports.html':        'stock_med_reports',
    'network_manager.html':'lan_multi_pc'
};

// Plan tier required for each gated page — used for the lock tooltip.
var _FEATURE_PLANS = {
    'karigar.html':        'Pro',
    'analytics_dashboard': 'Pro',
    'staff.html':          'Pro',
    'chart_of_accounts.html': 'Pro',
    'accounting.html':     'Pro',
    'ledger.html':         'Pro',
    'reports.html':        'Pro',
    'network_manager.html':'Enterprise'
};

function _lockTooltip(page) {
    var plan = _FEATURE_PLANS[page];
    return plan ? ('Upgrade to ' + plan + ' to unlock') : 'Upgrade to unlock';
}

function _isFeatureLocked(page) {
    var featureId = _FEATURE_MAP[page];
    if (!featureId) return false;
    // Check subscription state directly — works even before AurumOS namespace is ready
    if (typeof window.__aurumSubHasFeature === 'function') {
        return !window.__aurumSubHasFeature(featureId);
    }
    if (typeof window.AurumOS !== 'undefined' && window.AurumOS.hasFeature) {
        return !window.AurumOS.hasFeature(featureId);
    }
    return false;
}

// ── RE-APPLY LOCKS after subscription state loads ──────────────────
// Sidebar renders first (fast), subscription loads later. This listener
// re-applies lock icons + click handlers once features are known.
function _reapplyFeatureLocks() {
    var items = document.querySelectorAll('.sb-item[data-feature]');
    items.forEach(function(item) {
        var fid = item.getAttribute('data-feature');
        if (!fid) return;
        var locked = false;
        if (typeof window.__aurumSubHasFeature === 'function') {
            locked = !window.__aurumSubHasFeature(fid);
        } else if (window.AurumOS && window.AurumOS.hasFeature) {
            locked = !window.AurumOS.hasFeature(fid);
        }
        if (locked) {
            item.classList.add('feature-locked');
            item.style.opacity = '0.4';
            item.style.cursor = 'not-allowed';
            item.title = 'Upgrade to unlock';
            item.onclick = function(ev) {
                ev.preventDefault();
                ev.stopPropagation();
                if (window.AurumOS && window.AurumOS.showUpgradeModal) window.AurumOS.showUpgradeModal(fid);
            };
            if (!item.querySelector('.sb-lock')) {
                var lock = document.createElement('span');
                lock.className = 'sb-lock';
                lock.innerHTML = '&#128274;';
                lock.style.cssText = 'font-size:0.6rem;margin-left:auto;opacity:0.5;';
                item.appendChild(lock);
            }
        } else {
            item.classList.remove('feature-locked');
            item.style.opacity = '';
            item.style.cursor = '';
            item.removeAttribute('title');
            var lockEl = item.querySelector('.sb-lock');
            if (lockEl) lockEl.remove();
        }
    });
}
window.addEventListener('subscription:features_updated', _reapplyFeatureLocks);
window.addEventListener('apiready', function() { setTimeout(_reapplyFeatureLocks, 1000); });
setTimeout(_reapplyFeatureLocks, 2000);
setTimeout(_reapplyFeatureLocks, 5000);

// ── BUILD NAV FROM ITEM ARRAY ────────────────────────────────────────────────

function _buildNav(items, activePage, compact) {
    if (!items || !items.length) return '<nav class="sb-nav"></nav>';
    var sections = {}, order = [];
    items.forEach(function(item) {
        var sec = item.section || 'Main';
        if (!sections[sec]) { sections[sec] = []; order.push(sec); }
        sections[sec].push(item);
    });

    var ns = compact ? ' style="flex:0 0 auto;"' : '';
    var h = '<nav class="sb-nav"' + ns + '>';
    order.forEach(function(sec, si) {
        if (!sections[sec] || sections[sec].length === 0) return;
        if (si > 0) h += '<div class="sb-divider"></div>';
        h += '<div class="sb-section-label">' + sec + '</div>';
        sections[sec].forEach(function(item) {
            var cur = activePage || _cacheGet('aurum_active_page', '');
            var isActive = item.id === cur || item.page === cur || _currentPage() === item.page;
            var locked = _isFeatureLocked(item.page);
            var cls = 'sb-item' + (isActive ? ' active' : '') + (locked ? ' feature-locked' : '');
            var clickAction = locked
                ? 'event.preventDefault();event.stopPropagation();if(window.AurumOS)window.AurumOS.showUpgradeModal(\'' + (_FEATURE_MAP[item.page] || '') + '\');'
                : 'sideNavigate(\'' + item.page + '\')';
            h += '<div class="' + cls + '" data-tip="' + item.label + '"'
               + ' data-feature="' + (_FEATURE_MAP[item.page] || '') + '"'
               + ' data-page="' + item.page + '"'
               + (locked ? ' title="' + (_FEATURE_NAMES[item.page] || 'Upgrade to unlock') + '"' : '')
               + ' onclick="' + clickAction + '">'
               + '<div class="sb-icon">' + (ICONS[item.icon] || '') + '</div>'
               + '<span class="sb-label">' + item.label + '</span>'
               + (locked ? '<span class="sb-lock">&#128274;</span>' : '')
               + '</div>';
        });
    });
    h += '</nav>';
    return h;
}

// ── BUILD ITEMS FROM MODULE PERMISSION BUNDLES ───────────────────────────────

function _pagesNavItems(pages) {
    var items = [];
    (pages || []).forEach(function(p) {
        var fname = String(p).replace(/.*[\\/]/, '').toLowerCase().trim();
        var meta  = PAGE_META[fname];
        if (meta) {
            items.push({ id: meta.id, label: meta.label, icon: meta.icon, page: fname, section: meta.section });
        } else if (fname) {
            var label = fname.replace('.html','').replace(/_/g,' ');
            label = label.charAt(0).toUpperCase() + label.slice(1);
            items.push({ id: fname, label: label, icon: 'file-text', page: fname, section: 'Main' });
        }
    });
    return items;
}

// ── STAFF ACCESS ENFORCEMENT ROUTER ──────────────────────────────────────────

function _enforceStaffAccess(pages) {
    if (!pages || !pages.length) return;

    var clean = pages.map(function(p) {
        return String(p).replace(/.*[\\/]/, '').toLowerCase().trim();
    });

    var cur = _currentPage();
    if (!cur || cur === 'login.html' || cur === 'setup.html') return;

    if (clean.indexOf(cur) !== -1) return;

    var dest = clean[0];
    _dbg('[STAFF ACCESS] Boundary Restriction: "' + cur + '" unauthorized. Forwarding back to ' + dest);
    sideNavigate(dest);
}

// ── RENDER NAV IN SIDEBAR CONTAINER ─────────────────────────────────────────

function _replaceNav(items, activePage) {
    var container = document.getElementById('sidebar-container');
    if (!container) return;
    var navEl = container.querySelector('.sb-nav');
    if (navEl) {
        var newNav = document.createElement('div');
        newNav.innerHTML = _buildNav(items, activePage, items.length <= 3);
        var newNavEl = newNav.firstChild;
        navEl.parentNode.replaceChild(newNavEl, navEl);
    }
}

// ── MAIN RENDER ──────────────────────────────────────────────────────────────

// Pages whose sidebar is staff-only. Admin/owner see the page full-width
// with no sidebar at all. Add more filenames here as needed.
var STAFF_ONLY_PAGES = ['tag_audit.html'];

function _renderSidebar(activePage) {
    var container = document.getElementById('sidebar-container');
    if (!container) return;

    var role = _getRole();
    var page = (typeof _currentPage === 'function') ? _currentPage() : '';

    // Staff-only pages: render the sidebar for staff only. Non-staff roles
    // (admin / owner) get the page full-width with no sidebar.
    if (STAFF_ONLY_PAGES.indexOf(page) !== -1 && role !== 'staff') {
        try { document.body.classList.add('no-sidebar'); } catch(e) {}
        try { container.style.display = 'none'; } catch(e) {}
        return;
    }
    try { document.body.classList.remove('no-sidebar'); } catch(e) {}
    try { container.style.display = ''; } catch(e) {}

    try {
        if (_cacheGet('sb_collapsed','') === '1') {
            document.body.classList.add('sidebar-collapsed');
        }
    } catch(e) {}

    var role     = _getRole();
    var swType   = _getSoftwareType();
    var isRetail = swType === 'retail';
    var isStaff  = role === 'staff';

    // Staff: paint instantly from cached allowed pages so a reload is fast
    // and the sidebar is fully rendered without waiting for pywebview.
    var staffPages = [];
    if (isStaff) {
        try { staffPages = JSON.parse(_cacheGet('aurum_staff_pages', '[]')) || []; } catch(e) { staffPages = []; }
        // Staff never get Settings, even if a stale cache somehow lists it.
        staffPages = staffPages.filter(function(p) {
            return String(p).toLowerCase().indexOf('settings.html') === -1;
        });
    }
    var items = isStaff ? _pagesNavItems(staffPages) : (isRetail ? NAV_RETAIL : NAV_ADMIN);

    var ownerName = _cacheGet('aurum_owner_name', 'Director');
    var ownerInit = _initials(ownerName);
    var appVer    = _cacheGet('aurum_app_version', '1.0');

    var accessLevel = isStaff ? 'STAFF NODE' : (isRetail ? 'RETAIL NODE' : 'SYSTEM NODE');

    var h = '';

    h += '<div class="sb-logo">'
       + '<div class="sb-logo-mark"><span>Au</span></div>'
       + '<div class="sb-logo-text">'
       + '<div class="sb-logo-name">Aurum<em>OS</em></div>'
       + '<div class="sb-logo-sub">' + (isRetail ? 'Retail POS' : 'Jewelry ERP') + '</div>'
       + '</div></div>';

    h += '<div class="sb-terminal-label">Terminal Navigation</div>';

    h += _buildNav(items, activePage, false);

    h += '<div class="sb-footer" style="margin-top:auto;">'
       + '<div id="sb-owner-wrap" onclick="sbToggleLogout()" class="sb-owner-btn">'
       + '<div class="sb-owner-btn-left">'
       + '<div class="sb-access-avatar" id="sb-owner-avatar">' + ownerInit + '</div>'
       + '<div class="sb-access-info">'
       + '<div class="sb-access-name" id="sb-owner-name">' + ownerName + '</div>'
       + '<div class="sb-access-role">' + accessLevel + '</div>'
       + '</div></div>'
       + '<svg id="sb-owner-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;color:#94a3b8;transition:transform 0.2s;">'
       + '<path d="M18 15l-6-6-6 6"/></svg>'
       + '</div>'
       + '<div class="sb-footer-meta">'
       + '<span id="sb-staff-name" style="font-size:0.56rem;font-weight:500;color:#94a3b8;letter-spacing:0.3px;">'
       + (isStaff ? _cacheGet('aurum_staff_name','Staff') : '') + '</span>'
       + (isStaff ? '<span style="width:3px;height:3px;border-radius:50%;background:#94a3b8;"></span>' : '')
       + '<span id="sb-ver-pill" style="font-size:0.52rem;font-weight:500;color:#94a3b8;font-family:var(--sb-mono);">v' + appVer + '</span>'
       + '</div>'
       + '<button class="sb-collapse-btn" onclick="toggleSidebar()">'
       + '<span class="sb-collapse-icon">&#8592;</span>'
       + '<span class="sb-collapse-text">Collapse</span>'
       + '</button>'
       + '</div>';

    container.innerHTML = h;

    _buildLogoutPopup(ownerName, isRetail, isStaff);

    document.addEventListener('click', function(e) {
        var p = document.getElementById('sb-logout-pop');
        var w = document.getElementById('sb-owner-wrap');
        if (!p || p.style.display === 'none') return;
        if (w && w.contains(e.target)) return;
        if (p && p.contains(e.target)) return;
        _closePopup();
    });
}

// ── LOGOUT POPUP ─────────────────────────────────────────────────────────────

function _buildLogoutPopup(ownerName, isRetail, isStaff) {
    var old = document.getElementById('sb-logout-pop');
    if (old) old.parentNode.removeChild(old);

    var pop = document.createElement('div');
    pop.id = 'sb-logout-pop';
    pop.style.cssText = 'display:none;position:fixed;z-index:99999;background:#ffffff;border-radius:12px;'
        + 'padding:8px;box-shadow:0 -8px 32px rgba(15,23,42,0.18);'
        + 'border:1px solid #e2e8f0;min-width:200px;';

    var modeTag = isRetail
        ? '<span style="font-size:0.5rem;font-weight:700;padding:2px 7px;border-radius:10px;'
          + 'background:rgba(37,99,235,0.08);color:#2563eb;border:1px solid rgba(37,99,235,0.18);'
          + 'margin-left:6px;letter-spacing:0.5px;">AR · RETAIL</span>'
        : '<span style="font-size:0.5rem;font-weight:700;padding:2px 7px;border-radius:10px;'
          + 'background:rgba(37,99,235,0.08);color:#2563eb;border:1px solid rgba(37,99,235,0.18);'
          + 'margin-left:6px;letter-spacing:0.5px;">AR · WHOLESALE</span>';

    var roleLabel = isStaff ? 'Staff Member' : (isRetail ? 'Retail Owner' : 'System Owner');

    pop.innerHTML = '<div style="padding:8px 10px 10px;border-bottom:1px solid #f1f5f9;margin-bottom:6px;">'
        + '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:2px;margin-bottom:2px;">'
        + '<div id="sb-pop-name" style="font-size:0.78rem;font-weight:600;color:#0f172a;">' + ownerName + '</div>'
        + modeTag + '</div>'
        + '<div style="font-size:0.56rem;color:#64748b;font-weight:500;">' + roleLabel + '</div></div>'
        + (isStaff ? '' :
           '<button onclick="sbOpenSettings()" style="width:100%;padding:9px 12px;margin-bottom:6px;'
        + 'background:#f8fafb;border:1px solid #e2e8f0;border-radius:8px;'
        + 'font-family:inherit;font-size:0.74rem;font-weight:500;cursor:pointer;'
        + 'display:flex;align-items:center;gap:8px;color:#475569;transition:all 0.13s;" '
        + 'onmouseover="this.style.background=\'rgba(37,99,235,0.06)\';this.style.borderColor=\'rgba(37,99,235,0.2)\';this.style.color=\'#2563eb\';" '
        + 'onmouseout="this.style.background=\'#f8fafb\';this.style.borderColor=\'#e2e8f0\';this.style.color=\'#475569\';">'
        + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        + '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>Settings</button>')
        + '<button onclick="sbLogout()" style="width:100%;padding:9px 12px;'
        + 'background:rgba(220,38,38,0.06);border:1px solid rgba(220,38,38,0.18);border-radius:8px;'
        + 'font-family:inherit;font-size:0.74rem;font-weight:600;cursor:pointer;'
        + 'display:flex;align-items:center;gap:8px;color:#dc2626;transition:all 0.13s;" '
        + 'onmouseover="this.style.background=\'rgba(220,38,38,0.10)\';this.style.borderColor=\'rgba(220,38,38,0.3)\';" '
        + 'onmouseout="this.style.background=\'rgba(220,38,38,0.06)\';this.style.borderColor=\'rgba(220,38,38,0.18)\';">'
        + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">'
        + '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
        + '<polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>Sign Out</button>';

    document.body.appendChild(pop);
}

function _dbg(msg) {
    try { console.log('[Sidebar] ' + msg); } catch(e) {}
}

function _fillDynamic() {
    var attempts = 0;
    var t = setInterval(function() {
        attempts++;
        if (window.pywebview && window.pywebview.api) {
            clearInterval(t);

            try {
                window.pywebview.api.get_dynamic_greeting &&
                window.pywebview.api.get_dynamic_greeting().then(function(g) {
                    if (!g) return;
                    var n = g.owner_title || g.business_name || 'Director';
                    var ne = document.getElementById('sb-owner-name');
                    var pe = document.getElementById('sb-pop-name');
                    var av = document.getElementById('sb-owner-avatar');
                    if (ne) ne.innerText = n;
                    if (pe) pe.innerText = n;
                    if (av) av.innerText = _initials(n);
                    _cacheSet('aurum_owner_name', n);
                }).catch(function(){});
            } catch(e) {}

            try {
                window.pywebview.api.get_current_version &&
                window.pywebview.api.get_current_version().then(function(v) {
                    if (!v) return;
                    var el = document.getElementById('sb-ver-pill');
                    if (el) el.innerText = 'v' + v;
                    _cacheSet('aurum_app_version', v);
                }).catch(function(){});
            } catch(e) {}
            // Silent auto-updates change the version mid-session — repaint
            // the pill immediately instead of showing a stale number.
            try {
                window.addEventListener('aurum-auto-updated', function(e) {
                    var v=(e.detail&&e.detail.version)||'';
                    if (!v) {
                        try {
                            window.pywebview.api.get_current_version &&
                            window.pywebview.api.get_current_version().then(function(rv) {
                                if (!rv) return;
                                var el2 = document.getElementById('sb-ver-pill');
                                if (el2) el2.innerText = 'v' + rv;
                                _cacheSet('aurum_app_version', rv);
                            }).catch(function(){});
                        } catch(err) {}
                        return;
                    }
                    var el = document.getElementById('sb-ver-pill');
                    if (el) el.innerText = 'v' + v;
                    _cacheSet('aurum_app_version', v);
                });
            } catch(e) {}

            if (_getRole() === 'staff') {
                _loadStaffAccess();
            }
        }
        if (attempts > 120) clearInterval(t);
    }, 100);
}

var _staffAccessLoaded = false;
try { _staffAccessLoaded = false; } catch(e) {}

function _loadStaffAccess() {
    if (_staffAccessLoaded) return;
    var api = window.pywebview.api;

    if (typeof api.get_staff_session === 'function') {
        api.get_staff_session().then(function(res) {
            if (!res) return;
            _applyStaffAccess(res);
        }).catch(function(e) {
            _dbg('get_staff_session error: ' + e);
            _loadStaffAccessFallback();
        });
        return;
    }
    _loadStaffAccessFallback();
}

function _loadStaffAccessFallback() {
    var api = window.pywebview.api;
    var username = _getUsername();
    if (!username || !api.get_all_staff) return;

    api.get_all_staff().then(function(staff) {
        if (!staff || !staff.length) return;
        var me = null;
        for (var i = 0; i < staff.length; i++) {
            if ((staff[i].username || '').toLowerCase() === username.toLowerCase()) {
                me = staff[i];
                break;
            }
        }
        if (!me) return;
        _applyStaffAccess({
            permissions: me.permissions || (me.allow_inventory ? ['inventory'] : ['billing']),
            username: me.username || username,
        });
    }).catch(function(){});
}

function _applyStaffAccess(res) {
    if (_staffAccessLoaded) return;
    _staffAccessLoaded = true;

    var swType = _getSoftwareType();
    var rawPages = res.allowed_pages || res.pages || res.access_pages || [];
    var perms = res.permissions || [];
    var pagesStr = JSON.stringify(rawPages).toLowerCase();
    var permsStr = JSON.stringify(perms).toLowerCase();

    var hasInventory = permsStr.indexOf('inventory') !== -1 ||
                       pagesStr.indexOf('inventory.html') !== -1 ||
                       pagesStr.indexOf('stock_ledger.html') !== -1 ||
                       (res.allow_inventory && String(res.allow_inventory) === '1');

    var hasBilling = permsStr.indexOf('billing') !== -1 ||
                     pagesStr.indexOf('billing.html') !== -1 ||
                     pagesStr.indexOf('pos_billing.html') !== -1 ||
                     (res.allow_billing && String(res.allow_billing) === '1');

    var hasKarigar = permsStr.indexOf('karigar') !== -1 || pagesStr.indexOf('karigar.html') !== -1;

    var expandedPages = [];

    if (hasInventory || hasKarigar) {
        if (swType === 'retail') {
            expandedPages = expandedPages.concat(['inventory.html', 'karigar.html']);
        } else {
            expandedPages = expandedPages.concat(['inventory.html', 'karigar.html', 'uchak_stock_entry.html']);
        }
    }

    if (hasBilling) {
        if (swType === 'retail') {
            expandedPages = expandedPages.concat(['pos_billing.html', 'tag_audit.html']);
        } else {
            expandedPages = expandedPages.concat(['billing.html', 'tag_audit.html']);
        }
    }

    if (!expandedPages.length) {
        expandedPages = swType === 'retail' ? ['pos_billing.html'] : ['billing.html'];
    }

    _dbg('Expanded clusters authorized: ' + JSON.stringify(expandedPages));

    var sn = document.getElementById('sb-staff-name');
    if (sn) {
        sn.innerText = res.username || res.name || _getUsername() || 'Staff';
    }

    var newPagesJson = JSON.stringify(expandedPages);
    var cachedJson   = _cacheGet('aurum_staff_pages', '');
    _cacheSet('aurum_staff_pages', newPagesJson);
    _cacheSet('aurum_staff_name', res.username || res.name || '');

    // Only rebuild the nav if the page set actually changed. On a reload the
    // sidebar was already painted synchronously from the cache above, so this
    // avoids a needless re-animation flicker and keeps it fast.
    if (newPagesJson !== cachedJson) {
        var items = _pagesNavItems(expandedPages);
        if (items.length) {
            _replaceNav(items, _cacheGet('aurum_active_page', ''));
        }
    }

    _enforceStaffAccess(expandedPages);
}

function initSidebar(activePage) {
    _cacheSet('aurum_active_page', activePage || '');
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            _renderSidebar(activePage);
            _fillDynamic();
        });
    } else {
        _renderSidebar(activePage);
        _fillDynamic();
    }
}

function sbToggleLogout() {
    var pop  = document.getElementById('sb-logout-pop');
    var wrap = document.getElementById('sb-owner-wrap');
    if (!pop || !wrap) return;
    if (pop.style.display !== 'none') { _closePopup(); return; }
    var rect = wrap.getBoundingClientRect();
    pop.style.left    = rect.left + 'px';
    pop.style.width   = rect.width + 'px';
    pop.style.bottom  = (window.innerHeight - rect.top + 6) + 'px';
    pop.style.top     = 'auto';
    pop.style.display = 'block';
    var ch = document.getElementById('sb-owner-chev');
    if (ch) ch.style.transform = 'rotate(180deg)';
}

function _closePopup() {
    var p = document.getElementById('sb-logout-pop');
    var c = document.getElementById('sb-owner-chev');
    if (p) p.style.display = 'none';
    if (c) c.style.transform = '';
}

function sbOpenSettings() {
    _closePopup();
    sideNavigate('settings.html');
}

function sbLogout() {
    try {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.logout && window.pywebview.api.logout();
        }
    } catch(e) {}
    try {
        ['aurum_user_role','aurum_active_user','aurum_software_type',
         'aurum_active_page','aurum_owner_name','aurum_2fa_enabled'].forEach(function(k){
            localStorage.removeItem(k);
        });
    } catch(e) {}
    location.href = 'login.html';
}

(function() {
    function _try() {
        var c = document.getElementById('sidebar-container');
        if (c && c.children.length === 0 && c.innerHTML.trim() === '') {
            _renderSidebar(_cacheGet('aurum_active_page',''));
            _fillDynamic();
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _try);
    } else {
        _try();
    }
})();

// ── AURUM PRE-FETCH CACHE ────────────────────────────────────────────────
// Warms common reference datasets once and serves them instantly from
// localStorage so every page renders WITHOUT a loading state. Mirrors the
// snapshot that AurumAPI.warm_prefetch() pre-computes at Python startup.
// (METHODS below MUST match AurumAPI.PRECACHE_METHODS in main.py.)
(function () {
    var CACHE_KEY = 'aurum_prefetch_v1';
    var METHODS = [
        'get_client_list', 'get_products', 'get_categories', 'get_touch_groups',
        'get_pos_stock', 'get_stock_ledger', 'get_opening_stock', 'get_all_staff',
        'get_all_karigars', 'get_weight_stock_it_codes', 'get_held_bills',
        'get_settings', 'get_active_year', 'get_network_config'
    ];
    var STALE_MS = 60 * 1000; // re-pull from Python at least once a minute

    function _lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    function _api() { return (window.pywebview && window.pywebview.api) || null; }

    // Load any previously cached data synchronously → instant on repeat visits.
    try { window.__AURUM_CACHE__ = JSON.parse(_lsGet(CACHE_KEY) || '{}') || {}; }
    catch (e) { window.__AURUM_CACHE__ = {}; }

    function _installShim() {
        var api = _api();
        if (!api || window.__AURUM_SHIM__) return;
        window.__AURUM_SHIM__ = true;
        METHODS.forEach(function (m) {
            if (typeof api[m] === 'function') {
                var orig = api[m];
                api[m] = function () {
                    var args = arguments;
                    var cached = window.__AURUM_CACHE__[m];
                    if (cached !== undefined) {
                        // Serve cached instantly, refresh in background so the
                        // cache (and next navigation) stays current.
                        try {
                            orig.apply(api, args).then(function (fresh) {
                                if (fresh !== undefined) {
                                    window.__AURUM_CACHE__[m] = fresh;
                                    var all = window.__AURUM_CACHE__; all._ts = Date.now();
                                    _lsSet(CACHE_KEY, JSON.stringify(all));
                                }
                            }).catch(function () {});
                        } catch (e) {}
                        return Promise.resolve(cached);
                    }
                    return orig.apply(api, args);
                };
            }
        });
    }

    function _warm() {
        _installShim();
        var api = _api();
        if (!api) return;
        var cached = window.__AURUM_CACHE__;
        var stale = !cached || (cached._ts && (Date.now() - cached._ts > STALE_MS));
        if (!stale && Object.keys(cached).length > 0) return; // already warm
        if (typeof api.get_prefetch_payload === 'function') {
            api.get_prefetch_payload().then(function (res) {
                if (res && res.status === 'success' && res.cache) {
                    res.cache._ts = Date.now();
                    window.__AURUM_CACHE__ = res.cache;
                    _lsSet(CACHE_KEY, JSON.stringify(res.cache));
                }
                _installShim();
            }).catch(function () { _installShim(); });
        }
    }

    _warm(); // run now (sidebar.js is a non-deferred script, api is ready)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _warm);
    }
})();

// ── BASTION AI SUPPORT WIDGET LOADER ───────────────────────────────────────
// Loads the self-contained floating help assistant on every sidebar page.
(function () {
    if (window.__aurumAiLoader) return;
    window.__aurumAiLoader = true;
    var s = document.createElement('script');
    s.src = 'ai_support.js';
    s.onerror = function () { /* optional helper; ignore if missing */ };
    document.head.appendChild(s);
})();