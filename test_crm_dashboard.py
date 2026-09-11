"""Render the CRM dashboard's lists and check what they actually say.

Run from the project root:  python test_crm_dashboard.py   (needs node on PATH)

test_crm.py covers the arithmetic and the storage. This covers the half that
decides what a shopkeeper sees: that a birthday today is marked today and not
buried among next month's, that the oldest debt is flagged, that a savings
plan which has fallen behind sorts above the ones that are fine, and that
money is grouped the Indian way rather than the American.

It pulls the real functions out of ui/crm.html and runs them against a stub
DOM, so it breaks when the page breaks. No browser, no database, no network.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(__file__))
PAGE = os.path.join(ROOT, 'ui', 'crm.html')

CHECKS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const els = {};
const timers = [];
const listeners = {};
function mk(id) {
  return { id: id, innerHTML: '', textContent: '', value: '', className: '',
           style: {}, hidden: false, disabled: false,
           addEventListener: function () {} };
}

const ctx = {
  window: { addEventListener: function () {},
            apiReady: function (cb) { ctx.__booted = cb; } },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  document: {
    hidden: false, activeElement: null, readyState: 'complete',
    addEventListener: function (t, f) { listeners[t] = f; },
    getElementById: function (id) { if (!els[id]) els[id] = mk(id); return els[id]; },
    querySelector: function () { return null; }
  },
  console, setTimeout: function () {},
  setInterval: function (f, ms) { timers.push(ms); return timers.length; },
  clearInterval: function () {},
  parseFloat, parseInt, JSON, Math, Date, String, Number, Array, Object, RegExp,
  isNaN, isFinite
};
ctx.globalThis = ctx;
vm.createContext(ctx);
try { vm.runInContext(src, ctx); } catch (e) { /* page-load wiring */ }

if (typeof ctx.renderOccasions !== 'function') {
  console.log('  [FAIL] could not load the dashboard functions');
  process.exit(1);
}

const E = (id) => ctx.document.getElementById(id);
let fail = 0;
const chk = (label, cond) => { if (!cond) fail++; console.log('  [' + (cond ? 'PASS' : 'FAIL') + '] ' + label); };

console.log('-- it waits for the program before judging it --');
// pywebview hands the page its bridge a moment after the page is parsed.
// Asking on the first line always finds nothing, so the screen used to open
// claiming the build was too old, and only a reload made it look right.
chk('the page hands its start to the waiter', typeof ctx.__booted === 'function');
chk('and asks for nothing before then', E('head-sub').textContent === '');
chk('while it says it is connecting', E('live-text').textContent === 'connecting');

ctx.__booted();   // let the page start, as pywebview would

console.log('-- money is grouped the Indian way --');
chk('a lakh reads 1,23,456', ctx.money(123456) === 'Rs 1,23,456');
chk('rounded to the rupee', ctx.money(4999.6) === 'Rs 5,000');
chk('nothing is zero, not NaN', ctx.money(null) === 'Rs 0');
chk('so is junk', ctx.money('abc') === 'Rs 0');

console.log('-- customer names are typed by people, so they are escaped --');
chk('tags cannot get through', ctx.esc('<b>x</b>') === '&lt;b&gt;x&lt;/b&gt;');
chk('nor quotes', ctx.esc('a"b').indexOf('&quot;') >= 0);
chk('nothing renders as nothing', ctx.esc(null) === '');

console.log('-- who to call --');
ctx.renderOccasions([
  { name: 'Asha', occasion: 'birthday', days_until: 0, phone: '999', turns: 36 },
  { name: 'Bhavin', occasion: 'anniversary', days_until: 5, turns: 10 }]);
chk('today is marked today', /pill-today">Today</.test(els['occasions'].innerHTML));
chk('and the rest count down', /5 days/.test(els['occasions'].innerHTML));
chk('both names appear', /Asha/.test(els['occasions'].innerHTML) && /Bhavin/.test(els['occasions'].innerHTML));
ctx.renderOccasions([]);
chk('an empty list says so plainly', /Nobody to call/.test(els['occasions'].innerHTML));

console.log('-- money owed, by age --');
ctx.renderAgeing({ '0-30': 1000, '31-60': 0, '61-90': 0, '90+': 4000 },
                 [{ customer: 'Old Debt', total: 4000, oldest_days: 253, buckets: {} }]);
chk('four buckets', (els['ageing'].innerHTML.match(/age-cell/g) || []).length === 4);
chk('the 90-day bucket is flagged', /age-cell hot/.test(els['ageing'].innerHTML));
chk('and the customer shows how old it is',
    /Old Debt/.test(els['dues'].innerHTML) && /253/.test(els['dues'].innerHTML));

console.log('-- savings plans put the problems first --');
ctx.renderPlans([
  { customer: 'OnTrack', scheme_name: 'P', status: 'on_track', paid_total: 1000, paid_count: 2, instalments: 12 },
  { customer: 'Behind', scheme_name: 'P', status: 'behind', behind_by: 5000, paid_total: 2000, paid_count: 1, instalments: 12 }]);
chk('a plan that has fallen behind sorts above one that has not',
    els['plans'].innerHTML.indexOf('Behind') < els['plans'].innerHTML.indexOf('OnTrack'));
chk('and it says how far behind', /Behind Rs 5,000/.test(els['plans'].innerHTML));

console.log('-- anything overdue says how late --');
ctx.renderFollowups([{ id: 1, task: 'Call', customer: 'Asha', due_date: '2000-01-01' }]);
chk('a late follow-up', /d late/.test(els['followups'].innerHTML));
ctx.renderOrders([{ id: 2, description: 'Clasp', customer: 'Asha', kind: 'repair',
                    promised_date: '2000-01-01', advance: 500 }]);
chk('a late order', /d late/.test(els['orders'].innerHTML));
chk('and an advance taken is shown', /advance Rs 500/.test(els['orders'].innerHTML));

console.log('-- a customer name is typed by a person --');
// Every name on this screen is clickable, and the name goes inside a
// single-quoted onclick attribute. One apostrophe would otherwise close the
// attribute early and take the rest of the row's markup with it.
ctx.renderTop([{ name: "D'Souza & Co", lifetime_value: 1000, bill_count: 2, segment_computed: 'vip' }]);
const nameHtml = els['top'].innerHTML;
// Assert on the click handler specifically. Checking for "D'Souza" anywhere
// passes against the visible row text whether or not the attribute is safe,
// which is the bug this is meant to catch.
const wanted = "openCustomer('D\\'Souza & Co')";
chk('the whole name reaches the click handler, apostrophe escaped',
    nameHtml.indexOf(wanted) >= 0);
chk('and the row is still one row', (nameHtml.match(/class="row /g) || []).length === 1);
chk('the attribute is not cut short at the apostrophe',
    nameHtml.indexOf("openCustomer('D')") < 0);
ctx.renderTop([{ name: '<img src=x>', lifetime_value: 1, bill_count: 1, segment_computed: 'new' }]);
chk('a tag in a name cannot become markup', els['top'].innerHTML.indexOf('<img') < 0);

console.log('-- the counters across the top --');
ctx.renderTiles({
  counts: { customers: 2, segments: { vip: 1, lapsed: 1 }, open_followups: 3,
            open_orders: 1, active_plans: 2, plans_behind: 1 },
  occasions: [{}], dues: [{ total: 9000 }] });
chk('customers counted', /Customers/.test(els['tiles'].innerHTML));
chk('a tile is a way in, not just a number', /onclick="jumpTo\('sec-/.test(els['tiles'].innerHTML));
chk('money owed totalled', /Rs 9,000/.test(els['tiles'].innerHTML));
chk('plans behind shown in the alert colour', /tile is-alert/.test(els['tiles'].innerHTML));

console.log('-- the screen keeps itself current --');
chk('a refresh interval is set', timers.indexOf(60000) >= 0);
chk('and a shorter one keeps the label honest', timers.indexOf(5000) >= 0);
chk('coming back to the window reloads', typeof listeners['visibilitychange'] === 'function');

console.log('-- but never over the top of someone working --');
// Rebuilding the lists under a half-written follow-up loses what was typed,
// which is the failure that makes auto-refresh worse than a button.
ctx.document.activeElement = { tagName: 'INPUT', value: 'half typed note' };
chk('a part-filled field counts as busy', ctx.isBusyTyping() === true);
ctx.document.activeElement = { tagName: 'INPUT', value: '' };
chk('an empty one does not', ctx.isBusyTyping() === false);
ctx.document.activeElement = null;

console.log('-- a birthday wish is offered on the day and not before --');
ctx.renderOccasions([
  { name: 'Asha', occasion: 'birthday', days_until: 0, phone: '99', turns: 36 },
  { name: 'Later', occasion: 'birthday', days_until: 5, phone: '99', turns: 20 }]);
const occHtml = E('occasions').innerHTML;
chk("today's gets a Wish button", occHtml.indexOf("wishOne('Asha','birthday')") >= 0);
chk('one five days away does not', occHtml.indexOf("wishOne('Later'") < 0);
chk('and the row still opens the customer', occHtml.indexOf("openCustomer('Asha')") >= 0);
ctx.renderOccasions([{ name: 'NoPhone', occasion: 'birthday', days_until: 0, phone: '', turns: 1 }]);
chk('nor does somebody with no number', E('occasions').innerHTML.indexOf('wishOne') < 0);

console.log('-- a campaign is a queue worked through, not a blast --');
ctx.CAMP = { queue: [{ name: 'A', phone: '91', text: 'hi A', url: 'https://wa.me/91?text=hi' },
                     { name: 'B', phone: '92', text: 'hi B', url: 'https://wa.me/92?text=hi' }],
             at: 0, kind: 'new_item', skipped: [] };
ctx.showCurrent();
chk('it shows one at a time', E('camp-count').textContent === '1 of 2');
chk('with the message to read first', E('camp-preview').textContent === 'hi A');
ctx.skipOne();
chk('skip moves to the next', E('camp-count').textContent === '2 of 2');
ctx.skipOne();
chk('and it ends rather than looping', E('camp-title').textContent === 'All done');

console.log('-- one card, four things you might do --');
// Four tabs share one card. A switch that only knows about two leaves the
// other two open underneath, which is what a half-updated toggle looks like.
ctx.showAdd('camp');
chk('the chosen tab opens', E('add-camp').hidden === false);
chk('and the others close',
    E('add-fu').hidden && E('add-or').hidden && E('add-pic').hidden);
chk('the tab is marked as current', E('tab-camp').className.indexOf(' on') >= 0);
chk('and only that one is', E('tab-fu').className.indexOf(' on') < 0);
ctx.showAdd('pic');
chk('switching again closes the last one', E('add-camp').hidden === true);
chk('and opens the new one', E('add-pic').hidden === false);

console.log('-- a screen that cannot load says so on every card --');
// The bridge is missing when the app is running an older process than the
// files on disk. Only the header used to mention it; every card sat on
// "Loading..." for ever, which reads as a hang rather than a restart.
LISTS = ['occasions', 'followups', 'orders', 'dues', 'plans', 'lapsed', 'top'];
const saysOnEveryCard = (word) => {
  let n = 0;
  for (const id of LISTS) { if (new RegExp(word, 'i').test(E(id).innerHTML)) n++; }
  return n === LISTS.length;
};

// No bridge at all: the screen never reached the program.
ctx.window.pywebview = undefined;
ctx.loadAll(true);
chk('no bridge is reported as no connection', /could not reach/i.test(E('head-sub').textContent));
chk('and every card says the same', saysOnEveryCard('could not reach'));

// A bridge without the customer methods: it reached an older program.
ctx.window.pywebview = { api: { something_else: function () {} } };
ctx.loadAll(true);
chk('an older program is reported as a restart', /reopen/i.test(E('head-sub').textContent));
chk('and every card says the same', saysOnEveryCard('restart'));
// The two are different problems and must not share one message.
chk('the two failures do not read alike', !/could not reach/i.test(E('top').innerHTML));

let stillLoading = 0;
for (const id of LISTS) { if (/Loading/.test(E(id).innerHTML)) stillLoading++; }
chk('neither leaves a card on Loading', stillLoading === 0);
ctx.window.pywebview = undefined;

console.log('');
if (fail) { console.log(fail + ' FAILED'); process.exit(1); }
console.log('ALL PASS');
"""


def check_layout(html):
    """The shape of the page, which no amount of script testing would catch.

    Three stacks cannot fill two cells. On any window too narrow for three,
    the last one dropped to a second row and sat under a hole the height of
    the tallest column. Two stacks have no second row to fall to.
    """
    out = []
    cols = re.findall(r'<div class="col">', html)
    if len(cols) != 2:
        out.append('expected 2 column stacks, found %d' % len(cols))

    first = re.search(r'<div class="col">\s*<div class="card" id="([\w-]+)"', html)
    if not first:
        out.append('could not find the first card of the first column')
    elif first.group(1) != 'sec-do':
        out.append('the card you type into should lead the first column, '
                   'found %s' % first.group(1))

    # Stacked on a narrow window the lists matter more than the form, so the
    # form is ordered last rather than pushing them all down a screen.
    if not re.search(r'#sec-do\{order:\s*9', html):
        out.append('the form is not ordered below the lists when stacked')

    # Every tile is a shortcut to a card; a renamed card would break one.
    for target in re.findall(r"go:'([\w-]+)'", html):
        if ('id="%s"' % target) not in html:
            out.append('a counter points at %s, which is not on the page' % target)
    return out


def node_available():
    try:
        subprocess.run(['node', '--version'], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def main():
    if not node_available():
        print('node is not on PATH, so the dashboard could not be rendered.')
        print('Install node, or run this on a machine that has it.')
        return 0
    if not os.path.isfile(PAGE):
        print('FAIL: ui/crm.html not found')
        return 1

    with open(PAGE, encoding='utf-8', errors='replace') as f:
        html = f.read()
    print('-- the shape of the page --')
    problems = check_layout(html)
    for p in problems:
        print('  [FAIL] ' + p)
    if not problems:
        print('  [PASS] two stacks, the form leads, every counter has a card')
    print('')

    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
    if not blocks:
        print('FAIL: no inline script found in ui/crm.html')
        return 1

    tmp = tempfile.mkdtemp(prefix='aurum_crmdash_')
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
        if problems:
            return 1
        if done.returncode != 0 and (done.stderr or '').strip():
            sys.stdout.write(done.stderr)
        return done.returncode
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
