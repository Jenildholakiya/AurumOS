"""Parse the JavaScript in every UI page and every .js file.

Run from the project root:  python test_ui_syntax.py   (needs node on PATH)

The pages carry their logic in inline <script> blocks that nothing compiles.
A stray backslash in a regular expression takes a whole screen down, and the
only symptom is a page that silently does nothing, which is easy to miss until
it is on a shop counter. Handing each block to node's parser catches that in a
second.

Syntax only. It does not run anything, so nothing here touches a database, a
printer or the network.
"""

import os
import re
import subprocess
import sys
import tempfile

UI = 'ui'
SCRIPT_BLOCK = re.compile(
    r'<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script>',
    re.IGNORECASE | re.DOTALL)

failures = []
checked = 0


def node_available():
    try:
        subprocess.run(['node', '--version'], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def parses(source, label):
    """True if node can parse this as a script."""
    global checked
    checked += 1
    handle, path = tempfile.mkstemp(suffix='.js')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as f:
            f.write(source)
        done = subprocess.run(['node', '--check', path],
                              capture_output=True, text=True, timeout=60)
        if done.returncode == 0:
            return True
        detail = (done.stderr or '').strip().split('\n')
        # Trim node's echo of the temp path down to the useful lines.
        useful = [ln for ln in detail if path not in ln][:4]
        failures.append((label, '\n'.join(useful) or 'parse failed'))
        return False
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


if not node_available():
    print('node is not on PATH, so the JavaScript could not be checked.')
    print('Install node, or run this on a machine that has it.')
    sys.exit(0)

print('-- inline page scripts --')
for name in sorted(os.listdir(UI)):
    if not name.lower().endswith('.html'):
        continue
    path = os.path.join(UI, name)
    with open(path, encoding='utf-8', errors='replace') as f:
        html = f.read()
    for index, block in enumerate(SCRIPT_BLOCK.finditer(html), start=1):
        attrs = block.group('attrs') or ''
        if 'src=' in attrs.lower():
            continue                       # external file, checked below
        kind = re.search(r'type\s*=\s*["\']([^"\']+)', attrs, re.IGNORECASE)
        if kind and 'javascript' not in kind.group(1).lower() \
                and 'module' not in kind.group(1).lower():
            continue                       # a template or JSON block, not code
        body = block.group('body')
        if not body.strip():
            continue
        # Keep the line numbering so a reported line matches the HTML file.
        offset = html[:block.start('body')].count('\n')
        parses('\n' * offset + body, f'{name} (script block {index})')

print('-- standalone scripts --')
for name in sorted(os.listdir(UI)):
    if name.lower().endswith('.js'):
        with open(os.path.join(UI, name), encoding='utf-8', errors='replace') as f:
            parses(f.read(), name)

print(f'\n  parsed {checked} scripts')
if failures:
    print(f'\n{len(failures)} FAILED:')
    for label, detail in failures:
        print(f'\n  {label}')
        for line in detail.split('\n'):
            print(f'    {line}')
    sys.exit(1)

print('\nALL SCRIPTS PARSE')
