"""
Restore original sidebar.js approach - remove injected static sidebar,
add back sidebar.js script tag + sidebar-container div + initSidebar call.
"""
import os, re

PAGES = {
    'dashboard.html':   'dashboard',
    'billing.html':     'billing',
    'history.html':     'history',
    'accounts.html':    'accounts',
    'ledger.html':      'ledger',
    'inventory.html':   'inventory',
    'staff.html':       'staff',
}

NO_SIDEBAR = [
    'create_item.html', 'stock_ledger.html', 'katti_entry.html',
    'uchak_inward.html', 'client_ledger.html', 'report_inventory.html', 'bill_print.html',
]

UI_DIR = 'ui'

def strip_all_sidebars(html):
    """Remove every sidebar-container div."""
    while True:
        m = re.search(r'<div[^>]+id=["\']sidebar-container["\'][^>]*>', html, re.IGNORECASE)
        if not m: break
        s = m.start(); pos = m.end(); d = 1
        while pos < len(html) and d > 0:
            no = html.find('<div', pos); nc = html.find('</div', pos)
            if nc == -1: break
            if no != -1 and no < nc: d += 1; pos = no + 4
            else: d -= 1; pos = nc + 6
        html = html[:s] + html[pos:]
    return html

def clean_page(html):
    html = strip_all_sidebars(html)
    # Remove old sidebar.js tags
    html = re.sub(r'\s*<script\s+src=["\']sidebar\.js["\']>\s*</script>', '', html)
    # Remove orphaned sidebar styles
    for pat in [r'<style>\s*#sb[^{]*\{.*?</style>', r'<style>\s*\.sb-[^{]*\{.*?</style>',
                r'<style>\s*\.sbl\{.*?</style>']:
        html = re.sub(pat, '', html, flags=re.DOTALL)
    # Remove aurum-layout-fix
    html = re.sub(r'\s*<style\s+id=["\']aurum-layout-fix["\']>.*?</style>', '', html, flags=re.DOTALL)
    # Remove initSidebar calls
    html = re.sub(r'[ \t]*(await\s+)?initSidebar\([^)]*\);?\r?\n?', '', html)
    return html

LAYOUT_FIX = '''<style id="aurum-layout-fix">
body { display: flex !important; height: 100vh !important; overflow: hidden !important; width: 100vw !important; }
#sidebar-container { flex-shrink: 0 !important; width: 252px !important; }
body > main { flex: 1 !important; min-width: 0 !important; overflow: hidden !important; display: flex !important; flex-direction: column !important; }
body.sidebar-collapsed #sidebar-container { width: 66px !important; }
</style>'''

SIDEBAR_INJECT = '''<script src="sidebar.js"></script>
<div id="sidebar-container"></div>'''

# Process pages WITH sidebar
for fname, page_key in PAGES.items():
    path = os.path.join(UI_DIR, fname)
    if not os.path.exists(path):
        print(f'SKIP: {fname}'); continue
    c = open(path, encoding='utf-8').read()
    c = clean_page(c)
    # Add layout fix before </head>
    c = c.replace('</head>', LAYOUT_FIX + '\n</head>', 1)
    # Add sidebar script + container after <body>
    c = re.sub(r'(<body[^>]*>)', r'\1\n' + SIDEBAR_INJECT, c, count=1)
    # Add initSidebar call to window.onload
    # Find window.onload and inject initSidebar as first call
    c = re.sub(
        r'(window\.onload\s*=\s*(?:async\s+)?function\s*\(\s*\)\s*\{)',
        r"\1\n        waitForAPI(function(){ initSidebar('" + page_key + "'); });",
        c, count=1
    )
    open(path, 'w', encoding='utf-8').write(c)
    print(f'OK: {fname}')

# Strip sidebar from pages that shouldn't have it
for fname in NO_SIDEBAR:
    path = os.path.join(UI_DIR, fname)
    if not os.path.exists(path): continue
    c = open(path, encoding='utf-8').read()
    c = clean_page(c)
    open(path, 'w', encoding='utf-8').write(c)
    print(f'CLEAN: {fname}')

print('\nDone. Make sure sidebar.js and sidebar.css are in ui/ folder.')