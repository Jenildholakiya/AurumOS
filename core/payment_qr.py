"""
UPI Payment QR code generator for AurumOS.
Uses the bundled qrcode library to create UPI deep-link QR codes.
"""
import base64
import io
import urllib.parse


def generate_upi_link(upi_id, shop_name, amount, bill_ref=''):
    params = {
        'pa': upi_id,
        'pn': shop_name,
        'cu': 'INR',
    }
    if amount and float(amount) > 0:
        params['am'] = f"{float(amount):.2f}"
    if bill_ref:
        params['tn'] = f"Payment for {bill_ref}"
    return 'upi://pay?' + urllib.parse.urlencode(params)


def generate_upi_qr(upi_id, shop_name, amount, bill_ref=''):
    try:
        import qrcode as _qr
    except ImportError:
        return None
    link = generate_upi_link(upi_id, shop_name, amount, bill_ref)
    qr = _qr.QRCode(version=1, error_correction=_qr.constants.ERROR_CORRECT_M,
                     box_size=8, border=2)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/png;base64,{b64}"


def generate_payment_qr(upi_id, shop_name, amount, bill_ref=''):
    return {
        'qr_base64': generate_upi_qr(upi_id, shop_name, amount, bill_ref),
        'upi_link': generate_upi_link(upi_id, shop_name, amount, bill_ref),
        'amount': float(amount or 0),
        'bill_ref': bill_ref,
    }
