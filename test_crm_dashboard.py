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
function mk(id) { return { id: id, innerHTML: '', textContent: '', value: '', className: '', style: {} }; }

const ctx = {
  window: { addEventListener: function () {} },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  document: {
    addEventListener: function () {}, readyState: 'complete',
    getElementById: function (id) { if (!els[id]) els[id] = mk(id); return els[id]; },
    querySelector: function () { return null; }
  },
  console, setTimeout: function () {},
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

let fail = 0;
const chk = (label, cond) => { if (!cond) fail++; console.log('  [' + (cond ? 'PASS' : 'FAIL') + '] ' + label); };

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

console.log('-- the counters across the top --');
ctx.renderTiles({
  counts: { customers: 2, segments: { vip: 1, lapsed: 1 }, open_followups: 3,
            open_orders: 1, active_plans: 2, plans_behind: 1 },
  occasions: [{}], dues: [{ total: 9000 }] });
chk('customers counted', /Customers/.test(els['tiles'].innerHTML));
chk('money owed totalled', /Rs 9,000/.test(els['tiles'].innerHTML));
chk('plans behind shown in the alert colour', /tile alert/.test(els['tiles'].innerHTML));

console.log('');
if (fail) { console.log(fail + ' FAILED'); process.exit(1); }
console.log('ALL PASS');
"""


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
