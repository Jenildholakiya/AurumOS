# -*- coding: utf-8 -*-
"""AurumOS Natural-Language Reporting engine.

Turns a plain-language business question -- English, Hinglish, Gujlish or
native script -- into a validated, parameterised query over the local SQLite
database, and returns a table + KPI + chart-ready payload.

Design rules (all deliberate):

1. OFFLINE FIRST. The parser is pure Python with a hand-built lexicon: no
   network, no API key, no extra dependency, works in the frozen EXE on a shop
   counter with no internet. It answers the questions a jeweller actually asks.

2. THE LLM NEVER WRITES SQL. An optional LLM assist (reusing the Groq key the
   owner already entered for BASTION AI) is only asked to pick a *plan* --
   source / metric / dimension / filters -- from the fixed catalog below, as
   JSON. The plan is then validated against that catalog before anything runs.
   An unrecognised name is rejected, not executed.

3. NO BUSINESS DATA LEAVES THE MACHINE. The LLM assist sends the question text
   and the catalog *names* only -- never a row, a total, or a customer list.

4. EVERY QUERY IS READ-ONLY AND WHITELISTED. Table and column expressions come
   from module constants only; every user value is a bound parameter; the
   connection is pinned with PRAGMA query_only=ON and a hard row cap applies.

5. THE PLAN IS SHOWN TO THE USER. The result carries the resolved metric,
   range, filters and the generated SQL so the UI can render them as editable
   controls. A misread question is corrected in one click instead of being
   silently wrong.

Date handling note
------------------
Every business date column in this app is stamped by SQLite's date('now'),
which is UTC -- see record_sale() in database/db_manager.py. So relative ranges
("today", "last month") are anchored to the DATABASE's own clock via
SELECT date('now'), not to Python's local time. That guarantees "today" always
means the same day the rows were stamped with.
"""

import json
import re
import difflib
import datetime

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------
DEFAULT_LIMIT = 100
TOP_N_DEFAULT = 10
MAX_ROWS = 5000          # hard cap; never exceeded regardless of the question
MAX_SCAN_ROWS = 200000   # cap on rows pulled for item-level (JSON) aggregation

# Column display types the UI knows how to format.
T_TEXT, T_INT, T_WT, T_MONEY, T_TOUCH = 'text', 'int', 'weight', 'money', 'touch'


# ==========================================================================
#  CATALOG -- the whitelist. Nothing outside this dict can ever be queried.
# ==========================================================================
# Each metric's 'expr' is a literal SQL aggregate over the source table.
# Each dimension's 'expr' is a literal SQL expression used in SELECT/GROUP BY.
# Neither is ever built from user text.

CATALOG = {

    # ---------------------------------------------------------------- sales
    'sales': {
        'label': 'Sales / Bills',
        'table': 'sales_history',
        'date_col': 'date',
        'engine': 'sql',
        'metrics': {
            'fine':          {'label': 'Fine',            'expr': 'SUM(COALESCE(ledger_fine,0))',      'type': T_WT},
            'collected':     {'label': 'Collected Fine',  'expr': 'SUM(COALESCE(collected_fine,0))',   'type': T_WT},
            'remaining':     {'label': 'Outstanding Fine','expr': 'SUM(COALESCE(remaining_fine,0))',   'type': T_WT},
            'fine_995':      {'label': '995 Fine',        'expr': 'SUM(COALESCE(fine_995,0))',         'type': T_WT},
            'fine_dhal':     {'label': 'Dhal Fine',       'expr': 'SUM(COALESCE(fine_dhal,0))',        'type': T_WT},
            'amount':        {'label': 'Amount',          'expr': 'SUM(COALESCE(total_amount,0))',     'type': T_MONEY},
            'bills':         {'label': 'Bills',           'expr': 'COUNT(*)',                          'type': T_INT},
            'customers':     {'label': 'Customers',       'expr': 'COUNT(DISTINCT TRIM(customer))',    'type': T_INT},
            'discount_fine': {'label': 'Discount Fine',   'expr': 'SUM(COALESCE(discount_fine,0))',    'type': T_WT},
            'discount_amt':  {'label': 'Discount Amount', 'expr': 'SUM(COALESCE(discount_amount,0))',  'type': T_MONEY},
            'avg_fine':      {'label': 'Avg Fine / Bill', 'expr': 'AVG(COALESCE(ledger_fine,0))',      'type': T_WT},
            'avg_amount':    {'label': 'Avg Amount / Bill','expr': 'AVG(COALESCE(total_amount,0))',    'type': T_MONEY},
            'avg_rate':      {'label': 'Avg Gold Rate',   'expr': 'AVG(NULLIF(gold_rate,0))',          'type': T_MONEY},
            'max_rate':      {'label': 'Highest Rate',    'expr': 'MAX(COALESCE(gold_rate,0))',        'type': T_MONEY},
        },
        'dimensions': {
            'customer': {'label': 'Customer', 'expr': "CASE WHEN TRIM(COALESCE(customer,''))='' THEN '(no name)' ELSE TRIM(customer) END"},
            'status':   {'label': 'Bill Type', 'expr': "UPPER(COALESCE(status,''))"},
            'date':     {'label': 'Date',      'expr': 'date',                'order_by': 'grp ASC', 'series': True},
            'month':    {'label': 'Month',     'expr': 'substr(date,1,7)',    'order_by': 'grp ASC', 'series': True},
            'year':     {'label': 'Year',      'expr': 'substr(date,1,4)',    'order_by': 'grp ASC', 'series': True},
            'voucher':  {'label': 'Voucher',   'expr': 'vch_id'},
            'device':   {'label': 'Device',    'expr': "COALESCE(NULLIF(TRIM(device_id),''),'(local)')"},
            'discount': {'label': 'Discount Type', 'expr': "LOWER(COALESCE(discount_type,'none'))"},
        },
        'filters': {
            'customer': {'label': 'Customer', 'col': 'TRIM(customer)',        'ci': True},
            'status':   {'label': 'Bill Type', 'col': 'UPPER(TRIM(status))',  'ci': True},
            'voucher':  {'label': 'Voucher',  'col': 'TRIM(vch_id)',          'ci': True},
            'mobile':   {'label': 'Mobile',   'col': 'TRIM(mobile)',          'ci': True},
        },
    },

    # ------------------------------------------------------- sold items (JSON)
    # sales_history.items is a JSON string, so this source is aggregated in
    # Python after a filtered SQL fetch. Same catalog contract, different engine.
    'items': {
        'label': 'Sold Items (bill lines)',
        'table': 'sales_history',
        'date_col': 'date',
        'engine': 'items',
        'metrics': {
            'gross_wt':  {'label': 'Gross Wt',  'field': 'gross_wt', 'agg': 'sum', 'type': T_WT},
            'net_wt':    {'label': 'Net Wt',    'field': 'net_wt',   'agg': 'sum', 'type': T_WT},
            'fine':      {'label': 'Fine',      'field': 'fine',     'agg': 'sum', 'type': T_WT},
            'pcs':       {'label': 'Pieces',    'field': 'pcs',      'agg': 'sum', 'type': T_INT},
            'lines':     {'label': 'Item Lines','field': None,       'agg': 'count', 'type': T_INT},
            'amount':    {'label': 'Amount',    'field': 'amount',   'agg': 'sum', 'type': T_MONEY},
            'avg_touch': {'label': 'Avg Touch', 'field': 'touch',    'agg': 'avg', 'type': T_TOUCH},
            'avg_wt':    {'label': 'Avg Wt / Line', 'field': 'gross_wt', 'agg': 'avg', 'type': T_WT},
            'bills':     {'label': 'Bills',     'field': 'vch_id',   'agg': 'nunique', 'type': T_INT},
        },
        'dimensions': {
            'item':     {'label': 'Item',     'field': 'it_name'},
            'touch':    {'label': 'Touch',    'field': 'touch_label'},
            'tag':      {'label': 'Tag ID',   'field': 'tag_id'},
            'customer': {'label': 'Customer', 'field': 'customer'},
            'status':   {'label': 'Bill Type','field': 'status'},
            'date':     {'label': 'Date',     'field': 'date',       'order_by': 'grp ASC', 'series': True},
            'month':    {'label': 'Month',    'field': 'month',      'order_by': 'grp ASC', 'series': True},
            'voucher':  {'label': 'Voucher',  'field': 'vch_id'},
            'wastage':  {'label': 'Wastage',  'field': 'wastage_label'},
        },
        # SQL-level pre-filters (cheap); item-level ones are applied in Python.
        'filters': {
            'customer': {'label': 'Customer', 'col': 'TRIM(customer)',       'ci': True},
            'status':   {'label': 'Bill Type', 'col': 'UPPER(TRIM(status))', 'ci': True},
            'voucher':  {'label': 'Voucher',  'col': 'TRIM(vch_id)',         'ci': True},
        },
        'item_filters': {
            'item':  {'label': 'Item',  'field': 'it_name'},
            'touch': {'label': 'Touch', 'field': 'touch'},
            'tag':   {'label': 'Tag ID', 'field': 'tag_id'},
        },
    },

    # ---------------------------------------------------------------- stock
    'stock': {
        'label': 'Stock / Inventory',
        'table': 'stock_inventory',
        'date_col': 'entry_date',
        'engine': 'sql',
        'metrics': {
            'gross_wt':  {'label': 'Gross Wt',   'expr': 'SUM(COALESCE(gr_wt,0))',   'type': T_WT},
            'net_wt':    {'label': 'Net Wt',     'expr': 'SUM(COALESCE(nt_wt,0))',   'type': T_WT},
            'less_wt':   {'label': 'Less Wt',    'expr': 'SUM(COALESCE(ls_wt,0))',   'type': T_WT},
            'pkg_wt':    {'label': 'Package Wt', 'expr': 'SUM(COALESCE(pkg_wt,0))',  'type': T_WT},
            'fine':      {'label': 'Fine',       'expr': 'SUM(COALESCE(nt_wt,0)*COALESCE(touch,0)/100.0)', 'type': T_WT},
            'fine_ws':   {'label': 'Fine + Wastage', 'expr': 'SUM(COALESCE(nt_wt,0)*(COALESCE(touch,0)+COALESCE(wastage,0))/100.0)', 'type': T_WT},
            'pcs':       {'label': 'Pieces',     'expr': 'SUM(COALESCE(pcs,0))',     'type': T_INT},
            'items':     {'label': 'Stock Rows', 'expr': 'COUNT(*)',                 'type': T_INT},
            'tags':      {'label': 'Tagged Items', 'expr': "SUM(CASE WHEN TRIM(COALESCE(tag_id,'')) NOT IN ('','N/A') THEN 1 ELSE 0 END)", 'type': T_INT},
            'avg_touch': {'label': 'Avg Touch',  'expr': 'AVG(NULLIF(touch,0))',     'type': T_TOUCH},
            'avg_wt':    {'label': 'Avg Wt / Row', 'expr': 'AVG(COALESCE(gr_wt,0))', 'type': T_WT},
        },
        'dimensions': {
            'item':   {'label': 'Item',   'expr': "CASE WHEN TRIM(COALESCE(it_name,''))='' THEN COALESCE(it_code,'(unnamed)') ELSE TRIM(it_name) END"},
            'code':   {'label': 'Item Code', 'expr': "TRIM(COALESCE(it_code,''))"},
            'touch':  {'label': 'Touch',  'expr': "CAST(ROUND(COALESCE(touch,0),2) AS TEXT)"},
            'tag':    {'label': 'Tag ID', 'expr': "COALESCE(NULLIF(TRIM(tag_id),''),'(untagged)')"},
            'tagged': {'label': 'Tagged?', 'expr': "CASE WHEN TRIM(COALESCE(tag_id,'')) NOT IN ('','N/A') THEN 'Tagged' ELSE 'Untagged' END"},
            'huid':   {'label': 'HUID',   'expr': "COALESCE(NULLIF(TRIM(huid),''),'(none)')"},
            'date':   {'label': 'Entry Date', 'expr': 'entry_date', 'order_by': 'grp ASC', 'series': True},
            'month':  {'label': 'Month',  'expr': 'substr(entry_date,1,7)', 'order_by': 'grp ASC', 'series': True},
            'device': {'label': 'Device', 'expr': "COALESCE(NULLIF(TRIM(device_id),''),'(local)')"},
            'ref':    {'label': 'Reference', 'expr': "COALESCE(NULLIF(TRIM(vch_reference),''),'(none)')"},
        },
        'filters': {
            'item':  {'label': 'Item',   'col': "TRIM(it_name)", 'ci': True},
            'code':  {'label': 'Item Code', 'col': "TRIM(it_code)", 'ci': True},
            'tag':   {'label': 'Tag ID', 'col': "TRIM(tag_id)",  'ci': True},
            'touch': {'label': 'Touch',  'col': "ROUND(COALESCE(touch,0),2)", 'numeric': True},
            'huid':  {'label': 'HUID',   'col': "TRIM(huid)",    'ci': True},
        },
    },

    # ---------------------------------------------------------------- credit
    'credit': {
        'label': 'Credit Ledger',
        'table': 'credit_ledger',
        'date_col': 'date',
        'engine': 'sql',
        'metrics': {
            # Debit = what the party owes us; Credit = what they have settled.
            'metal_dr':   {'label': 'Metal Debit',   'expr': 'SUM(COALESCE(metal_dr,0))', 'type': T_WT},
            'metal_cr':   {'label': 'Metal Credit',  'expr': 'SUM(COALESCE(metal_cr,0))', 'type': T_WT},
            'metal_bal':  {'label': 'Metal Balance', 'expr': 'SUM(COALESCE(metal_dr,0)-COALESCE(metal_cr,0))', 'type': T_WT},
            'cash_dr':    {'label': 'Cash Debit',    'expr': 'SUM(COALESCE(cash_dr,0))',  'type': T_MONEY},
            'cash_cr':    {'label': 'Cash Credit',   'expr': 'SUM(COALESCE(cash_cr,0))',  'type': T_MONEY},
            'cash_bal':   {'label': 'Cash Balance',  'expr': 'SUM(COALESCE(cash_dr,0)-COALESCE(cash_cr,0))', 'type': T_MONEY},
            'entries':    {'label': 'Entries',       'expr': 'COUNT(*)',                  'type': T_INT},
            'clients':    {'label': 'Clients',       'expr': 'COUNT(DISTINCT TRIM(client_name))', 'type': T_INT},
            'avg_rate':   {'label': 'Avg Gold Rate', 'expr': 'AVG(NULLIF(gold_rate,0))',  'type': T_MONEY},
        },
        'dimensions': {
            'customer': {'label': 'Client', 'expr': "CASE WHEN TRIM(COALESCE(client_name,''))='' THEN '(no name)' ELSE TRIM(client_name) END"},
            'date':     {'label': 'Date',   'expr': 'date', 'order_by': 'grp ASC', 'series': True},
            'month':    {'label': 'Month',  'expr': 'substr(date,1,7)', 'order_by': 'grp ASC', 'series': True},
            'voucher':  {'label': 'Voucher', 'expr': "COALESCE(NULLIF(TRIM(vch_reference),''),'(none)')"},
        },
        'filters': {
            'customer': {'label': 'Client',  'col': 'TRIM(client_name)',   'ci': True},
            'voucher':  {'label': 'Voucher', 'col': 'TRIM(vch_reference)', 'ci': True},
        },
    },

    # ----------------------------------------------------------------- katti
    'katti': {
        'label': 'Katti Batches',
        'table': 'katti_vouchers',
        'date_col': 'date',
        'engine': 'sql',
        'metrics': {
            'weight':   {'label': 'Total Weight', 'expr': 'SUM(COALESCE(total_weight,0))',  'type': T_WT},
            'packets':  {'label': 'Packets',      'expr': 'SUM(COALESCE(total_packets,0))', 'type': T_INT},
            'pcs':      {'label': 'Pieces',       'expr': 'SUM(COALESCE(total_pcs,0))',     'type': T_INT},
            'vouchers': {'label': 'Batches',      'expr': 'COUNT(*)',                       'type': T_INT},
            'avg_wt':   {'label': 'Avg Weight',   'expr': 'AVG(COALESCE(total_weight,0))',  'type': T_WT},
        },
        'dimensions': {
            'date':    {'label': 'Date',  'expr': 'date', 'order_by': 'grp ASC', 'series': True},
            'month':   {'label': 'Month', 'expr': 'substr(date,1,7)', 'order_by': 'grp ASC', 'series': True},
            'touch':   {'label': 'Touch', 'expr': "COALESCE(NULLIF(TRIM(CAST(touch AS TEXT)),''),'(none)')"},
            'box':     {'label': 'Box',   'expr': "COALESCE(NULLIF(TRIM(box_id),''),'(none)')"},
            'voucher': {'label': 'Batch', 'expr': 'vch_id'},
        },
        'filters': {
            'voucher': {'label': 'Batch', 'col': 'TRIM(vch_id)', 'ci': True},
            'box':     {'label': 'Box',   'col': 'TRIM(box_id)', 'ci': True},
        },
    },

    # ----------------------------------------------------------------- uchak
    'uchak': {
        'label': 'Uchak Inward',
        'table': 'uchak_inward_vouchers',
        'date_col': 'date',
        'engine': 'sql',
        'metrics': {
            'value':    {'label': 'Total Value', 'expr': 'SUM(COALESCE(total_value,0))', 'type': T_MONEY},
            'pcs':      {'label': 'Pieces',      'expr': 'SUM(COALESCE(total_pcs,0))',   'type': T_INT},
            'lines':    {'label': 'Lines',       'expr': 'SUM(COALESCE(total_lines,0))', 'type': T_INT},
            'vouchers': {'label': 'Vouchers',    'expr': 'COUNT(*)',                     'type': T_INT},
        },
        'dimensions': {
            'date':    {'label': 'Date',    'expr': 'date', 'order_by': 'grp ASC', 'series': True},
            'month':   {'label': 'Month',   'expr': 'substr(date,1,7)', 'order_by': 'grp ASC', 'series': True},
            'voucher': {'label': 'Voucher', 'expr': 'vch_id'},
        },
        'filters': {
            'voucher': {'label': 'Voucher', 'col': 'TRIM(vch_id)', 'ci': True},
        },
    },
}

# Default KPI set shown when the question names no specific metric.
SOURCE_DEFAULT_METRICS = {
    'sales':  ['fine', 'amount', 'bills', 'remaining'],
    'items':  ['gross_wt', 'fine', 'pcs', 'lines'],
    'stock':  ['gross_wt', 'net_wt', 'fine', 'pcs'],
    'credit': ['metal_bal', 'cash_bal', 'entries'],
    'katti':  ['weight', 'packets', 'pcs', 'vouchers'],
    'uchak':  ['value', 'pcs', 'vouchers'],
}


# ==========================================================================
#  LEXICON -- phrases the shop floor actually types.
#  English + Hinglish + Gujlish (Latin) + Gujarati/Devanagari script.
#  Longer phrases are matched first so "fine 995" beats "fine".
# ==========================================================================

SOURCE_LEX = [
    (('sold item', 'sold items', 'item wise sale', 'itemwise sale', 'bill line', 'bill lines',
      'line item', 'line items', 'which item sold', 'item sold', 'items sold'), 'items'),
    (('stock', 'inventory', 'godown', 'maal', 'mal stock', 'સ્ટોક', 'स्टॉक'), 'stock'),
    (('credit ledger', 'ledger', 'khata', 'khatu', 'udhar', 'udhaar', 'party balance',
      'client balance', 'ખાતું', 'खाता'), 'credit'),
    (('katti', 'batch', 'batches', 'packet', 'packets', 'કટ્ટી', 'कट्टी'), 'katti'),
    (('uchak inward', 'uchak stock', 'inward'), 'uchak'),
    (('sale', 'sales', 'bill', 'bills', 'billing', 'voucher', 'vouchers', 'invoice', 'invoices',
      'vechan', 'vepar', 'વેચાણ', 'बिक्री', 'बिल'), 'sales'),
]

# metric phrases -> (source_hint, metric_key). source_hint None = any source.
METRIC_LEX = [
    # --- fine / metal -----------------------------------------------------
    (('995 fine', 'fine 995', '995 wt', '995'), (None, 'fine_995')),
    (('dhal fine', 'fine dhal', 'dhal wt', 'dhal', 'ધાળ'), (None, 'fine_dhal')),
    (('outstanding fine', 'remaining fine', 'pending fine', 'balance fine',
      'outstanding', 'remaining', 'pending', 'baki', 'baaki', 'bakii', 'due', 'dues',
      'બાકી', 'बाकी', 'बकाया'), (None, 'remaining')),
    (('collected fine', 'fine collected', 'collected', 'collection', 'received',
      'jama', 'jamaa', 'vasool', 'vasul', 'જમા', 'जमा'), (None, 'collected')),
    (('discount fine',), (None, 'discount_fine')),
    (('discount amount', 'discount rs', 'discount rupees'), (None, 'discount_amt')),
    (('discount', 'kasar', 'કસર'), (None, 'discount_fine')),
    (('average fine', 'avg fine'), (None, 'avg_fine')),
    (('fine plus wastage', 'fine with wastage', 'fine + wastage'), ('stock', 'fine_ws')),
    (('fine', 'shudh', 'shuddh', 'pure gold', 'pure', 'chokhu', 'chokhkhu',
      'શુદ્ધ', 'शुद्ध', 'फाइन'), (None, 'fine')),

    # --- weight -----------------------------------------------------------
    (('gross weight', 'gross wt', 'gross'), (None, 'gross_wt')),
    (('net weight', 'net wt', 'nt wt'), (None, 'net_wt')),
    (('less weight', 'less wt'), ('stock', 'less_wt')),
    (('package weight', 'package wt', 'pkg wt'), ('stock', 'pkg_wt')),
    (('average weight', 'avg weight', 'avg wt'), (None, 'avg_wt')),
    (('total weight', 'weight', 'wt', 'gram', 'grams', 'gm', 'gms', 'vajan', 'vajah',
      'વજન', 'वजन', 'ग्राम'), (None, 'weight')),

    # --- money ------------------------------------------------------------
    (('average amount', 'avg amount', 'average bill', 'avg bill value'), (None, 'avg_amount')),
    (('highest rate', 'max rate', 'peak rate'), (None, 'max_rate')),
    (('average rate', 'avg rate', 'gold rate', 'rate', 'bhav', 'bhaav',
      'ભાવ', 'भाव'), (None, 'avg_rate')),
    (('total value', 'value'), (None, 'value')),
    (('amount', 'rupees', 'rupee', 'rupiya', 'rupaya', 'rs', 'inr', 'cash', 'money',
      'turnover', 'revenue', 'paisa', 'paise', 'રૂપિયા', 'रुपये', 'नकद'), (None, 'amount')),

    # --- counts -----------------------------------------------------------
    (('how many bills', 'number of bills', 'bill count', 'total bills', 'no of bills',
      'ketla bill', 'kitne bill'), (None, 'bills')),
    (('pieces', 'piece', 'pcs', 'nag', 'nug', 'quantity', 'qty', 'નંગ', 'नग'), (None, 'pcs')),
    (('packets', 'packet'), ('katti', 'packets')),
    (('how many customers', 'number of customers', 'customer count'), (None, 'customers')),
    (('how many clients', 'number of clients', 'client count'), ('credit', 'clients')),
    (('item lines', 'lines'), (None, 'lines')),
    (('tagged items', 'tag count', 'how many tags'), ('stock', 'tags')),
    (('stock rows', 'how many items', 'item count', 'number of items'), ('stock', 'items')),

    # --- purity -----------------------------------------------------------
    (('average touch', 'avg touch', 'average purity', 'avg purity'), (None, 'avg_touch')),

    # --- credit ledger sides ---------------------------------------------
    (('metal debit', 'metal dr'), ('credit', 'metal_dr')),
    (('metal credit', 'metal cr'), ('credit', 'metal_cr')),
    (('metal balance', 'metal baki', 'gold balance'), ('credit', 'metal_bal')),
    (('cash debit', 'cash dr'), ('credit', 'cash_dr')),
    (('cash credit', 'cash cr'), ('credit', 'cash_cr')),
    (('cash balance',), ('credit', 'cash_bal')),
]

DIM_LEX = [
    (('customer', 'customers', 'party', 'parties', 'client', 'clients', 'grahak', 'gharak',
      'vepari', 'ગ્રાહક', 'ग्राहक', 'पार्टी'), 'customer'),
    (('item', 'items', 'product', 'products', 'design', 'designs',
      'આઇટમ', 'वस्तु'), 'item'),
    (('item code', 'itcode', 'it code', 'code'), 'code'),
    (('touch', 'purity', 'carat', 'karat', 'kt', 'ટચ', 'टच'), 'touch'),
    (('tag', 'tags', 'tag id', 'tagid'), 'tag'),
    (('bill type', 'status', 'paid or credit'), 'status'),
    (('month', 'monthly', 'month wise', 'per month', 'mahina', 'mahine', 'mahino',
      'મહિનો', 'महीना'), 'month'),
    (('year', 'yearly', 'year wise'), 'year'),
    (('day', 'daily', 'date', 'day wise', 'date wise', 'per day', 'divas', 'din',
      'દિવસ', 'दिन'), 'date'),
    (('voucher', 'vouchers', 'bill', 'bills', 'invoice'), 'voucher'),
    (('device', 'computer', 'pc', 'terminal'), 'device'),
    (('box', 'box id'), 'box'),
    (('huid',), 'huid'),
    (('wastage', 'waste', 'ghat', 'ઘટ', 'घट'), 'wastage'),
    (('tagged', 'untagged'), 'tagged'),
]

# Grouping cues -- a dimension word only becomes a GROUP BY when one of these
# appears, otherwise "customer name" in a filter would be mistaken for a group.
GROUP_CUES = ('by ', 'wise', 'per ', 'each ', 'every ', 'group', 'breakup', 'break up',
              'breakdown', 'split', 'top ', 'best ', 'worst ', 'lowest ', 'highest ',
              'bottom ', 'ranking', 'rank ', 'compare', 'vs ', 'chart', 'graph', 'trend',
              'list of', 'har ', 'darek', 'દરેક', 'हर ')

SUPERLATIVE_DESC = ('top', 'best', 'highest', 'maximum', 'max', 'most', 'largest', 'biggest',
                    'sabse jyada', 'sabse zyada', 'sauthi vadhu', 'vadhu', 'jyada',
                    'સૌથી વધુ', 'सबसे ज्यादा')
SUPERLATIVE_ASC = ('bottom', 'lowest', 'least', 'minimum', 'min', 'smallest', 'worst',
                   'sabse kam', 'sauthi ochu', 'ochu', 'kam',
                   'સૌથી ઓછું', 'सबसे कम')

STATUS_LEX = [
    (('uchak paid',), 'UCHAK_PAID'),
    (('uchak unpaid', 'uchak credit'), 'UCHAK_UNPAID'),
    (('uchak',), 'UCHAK%'),
    (('paid', 'chukta', 'ચૂકતે', 'चुकता'), 'PAID'),
    (('credit', 'udhar', 'udhaar', 'unpaid', 'ઉધાર', 'उधार'), 'CREDIT'),
]

MONTHS = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
    'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}

# Everything the parser consumes as grammar rather than as an entity name.
STOPWORDS = set("""
a an the of for in on at to from by is are was were be been what whats which who whom how
much many total sum count average avg show me give tell list all my our me and or with
this that these those last past previous next current between during report reports
please kindly do you can i want need get find see view display fetch print data
kitna kitne ketla ketlu ketli kul batao bata dekhao dikha jara chhe che nu na ni no ka ke ki
top best highest lowest least most bottom worst maximum minimum max min wise per each every
group breakup breakdown split rank ranking compare vs chart graph trend order
""".split())


# ==========================================================================
#  Text normalisation
# ==========================================================================

def _norm(text):
    """Lowercase, strip punctuation that never carries meaning, collapse space."""
    t = (text or '').lower().strip()
    t = t.replace('₹', ' rs ').replace('%', ' percent ')
    # Keep digits, letters (incl. Indic), dot, slash, hyphen, quotes.
    t = re.sub(r"[^\w\s./\-'\"ऀ-ॿ઀-૿]", ' ', t, flags=re.UNICODE)
    t = re.sub(r'\s+', ' ', t)
    return ' ' + t + ' '


def _has(text, phrase):
    """Word-boundary-ish containment on the padded normalised string."""
    p = phrase.lower().strip()
    if not p:
        return False
    if ' ' in p:
        return (' ' + p + ' ') in text or text.strip().endswith(' ' + p)
    return (' ' + p + ' ') in text


def _find_pos(text, phrase):
    p = ' ' + phrase.lower().strip() + ' '
    return text.find(p)


# ==========================================================================
#  Date range resolution
# ==========================================================================

def _fy_start_month():
    """Indian financial year starts in April."""
    return 4


def _fy_bounds(anchor, back=0):
    """Financial-year window containing `anchor`, shifted `back` years."""
    y = anchor.year if anchor.month >= _fy_start_month() else anchor.year - 1
    y -= back
    return (datetime.date(y, 4, 1), datetime.date(y + 1, 3, 31),
            'FY %d-%02d' % (y, (y + 1) % 100))


def _month_bounds(y, m):
    first = datetime.date(y, m, 1)
    last = datetime.date(y + (m == 12), 1 if m == 12 else m + 1, 1) - datetime.timedelta(days=1)
    return first, last


def _iso(d):
    return d.strftime('%Y-%m-%d')


def _parse_explicit_date(tok):
    """Accept 2026-08-26, 26-08-2026, 26/08/2026, 26.8.2026."""
    tok = tok.strip().strip('"\'')
    m = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$', tok)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r'^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$', tok)
    if m:
        y = int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime.date(y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def resolve_range(text, anchor):
    """Return {'from','to','label','relative'} or None for all-time.

    `anchor` is TODAY as the DATABASE sees it, so relative windows line up with
    how rows were stamped (see module docstring).
    """
    t = _norm(text)

    if _has(t, 'all time') or _has(t, 'alltime') or _has(t, 'lifetime') or \
       _has(t, 'ever') or _has(t, 'total till date') or _has(t, 'till date') or \
       _has(t, 'since beginning'):
        return None

    # -- explicit "from X to Y" / "between X and Y" ------------------------
    m = re.search(r'(?:from|between)\s+([\d]{1,4}[-/.][\d]{1,2}[-/.][\d]{2,4})\s*'
                  r'(?:to|until|till|and|-)\s*([\d]{1,4}[-/.][\d]{1,2}[-/.][\d]{2,4})', t)
    if m:
        a, b = _parse_explicit_date(m.group(1)), _parse_explicit_date(m.group(2))
        if a and b:
            if a > b:
                a, b = b, a
            return {'from': _iso(a), 'to': _iso(b),
                    'label': '%s to %s' % (_iso(a), _iso(b)), 'relative': False}

    # -- single explicit date ("on 26-08-2026") ---------------------------
    for tok in re.findall(r'\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}', t):
        d = _parse_explicit_date(tok)
        if d:
            return {'from': _iso(d), 'to': _iso(d), 'label': _iso(d), 'relative': False}

    # -- named relative windows -------------------------------------------
    if _has(t, 'today') or _has(t, 'aaj') or _has(t, 'aje') or _has(t, 'aaje') \
       or _has(t, 'આજે') or _has(t, 'आज'):
        return {'from': _iso(anchor), 'to': _iso(anchor), 'label': 'Today', 'relative': True}

    if _has(t, 'yesterday') or _has(t, 'kal') or _has(t, 'kale') or _has(t, 'gai kale') \
       or _has(t, 'ગઈકાલે') or _has(t, 'कल'):
        y = anchor - datetime.timedelta(days=1)
        return {'from': _iso(y), 'to': _iso(y), 'label': 'Yesterday', 'relative': True}

    # "last 7 days", "chhella 15 divas", "past 3 months"
    m = re.search(r'(?:last|past|previous|pichhla|pichla|chhella|chella|recent)\s+(\d{1,4})\s*'
                  r'(day|days|din|divas|week|weeks|month|months|mahina|mahine|year|years)', t)
    if not m:
        m = re.search(r'(\d{1,4})\s*(day|days|din|divas|week|weeks|month|months|mahina|mahine|year|years)\s*'
                      r'(?:ago|ma|man|pehla|pahela|back)?', t)
    if m:
        n = max(1, min(int(m.group(1)), 3650))
        unit = m.group(2)
        if unit.startswith(('day', 'din', 'div')):
            start = anchor - datetime.timedelta(days=n - 1)
            lbl = 'Last %d day%s' % (n, '' if n == 1 else 's')
        elif unit.startswith('week'):
            start = anchor - datetime.timedelta(days=n * 7 - 1)
            lbl = 'Last %d week%s' % (n, '' if n == 1 else 's')
        elif unit.startswith('year'):
            start = datetime.date(anchor.year - n, anchor.month, 1)
            lbl = 'Last %d year%s' % (n, '' if n == 1 else 's')
        else:
            ym = anchor.year * 12 + (anchor.month - 1) - (n - 1)
            start = datetime.date(ym // 12, ym % 12 + 1, 1)
            lbl = 'Last %d month%s' % (n, '' if n == 1 else 's')
        return {'from': _iso(start), 'to': _iso(anchor), 'label': lbl, 'relative': True}

    is_last = (_has(t, 'last') or _has(t, 'previous') or _has(t, 'pichhla') or _has(t, 'pichla')
               or _has(t, 'gaya') or _has(t, 'gaye') or _has(t, 'gata') or _has(t, 'chhella')
               or _has(t, 'ગયા') or _has(t, 'पिछले') or _has(t, 'पिछला'))

    if _has(t, 'week') or _has(t, 'saptah') or _has(t, 'હફતો') or _has(t, 'सप्ताह'):
        # Week starts Monday.
        mon = anchor - datetime.timedelta(days=anchor.weekday())
        if is_last:
            mon -= datetime.timedelta(days=7)
            return {'from': _iso(mon), 'to': _iso(mon + datetime.timedelta(days=6)),
                    'label': 'Last week', 'relative': True}
        return {'from': _iso(mon), 'to': _iso(anchor), 'label': 'This week', 'relative': True}

    if _has(t, 'financial year') or _has(t, 'fiscal year') or _has(t, 'fy') or _has(t, 'f.y'):
        a, b, lbl = _fy_bounds(anchor, 1 if is_last else 0)
        return {'from': _iso(a), 'to': _iso(b),
                'label': ('Last ' if is_last else '') + lbl, 'relative': True}

    # explicit month name, optionally with a year: "august", "aug 2025"
    for name, mn in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        if _has(t, name):
            ym = re.search(r'\b(20\d{2})\b', t)
            yr = int(ym.group(1)) if ym else anchor.year
            if not ym and mn > anchor.month:
                yr -= 1           # "december" asked in March means last December
            a, b = _month_bounds(yr, mn)
            return {'from': _iso(a), 'to': _iso(b),
                    'label': '%s %d' % (name.capitalize(), yr), 'relative': False}

    if _has(t, 'month') or _has(t, 'mahina') or _has(t, 'mahine') or _has(t, 'mahino') \
       or _has(t, 'મહિનો') or _has(t, 'महीने') or _has(t, 'महीना'):
        if is_last:
            ym = anchor.year * 12 + anchor.month - 2
            a, b = _month_bounds(ym // 12, ym % 12 + 1)
            return {'from': _iso(a), 'to': _iso(b), 'label': 'Last month', 'relative': True}
        a, _ = _month_bounds(anchor.year, anchor.month)
        return {'from': _iso(a), 'to': _iso(anchor), 'label': 'This month', 'relative': True}

    if _has(t, 'quarter'):
        q = (anchor.month - 1) // 3
        if is_last:
            q -= 1
        yr = anchor.year
        if q < 0:
            q, yr = 3, yr - 1
        a, _ = _month_bounds(yr, q * 3 + 1)
        _, b = _month_bounds(yr + (q == 3), 12 if q == 3 else q * 3 + 3)
        b = min(b, anchor) if (not is_last and yr == anchor.year) else b
        return {'from': _iso(a), 'to': _iso(b),
                'label': ('Last' if is_last else 'This') + ' quarter Q%d %d' % (q + 1, yr),
                'relative': True}

    ym = re.search(r'\b(20\d{2})\b', t)
    if _has(t, 'year') or _has(t, 'saal') or _has(t, 'varsh') or ym:
        if ym and not is_last:
            yr = int(ym.group(1))
            return {'from': '%d-01-01' % yr, 'to': '%d-12-31' % yr,
                    'label': str(yr), 'relative': False}
        yr = anchor.year - (1 if is_last else 0)
        to = '%d-12-31' % yr if is_last else _iso(anchor)
        return {'from': '%d-01-01' % yr, 'to': to,
                'label': ('Last year ' + str(yr)) if is_last else ('This year ' + str(yr)),
                'relative': True}

    return None   # all time


# ==========================================================================
#  Plan building (the local, offline parser)
# ==========================================================================

def _pick_source(t, entities):
    """Choose the fact table. Earliest strong cue wins; sales is the default."""
    best, best_pos = None, 10 ** 9
    for phrases, src in SOURCE_LEX:
        for p in phrases:
            pos = _find_pos(t, p)
            if pos >= 0 and pos < best_pos:
                best, best_pos = src, pos
    # Item-level words force the item engine even if "sale" appeared first.
    if best in (None, 'sales'):
        if (_has(t, 'tag') or _has(t, 'tag id') or _has(t, 'huid')) and _has(t, 'sold'):
            best = 'items'
        elif _has(t, 'item') or _has(t, 'design') or _has(t, 'product'):
            # "top items sold" -> item lines; "item stock" -> stock
            best = 'items' if (_has(t, 'sold') or _has(t, 'sale') or _has(t, 'sales')
                               or _has(t, 'bill')) else 'stock'
    return best or 'sales'


def _pick_metrics(t, source):
    """Ordered, de-duplicated metric keys valid for `source`."""
    avail = CATALOG[source]['metrics']
    hits = []
    for phrases, (hint, key) in METRIC_LEX:
        if hint and hint != source:
            continue
        for p in phrases:
            pos = _find_pos(t, p)
            if pos < 0:
                continue
            k = key
            if k not in avail:
                # Graceful cross-source aliases.
                alias = {'weight': 'gross_wt', 'gross_wt': 'weight', 'value': 'amount',
                         'amount': 'value', 'fine': 'net_wt', 'pcs': 'pcs'}.get(k)
                if alias and alias in avail:
                    k = alias
                else:
                    continue
            hits.append((pos, k))
            break
    seen, out = set(), []
    for _, k in sorted(hits):
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _pick_dimension(t, source):
    avail = CATALOG[source]['dimensions']
    grouping = any(c in t for c in GROUP_CUES)
    best, best_pos = None, 10 ** 9
    for phrases, dim in DIM_LEX:
        if dim not in avail:
            continue
        for p in phrases:
            pos = _find_pos(t, p)
            if pos < 0:
                continue
            # 'daily'/'monthly'/'trend' imply grouping on their own.
            implied = p in ('daily', 'monthly', 'yearly', 'day wise', 'date wise',
                            'month wise', 'year wise', 'per day', 'per month')
            if not grouping and not implied:
                continue
            if pos < best_pos:
                best, best_pos = dim, pos
            break
    if best is None and (_has(t, 'trend') or _has(t, 'graph') or _has(t, 'chart')):
        best = 'date' if 'date' in avail else None
    return best


def _pick_shape(t):
    limit, order = DEFAULT_LIMIT, 'desc'
    m = re.search(r'(?:top|best|first|bottom|lowest|worst|last)\s+(\d{1,4})', t)
    if not m:
        m = re.search(r'\b(\d{1,4})\s+(?:top|best|sabse|sauthi)\b', t)
    if m:
        limit = max(1, min(int(m.group(1)), MAX_ROWS))
    elif any(_has(t, s) or s in t for s in SUPERLATIVE_DESC + SUPERLATIVE_ASC):
        limit = TOP_N_DEFAULT
    if any(s in t for s in SUPERLATIVE_ASC):
        order = 'asc'
    return limit, order


def _pick_status(t):
    for phrases, val in STATUS_LEX:
        for p in phrases:
            if _has(t, p):
                return val
    return None


def build_plan(question, anchor, entities=None):
    """Parse `question` into a validated plan dict. Never raises."""
    entities = entities or {}
    t = _norm(question)

    source = _pick_source(t, entities)
    src = CATALOG[source]

    metrics = _pick_metrics(t, source)
    default_used = not metrics
    if default_used:
        metrics = [m for m in SOURCE_DEFAULT_METRICS[source] if m in src['metrics']]

    dimension = _pick_dimension(t, source)
    limit, order = _pick_shape(t)

    filters = []

    # status / bill type
    st = _pick_status(t)
    if st and 'status' in src.get('filters', {}):
        filters.append({'field': 'status',
                        'op': 'like' if st.endswith('%') else 'eq',
                        'value': st.rstrip('%') + ('%' if st.endswith('%') else ''),
                        'label': 'Bill type ' + st.replace('%', '*')})

    # touch value: "touch 76", "76 touch", "76%"
    m = re.search(r'touch\s+(\d{1,3}(?:\.\d{1,2})?)', t) or \
        re.search(r'(\d{2,3}(?:\.\d{1,2})?)\s*touch', t)
    if m:
        tv = float(m.group(1))
        if 0 < tv <= 100:
            if source == 'items':
                filters.append({'field': 'touch', 'op': 'eq', 'value': tv,
                                'label': 'Touch = %g' % tv, 'item_level': True})
            elif 'touch' in src.get('filters', {}):
                filters.append({'field': 'touch', 'op': 'eq', 'value': round(tv, 2),
                                'label': 'Touch = %g' % tv})

    # Known entity names appearing verbatim in the question (longest first),
    # so "sales for JD Jeweller" filters instead of being read as a group.
    for field, values in (entities or {}).items():
        if field not in src.get('filters', {}) and field not in src.get('item_filters', {}):
            continue
        if dimension == field:
            continue
        for val in sorted([v for v in values if v and len(str(v)) >= 3],
                          key=lambda v: -len(str(v))):
            if _has(t, str(val).lower()):
                fl = {'field': field, 'op': 'eq', 'value': val,
                      'label': '%s = %s' % (src.get('filters', {}).get(field, {}).get('label')
                                            or field.title(), val)}
                if field in src.get('item_filters', {}) and field not in src.get('filters', {}):
                    fl['item_level'] = True
                filters.append(fl)
                break

    # A quoted phrase is always an explicit filter value.
    q = re.search(r'["\']([^"\']{2,60})["\']', question or '')
    if q and not any(f.get('field') in ('customer', 'item') for f in filters):
        val = q.group(1).strip()
        fld = 'item' if source == 'stock' else 'customer'
        if fld in src.get('filters', {}):
            filters.append({'field': fld, 'op': 'contains', 'value': val,
                            'label': '%s contains "%s"' % (fld.title(), val)})

    rng = resolve_range(question, anchor)

    plan = {
        'source': source,
        'metrics': metrics,
        'dimension': dimension,
        'range': rng,
        'filters': filters,
        'order': order,
        'limit': limit if dimension else 1,
        'row_limit': limit if dimension else DEFAULT_LIMIT,
        'default_metrics': default_used,
    }
    return plan


def validate_plan(plan):
    """Coerce any plan (local OR llm-proposed) onto the catalog. Raises ValueError."""
    if not isinstance(plan, dict):
        raise ValueError('plan must be an object')
    source = str(plan.get('source') or 'sales')
    if source not in CATALOG:
        raise ValueError('unknown source: %s' % source)
    src = CATALOG[source]

    metrics = plan.get('metrics') or []
    if isinstance(metrics, str):
        metrics = [metrics]
    metrics = [m for m in metrics if m in src['metrics']]
    if not metrics:
        metrics = [m for m in SOURCE_DEFAULT_METRICS[source] if m in src['metrics']]
    metrics = metrics[:6]

    dim = plan.get('dimension') or None
    if dim not in (None, '') and dim not in src['dimensions']:
        dim = None

    rng = plan.get('range') or None
    if rng:
        f, to = str(rng.get('from') or ''), str(rng.get('to') or '')
        if not (re.match(r'^\d{4}-\d{2}-\d{2}$', f) and re.match(r'^\d{4}-\d{2}-\d{2}$', to)):
            rng = None
        else:
            if f > to:
                f, to = to, f
            rng = {'from': f, 'to': to,
                   'label': str(rng.get('label') or ('%s to %s' % (f, to)))[:60],
                   'relative': bool(rng.get('relative'))}

    ok_f, ok_i = src.get('filters', {}), src.get('item_filters', {})
    filters = []
    for f in (plan.get('filters') or [])[:8]:
        if not isinstance(f, dict):
            continue
        fld = f.get('field')
        item_level = bool(f.get('item_level')) or (fld in ok_i and fld not in ok_f)
        if fld not in ok_f and fld not in ok_i:
            continue
        op = f.get('op') if f.get('op') in ('eq', 'contains', 'like', 'gte', 'lte') else 'eq'
        val = f.get('value')
        if val is None or val == '':
            continue
        spec = ok_f.get(fld) or ok_i.get(fld) or {}
        lbl = f.get('label') or ('%s %s %s' % (spec.get('label') or fld, op, val))
        out = {'field': fld, 'op': op, 'value': val, 'label': str(lbl)[:80]}
        if item_level:
            out['item_level'] = True
        filters.append(out)

    order = plan.get('order') if plan.get('order') in ('asc', 'desc', 'label') else 'desc'
    try:
        limit = int(plan.get('limit') or (DEFAULT_LIMIT if dim else 1))
    except Exception:
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_ROWS))

    return {'source': source, 'metrics': metrics, 'dimension': dim, 'range': rng,
            'filters': filters, 'order': order, 'limit': limit,
            'row_limit': limit, 'default_metrics': bool(plan.get('default_metrics'))}


# ==========================================================================
#  Execution
# ==========================================================================

def _where(src, plan, params):
    """Build the WHERE clause from whitelisted column expressions only."""
    parts = []
    rng = plan.get('range')
    if rng:
        parts.append('%s BETWEEN ? AND ?' % src['date_col'])
        params.extend([rng['from'], rng['to']])
        # Guard against NULL/blank dates silently vanishing rows -- surfaced in
        # the notes instead of being hidden.
    for f in plan.get('filters') or []:
        if f.get('item_level'):
            continue
        spec = (src.get('filters') or {}).get(f['field'])
        if not spec:
            continue
        col, val, op = spec['col'], f['value'], f['op']
        if spec.get('numeric'):
            try:
                val = float(val)
            except Exception:
                continue
            parts.append('%s = ?' % col)
            params.append(round(val, 2))
            continue
        if op == 'contains':
            parts.append('LOWER(%s) LIKE ?' % col)
            params.append('%' + str(val).lower() + '%')
        elif op == 'like':
            parts.append('LOWER(%s) LIKE ?' % col)
            params.append(str(val).lower().replace('*', '%'))
        else:
            parts.append('LOWER(%s) = ?' % col)
            params.append(str(val).strip().lower())
    return (' WHERE ' + ' AND '.join(parts)) if parts else ''


def _run_sql(conn, plan):
    src = CATALOG[plan['source']]
    metrics = plan['metrics']
    dim = plan.get('dimension')
    params = []
    sel = [src['metrics'][m]['expr'] + ' AS ' + ('m%d' % i) for i, m in enumerate(metrics)]

    if dim:
        dspec = src['dimensions'][dim]
        sql = 'SELECT %s AS grp, %s FROM %s' % (dspec['expr'], ', '.join(sel), src['table'])
        sql += _where(src, plan, params)
        sql += ' GROUP BY grp'
        if plan['order'] == 'label' or dspec.get('order_by'):
            sql += ' ORDER BY ' + (dspec.get('order_by') or 'grp ASC')
        else:
            sql += ' ORDER BY m0 ' + ('ASC' if plan['order'] == 'asc' else 'DESC')
        sql += ' LIMIT %d' % min(plan['limit'], MAX_ROWS)
    else:
        sql = 'SELECT %s FROM %s' % (', '.join(sel), src['table'])
        sql += _where(src, plan, params)

    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = {}
        if dim:
            d['grp'] = r['grp'] if r['grp'] is not None else '(blank)'
        for i, m in enumerate(metrics):
            v = r['m%d' % i]
            d[m] = float(v) if v is not None else 0.0
        out.append(d)
    return out, sql, params


_ALIAS_WT = ('weight', 'gr_wt', 'gross_wt', 'grwt', 'wt')
_ALIAS_NAME = ('it_name', 'name', 'item_name', 'it_code', 'code', 'itcode')
_ALIAS_PCS = ('pcs', 'qty', 'quantity', 'nag')
_ALIAS_AMT = ('amount', 'price', 'value', 'total')


def _first(d, keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ''):
            return v
    return default


def _num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _explode_items(conn, plan):
    """Fetch filtered bills, then flatten sales_history.items in Python.

    Handles every key spelling the two billing screens have ever written
    (weight/gr_wt/gross_wt, it_name/name/code, pcs/qty), so item-level reports
    do not silently read 0 for older bills.
    """
    src = CATALOG['items']
    params = []
    sql = ('SELECT vch_id, customer, status, date, items FROM %s' % src['table']) \
        + _where(src, plan, params) + (' LIMIT %d' % MAX_SCAN_ROWS)
    rows = conn.execute(sql, params).fetchall()

    item_filters = [f for f in (plan.get('filters') or []) if f.get('item_level')]
    lines, skipped = [], 0
    for r in rows:
        try:
            raw = json.loads(r['items'] or '[]')
        except Exception:
            skipped += 1
            continue
        if not isinstance(raw, list):
            skipped += 1
            continue
        for it in raw:
            if not isinstance(it, dict):
                continue
            gross = _num(_first(it, _ALIAS_WT, 0))
            para = _num(it.get('para'))
            less = _num(it.get('less'))
            touch = _num(it.get('touch'))
            wast = _num(it.get('wastage'))
            net = gross - para - less
            fine = _num(it.get('fine')) if it.get('fine') not in (None, '') \
                else net * (touch + wast) / 100.0
            name = str(_first(it, _ALIAS_NAME, '') or '').strip() or '(unnamed)'
            tag = str(it.get('tag_id') or '').strip()
            line = {
                'vch_id': r['vch_id'] or '',
                'customer': (r['customer'] or '').strip() or '(no name)',
                'status': (r['status'] or '').upper(),
                'date': r['date'] or '',
                'month': (r['date'] or '')[:7],
                'it_name': name,
                'tag_id': tag if tag and tag not in ('N/A', '---', '-') else '(untagged)',
                'touch': touch,
                'touch_label': ('%g' % touch) if touch else '(none)',
                'wastage': wast,
                'wastage_label': ('%g' % wast) if wast else '0',
                'gross_wt': gross,
                'net_wt': net,
                'fine': fine,
                'pcs': int(_num(_first(it, _ALIAS_PCS, 1)) or 1),
                'amount': _num(_first(it, _ALIAS_AMT, 0)),
            }
            keep = True
            for f in item_filters:
                spec = src['item_filters'].get(f['field'])
                if not spec:
                    continue
                lv, fv = line.get(spec['field']), f['value']
                if f['field'] == 'touch':
                    keep = abs(_num(lv) - _num(fv)) < 0.005
                elif f['op'] == 'contains':
                    keep = str(fv).lower() in str(lv).lower()
                else:
                    keep = str(lv).strip().lower() == str(fv).strip().lower()
                if not keep:
                    break
            if keep:
                lines.append(line)
    return lines, sql, params, skipped


def _agg_items(lines, plan):
    src = CATALOG['items']
    metrics, dim = plan['metrics'], plan.get('dimension')
    dfield = src['dimensions'][dim]['field'] if dim else None

    buckets = {}
    for ln in lines:
        key = ln.get(dfield, '(blank)') if dfield else '__all__'
        if key in (None, ''):
            key = '(blank)'
        b = buckets.setdefault(key, {'n': 0, 'sets': {}, 'sums': {}})
        b['n'] += 1
        for m in metrics:
            spec = src['metrics'][m]
            if spec['agg'] == 'count':
                continue
            if spec['agg'] == 'nunique':
                b['sets'].setdefault(m, set()).add(ln.get(spec['field']))
            else:
                b['sums'][m] = b['sums'].get(m, 0.0) + _num(ln.get(spec['field']))

    out = []
    for key, b in buckets.items():
        row = {}
        if dfield:
            row['grp'] = key
        for m in metrics:
            spec = src['metrics'][m]
            if spec['agg'] == 'count':
                row[m] = float(b['n'])
            elif spec['agg'] == 'nunique':
                row[m] = float(len(b['sets'].get(m, ())))
            elif spec['agg'] == 'avg':
                row[m] = (b['sums'].get(m, 0.0) / b['n']) if b['n'] else 0.0
            else:
                row[m] = b['sums'].get(m, 0.0)
        out.append(row)

    if dfield:
        dspec = src['dimensions'][dim]
        if dspec.get('order_by') or plan['order'] == 'label':
            out.sort(key=lambda r: str(r['grp']))
        else:
            out.sort(key=lambda r: r.get(metrics[0], 0), reverse=(plan['order'] != 'asc'))
        out = out[:min(plan['limit'], MAX_ROWS)]
    return out


# ==========================================================================
#  Presentation helpers
# ==========================================================================

def _columns(plan):
    src = CATALOG[plan['source']]
    cols = []
    if plan.get('dimension'):
        cols.append({'key': 'grp', 'label': src['dimensions'][plan['dimension']]['label'],
                     'type': T_TEXT})
    for m in plan['metrics']:
        spec = src['metrics'][m]
        cols.append({'key': m, 'label': spec['label'], 'type': spec['type']})
    return cols


def _chart(plan, rows):
    dim = plan.get('dimension')
    if not dim or not rows or not plan['metrics']:
        return None
    src = CATALOG[plan['source']]
    dspec = src['dimensions'][dim]
    if dspec.get('series'):
        ctype = 'line'
    elif dim in ('status', 'tagged', 'discount') and len(rows) <= 8:
        ctype = 'doughnut'
    else:
        ctype = 'bar'
    m0 = plan['metrics'][0]
    return {
        'type': ctype,
        'labels': [str(r.get('grp', '')) for r in rows],
        'series': [{'label': src['metrics'][m0]['label'],
                    'data': [round(float(r.get(m0) or 0), 4) for r in rows]}],
        'metric': m0,
    }


def _explain(plan):
    src = CATALOG[plan['source']]
    ms = ', '.join(src['metrics'][m]['label'] for m in plan['metrics'])
    bits = [ms, 'from ' + src['label']]
    rng = plan.get('range')
    bits.append('for ' + (rng['label'] if rng else 'all time'))
    if plan.get('dimension'):
        bits.append('grouped by ' + src['dimensions'][plan['dimension']]['label'])
        if plan['limit'] < MAX_ROWS:
            bits.append(('top ' if plan['order'] != 'asc' else 'lowest ') + str(plan['limit']))
    for f in plan.get('filters') or []:
        bits.append('where ' + f['label'])
    return ' • '.join(bits)


SUGGESTIONS = [
    'Total fine sale this month',
    'Top 10 customers by fine this year',
    'Outstanding fine by customer',
    'Daily sales trend last 30 days',
    'How many bills today',
    'Sales by bill type this month',
    'Stock fine by touch',
    'Untagged stock weight',
    'Top 5 items sold by weight this year',
    'Average gold rate last 3 months',
    'Cash balance by client',
    'Katti weight by month',
]


# ==========================================================================
#  The public engine
# ==========================================================================

class NLReport:
    """Answer natural-language business questions from the local database.

    `db` is the app's DBManager -- only its connection factory is used, and the
    connection is pinned read-only with PRAGMA query_only=ON.
    """

    def __init__(self, db, ai=None):
        self.db = db
        self.ai = ai
        self._entities = None
        self._entities_at = None

    # -- read-only connection ------------------------------------------
    def _conn(self):
        conn = self.db._get_connection()
        conn.execute('PRAGMA query_only=ON')   # hard read-only for this handle
        return conn

    def _anchor(self, conn):
        """TODAY as the DATABASE stamps it (date('now') is UTC in this app)."""
        try:
            v = conn.execute("SELECT date('now')").fetchone()[0]
            return datetime.datetime.strptime(v, '%Y-%m-%d').date()
        except Exception:
            return datetime.date.today()

    # -- entity vocabulary (for name filters) ---------------------------
    def _load_entities(self, conn, force=False):
        now = datetime.datetime.now()
        if (self._entities is not None and not force and self._entities_at
                and (now - self._entities_at).total_seconds() < 300):
            return self._entities
        ent = {'customer': [], 'item': [], 'code': [], 'tag': []}
        try:
            ent['customer'] = [r[0].strip() for r in conn.execute(
                "SELECT DISTINCT TRIM(customer) FROM sales_history "
                "WHERE TRIM(COALESCE(customer,''))<>'' LIMIT 4000") if r[0]]
            more = [r[0].strip() for r in conn.execute(
                "SELECT DISTINCT TRIM(client_name) FROM credit_ledger "
                "WHERE TRIM(COALESCE(client_name,''))<>'' LIMIT 4000") if r[0]]
            seen = set(x.lower() for x in ent['customer'])
            ent['customer'] += [m for m in more if m.lower() not in seen]
            ent['item'] = [r[0].strip() for r in conn.execute(
                "SELECT DISTINCT TRIM(it_name) FROM stock_inventory "
                "WHERE TRIM(COALESCE(it_name,''))<>'' LIMIT 4000") if r[0]]
            ent['code'] = [r[0].strip() for r in conn.execute(
                "SELECT DISTINCT TRIM(it_code) FROM stock_inventory "
                "WHERE TRIM(COALESCE(it_code,''))<>'' LIMIT 4000") if r[0]]
        except Exception as e:
            print('[NLR] entity load: %s' % e)
        self._entities, self._entities_at = ent, now
        return ent

    # -- catalog for the UI / LLM --------------------------------------
    def catalog(self):
        out = {'sources': {}, 'suggestions': SUGGESTIONS}
        for key, src in CATALOG.items():
            out['sources'][key] = {
                'label': src['label'],
                'metrics': {m: {'label': s['label'], 'type': s['type']}
                            for m, s in src['metrics'].items()},
                'dimensions': {d: {'label': s['label']} for d, s in src['dimensions'].items()},
                'filters': {f: {'label': s.get('label', f)}
                            for f, s in list((src.get('filters') or {}).items())
                            + list((src.get('item_filters') or {}).items())},
                'default_metrics': SOURCE_DEFAULT_METRICS.get(key, []),
            }
        return out

    # -- optional LLM planner (plan only, never SQL, never data) --------
    def _llm_plan(self, question):
        if not self.ai or not getattr(self.ai, 'is_configured', lambda: False)():
            return None, 'AI assist not configured'
        cat = []
        for key, src in CATALOG.items():
            cat.append('%s (%s)\n  metrics: %s\n  dimensions: %s\n  filters: %s' % (
                key, src['label'],
                ', '.join(src['metrics'].keys()),
                ', '.join(src['dimensions'].keys()),
                ', '.join(list((src.get('filters') or {}).keys())
                          + list((src.get('item_filters') or {}).keys()))))
        prompt = (
            "You convert a jewellery-shop owner's question into a REPORT PLAN. "
            "Reply with ONE JSON object and nothing else. Do not write SQL.\n\n"
            "Schema:\n"
            '{"source":"<one source key>","metrics":["<metric key>",...],'
            '"dimension":"<dimension key or null>",'
            '"range":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD","label":"..."} or null,'
            '"filters":[{"field":"<filter key>","op":"eq|contains","value":"..."}],'
            '"order":"desc|asc","limit":10}\n\n'
            "Use ONLY these keys:\n" + '\n'.join(cat) + "\n\n"
            "range=null means all time. Today is %s. The Indian financial year starts 1 April.\n"
            "The question may be in English, Hindi, Gujarati, Hinglish or Gujlish.\n\n"
            "Question: %s" % (self._anchor_str, question))
        try:
            res = self.ai.ask(prompt, history=None)
        except Exception as e:
            return None, 'AI assist failed: %s' % e
        if not isinstance(res, dict) or res.get('status') != 'success':
            return None, (res or {}).get('message') or 'AI assist unavailable'
        txt = res.get('answer') or ''
        m = re.search(r'\{.*\}', txt, re.S)
        if not m:
            return None, 'AI assist returned no plan'
        try:
            raw = json.loads(m.group(0))
        except Exception as e:
            return None, 'AI assist plan unreadable: %s' % e
        try:
            return validate_plan(raw), None       # rejects anything off-catalog
        except Exception as e:
            return None, 'AI assist plan rejected: %s' % e

    # -- main entry point ------------------------------------------------
    def ask(self, question, plan_override=None, use_ai=False):
        question = (question or '').strip()
        if not question and not plan_override:
            return {'status': 'error', 'message': 'Type a question first.',
                    'suggestions': SUGGESTIONS}
        conn = None
        try:
            conn = self._conn()
            anchor = self._anchor(conn)
            self._anchor_str = _iso(anchor)
            notes, source_of_plan = [], 'local'

            if plan_override:
                plan = validate_plan(plan_override)
                source_of_plan = 'manual'
            else:
                entities = self._load_entities(conn)
                plan = validate_plan(build_plan(question, anchor, entities))
                # Ask the LLM only when the local parser found nothing specific
                # AND the owner opted in for this request.
                if use_ai and plan.get('default_metrics') and not plan.get('dimension'):
                    lp, err = self._llm_plan(question)
                    if lp:
                        plan, source_of_plan = lp, 'ai'
                        notes.append('AI assist chose this plan (only your question text '
                                     'and the field names were sent — no data).')
                    elif err:
                        notes.append(err + ' — used the offline reader instead.')

            src = CATALOG[plan['source']]
            if plan['source'] == 'items':
                lines, sql, params, bad = _explode_items(conn, plan)
                rows = _agg_items(lines, plan)
                if bad:
                    notes.append('%d bill(s) had unreadable item data and were skipped.' % bad)
                scanned = len(lines)
            else:
                rows, sql, params = _run_sql(conn, plan)
                scanned = None

            # scalar shape -> KPI cards, no table
            kpis = []
            if not plan.get('dimension'):
                base = rows[0] if rows else {}
                for m in plan['metrics']:
                    spec = src['metrics'][m]
                    kpis.append({'key': m, 'label': spec['label'],
                                 'value': round(float(base.get(m) or 0), 4),
                                 'type': spec['type']})
                rows = []
            else:
                for m in plan['metrics']:
                    spec = src['metrics'][m]
                    if spec['type'] in (T_TOUCH,) or m.startswith('avg'):
                        continue
                    kpis.append({'key': m, 'label': 'Total ' + spec['label'],
                                 'value': round(sum(float(r.get(m) or 0) for r in rows), 4),
                                 'type': spec['type']})

            totals = {}
            if plan.get('dimension'):
                for m in plan['metrics']:
                    if src['metrics'][m]['type'] == T_TOUCH or m.startswith('avg'):
                        totals[m] = None
                    else:
                        totals[m] = round(sum(float(r.get(m) or 0) for r in rows), 4)

            if plan.get('range') and plan['range'].get('relative'):
                notes.append('"%s" = %s to %s (the app stamps bill dates in UTC).'
                             % (plan['range']['label'], plan['range']['from'],
                                plan['range']['to']))
            if plan.get('default_metrics') and source_of_plan == 'local':
                notes.append('No specific figure was named, so the headline numbers for '
                             '%s are shown. Use the controls above to change it.' % src['label'])
            if plan.get('dimension') and len(rows) >= min(plan['limit'], MAX_ROWS):
                notes.append('Showing %d row(s) — there may be more.' % len(rows))

            return {
                'status': 'success',
                'question': question,
                'plan': plan,
                'plan_source': source_of_plan,
                'explain': _explain(plan),
                'notes': notes,
                'columns': _columns(plan),
                'rows': rows,
                'totals': totals,
                'kpis': kpis,
                'chart': _chart(plan, rows),
                'row_count': len(rows),
                'scanned_lines': scanned,
                'sql': sql,
                'params': [str(p) for p in params],
                'anchor': _iso(anchor),
                'suggestions': SUGGESTIONS,
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e), 'suggestions': SUGGESTIONS}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # -- "did you mean" helper for an unrecognised word -----------------
    def suggest(self, question):
        t = _norm(question)
        words = [w for w in t.split() if w and w not in STOPWORDS and len(w) > 2]
        vocab = []
        for src in CATALOG.values():
            vocab += [p for phrases, _ in [] for p in phrases]
        for phrases, _ in METRIC_LEX + DIM_LEX:
            vocab += list(phrases)
        hints = []
        for w in words[:6]:
            near = difflib.get_close_matches(w, vocab, n=2, cutoff=0.78)
            for n in near:
                if n != w and n not in hints:
                    hints.append(n)
        return hints[:5]
