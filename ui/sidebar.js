/**
 * AurumOS Sidebar
 * Theme: Cream/gold — matches opening page (stock_ledger.html) aesthetic
 */

var NAV_ITEMS = [
    { id:'dashboard', label:'Dashboard',   icon:'grid',      page:'dashboard.html',  section:'Main' },
    { id:'inventory', label:'Inventory',   icon:'box',       page:'inventory.html',  section:'Main' },
    { id:'billing',   label:'Create Bill', icon:'file-text', page:'billing.html',    section:'Main' },
    { id:'history',   label:'History',     icon:'clock',     page:'history.html',    section:'Main' },
    { id:'accounts',  label:'Accounts',    icon:'users',     page:'accounts.html',   section:'Finance' },
    { id:'ledger',    label:'Ledger',      icon:'book',      page:'ledger.html',     section:'Finance' },
    { id:'staff',     label:'Staff',       icon:'user',      page:'staff.html',      section:'Finance' },
];

// SVG icons — crisp, minimal line icons matching the luxury editorial theme
var ICONS = {
    'grid':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
    'box':       '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
    'file-text': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>',
    'clock':     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    'users':     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    'book':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    'user':      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
};

var SIDEBAR_VERSION = '1.0.0';

// ── INIT ─────────────────────────────────────────────────────
function initSidebar(activePage) {
    var container = document.getElementById('sidebar-container');
    if (!container) return;

    // Render sidebar HTML IMMEDIATELY — no bridge needed for nav structure
    var collapsed = localStorage.getItem('sb_collapsed') === '1';
    if (collapsed) document.body.classList.add('sidebar-collapsed');
    container.innerHTML = buildSidebar(activePage);

    // Load owner/version from bridge AFTER render — non-blocking
    // Sidebar is already visible; this just fills in name/version
    setTimeout(_loadSidebarData, 0);
}

// ── RETRY BRIDGE ─────────────────────────────────────────────
function _loadSidebarData() {
    var start = Date.now();
    var poll  = setInterval(function() {
        if (window.pywebview && window.pywebview.api) {
            clearInterval(poll);
            _fetchOwner();
            _fetchVersion();
        } else if (Date.now() - start > 8000) {
            clearInterval(poll);
        }
    }, 120);
}

function _fetchOwner() {
    window.pywebview.api.get_dynamic_greeting()
        .then(function(g) {
            if (!g || g.status !== 'success') return;
            var name = g.owner_title || 'Director';
            var el = document.getElementById('sb-owner-name');
            var av = document.getElementById('sb-owner-avatar');
            if (el) el.innerText = name;
            if (av) {
                av.innerText = name.split(' ')
                    .filter(function(w){ return w.length > 0; })
                    .map(function(w){ return w[0]; })
                    .join('').substring(0, 2).toUpperCase() || 'OS';
            }
        }).catch(function(){});
}

function _fetchVersion() {
    if (!window.pywebview.api.get_current_version) return;
    window.pywebview.api.get_current_version()
        .then(function(v) {
            var el = document.getElementById('sb-ver-pill');
            if (v && el) el.innerText = 'v' + v;
        }).catch(function(){});
}

// ── BUILD HTML ────────────────────────────────────────────────
function buildSidebar(activePage) {
    // Group by section
    var sections = {};
    var order    = [];
    NAV_ITEMS.forEach(function(item) {
        if (!sections[item.section]) { sections[item.section] = []; order.push(item.section); }
        sections[item.section].push(item);
    });

    var h = '';

    // Logo
    h += '<div class="sb-logo">'
       +   '<div class="sb-logo-mark"><span>Au</span></div>'
       +   '<div class="sb-logo-text">'
       +     '<div class="sb-logo-name">Aurum<em>OS</em></div>'
       +     '<div class="sb-logo-sub">Jewelry ERP</div>'
       +   '</div>'
       + '</div>';

    // Owner
    h += '<div class="sb-owner">'
       +   '<div class="sb-owner-avatar" id="sb-owner-avatar">OS</div>'
       +   '<div class="sb-owner-info">'
       +     '<div class="sb-owner-name" id="sb-owner-name">Director</div>'
       +     '<div class="sb-owner-role">System Owner</div>'
       +   '</div>'
       + '</div>';

    // Version
    h += '<div class="sb-version">'
       +   '<span class="sb-ver-label">Version</span>'
       +   '<span class="sb-ver-pill" id="sb-ver-pill">v' + SIDEBAR_VERSION + '</span>'
       + '</div>';

    // Nav
    h += '<nav class="sb-nav">';
    order.forEach(function(sec, si) {
        if (si > 0) h += '<div class="sb-divider"></div>';
        h += '<div class="sb-section-label">' + sec + '</div>';

        sections[sec].forEach(function(item) {
            var active = (item.id === activePage);
            var icon   = ICONS[item.icon] || '';
            h += '<div class="sb-item' + (active ? ' active' : '') + '"'
               +   ' data-tip="' + item.label + '"'
               +   ' onclick="sideNavigate(\'' + item.page + '\')">'
               +   '<div class="sb-icon">' + icon + '</div>'
               +   '<span class="sb-label">' + item.label + '</span>'
               + '</div>';
        });
    });
    h += '</nav>';

    // Footer
    h += '<div class="sb-footer">'
       +   '<button class="sb-collapse-btn" onclick="toggleSidebar()">'
       +     '<span class="sb-collapse-icon">←</span>'
       +     '<span class="sb-collapse-text">Collapse</span>'
       +   '</button>'
       + '</div>';

    return h;
}

// ── NAVIGATE — no return value needed, use load_url via api ──
function sideNavigate(page) {
    // Use window.location.href — works always, no bridge needed
    // This is the most reliable navigation in PyInstaller exe
    try {
        var base = window.location.href.replace(/[^\/\\]*$/, '');
        window.location.href = base + page;
    } catch(e) {
        // Fallback to pywebview if location fails
        try {
            if (window.pywebview && window.pywebview.api)
                window.pywebview.api.navigate(page);
        } catch(e2) {}
    }
}

function toggleSidebar() {
    var collapsed = document.body.classList.toggle('sidebar-collapsed');
    localStorage.setItem('sb_collapsed', collapsed ? '1' : '0');
}

// waitForAPI — used by all pages
function waitForAPI(callback, maxMs) {
    maxMs = maxMs || 8000;
    var start = Date.now();
    var t = setInterval(function() {
        if (window.pywebview && window.pywebview.api) {
            clearInterval(t); callback();
        } else if (Date.now() - start > maxMs) {
            clearInterval(t); callback();
        }
    }, 80);
}