// sidebar.js — AurumOS
// Role comes from Python server session via get_session()

const sidebarConfig = [
    { id: 'dashboard', label: 'Dashboard',   icon: 'grid',      url: 'dashboard.html' },
    { id: 'inventory', label: 'Inventory',   icon: 'box',       url: 'inventory.html' },
    { id: 'billing',   label: 'Create Bill', icon: 'file-text', url: 'billing.html'   },
    { id: 'history',   label: 'History',     icon: 'clock',     url: 'history.html'   },
    { id: 'accounts',  label: 'Accounts',    icon: 'users',     url: 'accounts.html'  },
    { id: 'ledger',    label: 'Ledger',      icon: 'book',      url: 'ledger.html'    },
    { id: 'staff',     label: 'Staff',       icon: 'user',      url: 'staff.html'     }
];

async function initSidebar(activePageId) {
    const container = document.getElementById('sidebar-container');
    if (!container) return;

    let currentSessionRole = null;

    try {
        if (window.pywebview && window.pywebview.api) {
            const session = await window.pywebview.api.get_session();
            if (session && session.status === 'ok' && session.role) {
                currentSessionRole = session.role;
                localStorage.setItem('aurum_user_role', session.role);
                if (session.username) {
                    localStorage.setItem('aurum_active_user', session.username);
                }
            }
        }
    } catch (e) {
        console.warn('get_session error:', e);
    }

    if (!currentSessionRole) {
        currentSessionRole = localStorage.getItem('aurum_user_role');
    }

    if (!currentSessionRole) {
        window.location.href = 'login.html';
        return;
    }

    const isCollapsed = localStorage.getItem('aurum_sidebar_collapsed') === 'true';
    if (isCollapsed) document.body.classList.add('sidebar-collapsed');
    else document.body.classList.remove('sidebar-collapsed');

    const allowedConfig = sidebarConfig.filter(item => {
        if (currentSessionRole === 'staff') return item.id === 'billing';
        return true;
    });

    const menuItems = allowedConfig.map(item => `
        <li class="nav-item ${activePageId === item.id ? 'active' : ''}"
            onclick="sideNavigate('${item.url}')" title="${item.label}">
            <span class="nav-icon">${getIcon(item.icon)}</span>
            <span class="nav-label">${item.label}</span>
        </li>
    `).join('');

    container.innerHTML = `
        <nav id="sidebar">
            <div class="sidebar-brand">
                <div class="brand-text">Aurum<span>OS</span></div>
                <div class="brand-icon">A<span>OS</span></div>
                ${currentSessionRole === 'staff' ? '<small class="staff-mode-tag">STAFF MODE</small>' : ''}
            </div>
            <div class="sidebar-toggle" onclick="toggleSidebarMenu(event)" title="Toggle Navigation Panel">
                ${getIcon('chevron')}
            </div>
            <ul class="nav-menu">
                ${menuItems}
            </ul>
            <div class="sidebar-footer">
                <div class="logout-btn" onclick="handleSystemLogout()" title="Logout">
                    <span class="nav-icon">${getIcon('logout')}</span>
                    <span class="nav-label">Logout</span>
                </div>
            </div>
        </nav>
    `;
}

function toggleSidebarMenu(event) {
    event.stopPropagation();
    const isCollapsed = document.body.classList.toggle('sidebar-collapsed');
    localStorage.setItem('aurum_sidebar_collapsed', isCollapsed);
    if (typeof Chart !== 'undefined') {
        Object.values(Chart.instances).forEach(chart => {
            setTimeout(() => chart.resize(), 160);
        });
    }
}

function sideNavigate(url) {
    document.body.style.opacity    = '0.5';
    document.body.style.transform  = 'scale(0.99)';
    document.body.style.transition = 'all 0.3s ease';
    window.location.href = url;
}

async function handleSystemLogout() {
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.logout();
        }
    } catch (e) {}
    localStorage.clear();
    sideNavigate('login.html');
}

function waitForAPI(callback, maxMs) {
    maxMs = maxMs || 8000;
    var start = Date.now();
    var t = setInterval(function () {
        if (window.pywebview && window.pywebview.api) {
            clearInterval(t);
            callback();
        } else if (Date.now() - start > maxMs) {
            clearInterval(t);
            callback();
        }
    }, 100);
}

function getIcon(name) {
    const icons = {
        grid:        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>',
        box:         '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"></path></svg>',
        'file-text': '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line></svg>',
        clock:       '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>',
        users:       '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>',
        book:        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>',
        user:        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>',
        logout:      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>',
        chevron:     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>'
    };
    return icons[name] || '';
}