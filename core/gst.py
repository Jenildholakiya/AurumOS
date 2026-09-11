# -*- coding: utf-8 -*-
"""
GST registration details for the shop: validate, normalise, and read apart.

A GSTIN is not an opaque string. It carries the state, the PAN and a check
digit, so a typed one can be checked completely offline, and the state and PAN
never have to be asked for separately. Getting this wrong is not cosmetic: the
number is printed on a tax invoice and filed in a return, and a transposed
character makes both wrong in a way nobody notices until it is filed.

Used by main.py for the setup and settings screens. The same rules exist in
ui/gst.js so the field can go red as it is typed; this side is the authority.
"""

import re

# Position 1-2 of a GSTIN. Source: GST state code list, including the union
# territories added in 2020 (Ladakh 38, and 26 covering the merged
# Dadra & Nagar Haveli and Daman & Diu).
STATE_CODES = {
    '01': 'Jammu and Kashmir',      '02': 'Himachal Pradesh',
    '03': 'Punjab',                 '04': 'Chandigarh',
    '05': 'Uttarakhand',            '06': 'Haryana',
    '07': 'Delhi',                  '08': 'Rajasthan',
    '09': 'Uttar Pradesh',          '10': 'Bihar',
    '11': 'Sikkim',                 '12': 'Arunachal Pradesh',
    '13': 'Nagaland',               '14': 'Manipur',
    '15': 'Mizoram',                '16': 'Tripura',
    '17': 'Meghalaya',              '18': 'Assam',
    '19': 'West Bengal',            '20': 'Jharkhand',
    '21': 'Odisha',                 '22': 'Chhattisgarh',
    '23': 'Madhya Pradesh',         '24': 'Gujarat',
    '25': 'Daman and Diu',          '26': 'Dadra and Nagar Haveli and Daman and Diu',
    '27': 'Maharashtra',            '28': 'Andhra Pradesh (old)',
    '29': 'Karnataka',              '30': 'Goa',
    '31': 'Lakshadweep',            '32': 'Kerala',
    '33': 'Tamil Nadu',             '34': 'Puducherry',
    '35': 'Andaman and Nicobar Islands',
    '36': 'Telangana',              '37': 'Andhra Pradesh',
    '38': 'Ladakh',                 '97': 'Other Territory',
    '99': 'Centre Jurisdiction',
}

# Jewellery. 7113 is articles of jewellery of precious metal, taxed at 3%,
# split half CGST half SGST within a state. These are the defaults offered on
# the setup screen, not a rule: the shop can change them, and should ask its
# accountant rather than trust a default.
DEFAULT_HSN = '7113'
DEFAULT_CGST_RATE = 1.5
DEFAULT_SGST_RATE = 1.5

_ALPHANUM = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'

# 2 digits, PAN (5 letters, 4 digits, 1 letter), entity char, 'Z', check char.
GSTIN_RE = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$')
PAN_RE = re.compile(r'^[A-Z]{5}[0-9]{4}[A-Z]$')


def normalise(gstin):
    """Upper-case, with spaces and dashes removed. Never raises."""
    return re.sub(r'[\s\-]', '', str(gstin or '')).upper()


def checksum_char(first14):
    """The 15th character for the first 14 of a GSTIN.

    Each character's value is multiplied by an alternating 1, 2 weight; the
    two digits of that product are added (in base 36), and the check is what
    brings the running total up to a multiple of 36.
    """
    total = 0
    for i, ch in enumerate(first14):
        v = _ALPHANUM.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return _ALPHANUM[(36 - total % 36) % 36]


def validate(gstin):
    """Check a GSTIN completely, offline.

    Returns a dict that is always the same shape:
        ok            bool
        gstin         the normalised number
        error         why not, when ok is False
        state_code    '24'
        state         'Gujarat'
        pan           'AAPFU0939F'
        entity_code   the 13th character
    """
    g = normalise(gstin)
    out = {'ok': False, 'gstin': g, 'error': '', 'state_code': '',
           'state': '', 'pan': '', 'entity_code': ''}

    if not g:
        out['error'] = 'Enter a GSTIN.'
        return out
    if len(g) != 15:
        out['error'] = f'A GSTIN is 15 characters. This one is {len(g)}.'
        return out
    if not GSTIN_RE.match(g):
        out['error'] = 'That is not the shape of a GSTIN (15 characters: 22AAAAA0000A1Z5).'
        return out

    state_code = g[:2]
    if state_code not in STATE_CODES:
        out['error'] = f'{state_code} is not a GST state code.'
        return out

    expected = checksum_char(g[:14])
    if g[14] != expected:
        out['error'] = ('The check character is wrong, so there is a typo somewhere '
                        'in the number.')
        return out

    out.update({'ok': True, 'state_code': state_code, 'state': STATE_CODES[state_code],
                'pan': g[2:12], 'entity_code': g[12]})
    return out


def validate_pan(pan):
    """True when a PAN is well formed. There is no check digit to verify."""
    return bool(PAN_RE.match(normalise(pan)))


def clean_rate(value, fallback=0.0):
    """A tax rate as a number between 0 and 100. Junk becomes the fallback."""
    try:
        r = float(str(value).strip().rstrip('%') or fallback)
    except (TypeError, ValueError):
        return float(fallback)
    if r < 0 or r > 100:
        return float(fallback)
    return round(r, 3)


def defaults():
    """What a new shop sees before it types anything."""
    return {
        'gst_registered': '0',
        'gstin': '',
        'gst_legal_name': '',
        'gst_state': '',
        'gst_state_code': '',
        'pan': '',
        'gst_cgst_rate': str(DEFAULT_CGST_RATE),
        'gst_sgst_rate': str(DEFAULT_SGST_RATE),
        'gst_hsn': DEFAULT_HSN,
        'gst_price_inclusive': '0',
    }


def build(data, existing=None):
    """Turn whatever the screen sent into the values to store.

    Returns (fields, error). A shop that says it is not registered stores the
    flag and keeps the rest, so switching the toggle back does not mean typing
    the number in again.
    """
    src = dict(existing or {})
    src.update({k: v for k, v in (data or {}).items() if v is not None})

    registered = str(src.get('gst_registered', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
    fields = defaults()
    fields.update({k: str(src.get(k, fields[k])) for k in fields})
    fields['gst_registered'] = '1' if registered else '0'
    fields['gst_price_inclusive'] = '1' if str(
        src.get('gst_price_inclusive', '0')).strip().lower() in ('1', 'true', 'yes', 'on') else '0'
    fields['gst_cgst_rate'] = str(clean_rate(src.get('gst_cgst_rate'), DEFAULT_CGST_RATE))
    fields['gst_sgst_rate'] = str(clean_rate(src.get('gst_sgst_rate'), DEFAULT_SGST_RATE))
    fields['gst_hsn'] = re.sub(r'[^0-9]', '', str(src.get('gst_hsn', DEFAULT_HSN)))[:8]
    fields['gst_legal_name'] = str(src.get('gst_legal_name', '')).strip()

    if not registered:
        # Keep whatever was typed, but never present an unverified number as
        # this shop's registration.
        fields['gstin'] = normalise(src.get('gstin', ''))
        return fields, ''

    check = validate(src.get('gstin', ''))
    if not check['ok']:
        return fields, check['error']

    fields['gstin'] = check['gstin']
    fields['gst_state'] = check['state']
    fields['gst_state_code'] = check['state_code']
    pan = normalise(src.get('pan', '')) or check['pan']
    fields['pan'] = pan if validate_pan(pan) else check['pan']
    return fields, ''
