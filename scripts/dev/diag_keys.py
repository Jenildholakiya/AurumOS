"""AurumOS keyboard diagnostic — finds exactly where keystrokes die.

Run:  run_diag_keys.bat   (or:  python diag_keys.py)

Opens ONE small window with three boxes:
  Box 1 (plain)  — normal text input.
  Box 2 (masked) — exact clone of the login password box
                   (type=text + -webkit-text-security:disc).
  Box 3 (button) — sets a value by code (tests the DOM path without keys).

Type ABC into Box 1 and Box 2. The page counts keydown/input events live
and prints a verdict. Screenshot the window (or paste diag_keys_report.txt
it writes next to this script) and the cause is known for certain:

  keys work in both boxes  -> login-page issue (send the report, fixed same day)
  keys dead in both boxes   -> Windows/WebView2/security-software eats keys
                              before ANY page sees them (no app code can cause
                              or fix that; fix is AV whitelist / WebView2 /
                              on-screen keyboard fallback)
  Box 1 works, Box 2 dead  -> masked-rendering issue (fix in CSS)

Does NOT touch the shop database, the mutex, or any running copy.
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(ROOT, 'diag_keys_report.txt')

HTML = r'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>AurumOS Keyboard Diagnostic</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;margin:0;padding:22px;color:#0f172a;}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;margin-bottom:14px;max-width:640px;}
h2{font-size:1rem;margin:0 0 4px;} p{font-size:.8rem;color:#64748b;margin:4px 0 10px;}
input.big{width:100%;height:44px;font-size:1.1rem;padding:0 12px;border:2px solid #2563eb;border-radius:8px;box-sizing:border-box;}
#box2{-webkit-text-security:disc;}
.counters{font-family:Consolas,monospace;font-size:.78rem;background:#0f172a;color:#a5f3fc;border-radius:8px;padding:10px 12px;margin-top:10px;white-space:pre-wrap;}
.verdict{font-size:1rem;font-weight:700;margin-top:10px;}
.ok{color:#16a34a;} .bad{color:#dc2626;} .wait{color:#b45309;}
button{font-size:.85rem;font-weight:600;padding:9px 16px;border-radius:8px;border:1px solid #2563eb;background:#2563eb;color:#fff;cursor:pointer;}
button.ghost{background:#fff;color:#2563eb;}
.row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;}
.ua{font-size:.68rem;color:#94a3b8;word-break:break-all;}
</style></head><body>
<div class="card"><h2>Box 1 — plain input</h2>
<p>Click inside, type <b>ABC</b>.</p>
<input class="big" id="box1" autocomplete="off" placeholder="type here">
<div class="counters" id="c1">keydown: 0 | input events: 0 | value length: 0</div>
<div class="verdict wait" id="v1">waiting…</div></div>
<div class="card"><h2>Box 2 — login-box clone (masked)</h2>
<p>Click inside, type <b>ABC</b>. Same masking as the sign-in screen.</p>
<input class="big" id="box2" autocomplete="off" spellcheck="false" placeholder="&#9679;&#9679;&#9679;&#9679;&#9679;&#9679;">
<div class="counters" id="c2">keydown: 0 | input events: 0 | value length: 0</div>
<div class="verdict wait" id="v2">waiting…</div></div>
<div class="card"><h2>Box 3 — no keyboard needed</h2>
<p>Proves the page itself is alive even if keys never arrive.</p>
<div class="row"><button onclick="setByCode()">Set value by code</button>
<button class="ghost" onclick="copyReport()">Copy report</button></div>
<div class="counters" id="c3">value: (empty)</div></div>
<div class="card"><h2>Environment</h2><div class="ua" id="ua"></div><div class="ua" id="py"></div></div>
<script>
var PYINFO = "PYINFOJSON";
var stats = {box1:{kd:0,inp:0}, box2:{kd:0,inp:0}};
function paint(id, key) {
    var el = document.getElementById(id), s = stats[key];
    var len = el.value.length;
    document.getElementById(id === 'box1' ? 'c1' : 'c2').textContent =
        'keydown: ' + s.kd + ' | input events: ' + s.inp + ' | value length: ' + len;
    var v = document.getElementById(id === 'box1' ? 'v1' : 'v2');
    if (s.kd > 0 && len > 0) { v.textContent = 'KEYS WORK HERE ✓'; v.className = 'verdict ok'; }
    else if (s.kd > 0) { v.textContent = 'keys arrive but value stays empty'; v.className = 'verdict bad'; }
}
['box1','box2'].forEach(function(id){
    var el = document.getElementById(id);
    el.addEventListener('keydown', function(){ stats[id].kd++; paint(id, id); });
    el.addEventListener('input', function(){ stats[id].inp++; paint(id, id); });
});
function setByCode() {
    document.getElementById('box1').value = 'SET-BY-CODE-OK';
    document.getElementById('c3').textContent = 'value: SET-BY-CODE-OK  (page is alive ✓)';
    paint('box1','box1');
}
function report() {
    return 'box1 keydown=' + stats.box1.kd + ' input=' + stats.box1.inp +
        ' len=' + document.getElementById('box1').value.length +
        ' | box2 keydown=' + stats.box2.kd + ' input=' + stats.box2.inp +
        ' len=' + document.getElementById('box2').value.length +
        ' | ua=' + navigator.userAgent + ' | py=' + PYINFO;
}
function copyReport() {
    var r = report();
    try { navigator.clipboard.writeText(r); } catch(e) {}
    try { if (window.pywebview && window.pywebview.api && window.pywebview.api.diag_save) window.pywebview.api.diag_save(r); } catch(e) {}
    document.getElementById('c3').textContent = 'report copied + saved. Paste it back in chat.';
}
document.getElementById('ua').textContent = 'ua=' + navigator.userAgent;
document.getElementById('py').textContent = 'py=' + PYINFO;
</script></body></html>'''


class Api:
    def diag_save(self, text):
        try:
            with open(REPORT, 'w', encoding='utf-8') as f:
                f.write(str(datetime.datetime.now()) + '\n' + str(text) + '\n')
            return True
        except Exception:
            return False


def main():
    try:
        import webview
        pyver = 'pywebview ' + getattr(webview, '__version__', '?')
    except Exception as e:
        print('pywebview import failed: ' + str(e))
        print('Fix: pip install pywebview')
        sys.exit(1)
    import platform
    pyinfo = pyver + ' | python ' + platform.python_version()
    html = HTML.replace('PYINFOJSON', pyinfo.replace('\\', '\\\\').replace('"', '\\"'))
    api = Api()
    win = webview.create_window('AurumOS Keyboard Diagnostic', html=html,
                                js_api=api, width=720, height=860)
    webview.start(gui='edgechromium')


if __name__ == '__main__':
    main()
