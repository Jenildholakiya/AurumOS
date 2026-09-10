"""Hold every copy of the plan rules against each other.

Run from the project root:  python test_plan_tiers.py   (needs node on PATH)

Which features a plan includes is written down in six places: the Python
subscription manager, the subscription script, the boot script's plan map, the
sidebar's lock tooltips, and the plan lists shown on the pricing screens in
both Python and HTML. Nothing kept them in step.

They drifted, and the result was a shop paying for Pro, seeing the feature
listed on its own pricing screen, and still being told to upgrade to
Enterprise. Every layer stripped it back out on the way through.

So this compares them. If a feature moves between plans and one of the six is
missed, this fails and names the file.
"""

import ast
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.dirname(__file__))
os.chdir(REPO)
FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


# -- reading the JavaScript ------------------------------------------------

EXTRACT = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

// Find `var NAME = <literal>` and balance brackets to its end, so nested
// objects and arrays come out whole. Plain text search rather than a regex:
// the backslashes needed to build one survive too few round trips between
// Python, the shell and node to be worth it.
function grab(name) {
  let at = src.indexOf('var ' + name + ' =');
  if (at === -1) at = src.indexOf('var ' + name + '=');
  if (at === -1) throw new Error('not found: ' + name);
  const from = src.indexOf('=', at + 4 + name.length) + 1;
  let depth = 0, i = from, started = false;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === '[' || c === '{' || c === '(') { depth++; started = true; }
    else if (c === ']' || c === '}' || c === ')') { depth--; }
    if (started && depth === 0) { i++; break; }
  }
  return src.slice(from, i);
}

const names = process.argv.slice(3);
const body = names.map(n => 'var ' + n + ' = ' + grab(n) + ';').join('\n');
const out = new Function(body + '\nreturn {' +
  names.map(n => JSON.stringify(n) + ':' + n).join(',') + '};')();
process.stdout.write(JSON.stringify(out));
"""


def js_values(path, *names):
    handle, script = tempfile.mkstemp(suffix='.js')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as f:
            f.write(EXTRACT)
        done = subprocess.run(['node', script, path, *names],
                              capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError(f'could not read {path}: {done.stderr.strip()[:200]}')
        return json.loads(done.stdout)
    finally:
        os.unlink(script)


def py_plans_from(path, pattern):
    """Pull the plan dicts out of a pricing list without importing the file."""
    text = io.open(path, encoding='utf-8').read()
    out = {}
    for match in re.finditer(pattern, text, re.S):
        blob = match.group(0)
        ident = re.search(r"id'?\s*:\s*'([a-z]+)'", blob).group(1)
        feats = re.search(r"features'?\s*:\s*\[(.*?)\]", blob, re.S).group(1)
        out[ident] = [f.strip().strip("'\"") for f in feats.split(',') if f.strip()]
    return out


try:
    subprocess.run(['node', '--version'], capture_output=True, timeout=20)
except Exception:
    print('node is not on PATH, so the JavaScript copies could not be read.')
    sys.exit(0)

spec = importlib.util.spec_from_file_location('sm', 'subscription_manager.py')
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)

print("-- the Python and JavaScript tier lists agree --")
js = js_values('ui/subscription.js', 'LITE_FEATURES', 'PRO_FEATURES',
               'ENTERPRISE_FEATURES')
check("lite matches", sorted(js['LITE_FEATURES']), sorted(sm.LITE_FEATURES))
check("pro matches", sorted(js['PRO_FEATURES']), sorted(sm.PRO_FEATURES))
check("enterprise matches", sorted(js['ENTERPRISE_FEATURES']),
      sorted(sm.ENTERPRISE_FEATURES))

print("\n-- each tier contains the one below it --")
check("pro includes all of lite",
      sorted(set(sm.LITE_FEATURES) - set(sm.PRO_FEATURES)), [])
check("enterprise includes all of pro",
      sorted(set(sm.PRO_FEATURES) - set(sm.ENTERPRISE_FEATURES)), [])


def cheapest_plan(feature):
    for tier in ('lite', 'pro', 'enterprise'):
        if feature in sm.PLAN_FEATURES[tier]:
            return tier
    return None


print("\n-- the upgrade box names the plan that actually unlocks it --")
boot = js_values('ui/boot.js', '_FEATURE_PLAN')['_FEATURE_PLAN']
wrong = {f: (named, cheapest_plan(f))
         for f, named in boot.items()
         if cheapest_plan(f) and named != cheapest_plan(f)}
check("ui/boot.js _FEATURE_PLAN agrees with the tier lists", wrong, {})

print("\n-- the sidebar's lock tooltip names the same plan --")
side = js_values('ui/sidebar.js', '_FEATURE_MAP', '_FEATURE_PLANS')
tooltip_wrong = {}
for page, plan_label in side['_FEATURE_PLANS'].items():
    feature = side['_FEATURE_MAP'].get(page)
    real = cheapest_plan(feature) if feature else None
    if real and plan_label.strip().lower() != real:
        tooltip_wrong[page] = (plan_label, real)
check("ui/sidebar.js _FEATURE_PLANS agrees", tooltip_wrong, {})

print("\n-- the pricing screens list what the tiers actually include --")
py_plans = py_plans_from('main.py', r"\{'id': '(?:lite|pro|enterprise)'.*?accent'?\s*:\s*'[^']*'\}")
html_plans = py_plans_from('ui/expiry.html', r"\{id:'(?:lite|pro|enterprise)'.*?accent:'[^']*'\}")

for label, plans in (('main.py', py_plans), ('ui/expiry.html', html_plans)):
    for tier in ('lite', 'pro', 'enterprise'):
        if tier not in plans:
            continue
        check(f"{label} lists the right features for {tier}",
              sorted(plans[tier]), sorted(sm.PLAN_FEATURES[tier]))

print("\n-- the feature test page uses the same tiers --")
page = io.open('ui/feature_test.html', encoding='utf-8').read()
tiers = dict(re.findall(r"'([a-z_]+)':\s*\{tier:'([a-z]+)'", page))
mismatched = {f: (t, cheapest_plan(f)) for f, t in tiers.items()
              if cheapest_plan(f) and t != cheapest_plan(f)}
check("ui/feature_test.html agrees", mismatched, {})

print("\n-- what a Pro shop ends up with --")
pro = sm._enforce_plan_tier('pro', [])
check("multi-PC is granted even if the server never lists it",
      'lan_multi_pc' in pro, True)
check("a stale server record cannot take it away",
      'lan_multi_pc' in sm._enforce_plan_tier('pro', ['billing_retail']), True)
check("but enterprise features are still withheld",
      'cloud_sync' in sm._enforce_plan_tier('pro', ['cloud_sync']), False)
check("lite still gets nothing extra",
      'lan_multi_pc' in sm._enforce_plan_tier('lite', ['lan_multi_pc']), False)
check("a purchased seat token rides through unlisted",
      'terminals=6' in sm._enforce_plan_tier('pro', ['terminals=6']), True)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
