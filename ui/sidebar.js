/**
 * AurumOS Sidebar — Offline-First v5
 */

var NAV_ADMIN = [
    { id:'dashboard', label:'Dashboard',   icon:'grid',      page:'dashboard.html',  section:'Main'    },
    { id:'inventory', label:'Inventory',   icon:'box',       page:'inventory.html',  section:'Main'    },
    { id:'billing',   label:'Create Bill', icon:'file-text', page:'billing.html',    section:'Main'    },
    { id:'history',   label:'History',     icon:'clock',     page:'history.html',    section:'Main'    },
    { id:'accounts',  label:'Accounts',    icon:'users',     page:'accounts.html',   section:'Finance' },
    { id:'ledger',    label:'Ledger',      icon:'book',      page:'ledger.html',     section:'Finance' },
    { id:'staff',     label:'Staff',       icon:'user',      page:'staff.html',      section:'Finance' },
];

var NAV_STAFF = [
    { id:'billing', label:'Create Bill', icon:'file-text', page:'billing.html', section:'Main' },
];

var ICONS = {
    'grid': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
    'box': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
    'file-text': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>',
    'clock': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    'users': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    'book': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    'settings': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    'user': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
};

function _cacheGet(key, fallback) {
    try { var v = localStorage.getItem(key); return (v !== null && v !== '') ? v : fallback; }
    catch(e) { return fallback; }
}

function _cacheSet(key, value) {
    try { localStorage.setItem(key, value); } catch(e) {}
}

function _getRole() {
    var u = '';
    try { u = (location.search.match(/[?&]role=([^&]+)/) || [])[1] || ''; } catch(e) {}
    var l = _cacheGet('aurum_user_role', '');
    var r = u || l || 'admin';
    if (u) _cacheSet('aurum_user_role', u);
    return r;
}

function _initials(name) {
    return (name || 'OS').split(' ').filter(function(w) { return w.length > 0; }).map(function(w) { return w[0]; }).join('').substring(0, 2).toUpperCase() || 'OS';
}

function sideNavigate(page) {
    var role = _getRole();
    try {
        var base = location.href.replace(/[^\/\\]*(\?.*)?$/, '');
        location.href = base + page + '?role=' + role;
    } catch(e) {
        try { if (window.pywebview && window.pywebview.api) window.pywebview.api.navigate(page); }
        catch(e2) {}
    }
}

function toggleSidebar() {
    var c = document.body.classList.toggle('sidebar-collapsed');
    _cacheSet('sb_collapsed', c ? '1' : '0');
}

function _buildNav(items, activePage, isStaff) {
    var sections = {}, order = [];
    items.forEach(function(item) {
        if (!sections[item.section]) { sections[item.section] = []; order.push(item.section); }
        sections[item.section].push(item);
    });
    var ns = isStaff ? ' style="flex:0 0 auto;"' : '';
    var h  = '<nav class="sb-nav"' + ns + '>';
    order.forEach(function(sec, si) {
        if (si > 0) h += '<div class="sb-divider"></div>';
        h += '<div class="sb-section-label">' + sec + '</div>';
        sections[sec].forEach(function(item) {
            var cls = 'sb-item' + (item.id === activePage ? ' active' : '');
            h += '<div class="' + cls + '" data-tip="' + item.label + '"'
               + ' onclick="sideNavigate(\'' + item.page + '\')">'
               + '<div class="sb-icon">' + (ICONS[item.icon] || '') + '</div>'
               + '<span class="sb-label">' + item.label + '</span></div>';
        });
    });
    h += '</nav>';
    return h;
}

function _renderSidebar(activePage) {
    var container = document.getElementById('sidebar-container');
    if (!container) return;

    try { if (_cacheGet('sb_collapsed','') === '1') document.body.classList.add('sidebar-collapsed'); } catch(e) {}

    var role    = _getRole();
    var isStaff = role === 'staff';
    var items   = isStaff ? NAV_STAFF : NAV_ADMIN;

    var ownerName = _cacheGet('aurum_owner_name', 'Director');
    var ownerInit = _initials(ownerName);
    var appVer    = _cacheGet('aurum_app_version', '1.0');

    var h = '';
    h += '<div class="sb-logo"><div class="sb-logo-mark"><span>Au</span></div><div class="sb-logo-text"><div class="sb-logo-name">Aurum<em>OS</em></div><div class="sb-logo-sub">Jewelry ERP</div></div></div>';
    h += '<div class="sb-version"><span class="sb-ver-label">Version</span><span class="sb-ver-pill" id="sb-ver-pill">v' + appVer + '</span></div>';
    h += _buildNav(items, activePage, isStaff);

    h += '<div id="sb-owner-wrap" style="margin-top:auto;padding:8px 10px;flex-shrink:0;">'
       + '<div class="sb-owner" id="sb-owner-row" onclick="sbToggleLogout()" style="cursor:pointer;margin:0;">'
       + '<div class="sb-owner-avatar" id="sb-owner-avatar">' + ownerInit + '</div>'
       + '<div class="sb-owner-info"><div class="sb-owner-name" id="sb-owner-name">' + ownerName + '</div><div class="sb-owner-role">System Owner</div></div>'
       + '<svg id="sb-owner-chev" style="flex-shrink:0;color:#b0aa9e;transition:transform 0.2s;margin-left:auto;" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 15l-6-6-6 6"/></svg>'
       + '</div></div>';

    h += '<div class="sb-footer"><button class="sb-collapse-btn" onclick="toggleSidebar()"><span class="sb-collapse-icon">&#8592;</span><span class="sb-collapse-text">Collapse</span></button></div>';
    container.innerHTML = h;

    var old = document.getElementById('sb-logout-pop');
    if (old) old.parentNode.removeChild(old);

    var pop = document.createElement('div');
    pop.id = 'sb-logout-pop';
    pop.style.cssText = 'display:none;position:fixed;z-index:99999;background:#0e0c09;border-radius:10px;padding:8px;box-shadow:0 -4px 32px rgba(14,12,9,0.5);border:1px solid rgba(201,162,39,0.25);min-width:200px;';

    // Conditional Settings Button
    var settingsBtn = isStaff ? '' :
        '<button onclick="window.location.href=\'settings.html\'" style="width:100%;padding:9px 12px;margin-bottom:6px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:7px;font-family:inherit;font-size:0.74rem;font-weight:500;cursor:pointer;display:flex;align-items:center;gap:8px;color:rgba(255,255,255,0.7);">'
        + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
        + 'Settings</button>';

    pop.innerHTML = '<div style="padding:6px 10px 10px;border-bottom:1px solid rgba(255,255,255,0.08);margin-bottom:6px;"><div id="sb-pop-name" style="font-size:0.75rem;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px;">'+ownerName+'</div><div style="font-size:0.58rem;color:rgba(255,255,255,0.4);">System Owner</div></div>'
        + settingsBtn
        + '<button onclick="sbLogout()" style="width:100%;padding:9px 12px;background:rgba(185,28,28,0.1);border:1px solid rgba(185,28,28,0.2);border-radius:7px;font-family:inherit;font-size:0.74rem;font-weight:500;cursor:pointer;display:flex;align-items:center;gap:8px;color:#f87171;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>Sign Out</button>';
    document.body.appendChild(pop);

    document.addEventListener('click', function(e) {
        var p = document.getElementById('sb-logout-pop');
        var w = document.getElementById('sb-owner-wrap');
        if (!p || p.style.display === 'none') return;
        if (w && w.contains(e.target)) return;
        if (p.contains(e.target)) return;
        _closePopup();
    });
}

function _fillDynamic() {
    var attempts = 0;
    var t = setInterval(function() {
        attempts++;
        if (window.pywebview && window.pywebview.api) {
            clearInterval(t);
            try {
                window.pywebview.api.get_dynamic_greeting().then(function(g) {
                    if (!g || g.status !== 'success') return;
                    var n = g.owner_title || 'Director';
                    var ne = document.getElementById('sb-owner-name');
                    var pe = document.getElementById('sb-pop-name');
                    var av = document.getElementById('sb-owner-avatar');
                    if (ne) ne.innerText = n;
                    if (pe) pe.innerText = n;
                    if (av) av.innerText = _initials(n);
                    _cacheSet('aurum_owner_name', n);
                }).catch(function() {});
            } catch(e) {}
            try {
                if (window.pywebview.api.get_current_version) {
                    window.pywebview.api.get_current_version().then(function(v) {
                        if (!v) return;
                        var el = document.getElementById('sb-ver-pill');
                        if (el) el.innerText = 'v' + v;
                        _cacheSet('aurum_app_version', v);
                    }).catch(function() {});
                }
            } catch(e) {}
        }
        if (attempts > 100) clearInterval(t);
    }, 100);
}

function initSidebar(activePage) {
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
    pop.style.top     = '-9999px';
    pop.style.display = 'block';
    var ph = pop.offsetHeight;
    pop.style.top = (rect.top - ph - 6) + 'px';
    var ch = document.getElementById('sb-owner-chev');
    if (ch) ch.style.transform = 'rotate(180deg)';
}

function _closePopup() {
    var p = document.getElementById('sb-logout-pop');
    var c = document.getElementById('sb-owner-chev');
    if (p) p.style.display = 'none';
    if (c) c.style.transform = '';
}

function sbLogout() {
    try { if (window.pywebview && window.pywebview.api) window.pywebview.api.logout(); } catch(e) {}
    try {
        localStorage.removeItem('aurum_user_role');
        localStorage.removeItem('aurum_active_user');
    } catch(e) {}
    location.href = 'login.html';
}

(function() {
    function _try() {
        var c = document.getElementById('sidebar-container');
        if (c && c.children.length === 0 && c.innerHTML.trim() === '') {
            _renderSidebar('');
            _fillDynamic();
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _try);
    } else {
        _try();
    }
})();