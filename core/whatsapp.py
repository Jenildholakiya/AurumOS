# -*- coding: utf-8 -*-
"""
WhatsApp messages to customers: phone numbers, wording, and who may be sent to.

HOW THIS SENDS, AND WHY IT IS NOT AUTOMATIC
-------------------------------------------
Every message here is built into a wa.me link that opens WhatsApp with the
text already written, and a person presses send. That is the same route
send_bill_whatsapp already uses, and it is deliberate.

A loop that fires messages at a customer list unattended is against WhatsApp's
Business Policy, and the practical consequence is not a warning letter: the
shop's number gets banned, usually within a day or two of the first blast, and
with it goes the number printed on every bill and business card. Sending
genuinely unattended needs the WhatsApp Business API through a provider, with
opted-in recipients and templates approved in advance.

So the shop reviews each message. For a broadcast that means working through a
queue, one tap each, which is slower and is the point: it keeps the number
alive and keeps someone reading what goes out.

WHO MAY BE SENT TO
------------------
A customer who has opted out is never included, by any route. A birthday wish
goes once a year, not once per time the screen is opened, which is what the
send log is for.

Everything in here is a pure function over plain values. No database, no
network, no clock of its own.
"""

import re
import urllib.parse

# Indian mobile numbers. A shop types these a dozen ways, and a wrong country
# code sends a birthday wish to a stranger.
IN_COUNTRY = '91'
IN_LOCAL_LEN = 10

# WhatsApp is intolerant of bursts from an ordinary number. This is the pacing
# the campaign screen suggests; it is guidance shown to a person, not a limiter
# on a loop, because there is no loop.
SUGGESTED_PER_HOUR = 40
SUGGESTED_GAP_SECONDS = 45

TEMPLATES = {
    'birthday': (
        "Happy birthday, {name}! 🎉\n\n"
        "Wishing you a wonderful year ahead, from all of us at {shop}.\n\n"
        "Do drop in when you are nearby — we would love to see you."
    ),
    'anniversary': (
        "Happy anniversary, {name}! 💍\n\n"
        "Wishing you both many more happy years, from all of us at {shop}."
    ),
    'new_item': (
        "*New at {shop}*\n\n"
        "{item}\n\n"
        "Come and have a look, or reply here and we will send photos."
    ),
    'festival': (
        "{greeting} from all of us at {shop}, {name}.\n\n"
        "{note}"
    ),
    'broadcast': "{note}",
    'due_reminder': (
        "Hello {name},\n\n"
        "A gentle reminder from {shop}: an amount of Rs {amount} is "
        "outstanding on your account.\n\n"
        "Please ignore this if you have already paid."
    ),
    'order_ready': (
        "Hello {name},\n\n"
        "Your {item} is ready for collection at {shop}.\n\n"
        "We look forward to seeing you."
    ),
    'scheme_due': (
        "Hello {name},\n\n"
        "This month's instalment of Rs {amount} for your {scheme} is due "
        "on {due_date}.\n\n"
        "From all of us at {shop}."
    ),
}

# Kept out of every send, whatever asked for it.
OPT_OUT_VALUES = {'1', 'true', 'yes', 'y', 'on', 'stop'}


def normalise_phone(raw, country=IN_COUNTRY):
    """A typed phone number as wa.me wants it: digits, with country code.

    Returns '' when there is nothing usable, because sending to a half-typed
    number is worse than not sending.
    """
    digits = re.sub(r'\D', '', str(raw or ''))
    if not digits:
        return ''
    # 0XXXXXXXXXX — the leading trunk zero people still write.
    if len(digits) == IN_LOCAL_LEN + 1 and digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == IN_LOCAL_LEN:
        return country + digits
    # Already carries the country code.
    if digits.startswith(country) and len(digits) == len(country) + IN_LOCAL_LEN:
        return digits
    # Some other country, left alone if it is a plausible length.
    if 10 <= len(digits) <= 15:
        return digits
    return ''


def is_opted_out(customer):
    """True when this customer has asked not to be messaged."""
    v = str((customer or {}).get('wa_opt_out', '') or '').strip().lower()
    return v in OPT_OUT_VALUES


def can_message(customer):
    """(ok, reason). Reason is something a person can act on."""
    c = customer or {}
    name = str(c.get('name') or '').strip()
    if not name:
        return False, 'No name'
    if is_opted_out(c):
        return False, 'Has opted out of messages'
    phone = normalise_phone(c.get('whatsapp') or c.get('phone'))
    if not phone:
        return False, 'No usable phone number'
    return True, ''


def render(kind, context):
    """Fill a template. Unknown placeholders stay as they are rather than
    raising, so a half-filled context still produces something sendable and
    the gap is visible to whoever reviews it."""
    body = TEMPLATES.get(kind)
    if body is None:
        body = str((context or {}).get('note') or '')
    out = body
    for key, value in (context or {}).items():
        out = out.replace('{' + str(key) + '}', str(value if value is not None else ''))
    return out.strip()


def link(phone, text):
    """The wa.me link that opens WhatsApp with the message already written."""
    number = normalise_phone(phone)
    if not number:
        return ''
    return 'https://wa.me/' + number + '?text=' + urllib.parse.quote(text or '')


def build(customer, kind, context=None, shop=''):
    """One message for one customer, ready to review and send.

    Always the same shape, so a caller never has to guess whether it worked:
        ok, name, phone, text, url, reason
    """
    c = dict(customer or {})
    ok, reason = can_message(c)
    ctx = dict(context or {})
    ctx.setdefault('name', str(c.get('name') or '').strip())
    ctx.setdefault('shop', shop or '')
    text = render(kind, ctx)
    phone = normalise_phone(c.get('whatsapp') or c.get('phone'))
    return {
        'ok': ok,
        'name': ctx['name'],
        'phone': phone,
        'text': text,
        'url': link(phone, text) if ok else '',
        'reason': reason,
        'kind': kind,
    }


def build_many(customers, kind, context=None, shop='', already_sent=None):
    """A queue to work through, and a plain account of who was left out.

    `already_sent` is the set of names this kind has gone to in the period
    that matters, so a birthday wish does not go twice because somebody opened
    the screen again after lunch.
    """
    sent = set(already_sent or ())
    queue, skipped = [], []
    for c in customers or []:
        name = str((c or {}).get('name') or '').strip()
        if name and name in sent:
            skipped.append({'name': name, 'reason': 'Already sent'})
            continue
        msg = build(c, kind, context, shop)
        if msg['ok']:
            queue.append(msg)
        else:
            skipped.append({'name': msg['name'] or '(no name)', 'reason': msg['reason']})
    return {
        'queue': queue,
        'skipped': skipped,
        'total': len(queue),
        'pacing': {
            'per_hour': SUGGESTED_PER_HOUR,
            'gap_seconds': SUGGESTED_GAP_SECONDS,
            'note': ('Send these a few at a time. An ordinary WhatsApp number '
                     'that sends a large burst gets banned, and it is the '
                     'number on your bills.'),
        },
    }
