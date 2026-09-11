"""Render the hallmarking panel's lists and check what they actually say.

Run from the project root:  python test_hallmark_ui.py   (needs node on PATH)

test_hallmark.py covers the rules and test_hallmark_store.py the storage.
This covers the half a shopkeeper touches: that a piece marked failed cannot
keep a number, that a late lot is shown as late, that choosing pieces does
not lose what was already chosen, and that a screen which cannot reach the
program says so on every card rather than sitting on "Loading" for ever.

It pulls the real functions out of ui/hallmark.html and runs them against a
stub DOM, so it breaks when the page breaks. No browser, no database, no
network.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(__file__))
PAGE = os.path.join(ROOT, 'ui', 'hallmark.html')

CHECKS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const els = {};
const timers = [];
const listeners = {};
function mk(id) {
  return { id: id, innerHTML: '', textContent: '', value: '', className: '',
           style: {}, hidden: false, disabled: false, checked: false,
           _attrs: {},
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
  clearTimeout: function () {},
  setInterval: function (f, ms) { timers.push(ms); return timers.length; },
  clearInterval: function () {},
  parseFloat, parseInt, JSON, Math, Date, String, Number, Array, Object, RegExp,
  isNaN, isFinite
};
ctx.globalThis = ctx;
vm.createContext(ctx);
try { vm.runInContext(src, ctx); } catch (e) { /* page-load wiring */ }

if (typeof ctx.renderWaiting !== 'function') {
  console.log('  [FAIL] could not load the panel functions');
  process.exit(1);
}

let fail = 0;
const chk = (label, cond) => { if (!cond) fail++; console.log('  [' + (cond ? 'PASS' : 'FAIL') + '] ' + label); };
const E = (id) => ctx.document.getElementById(id);

console.log('-- it waits for the program before judging it --');
chk('the page hands its start to the waiter', typeof ctx.__booted === 'function');
chk('and asks for nothing before then', E('head-sub').textContent === '');
chk('while it says it is connecting', E('live-text').textContent === 'connecting');
ctx.__booted();   // let the page start, as pywebview would

console.log('-- weights and money as a shop writes them --');
chk('grams to three places', ctx.grams(32.4) === '32.400g');
chk('nothing is zero, not NaN', ctx.grams(null) === '0.000g');
chk('a lakh is grouped the Indian way', ctx.money(123456) === 'Rs 1,23,456');
chk('and junk is zero', ctx.money('abc') === 'Rs 0');

console.log('-- tag numbers are typed by people --');
chk('tags cannot become markup', ctx.esc('<b>x</b>') === '&lt;b&gt;x&lt;/b&gt;');
// Every tag goes inside a single-quoted onclick attribute, where one
// apostrophe would close the attribute early and take the row with it.
chk("an apostrophe is escaped for the attribute",
    ctx.escAttr("T'1") === 'T&#39;1');

console.log('-- choosing what to send --');
ctx.DATA = { pending: [
  { tag_id: 'T-1', it_name: 'Chain', gr_wt: 8.0, karat: '22K', category: 'Chain' },
  { tag_id: 'T-2', it_name: 'Ring',  gr_wt: 4.0, karat: '18K', category: 'Ring' },
  { tag_id: 'T-3', it_name: 'Bangle', gr_wt: 12.0, karat: '22K', category: 'Bangle' }] };
E('wait-filter').value = '';
ctx.renderWaiting();
chk('every waiting piece is listed', E('wait-count').textContent === 3);
chk('with its weight', /8\.000g/.test(E('waiting').innerHTML));
chk('and its purity', /22K/.test(E('waiting').innerHTML));

ctx.pickAll();
chk('all can be chosen at once', Object.keys(ctx.PICKED).length === 3);
ctx.pickNone();
chk('and cleared', Object.keys(ctx.PICKED).length === 0);

console.log('-- a filter chooses only what it shows --');
// Choosing "all" while a filter is on must not quietly pack the pieces the
// filter is hiding, which is how the wrong jewellery leaves the building.
E('wait-filter').value = '22k';
ctx.renderWaiting();
chk('the filter narrows the list', E('wait-count').textContent === 2);
ctx.pickAll();
chk('and all only takes what is shown', Object.keys(ctx.PICKED).length === 2);
chk('the hidden piece is left alone', !ctx.PICKED['T-2']);
E('wait-filter').value = '';
ctx.pickNone();
ctx.renderWaiting();
chk('an empty list says so plainly',
    (ctx.DATA = { pending: [] }, ctx.renderWaiting(),
     /Everything is hallmarked/.test(E('waiting').innerHTML)));

console.log('-- lots, worst first --');
ctx.renderLots([
  { id: 1, lot_no: 'HM-2026-0001', centre_name: 'Rajkot', state: 'late',
    days_out: 14, pieces: 6, pending: 6, sent_date: '2026-08-28',
    expected_date: '2026-09-04' },
  { id: 2, lot_no: 'HM-2026-0002', centre_name: 'Rajkot', state: 'out',
    days_out: 2, pieces: 3, pending: 3, sent_date: '2026-09-09',
    expected_date: '2026-09-16' },
  { id: 3, lot_no: 'HM-2026-0000', centre_name: 'Rajkot', state: 'received',
    days_out: 6, pieces: 9, pending: 0, passed: 8, failed: 1,
    received_date: '2026-08-07', charge_total: 478 }]);
chk('two lots are still out', E('out-count').textContent === 2);
chk('one is finished', E('done-count').textContent === 1);
chk('a late lot says how late', /14d late/.test(E('out-lots').innerHTML));
chk('one merely out says how long', /2d out/.test(E('out-lots').innerHTML));
chk('a lot half back shows what is left there',
    /6 of 6 still there/.test(E('out-lots').innerHTML));
chk('a finished lot shows what was marked', /8 marked/.test(E('done-lots').innerHTML));
chk('and what it cost', /Rs 478/.test(E('done-lots').innerHTML));
chk('only an open lot can be booked in',
    E('done-lots').innerHTML.indexOf('Book in') < 0);
ctx.renderLots([]);
chk('no lots says so', /Nothing at the centre/.test(E('out-lots').innerHTML));

console.log('-- the counters across the top --');
ctx.renderTiles({ waiting: { pieces: 5, gross_wt: 105.26 },
                  out: { lots: 2, pieces: 9, gross_wt: 61.3 },
                  late: { lots: 1 }, failed: { pieces: 1, gross_wt: 4.1 },
                  marked: 8, saleable_gap: 14 });
chk('stock waiting to go', /Waiting to go/.test(E('tiles').innerHTML));
chk('an overdue lot is shown in the alert colour',
    /tile is-alert/.test(E('tiles').innerHTML));
chk('a tile is a way in, not just a number',
    /onclick="jumpTo\('sec-/.test(E('tiles').innerHTML));
// The number an owner actually wants: stock bought and paid for that
// cannot legally be sold this morning.
chk('and what cannot be sold is on the screen', /Cannot be sold/.test(E('tiles').innerHTML));

console.log('-- booking a lot back in --');
ctx.RC = { id: 1, _rows: [{ tag_id: 'T-1' }, { tag_id: 'T-2' }] };
E('rc-h0').setAttribute('data-tag', 'T-1');
E('rc-h1').setAttribute('data-tag', 'T-2');
E('rc-h0').value = 'A1B2C3';
let rows = ctx.receiveRows();
chk('a number typed means the piece passed', rows[0].result === 'pass');
chk('a row left blank is left for later', rows[1].result === 'pending');

E('rc-f1').checked = true;
ctx.onFail(1);
rows = ctx.receiveRows();
chk('ticking failed marks it failed', rows[1].result === 'fail');
chk('and the number field is closed off', E('rc-h1').disabled === true);
chk('and what was typed in it is gone', E('rc-h1').value === '');
// A failed piece was never marked. Even if a number somehow sits in the
// box, it must not reach the save, or an unhallmarked piece becomes
// saleable stock. That is the whole risk this screen exists to stop.
E('rc-h1').value = 'ZZ9999';
rows = ctx.receiveRows();
chk('and a stray number never reaches the save', rows[1].huid === '');
E('rc-h1').value = '';
E('rc-f1').checked = false;
ctx.onFail(1);
chk('un-ticking opens the field again', E('rc-h1').disabled === false);

console.log('-- numbers are read off a loupe --');
E('rc-h0').value = 'a1-b2 c3';
ctx.onHuid(0);
chk('typing is tidied to what is struck', E('rc-h0').value === 'A1B2C3');
E('rc-h0').value = 'AO0B12';
ctx.onHuid(0);
chk('confusable characters are flagged', /Check O and 0/.test(E('rc-n0').textContent));
chk('but never blocked', ctx.receiveRows()[0].result === 'pass');
E('rc-h0').value = 'AB2C34';
ctx.onHuid(0);
chk('a clean number says nothing', E('rc-n0').textContent === '');

console.log('-- the running count while typing --');
// One number typed, one piece failed, one row untouched: the footer is
// the only place a shopkeeper can see that before pressing save.
E('rc-h0').value = 'AB2C34';
E('rc-h1').value = '';
E('rc-f1').checked = true;
ctx.onFail(1);
ctx.updateReceiveState();
chk('one marked', /<b>1<\/b> marked/.test(E('rc-state').innerHTML));
chk('one failed', /<b>1<\/b> failed/.test(E('rc-state').innerHTML));
E('rc-f1').checked = false;
ctx.onFail(1);
ctx.updateReceiveState();
chk('and a row left alone is counted as later',
    /left for later/.test(E('rc-state').innerHTML));

console.log('-- a screen that cannot load says so on every card --');
const LISTS = ['waiting', 'out-lots', 'done-lots'];
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
ctx.window.pywebview = undefined;

console.log('-- it keeps itself current --');
chk('a refresh interval is set', timers.indexOf(90000) >= 0);
chk('and a shorter one keeps the label honest', timers.indexOf(5000) >= 0);
chk('coming back to the window reloads',
    typeof listeners['visibilitychange'] === 'function');

console.log('-- but never over the top of someone working --');
ctx.document.activeElement = { tagName: 'INPUT', value: 'A1B2' };
chk('a part-typed number counts as busy', ctx.isBusyTyping() === true);
ctx.document.activeElement = { tagName: 'INPUT', value: '' };
chk('an empty field does not', ctx.isBusyTyping() === false);
ctx.document.activeElement = null;

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
    return out


def node_available():
    try:
        subprocess.run(['node', '--version'], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def main():
    if not os.path.isfile(PAGE):
        print('FAIL: ui/hallmark.html not found')
        return 1
    with open(PAGE, encoding='utf-8', errors='replace') as f:
        html = f.read()

    print('-- the shape of the page --')
    problems = check_layout(html)
    for p in problems:
        print('  [FAIL] ' + p)
    if not problems:
        print('  [PASS] two stacks, every counter has a card, and it waits for the bridge')
    print('')

    if not node_available():
        print('node is not on PATH, so the panel could not be rendered.')
        return 1 if problems else 0

    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
    if not blocks:
        print('FAIL: no inline script found in ui/hallmark.html')
        return 1

    tmp = tempfile.mkdtemp(prefix='aurum_hmui_')
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
