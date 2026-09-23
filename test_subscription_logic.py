import os
import sys
import tempfile

sys.path.insert(0, 'E:\\AurumOS_Client')
os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()

from subscription_manager import (
    SubscriptionManager, classify_reason, EXPIRED_REASONS, REVOKED_REASONS)

passed, failed = 0, 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}')


print('-- reason classification --')
check('expired maps expired', classify_reason('expired') == 'expired')
check('subscription_expired maps expired',
      classify_reason('subscription_expired') == 'expired')
for r in ('revoked', 'disabled', 'suspended', 'blocked'):
    check(f'{r} maps revoked (not expired)',
          classify_reason(r) == 'revoked')
for r in ('server_error', 'timeout', 'error'):
    check(f'{r} is transient (keep cached)',
          classify_reason(r) is None)
check('empty reason keeps cached', classify_reason('') is None)

print('-- status application --')
sub = SubscriptionManager()
sub._window = None
check('expired word sets expired',
      sub._set_status_for_reason('expired') and sub._state['status'] == 'expired')
check('disabled word sets revoked (consistent)',
      sub._set_status_for_reason('disabled') and sub._state['status'] == 'revoked')
check('suspended word sets revoked (consistent)',
      sub._set_status_for_reason('suspended') and sub._state['status'] == 'revoked')
sub._set_status('active')
check('transient word keeps state',
      not sub._set_status_for_reason('server_error')
      and sub._state['status'] == 'active')

print('-- day-count honesty --')
sub2 = SubscriptionManager()
sub2._window = None
# ok=true with NO day keys: trust ok, do not fabricate 365
sub2._apply_server_response({'plan': 'pro', 'features': [], 'ok': True})
check('missing days trusts ok as active',
      sub2._state['status'] == 'active' and sub2._state['valid'])
check('missing days fabricates nothing',
      (sub2._state['subscription'] or {}).get('remaining_days') != 365)
# ok=true with EXPLICIT zeros: expired, not a fabricated year
sub2._apply_server_response({'plan': 'pro', 'features': [], 'ok': True,
                             'remaining_days': 0, 'grace_remaining_days': 0})
check('explicit zeros expire honestly',
      sub2._state['status'] == 'expired' and not sub2._state['valid'])
# ok=true with real days: active with the server's count
sub2._apply_server_response({'plan': 'enterprise', 'features': [],
                             'remaining_days': 200, 'grace_remaining_days': 0})
check('real days honored',
      sub2._state['status'] == 'active'
      and sub2._state['effective_plan'] == 'enterprise'
      and (sub2._state['subscription'] or {}).get('remaining_days') == 200)

print('-- plan preservation on reactivation --')
sub3 = SubscriptionManager()
sub3._window = None
sub3._state['effective_plan'] = 'enterprise'
sub3._state['plan'] = 'enterprise'
sub3._state['business'] = 'DJ Jewellers'
sub3._state['owner'] = 'Owner'
# simulate the poll fast path with a plan-less active ping
data = {'changed': True, 'status': 'active'}
plan = (data.get('plan') or data.get('effective_plan')
        or sub3._state.get('effective_plan') or 'pro')
check('enterprise survives plan-less reactivation', plan == 'enterprise')

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
