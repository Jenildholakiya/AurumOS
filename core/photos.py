# -*- coding: utf-8 -*-
"""
Photographs of stock, and getting one to a customer over WhatsApp.

WHY THIS IS NOT SIMPLER THAN IT LOOKS
-------------------------------------
Half the WhatsApp traffic in a jewellery shop is "send me a picture". The
awkward part is that a wa.me link cannot carry an image. It carries text and
nothing else, and no amount of encoding changes that. Sending the picture
needs either the WhatsApp Business API through a provider, or a person.

So the flow here is: put the photo on the Windows clipboard, open WhatsApp
with the caption already written, and the shopkeeper presses Ctrl+V and send.
Two keystrokes, no provider, no monthly fee, and the photo actually arrives.
It is what shops already do by hand; this just removes the hunting through
folders for the right picture.

This module holds the parts worth testing on their own: what counts as a
usable image, what a stored photo is called, and what the caption says.
Copying and sending live in main.py, where the window and the clipboard are.
"""

import os
import re

# What a phone camera or a supplier actually sends. Everything else is
# refused rather than stored and found to be unopenable later.
ALLOWED = ('.jpg', '.jpeg', '.png', '.webp')

# WhatsApp compresses hard above a few megabytes and a shop counter PC does
# not need 40MB of raw camera output per ring.
MAX_BYTES = 8 * 1024 * 1024

FOLDER = 'photos'


def is_allowed(filename):
    """True when this looks like an image we can store and send."""
    ext = os.path.splitext(str(filename or ''))[1].lower()
    return ext in ALLOWED


def check(filename, size_bytes):
    """(ok, reason). The reason is written for a shopkeeper, not a log."""
    name = str(filename or '').strip()
    if not name:
        return False, 'No file chosen.'
    if not is_allowed(name):
        return False, ('That is not a picture file. Choose a JPG, PNG or WEBP.')
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        return False, 'Could not read that file.'
    if size <= 0:
        return False, 'That file is empty.'
    if size > MAX_BYTES:
        return False, ('That picture is %.1f MB. Keep it under %d MB, or '
                       'WhatsApp will refuse it.' % (size / 1048576.0,
                                                     MAX_BYTES // 1048576))
    return True, ''


def safe_name(tag_id, original, index=1):
    """What the stored copy is called.

    Named after the tag rather than whatever the camera called it, so the
    photo for a piece can always be found from the piece, and two customers'
    photos named IMG_0001.jpg cannot overwrite each other.
    """
    tag = re.sub(r'[^A-Za-z0-9_-]', '', str(tag_id or '')).upper() or 'ITEM'
    ext = os.path.splitext(str(original or ''))[1].lower()
    if ext not in ALLOWED:
        ext = '.jpg'
    suffix = '' if int(index or 1) <= 1 else '-%d' % int(index)
    return tag + suffix + ext


def folder(base):
    """Where photos live: beside the database, not inside the program folder,
    so an update or a reinstall never walks over them."""
    return os.path.join(str(base or '.'), FOLDER)


def path_for(base, filename):
    if not filename:
        return ''
    return os.path.join(folder(base), str(filename))


def rupees(amount):
    """A number grouped the Indian way: 2,85,000 rather than 285,000.

    Python's own thousands separator groups in threes all the way up, which
    reads as a foreign number to the customer this caption is going to.
    """
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return ''
    sign = '-' if n < 0 else ''
    digits = str(abs(n))
    if len(digits) <= 3:
        return sign + digits
    # Last three, then pairs: the lakh/crore grouping.
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ','.join(parts) + ',' + tail


def caption(item, shop='', note=''):
    """What is written under the picture.

    Enough for a customer to reply "how much for that one" without the shop
    having to look anything up, and no jargon: touch and fine weight mean
    nothing to a walk-in buyer.
    """
    item = item or {}
    name = str(item.get('it_name') or item.get('name') or 'This piece').strip()
    lines = ['*' + name + '*']

    bits = []
    karat = str(item.get('karat') or '').strip()
    if karat:
        bits.append(karat)
    try:
        wt = float(item.get('gr_wt') or 0)
        if wt > 0:
            bits.append('%.3f g' % wt)
    except (TypeError, ValueError):
        pass
    cat = str(item.get('category') or '').strip()
    if cat:
        bits.append(cat)
    if bits:
        lines.append(' · '.join(bits))

    try:
        price = float(item.get('sale_price') or 0)
        if price > 0:
            lines.append('Rs ' + rupees(price))
    except (TypeError, ValueError):
        pass

    if str(note or '').strip():
        lines.append('')
        lines.append(str(note).strip())

    lines.append('')
    lines.append('— ' + (str(shop or '').strip() or 'our shop'))
    return '\n'.join(lines)


def describe(item):
    """One line naming a piece, for a list or an announcement."""
    item = item or {}
    name = str(item.get('it_name') or item.get('name') or '').strip()
    bits = [b for b in (str(item.get('karat') or '').strip(),
                        str(item.get('category') or '').strip()) if b]
    try:
        wt = float(item.get('gr_wt') or 0)
        if wt > 0:
            bits.append('%.3f g' % wt)
    except (TypeError, ValueError):
        pass
    return (name + (' (' + ', '.join(bits) + ')' if bits else '')).strip()
