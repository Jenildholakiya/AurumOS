"""
E-Invoice (GST) and E-Way Bill JSON generator for AurumOS.
Produces JSON payloads per the e-invoice schema v1.1 that can be
uploaded to any GST e-invoice portal or ASP/GSP.
"""
import json
import os
from datetime import datetime

try:
    from core.gst import STATE_CODES
except ImportError:
    STATE_CODES = {}


def _state_code_from_gstin(gstin):
    return gstin[:2] if gstin and len(gstin) >= 2 else ''


def _state_name(code):
    for name, sc in STATE_CODES.items():
        if str(sc) == str(code):
            return name
    return ''


def build_einvoice_json(bill_data, shop_info):
    b = bill_data or {}
    s = shop_info or {}
    items_raw = b.get('items') or []
    if isinstance(items_raw, str):
        try:
            items_raw = json.loads(items_raw)
        except Exception:
            items_raw = []

    seller_gstin = s.get('gstin', '')
    seller_sc = _state_code_from_gstin(seller_gstin)
    buyer_gstin = b.get('customer_gstin', '')
    buyer_sc = _state_code_from_gstin(buyer_gstin) or seller_sc

    doc_date = b.get('date', '') or datetime.now().strftime('%d/%m/%Y')
    if '-' in doc_date:
        try:
            doc_date = datetime.strptime(doc_date, '%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception:
            pass

    item_list = []
    total_val = 0
    total_cgst = 0
    total_sgst = 0
    for i, it in enumerate(items_raw):
        amt = float(it.get('amount', 0))
        making = float(it.get('making', 0))
        taxable = amt - making
        cgst_rate = float(b.get('cgst_rate', 1.5))
        sgst_rate = float(b.get('sgst_rate', 1.5))
        cgst_amt = round(taxable * cgst_rate / 100, 2)
        sgst_amt = round(taxable * sgst_rate / 100, 2)
        total_val += taxable
        total_cgst += cgst_amt
        total_sgst += sgst_amt
        item_list.append({
            'SlNo': str(i + 1),
            'PrdDesc': it.get('name', '') or it.get('code', ''),
            'IsServc': 'N',
            'HsnCd': s.get('hsn', '7113'),
            'Qty': float(it.get('qty', 1)),
            'Unit': 'GMS',
            'UnitPrice': round(float(it.get('rate', 0)), 2),
            'TotAmt': round(amt, 2),
            'AssAmt': round(taxable, 2),
            'CgstRt': cgst_rate,
            'SgstRt': sgst_rate,
            'CgstAmt': cgst_amt,
            'SgstAmt': sgst_amt,
            'TotItemVal': round(taxable + cgst_amt + sgst_amt, 2),
        })

    grand = round(total_val + total_cgst + total_sgst, 2)

    payload = {
        'Version': '1.1',
        'TranDtls': {
            'TaxSch': 'GST',
            'SupTyp': 'B2B',
            'RegRev': 'N',
            'IgstOnIntra': 'N',
        },
        'DocDtls': {
            'Typ': 'INV',
            'No': b.get('vch_id', ''),
            'Dt': doc_date,
        },
        'SellerDtls': {
            'Gstin': seller_gstin,
            'LglNm': s.get('business_name', ''),
            'TrdNm': s.get('business_name', ''),
            'Addr1': s.get('address', ''),
            'Loc': s.get('city', ''),
            'Pin': int(s.get('pin', '000000') or '000000'),
            'Stcd': seller_sc,
        },
        'BuyerDtls': {
            'Gstin': buyer_gstin or 'URP',
            'LglNm': b.get('customer', ''),
            'TrdNm': b.get('customer', ''),
            'Pos': buyer_sc,
            'Addr1': b.get('customer_address', ''),
            'Loc': b.get('customer_city', ''),
            'Pin': int(b.get('customer_pin', '000000') or '000000'),
            'Stcd': buyer_sc,
        },
        'ItemList': item_list,
        'ValDtls': {
            'AssVal': round(total_val, 2),
            'CgstVal': round(total_cgst, 2),
            'SgstVal': round(total_sgst, 2),
            'IgstVal': 0,
            'TotInvVal': grand,
        },
    }
    return payload


def export_einvoice(bill_data, shop_info):
    payload = build_einvoice_json(bill_data, shop_info)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    export_dir = os.path.join(base, 'exports')
    os.makedirs(export_dir, exist_ok=True)
    vch = bill_data.get('vch_id', 'unknown')
    path = os.path.join(export_dir, f'einvoice_{vch}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def build_eway_json(bill_data, shop_info, transport=None):
    einv = build_einvoice_json(bill_data, shop_info)
    t = transport or {}
    einv['EwbDtls'] = {
        'TransId': t.get('transporter_id', ''),
        'TransName': t.get('transporter_name', ''),
        'TransMode': t.get('mode', '1'),
        'Distance': int(t.get('distance', 0)),
        'TransDocNo': t.get('doc_no', ''),
        'TransDocDt': t.get('doc_date', ''),
        'VehNo': t.get('vehicle_no', ''),
        'VehType': t.get('vehicle_type', 'R'),
    }
    return einv
