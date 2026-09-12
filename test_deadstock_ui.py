"""Render the dead stock screen and check what it actually says.

Run from the project root:  python test_deadstock_ui.py   (needs node on PATH)

test_deadstock.py covers the arithmetic and test_deadstock_store.py the
query. This covers the half an owner reads: that a retail shop is told
rupees and a wholesaler grams, that a shop which has never billed is told
grams rather than a confident Rs 0, and that a screen which cannot reach the
program says so on every card rather than sitting on "Loading" for ever.

It pulls the real functions out of ui/dead_stock.html and runs them against
a stub DOM, so it breaks when the page breaks. No browser, no database.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(__file__))
PAGE = os.path.join(ROOT, 'ui', 'dead_stock.html')

CHECKS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const els = {};
const listeners = {};
function mk(id) {
  return { id: id, innerHTML: '', textContent: '', value: '', className: '',
           style: {}, hidden: false, disabled: false, checked: false,
           children: [], _attrs: {},
           setAttribute: function (k, v) { this._attrs[k] = v; },
           getAttribute: function (k) { return this._attrs[k]; },
           addEventListener: function () {}, focus: function () {},
           scrollIntoView: function () {},
           querySelector: function () { return null; },
           querySelectorAll: function () { return []; } };
}

const ctx = {
  window: { addEventListener: function () {},
            apiReady: function (cb) { ctx.__booted = cb; } },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  document: {
    hidden: false, activeElement: null, readyState: 'complete',
    addEventListener: function (t, f) { listeners[t] = f; },
    getElementById: function (id) { if (!els[id]) els[id] = mk(id); return els[id]; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; }
  },
  console, setTimeout: function (f) { if (typeof f === 'function') f(); },
  clearTimeout: function () {}, setInterval: function () {},
  clearInterval: function () {},
  parseFloat, parseInt, JSON, Math, Date, String, Number, Array, Object, RegExp,
  isNaN, isFinite
};
ctx.globalThis = ctx;
vm.createContext(ctx);
try { vm.runInContext(src, ctx); } catch (e) { /* page-load wiring */ }

if (typeof ctx.renderRows !== 'function') {
  console.log('  [FAIL] could not load the screen functions');
  process.exit(1);
}

let fail = 0;
const chk = (label, cond) => { if (!cond) fail++; console.log('  [' + (cond ? 'PASS' : 'FAIL') + '] ' + label); };
const E = (id) => ctx.document.getElementById(id);

console.log('-- it waits for the program before judging it --');
chk('the page hands its start to the waiter', typeof ctx.__booted === 'function');
chk('and asks for nothing before then', E('head-sub').textContent === '');

console.log('-- weights and money as a shop writes them --');
chk('grams to three places', ctx.grams(32.4) === '32.400g');
chk('a lakh is grouped the Indian way', ctx.money(553700) === 'Rs 5,53,700');
chk('junk is zero, not NaN', ctx.money('abc') === 'Rs 0');
// A shop says "25 months", not "741 days".
chk('a long age is said in months', ctx.months(741) === '25 months');
chk('a short one stays in days', ctx.months(40) === '40 days');

console.log('-- tag numbers are typed by people --');
chk('tags cannot become markup', ctx.esc('<b>x</b>') === '&lt;b&gt;x&lt;/b&gt;');
// Every tag goes inside a single-quoted onclick attribute, where one
// apostrophe would close the attribute early and take the row with it.
chk('an apostrophe is escaped for the attribute',
    ctx.escAttr("T'1") === 'T&#39;1');

const PIECES = [
  { tag_id: 'T-1', it_name: 'Necklace', category: 'Necklace', karat: '22K',
    gr_wt: 32.4, fine_wt: 29.678, metal_value: 213682, ticket_value: 0,
    entry_date: '2024-09-01', age_days: 741, band: 'dead', photo: 'x.jpg' },
  { tag_id: 'T-2', it_name: 'Ring', category: 'Ring', karat: '18K',
    gr_wt: 4.22, fine_wt: 3.165, metal_value: 22788, ticket_value: 41500,
    entry_date: '2026-01-20', age_days: 235, band: 'stale', photo: '' }
];

function report(mode, hasRate) {
  return {
    status: 'success', today: '2026-09-12', mode: mode,
    threshold_days: 180, rate_per_10g: hasRate ? 72000 : 0, has_rate: hasRate,
    rows: PIECES,
    all_pieces: { pieces: 4, gross_wt: 90, fine_wt: 82.4, metal_value: 593280, ticket_value: 0 },
    dead: { pieces: 2, gross_wt: 36.62, fine_wt: 32.843, metal_value: 236470, ticket_value: 41500 },
    weight_stock: { pieces: 1, gross_wt: 500, fine_wt: 497.5, metal_value: 3582000, ticket_value: 0 },
    share_of_stock: 39.9, oldest_days: 741,
    bands: [
      { band: 'fresh', label: 'Under 3 months', pieces: 1, gross_wt: 4.5, fine_wt: 4.1, metal_value: 29678, ticket_value: 0 },
      { band: 'slow', label: '3 to 6 months', pieces: 1, gross_wt: 6, fine_wt: 5.5, metal_value: 39571, ticket_value: 0 },
      { band: 'stale', label: '6 to 12 months', pieces: 1, gross_wt: 4.22, fine_wt: 3.165, metal_value: 22788, ticket_value: 41500 },
      { band: 'dead', label: 'Over a year', pieces: 1, gross_wt: 32.4, fine_wt: 29.678, metal_value: 213682, ticket_value: 0 }],
    by_category: [{ name: 'Necklace', pieces: 1, gross_wt: 32.4, fine_wt: 29.678, metal_value: 213682, ticket_value: 0 }],
    by_purity: [{ name: '22K', pieces: 1, gross_wt: 32.4, fine_wt: 29.678, metal_value: 213682, ticket_value: 0 }],
    headline: 'two pieces, none of it moving.'
  };
}

console.log('-- a retail owner is told rupees --');
ctx.DATA = report('retail', true);
ctx.renderTiles(ctx.DATA);
ctx.renderRows(ctx.DATA);
chk('the first counter is money', /Money asleep/.test(E('tiles').innerHTML));
chk('and it carries a rupee figure', /Rs 2,36,470/.test(E('tiles').innerHTML));
chk('a row shows its value in rupees', /Rs 2,13,682/.test(E('rows').innerHTML));
chk('and a ticket price where the shop set one', /ticket Rs 41,500/.test(E('rows').innerHTML));
chk('a piece with a photograph is marked', /📷/.test(E('rows').innerHTML));
chk('the count is on the card', E('rows-count').textContent === 2);

console.log('-- a wholesaler is told grams --');
ctx.DATA = report('wholesale', true);
ctx.renderTiles(ctx.DATA);
ctx.renderRows(ctx.DATA);
chk('the first counter is metal', /Metal asleep/.test(E('tiles').innerHTML));
chk('and it carries a weight', /32\.843g/.test(E('tiles').innerHTML));
chk('the share is measured by fine weight', /by fine weight/.test(E('tiles').innerHTML));
chk('a row shows fine weight, not rupees',
    /29\.678g/.test(E('rows').innerHTML) && !/Rs 2,13,682/.test(E('rows').innerHTML));

console.log('-- a shop that has never billed has no rate --');
// A confident "Rs 0" would be a lie. Grams are the honest answer.
ctx.DATA = report('retail', false);
ctx.renderTiles(ctx.DATA);
ctx.renderLead(ctx.DATA);
chk('it falls back to metal', /Metal asleep/.test(E('tiles').innerHTML));
chk('and says why in the lead', /No gold rate has been billed/.test(E('lead-sub').textContent));
chk('rather than showing Rs 0', !/Rs 0/.test(E('tiles').innerHTML));

console.log('-- how the whole shelf ages --');
ctx.DATA = report('retail', true);
ctx.renderBands(ctx.DATA);
chk('all four bands are drawn', (E('bands').innerHTML.match(/class="band"/g) || []).length === 4);
chk('each has a bar', (E('bands').innerHTML.match(/bar-fill/g) || []).length === 4);
// The widest bar is the largest value, or the shape says nothing.
chk('the biggest band fills the bar', /bar-fill dead" style="width:100%/.test(E('bands').innerHTML));
chk('every band is named in months', /Under 3 months/.test(E('bands').innerHTML));

console.log('-- loose metal is kept apart --');
// Counting half a kilo of bulk as dead rings would bury the number that
// matters under one that does not.
chk('it is reported, and named as raw material',
    /raw material/.test(E('weight-note').textContent));
chk('with its own weight', /497\.500g/.test(E('weight-note').textContent));
ctx.DATA = report('retail', true);
ctx.DATA.weight_stock = { pieces: 0, gross_wt: 0, fine_wt: 0, metal_value: 0, ticket_value: 0 };
ctx.renderBands(ctx.DATA);
chk('a shop with none is told so', /No loose metal/.test(E('weight-note').textContent));

console.log('-- an empty report --');
ctx.DATA = report('retail', true);
ctx.DATA.rows = [];
ctx.DATA.dead = { pieces: 0, gross_wt: 0, fine_wt: 0, metal_value: 0, ticket_value: 0 };
ctx.renderRows(ctx.DATA);
ctx.renderLead(ctx.DATA);
chk('says nothing is sitting that long', /Nothing is sitting that long/.test(E('rows').innerHTML));
chk('and the lead turns from a warning to a clean note',
    E('lead').className.indexOf('is-clean') >= 0);

console.log('-- a screen that cannot load says so on every card --');
const LISTS = ['rows', 'bands', 'groups'];
const everyCard = (word) => LISTS.every(id => new RegExp(word, 'i').test(E(id).innerHTML));

ctx.window.pywebview = undefined;
ctx.loadAll(true);
chk('no bridge is reported as no connection',
    /could not reach/i.test(E('head-sub').textContent));
chk('and every card says the same', everyCard('could not reach'));

ctx.window.pywebview = { api: { something_else: function () {} } };
ctx.loadAll(true);
chk('an older program is reported as a restart',
    /reopen/i.test(E('head-sub').textContent));
chk('and every card says the same', everyCard('restart'));
chk('neither leaves a card on Loading', !everyCard('Loading'));

console.log('');
if (fail) { console.log(fail + ' FAILED'); process.exit(1); }
console.log('ALL PASS');
"""


def check_layout(html):
    """The shape of the page, which no amount of script testing would catch."""
    out = []
    cols = re.findall(r'<div class="col">', html)
    if len(cols) != 2:
        out.append('expected 2 column stacks, found %d' % len(cols))
    for target in re.findall(r"go:'([\w-]+)'", html):
        if ('id="%s"' % target) not in html:
            out.append('a counter points at %s, which is not on the page' % target)
    if 'window.apiReady' not in html:
        out.append('the page does not wait to be handed the program')
    if '<script src="boot.js"></script>' not in html:
        out.append('boot.js is not the first script, so the wait cannot work')
    if 'id="sidebar-container"' not in html:
        out.append('no sidebar container, so the page renders without the menu')
    if not re.search(r'<script src="sidebar\.js\?v=\d+"></script>', html):
        out.append('the sidebar script is not loaded, so no menu is drawn')

    nav = os.path.join(ROOT, 'ui', 'sidebar.js')
    if os.path.isfile(nav):
        with open(nav, encoding='utf-8', errors='replace') as f:
            js = f.read()
        for name in ('NAV_RETAIL', 'NAV_ADMIN'):
            m = re.search(name + r'\s*=\s*(\[.*?\n\];)', js, re.S)
            if not m:
                out.append('%s not found in sidebar.js' % name)
            elif "page:'dead_stock.html'" not in m.group(1).replace(' ', ''):
                out.append('Dead Stock is missing from %s' % name)
        if "'dead_stock.html':" not in js:
            out.append('dead_stock.html has no routing entry')
        if "'deadstock':" not in js:
            out.append('the dead stock icon is not defined, so the row draws blank')

    try:
        sys.path.insert(0, ROOT)
        from core import permissions as _p
        ids = _p.bundles_for_plan('enterprise')
        for mode in ('retail', 'wholesale'):
            if 'dead_stock.html' not in _p.pages_for(ids, mode):
                out.append('the top plan does not reach dead_stock.html in %s '
                           'mode, so a staff login cannot open it' % mode)
    except Exception as e:
        out.append('could not read the permission policy: %s' % e)
    return out


def node_available():
    try:
        subprocess.run(['node', '--version'], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def main():
    if not os.path.isfile(PAGE):
        print('FAIL: ui/dead_stock.html not found')
        return 1
    with open(PAGE, encoding='utf-8', errors='replace') as f:
        html = f.read()

    print('-- the shape of the page --')
    problems = check_layout(html)
    for p in problems:
        print('  [FAIL] ' + p)
    if not problems:
        print('  [PASS] two stacks, every counter has a card, and it waits for the bridge')
        print('  [PASS] the menu carries Dead Stock in both modes, and policy agrees')
    print('')

    if not node_available():
        print('node is not on PATH, so the screen could not be rendered.')
        return 1 if problems else 0

    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
    if not blocks:
        print('FAIL: no inline script found in ui/dead_stock.html')
        return 1

    tmp = tempfile.mkdtemp(prefix='aurum_deadui_')
    page_js = os.path.join(tmp, 'page.js')
    runner = os.path.join(tmp, 'checks.js')
    try:
        with open(page_js, 'w', encoding='utf-8') as f:
            f.write('\n'.join(blocks))
        with open(runner, 'w', encoding='utf-8') as f:
            f.write(CHECKS)
        done = subprocess.run(['node', runner, page_js], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=120)
        sys.stdout.write(done.stdout or '')
        if done.returncode != 0 and (done.stderr or '').strip():
            sys.stdout.write(done.stderr)
        return 1 if (problems or done.returncode) else 0
    finally:
        for p in (page_js, runner):
            try:
                os.unlink(p)
            except Exception:
                pass
        try:
            os.rmdir(tmp)
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
