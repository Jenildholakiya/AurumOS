"""
Tally Prime XML export for AurumOS.
Generates import-ready XML from chart_accounts + cash_vouchers.
"""
import os
import xml.etree.ElementTree as ET
from datetime import datetime

_TYPE_MAP = {
    'ASSET': 'Current Assets',
    'LIABILITY': 'Current Liabilities',
    'INCOME': 'Sales Accounts',
    'EXPENSE': 'Purchase Accounts',
    'EQUITY': 'Capital Account',
}

_VCH_TYPE_MAP = {
    'CASH_RCPT': 'Receipt', 'CASH_PAY': 'Payment',
    'BANK_RCPT': 'Receipt', 'BANK_PAY': 'Payment',
    'CONTRA': 'Contra', 'JV': 'Journal', 'GST_JV': 'Journal',
    'SALES': 'Sales', 'PURCHASE': 'Purchase',
    'DEBIT_NOTE': 'Debit Note', 'CREDIT_NOTE': 'Credit Note',
}


def _tally_date(d):
    try:
        dt = datetime.strptime(str(d), '%Y-%m-%d')
        return dt.strftime('%Y%m%d')
    except Exception:
        return datetime.now().strftime('%Y%m%d')


def generate_tally_xml(db, from_date='', to_date=''):
    db.ensure_finance_tables()
    root = ET.Element('ENVELOPE')
    header = ET.SubElement(root, 'HEADER')
    ET.SubElement(header, 'TALLYREQUEST').text = 'Import Data'
    body = ET.SubElement(root, 'BODY')
    import_data = ET.SubElement(body, 'IMPORTDATA')
    req_desc = ET.SubElement(import_data, 'REQUESTDESC')
    ET.SubElement(req_desc, 'REPORTNAME').text = 'All Masters and Vouchers'
    data = ET.SubElement(import_data, 'REQUESTDATA')

    conn = db._get_connection()
    try:
        accounts = conn.execute("SELECT code, name, type, acc_group, balance_type, opening_balance FROM chart_accounts ORDER BY type, name").fetchall()
        for a in accounts:
            msg = ET.SubElement(data, 'TALLYMESSAGE', xmlns_UDF='TallyUDF')
            ledger = ET.SubElement(msg, 'LEDGER', NAME=a[1], ACTION='Create')
            parent = _TYPE_MAP.get(a[2], 'Sundry Debtors')
            ET.SubElement(ledger, 'PARENT').text = parent
            ob = float(a[5] or 0)
            if abs(ob) > 0.005:
                ET.SubElement(ledger, 'OPENINGBALANCE').text = f"{ob:.2f} {'Dr' if a[4] == 'Dr' else 'Cr'}"

        where = ""
        params = []
        if from_date:
            where += " AND v.date >= ?"; params.append(from_date)
        if to_date:
            where += " AND v.date <= ?"; params.append(to_date)
        vouchers = conn.execute(f"""
            SELECT v.vch_no, v.type, v.date, v.narration
            FROM cash_vouchers v WHERE 1=1 {where}
            ORDER BY v.date, v.id
        """, params).fetchall()

        acct_names = {r[0]: r[1] for r in accounts}

        for v in vouchers:
            lines = conn.execute(
                "SELECT account_code, dr, cr FROM cash_voucher_lines WHERE vch_no=?",
                (v[0],)).fetchall()
            if not lines:
                continue
            msg = ET.SubElement(data, 'TALLYMESSAGE', xmlns_UDF='TallyUDF')
            vch_type = _VCH_TYPE_MAP.get(v[1], 'Journal')
            voucher = ET.SubElement(msg, 'VOUCHER', VCHTYPE=vch_type, ACTION='Create')
            ET.SubElement(voucher, 'DATE').text = _tally_date(v[2])
            ET.SubElement(voucher, 'NARRATION').text = v[3] or ''
            ET.SubElement(voucher, 'VOUCHERNUMBER').text = v[0]
            ET.SubElement(voucher, 'VOUCHERTYPENAME').text = vch_type

            for ln in lines:
                entry = ET.SubElement(voucher, 'ALLLEDGERENTRIES.LIST')
                ledger_name = acct_names.get(ln[0], ln[0])
                ET.SubElement(entry, 'LEDGERNAME').text = ledger_name
                dr = float(ln[1] or 0)
                cr = float(ln[2] or 0)
                amount = -dr if dr > 0 else cr
                ET.SubElement(entry, 'AMOUNT').text = f"{amount:.2f}"
    finally:
        conn.close()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    export_dir = os.path.join(base, 'exports')
    os.makedirs(export_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m')
    path = os.path.join(export_dir, f'tally_export_{stamp}.xml')
    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(path, encoding='unicode', xml_declaration=True)
    return path
