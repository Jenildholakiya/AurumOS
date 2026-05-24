"""
Run: .\.venv\Scripts\python.exe diagnose.py
"""
import os, re

UI_DIR = os.path.join(os.getcwd(), 'ui')
SKIP   = {'login.html', 'setup.html', 'app.html'}

for fname in sorted(os.listdir(UI_DIR)):
    if not fname.endswith('.html') or fname in SKIP:
        continue
    path = os.path.join(UI_DIR, fname)
    c = open(path, encoding='utf-8', errors='ignore').read()

    has_div    = 'id="sidebar-container"' in c or "id='sidebar-container'" in c
    has_js     = bool(re.search(r'<script[^>]+sidebar\.js', c, re.IGNORECASE))
    has_init   = 'initSidebar' in c
    has_bridge = 'SPA BRIDGE' in c
    has_spa    = 'SPA:' in c or 'SPA LAYOUT' in c

    issues = []
    if not has_div:  issues.append('NO sidebar-container div')
    if not has_js:   issues.append('NO sidebar.js script tag')
    if not has_init: issues.append('NO initSidebar call')
    if has_bridge:   issues.append('HAS SPA BRIDGE leftover')
    if has_spa:      issues.append('HAS SPA style leftover')

    status = 'OK ' if not issues else 'BAD'
    print(f"[{status}] {fname}")
    for i in issues: print(f"        -> {i}")