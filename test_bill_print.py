"""Render a bill both ways and check the two are actually different.

Run from the project root:  python test_bill_print.py   (needs node on PATH)

One template prints both bills. A GST bill carries the shop's GSTIN, the state
it is registered in, the HSN code and the CGST/SGST split; a normal bill
carries none of it, even when those figures are sitting in the same payload.
That is easy to get subtly wrong and impossible to notice, because the person
who would notice is a tax officer months later.

So this pulls the real functions out of ui/bill_print.html, renders a retail
bill and a wholesale bill in both modes, and asserts on the HTML. No browser,
no database, no printer.

Worth knowing what this caught when it was written: every layout had the words
"Tax Invoice" hardcoded, so a shop with no GST registration at all was handing
customers something that called itself a tax invoice.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(__file__))
TEMPLATE = os.path.join(ROOT, 'ui', 'bill_print.html')

CHECKS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');  // [0]=node [1]=this file

function load() {
  const c = {
    window: { addEventListener: () => {} },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: { addEventListener: () => {}, getElementById: () => null,
                readyState: 'complete', body: { innerHTML: '' }, title: '' },
    console, setTimeout, parseFloat, parseInt, JSON, Math, Date,
    String, Number, Array, Object, RegExp, isNaN, isFinite
  };
  c.globalThis = c;
  vm.createContext(c);
  try { vm.runInContext(src, c); } catch (e) { /* page-load wiring, not needed */ }
  if (typeof c.buildSlip !== 'function') {
    console.log('  [FAIL] could not load buildSlip from the template');
    process.exit(1);
  }
  return c;
}

const ctx = load();
const SHOP = { shop_name: 'Test Jewellers', shop_gstin: '24AAACC1206D1ZM',
               shop_state: 'Gujarat', shop_state_code: '24',
               shop_hsn: '7113', shop_gst_registered: '1' };

const RETAIL = Object.assign({
  vch_id: 'R-1234567', kind: 'sale', customer: 'Ramesh', status: 'PAID',
  items: [{ code: 'Gold Ring', weight: 5.2, touch: 91.6, rate: 7200, qty: 1,
            fine: 4.76, making: 800, amount: 38240, huid: 'H1' }],
  sub_total: 38240, grand_total: 39387, cgst: 573.6, sgst: 573.6,
  gst_total: 1147.2, cgst_rate: 1.5, sgst_rate: 1.5, making_total: 800
}, SHOP);

const WHOLESALE = Object.assign({
  vch_id: 'S-42', customer: 'Mahesh', status: 'PAID',
  items: [{ it_code: '68', nt_wt: 10, touch: 68, huid: '' }],
  totalAmount: 52000, goldRate: 7200, gstAmount: 1560, gstRate: 3
}, SHOP);

let fail = 0;
const chk = (label, cond) => {
  if (!cond) fail++;
  console.log('  [' + (cond ? 'PASS' : 'FAIL') + '] ' + label);
};
const render = (D, mode) => {
  ctx.window.__BILL_MODE__ = mode;
  return ctx.buildSlip(Object.assign({}, D), '');
};

console.log('-- retail, GST bill --');
let g = render(RETAIL, 'gst');
chk('banner reads Tax Invoice', /invoice-banner">Tax Invoice/.test(g));
chk('HSN code is shown', /HSN 7113/.test(g));
chk('the shop GSTIN is printed', /GSTIN: 24AAACC1206D1ZM/.test(g));
chk('state and state code are printed', /State: Gujarat \(24\)/.test(g));
chk('CGST and SGST are broken out', /CGST @ 1.5%/.test(g) && /SGST @ 1.5%/.test(g));

console.log('-- retail, normal bill, same payload --');
let n = render(RETAIL, 'normal');
chk('exactly one banner, and it reads Invoice',
    (n.match(/invoice-banner/g) || []).length === 1 && /invoice-banner">Invoice</.test(n));
chk('the words Tax Invoice appear nowhere', !/Tax Invoice/i.test(n));
chk('no GSTIN, HSN or state', !/GSTIN/.test(n) && !/HSN/.test(n) && !/State: Gujarat/.test(n));
chk('no CGST or SGST line', !/CGST/.test(n) && !/SGST/.test(n));
chk('the item and the total are still printed',
    /Gold Ring/.test(n) && /39,387|39387/.test(n));

console.log('-- wholesale --');
let wg = render(WHOLESALE, 'gst'), wn = render(WHOLESALE, 'normal');
chk('GST bill: Tax Invoice, GSTIN and a GST row',
    /Tax Invoice/.test(wg) && /GSTIN/.test(wg) && /GST @ 3%/.test(wg));
chk('normal bill: none of the three',
    /invoice-banner">Invoice</.test(wn) && !/GSTIN/.test(wn) && !/GST @/.test(wn));

console.log('-- falling back rather than printing something unusable --');
chk('GST asked for but the shop has no GSTIN -> plain bill',
    !/Tax Invoice/i.test(render(Object.assign({}, RETAIL, { shop_gstin: '' }), 'gst')));
chk('no mode injected at all -> plain bill', (function () {
  ctx.window.__BILL_MODE__ = undefined;
  return !/Tax Invoice/i.test(ctx.buildSlip(Object.assign({}, RETAIL), ''));
})());

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
        print('node is not on PATH, so the bill template could not be rendered.')
        print('Install node, or run this on a machine that has it.')
        return 0

    with open(TEMPLATE, encoding='utf-8', errors='replace') as f:
        html = f.read()
    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
    if not blocks:
        print('FAIL: no inline script found in ui/bill_print.html')
        return 1

    tmp = tempfile.mkdtemp(prefix='aurum_billprint_')
    tpl = os.path.join(tmp, 'template.js')
    runner = os.path.join(tmp, 'checks.js')
    try:
        with open(tpl, 'w', encoding='utf-8') as f:
            f.write('\n'.join(blocks))
        with open(runner, 'w', encoding='utf-8') as f:
            f.write(CHECKS)
        done = subprocess.run(['node', runner, tpl], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=120)
        sys.stdout.write(done.stdout or '')
        if done.returncode != 0 and (done.stderr or '').strip():
            sys.stdout.write(done.stderr)
        return done.returncode
    finally:
        for p in (tpl, runner):
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
