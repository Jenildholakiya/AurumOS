# -*- coding: utf-8 -*-
"""Finance desk — heads, cash/bank, vouchers, daybook, trial balance."""
import os
import sys
import tempfile

sys.path.insert(0, 'E:\\AurumOS_Client')
os.environ['AURUM_DB_DIR'] = tempfile.mkdtemp()
os.environ['AURUM_DB_PATH'] = os.path.join(os.environ['AURUM_DB_DIR'], 't.db')

from database.db_manager import DBManager

passed, failed = 0, 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}')


db = DBManager()

print('-- heads register --')
check('blank code refused',
      db.finance_save_account({'code': '', 'name': 'X'})['status'] == 'error')
check('save cash head',
      db.finance_save_account({'code': 'CASH-01', 'name': 'Main Cash',
                               'type': 'ASSET', 'kind': 'CASH',
                               'opening_balance': 50000})['status'] == 'success')
check('save bank head',
      db.finance_save_account({'code': 'HDFC-01', 'name': 'HDFC Current',
                               'type': 'ASSET', 'kind': 'BANK',
                               'bank_name': 'HDFC', 'account_no': '1234',
                               'opening_balance': 200000})['status'] == 'success')
check('save sales head',
      db.finance_save_account({'code': 'SALES', 'name': 'Sales',
                               'type': 'INCOME', 'balance_type': 'Cr'})['status'] == 'success')
check('save capital head (offsets the openings)',
      db.finance_save_account({'code': 'CAPITAL', 'name': 'Owner Capital',
                               'type': 'EQUITY', 'balance_type': 'Cr',
                               'opening_balance': 250000})['status'] == 'success')
check('update keeps code',
      db.finance_save_account({'code': 'CASH-01', 'name': 'Main Cash Counter'})['status'] == 'success')
all_heads = db.finance_accounts()
check('four heads listed', len(all_heads.get('accounts', [])) == 4)
cb = db.finance_accounts(cash_bank_only=True)
check('cash/bank filter', len(cb.get('accounts', [])) == 2)

print('-- position --')
pos = db.finance_cash_position()
check('position balances',
      pos.get('cash') == 50000 and pos.get('bank') == 200000
      and pos.get('total') == 250000)

print('-- voucher numbering --')
n1 = db.finance_next_voucher_no('CASH_RCPT')
check('first receipt number', n1.get('vch_no') == 'CR-001')
check('unknown type falls back',
      db.finance_next_voucher_no('NOPE').get('vch_no', '').startswith('V-'))

print('-- posting guards --')
check('unbalanced refused',
      db.finance_post_voucher({'type': 'CASH_RCPT', 'lines': [
          {'account_code': 'CASH-01', 'dr': 1000, 'cr': 0},
          {'account_code': 'SALES', 'dr': 0, 'cr': 999}]})['status'] == 'error')
check('unknown account refused',
      db.finance_post_voucher({'type': 'CASH_RCPT', 'lines': [
          {'account_code': 'CASH-01', 'dr': 1000, 'cr': 0},
          {'account_code': 'GHOST', 'dr': 0, 'cr': 1000}]})['status'] == 'error')
check('single line refused',
      db.finance_post_voucher({'type': 'CASH_RCPT', 'lines': [
          {'account_code': 'CASH-01', 'dr': 1000, 'cr': 0}]})['status'] == 'error')
check('bad type refused',
      db.finance_post_voucher({'type': 'NOPE', 'lines': []})['status'] == 'error')

print('-- post a receipt --')
r = db.finance_post_voucher({'type': 'CASH_RCPT', 'narration': 'Sale 1', 'lines': [
    {'account_code': 'CASH-01', 'dr': 10000, 'cr': 0},
    {'account_code': 'SALES', 'dr': 0, 'cr': 10000}]})
check('receipt posts', r.get('status') == 'success' and r.get('vch_no') == 'CR-001')
n2 = db.finance_next_voucher_no('CASH_RCPT')
check('sequence advances', n2.get('vch_no') == 'CR-002')

print('-- contra --')
c = db.finance_post_voucher({'type': 'CONTRA', 'narration': 'Cash to bank', 'lines': [
    {'account_code': 'HDFC-01', 'dr': 20000, 'cr': 0},
    {'account_code': 'CASH-01', 'dr': 0, 'cr': 20000}]})
check('contra posts with CT prefix',
      c.get('status') == 'success' and c.get('vch_no') == 'CT-001')

print('-- position after posting --')
pos2 = db.finance_cash_position()
check('cash moved to bank',
      pos2.get('cash') == 40000 and pos2.get('bank') == 220000)

print('-- daybook --')
day = db.finance_daybook()
check('two entries today', len(day.get('entries', [])) == 2)
check('lines summary parses',
      all(':' in (e.get('lines') or '') for e in day.get('entries', [])))

print('-- trial balance --')
tb = db.finance_trial_balance().get('trial', {})
check('trial balances', tb.get('balanced') is True)
check('dr equals cr', tb.get('total_dr') == tb.get('total_cr'))
codes = {r['code'] for r in tb.get('rows', [])}
check('all heads present',
      codes == {'CASH-01', 'HDFC-01', 'SALES', 'CAPITAL'})

print('-- delete guards --')
check('posted head refused',
      db.finance_delete_account('CASH-01')['status'] == 'error')
check('clean head deletes',
      db.finance_save_account({'code': 'TEMP', 'name': 'Temp'})['status'] == 'success'
      and db.finance_delete_account('TEMP')['status'] == 'success')
check('delete-all keeps posted, drops rest',
      db.finance_save_account({'code': 'TEMP2', 'name': 'Temp2'})['status'] == 'success'
      and db.finance_delete_all().get('deleted') == 2)

print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
