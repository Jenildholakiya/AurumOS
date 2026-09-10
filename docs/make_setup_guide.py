"""Build the AurumOS multi-PC setup guide as a PDF.

Run from the project root:  python docs/make_setup_guide.py

Kept in the repository so the guide can be rebuilt whenever the setup flow
changes, rather than being a binary nobody can edit. Colours follow the
application's own palette so the printed guide and the screen match.
"""

import os
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

# -- palette, taken from the application's own CSS -----------------------
INK = colors.HexColor('#0f172a')
INK2 = colors.HexColor('#1e293b')
MUTED = colors.HexColor('#475569')
MUTED2 = colors.HexColor('#94a3b8')
RULE = colors.HexColor('#e2e8f0')
PAPER = colors.HexColor('#ffffff')
WASH = colors.HexColor('#f8fafb')

BLUE = colors.HexColor('#2563eb')
BLUE_WASH = colors.HexColor('#eff4ff')
GREEN = colors.HexColor('#16a34a')
GREEN_WASH = colors.HexColor('#eefbf3')
AMBER = colors.HexColor('#b45309')
AMBER_WASH = colors.HexColor('#fef6ec')
RED = colors.HexColor('#dc2626')
RED_WASH = colors.HexColor('#fef2f2')

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = os.path.join(ROOT, 'ui', 'aurum_mark.png')
OUT = os.path.join(ROOT, 'docs', 'AurumOS_MultiPC_Setup_Guide.pdf')

PORT = '58903'

styles = getSampleStyleSheet()


def style(name, **kw):
    base = dict(fontName='Helvetica', fontSize=9.5, leading=14.5,
                textColor=MUTED, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(name, **base)


BODY = style('body')
BODY_INK = style('bodyInk', textColor=INK2)
LEAD = style('lead', fontSize=11, leading=17, textColor=MUTED)
H1 = style('h1', fontName='Helvetica-Bold', fontSize=19, leading=24, textColor=INK)
H2 = style('h2', fontName='Helvetica-Bold', fontSize=12.5, leading=17, textColor=INK)
STEP_T = style('stepT', fontName='Helvetica-Bold', fontSize=10.5, leading=14, textColor=INK)
SMALL = style('small', fontSize=8.5, leading=12.5, textColor=MUTED2)
MONO = style('mono', fontName='Courier-Bold', fontSize=9.5, leading=14, textColor=INK)
CELL = style('cell', fontSize=8.8, leading=12.5, textColor=MUTED)
CELL_B = style('cellB', fontName='Helvetica-Bold', fontSize=8.8, leading=12.5, textColor=INK2)
CALL_T = style('callT', fontName='Helvetica-Bold', fontSize=9.5, leading=13)
CALL_B = style('callB', fontSize=9, leading=13.5)


# -- building blocks ------------------------------------------------------

class Rule(Flowable):
    """A hairline the width of the frame."""

    def __init__(self, colour=RULE, thickness=0.7, space=0):
        Flowable.__init__(self)
        self.colour, self.thickness, self.space = colour, thickness, space
        self.width, self.height = 0, thickness + space

    def wrap(self, w, h):
        self.width = w
        return w, self.height

    def draw(self):
        self.canv.setStrokeColor(self.colour)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, self.space / 2.0, self.width, self.space / 2.0)


class Callout(Flowable):
    """A tinted box with a coloured spine, for warnings and asides."""

    TONES = {
        'info':     (BLUE, BLUE_WASH),
        'warn':     (AMBER, AMBER_WASH),
        'critical': (RED, RED_WASH),
        'good':     (GREEN, GREEN_WASH),
    }

    def __init__(self, tone, title, body, width=CONTENT_W):
        Flowable.__init__(self)
        self.accent, self.wash = self.TONES.get(tone, self.TONES['info'])
        self.pad = 9
        self.width = width
        self.title = Paragraph(title, ParagraphStyle(
            'ct', parent=CALL_T, textColor=self.accent))
        self.body = Paragraph(body, ParagraphStyle(
            'cb', parent=CALL_B, textColor=INK2))
        self.height = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        inner = self.width - 2 * self.pad - 4
        _, th = self.title.wrap(inner, availHeight)
        _, bh = self.body.wrap(inner, availHeight)
        self.height = th + bh + 2 * self.pad + 4
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.wash)
        c.setStrokeColor(self.wash)
        c.roundRect(0, 0, self.width, self.height, 5, stroke=0, fill=1)
        c.setFillColor(self.accent)
        c.roundRect(0, 0, 3.2, self.height, 1.6, stroke=0, fill=1)
        inner = self.width - 2 * self.pad - 4
        y = self.height - self.pad
        _, th = self.title.wrap(inner, self.height)
        self.title.drawOn(c, self.pad + 4, y - th)
        y -= th + 4
        _, bh = self.body.wrap(inner, self.height)
        self.body.drawOn(c, self.pad + 4, y - bh)


class Step(Flowable):
    """A numbered instruction: badge on the left, text on the right."""

    def __init__(self, number, title, body, tone=BLUE):
        Flowable.__init__(self)
        self.number = str(number)
        self.tone = tone
        self.gutter = 26
        self.title = Paragraph(title, STEP_T)
        self.body = Paragraph(body, BODY) if body else None
        self.width = CONTENT_W
        self.height = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        inner = self.width - self.gutter
        _, th = self.title.wrap(inner, availHeight)
        bh = 0
        if self.body:
            _, bh = self.body.wrap(inner, availHeight)
        self.height = max(th + bh + (3 if self.body else 0), 20)
        return self.width, self.height

    def draw(self):
        c = self.canv
        inner = self.width - self.gutter
        top = self.height
        # badge, aligned to the first line of the title
        r = 8.2
        cy = top - 6.5
        c.setFillColor(self.tone)
        c.circle(r + 1, cy, r, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 9)
        c.drawCentredString(r + 1, cy - 3.1, self.number)

        y = top
        _, th = self.title.wrap(inner, top)
        self.title.drawOn(c, self.gutter, y - th)
        if self.body:
            y -= th + 3
            _, bh = self.body.wrap(inner, top)
            self.body.drawOn(c, self.gutter, y - bh)


class Diagram(Flowable):
    """How the PCs sit relative to one another."""

    BOX_W, BOX_H = 108, 50
    GAP = 16

    def __init__(self, width=CONTENT_W, height=152):
        Flowable.__init__(self)
        self.width, self.height = width, height

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return self.width, self.height

    def _box(self, x, y, w, h, label, sub, accent, filled):
        c = self.canv
        c.setFillColor(accent if filled else PAPER)
        c.setStrokeColor(accent)
        c.setLineWidth(1.1)
        c.roundRect(x, y, w, h, 5, stroke=1, fill=1)
        c.setFillColor(colors.white if filled else INK)
        c.setFont('Helvetica-Bold', 8.6)
        c.drawCentredString(x + w / 2.0, y + h - 15, label)
        c.setFillColor(colors.Color(1, 1, 1, 0.85) if filled else MUTED2)
        c.setFont('Helvetica', 7.2)
        for i, line in enumerate(sub):
            c.drawCentredString(x + w / 2.0, y + h - 26 - i * 9, line)

    def draw(self):
        c = self.canv
        bw, bh, gap = self.BOX_W, self.BOX_H, self.GAP
        server_y = self.height - bh
        mid = self.width / 2.0
        self._box(mid - bw / 2.0, server_y, bw, bh, 'MAIN PC',
                  ['Owner', 'Runs the shop server'], BLUE, True)

        n = 3
        start = (self.width - (n * bw + (n - 1) * gap)) / 2.0
        centres = [start + i * (bw + gap) + bw / 2.0 for i in range(n)]

        # One trunk, halfway down the gap, so nothing runs along a box edge.
        trunk = (server_y + bh) / 2.0 - 2
        c.setStrokeColor(BLUE)
        c.setLineWidth(1.0)
        c.line(mid, server_y, mid, trunk)
        c.line(centres[0], trunk, centres[-1], trunk)

        for i, cx in enumerate(centres):
            spare = i == n - 1
            self._box(cx - bw / 2.0, 0, bw, bh, 'STAFF PC %d' % (i + 1),
                      ['Spare seat'] if spare else ['Billing only'],
                      MUTED2 if spare else INK2, False)
            c.setStrokeColor(RULE if spare else BLUE)
            if spare:
                c.setDash(2, 2)
            c.line(cx, trunk, cx, bh)
            c.setDash()

        c.setFillColor(MUTED2)
        c.setFont('Courier-Bold', 7.4)
        c.drawString(mid + 7, (server_y + trunk) / 2.0 - 2.5, 'TCP ' + PORT)


def table(rows, widths, header=True):
    data = []
    for r, row in enumerate(rows):
        cells = []
        for col, cell in enumerate(row):
            if r == 0 and header:
                cells.append(Paragraph(
                    f'<font color="#ffffff"><b>{cell}</b></font>', CELL))
            else:
                # By position, not by value: two identical cells in one row
                # would otherwise both take the first column's styling.
                cells.append(Paragraph(cell, CELL_B if col == 0 else CELL))
        data.append(cells)
    t = Table(data, colWidths=widths, hAlign='LEFT', repeatRows=1 if header else 0)
    look = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, RULE),
        ('BOX', (0, 0), (-1, -1), 0.6, RULE),
    ]
    if header:
        look += [('BACKGROUND', (0, 0), (-1, 0), INK),
                 ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
                 ('TOPPADDING', (0, 0), (-1, 0), 7)]
        for i in range(2, len(data), 2):
            look.append(('BACKGROUND', (0, i), (-1, i), WASH))
    t.setStyle(TableStyle(look))
    return t


def heading(number, text, tone=BLUE):
    """A part heading with a coloured tab."""
    tab = Table([[Paragraph(
        f'<font color="#ffffff"><b>PART {number}</b></font>',
        style('tab', fontSize=7.6, leading=10, alignment=TA_CENTER))]],
        colWidths=[46], rowHeights=[14], hAlign='LEFT')
    tab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), tone),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    return [tab, Spacer(1, 7), Paragraph(text, H1), Spacer(1, 3),
            Rule(tone, 1.6), Spacer(1, 12)]


# -- page furniture -------------------------------------------------------

def cover(canv, doc):
    canv.saveState()
    canv.setFillColor(INK)
    canv.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    canv.setFillColor(BLUE)
    canv.rect(0, PAGE_H - 6, PAGE_W, 6, stroke=0, fill=1)

    if os.path.exists(MARK):
        try:
            canv.drawImage(ImageReader(MARK), MARGIN, PAGE_H - 118,
                           width=42, height=42, mask='auto')
        except Exception:
            pass

    canv.setFillColor(colors.white)
    canv.setFont('Helvetica-Bold', 15)
    canv.drawString(MARGIN + 54, PAGE_H - 100, 'AurumOS')
    canv.setFillColor(MUTED2)
    canv.setFont('Helvetica', 8.4)
    canv.drawString(MARGIN + 54, PAGE_H - 112, 'Jewellery management system')

    canv.setFillColor(colors.white)
    canv.setFont('Helvetica-Bold', 34)
    canv.drawString(MARGIN, PAGE_H - 250, 'Multi-PC Setup')
    canv.setFillColor(BLUE)
    canv.drawString(MARGIN, PAGE_H - 288, 'Guide')

    canv.setStrokeColor(colors.HexColor('#334155'))
    canv.setLineWidth(1)
    canv.line(MARGIN, PAGE_H - 312, MARGIN + 78, PAGE_H - 312)

    canv.setFillColor(colors.HexColor('#cbd5e1'))
    canv.setFont('Helvetica', 11.5)
    for i, line in enumerate([
            'Connect your staff computers to the main PC,',
            'so every bill reaches one place.']):
        canv.drawString(MARGIN, PAGE_H - 340 - i * 17, line)

    # contents, so the cover carries something useful rather than a gap
    top = PAGE_H - 400
    canv.setFillColor(colors.HexColor('#64748b'))
    canv.setFont('Helvetica-Bold', 7.6)
    canv.drawString(MARGIN, top, 'W H A T   I S   I N S I D E')
    canv.setStrokeColor(colors.HexColor('#1e293b'))
    canv.setLineWidth(0.8)
    canv.line(MARGIN, top - 9, PAGE_W - MARGIN, top - 9)

    entries = [
        ('How this works', 'Layout, requirements and seat limits', '1'),
        ('Part 1  ·  Set up the main PC', 'The computer that holds the shop', '2'),
        ('Part 2  ·  Connect a staff PC', 'Checking the address and joining', '3'),
        ('Part 3  ·  Day to day', 'What staff see, and common questions', '4'),
        ('Part 4  ·  When something goes wrong', 'Symptoms, causes and fixes', '5'),
        ('Part 5  ·  What is finished, and what is not', 'Read this first', '6'),
    ]
    y = top - 30
    for title, sub, page in entries:
        canv.setFillColor(colors.white)
        canv.setFont('Helvetica-Bold', 9.6)
        canv.drawString(MARGIN, y, title)
        canv.setFillColor(colors.HexColor('#64748b'))
        canv.setFont('Helvetica', 8)
        canv.drawString(MARGIN, y - 11, sub)
        canv.setFillColor(BLUE)
        canv.setFont('Helvetica-Bold', 9.6)
        canv.drawRightString(PAGE_W - MARGIN, y, page)
        y -= 32

    # status chip
    chip_y = 118
    canv.setFillColor(colors.HexColor('#7c2d12'))
    canv.roundRect(MARGIN, chip_y, 196, 21, 10.5, stroke=0, fill=1)
    canv.setFillColor(colors.HexColor('#fdba74'))
    canv.setFont('Helvetica-Bold', 8)
    canv.drawCentredString(MARGIN + 98, chip_y + 7, 'PRE-RELEASE  ·  NOT YET SHIPPED')

    canv.setFillColor(MUTED2)
    canv.setFont('Helvetica', 8.4)
    canv.drawString(MARGIN, 92, 'See Part 5 for exactly what is finished and what is not.')

    canv.setFillColor(colors.HexColor('#64748b'))
    canv.setFont('Helvetica', 7.8)
    canv.drawString(MARGIN, 52, 'Internal draft  ·  Prepared for the AurumOS rollout')
    canv.restoreState()


def furniture(canv, doc):
    canv.saveState()
    canv.setFillColor(MUTED2)
    canv.setFont('Helvetica', 7.4)
    canv.drawString(MARGIN, PAGE_H - MARGIN + 12, 'AurumOS  ·  Multi-PC Setup Guide')
    canv.setStrokeColor(RULE)
    canv.setLineWidth(0.6)
    canv.line(MARGIN, PAGE_H - MARGIN + 7, PAGE_W - MARGIN, PAGE_H - MARGIN + 7)
    canv.line(MARGIN, MARGIN - 9, PAGE_W - MARGIN, MARGIN - 9)
    canv.setFillColor(MUTED2)
    canv.setFont('Helvetica', 7.4)
    canv.drawRightString(PAGE_W - MARGIN, MARGIN - 19, str(canv.getPageNumber() - 1))
    canv.restoreState()


def build():
    doc = BaseDocTemplate(OUT, pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN,
                          title='AurumOS Multi-PC Setup Guide',
                          author='AurumOS', subject='Connecting staff PCs to the main PC')
    frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id='f')
    doc.addPageTemplates([
        PageTemplate(id='cover', frames=[frame], onPage=cover),
        PageTemplate(id='body', frames=[frame], onPage=furniture),
    ])

    s = [NextPageTemplate('body'), PageBreak()]

    # ---------------------------------------------------------------- intro
    s += [Paragraph('How this works', H1), Spacer(1, 3), Rule(BLUE, 1.6),
          Spacer(1, 12)]
    s += [Paragraph(
        'One computer is the <b>main PC</b>. It holds the shop and runs a small '
        'server the other computers talk to. Every other computer is a '
        '<b>staff PC</b>. A staff PC keeps its own copy of the data so a brief '
        'network drop does not stop a sale, but it cannot let anyone in on its '
        'own: passwords are checked on the main PC.', LEAD), Spacer(1, 16)]
    s += [Diagram(), Spacer(1, 16)]
    s += [Callout('info', 'The main PC must be switched on',
                  'Staff computers sign in through it. If it is off, or the two '
                  'machines are on different networks, staff cannot start work. '
                  'That is deliberate, not a fault.'), Spacer(1, 14)]

    s += [Paragraph('Before you start', H2), Spacer(1, 8)]
    s += [table([
        ['Requirement', 'Detail'],
        ['Same network', 'Every PC on one router, by cable or Wi-Fi. A guest '
                         'network will not work.'],
        ['Windows', 'Windows 10 or 11, with .NET Desktop Runtime 8.0 or later.'],
        ['Same version', 'Install the same AurumOS build on every computer.'],
        ['Licence', 'Pro or Enterprise. Lite is single-PC only.'],
        ['Staff accounts', 'Create them on the main PC first, under Staff.'],
    ], [95, CONTENT_W - 95]), Spacer(1, 14)]

    s += [Paragraph('How many computers you can connect', H2), Spacer(1, 8)]
    s += [table([
        ['Plan', 'Main PC', 'Staff PCs', 'Total'],
        ['Lite', '1', 'None', '1'],
        ['Pro', '1', '1', '2'],
        ['Enterprise', '1', '4', '5'],
    ], [110, 90, 110, CONTENT_W - 310]), Spacer(1, 9)]
    s += [Paragraph(
        'Need more than four? Extra connections can be added to your plan. They '
        'take effect on their own, with no reinstall.', SMALL)]

    # --------------------------------------------------------------- part 1
    s += [PageBreak()]
    s += heading(1, 'Set up the main PC')
    s += [Paragraph(
        'Do this first, and only on the computer that will hold the shop. This '
        'is normally the owner\'s PC.', LEAD), Spacer(1, 14)]

    for n, (t, b) in enumerate([
        ('Install AurumOS and open it',
         'On first run the setup screen appears. Complete Step 1 by entering '
         'your licence key and waiting for it to verify.'),
        ('At Step 2, choose Host Core',
         'Step 2 is titled Topology and offers two cards. Pick <b>Host Core</b> '
         'on the left. This is the card that makes the computer the server.'),
        ('Fill in your shop details and finish',
         'Enter the business name, owner, and the owner password at Step 3. '
         'Keep this password private. It is the only account with full access.'),
        ('Add your staff accounts',
         'Sign in, open <b>Staff</b>, and create one account per person, each '
         'with its own password. Staff sign in on their own PC with these.'),
        ('Write down this PC\'s address',
         'You need it on every staff computer. Finding it is described below.'),
    ], start=1):
        s += [Step(n, t, b), Spacer(1, 11)]

    s += [Spacer(1, 4), Callout(
        'critical', 'Windows Firewall will block staff PCs until you allow it',
        'This is the single most common reason a staff computer cannot connect. '
        'Windows blocks incoming connections by default, so the main PC will '
        'look switched off to every other computer. Open Command Prompt as '
        'administrator on the main PC and run the command below, once.'),
        Spacer(1, 10)]
    s += [_code('netsh advfirewall firewall add rule name="AurumOS Shop Server" '
                'dir=in action=allow protocol=TCP localport=' + PORT), Spacer(1, 14)]

    s += [Paragraph('Finding the address of the main PC', H2), Spacer(1, 8)]
    s += [Paragraph(
        'Open Command Prompt on the main PC and run <font face="Courier-Bold">'
        'ipconfig</font>. Look for <b>IPv4 Address</b>. It usually starts with '
        '192.168 and looks like 192.168.1.15. That is what staff computers need.',
        BODY), Spacer(1, 11)]
    s += [Callout('warn', 'Addresses can change on their own',
                  'Most routers hand out addresses that change after a restart. '
                  'If staff PCs stop connecting one morning, this is almost '
                  'always why. Ask whoever manages your router to reserve a '
                  'fixed address for the main PC and the problem disappears '
                  'for good.')]

    # --------------------------------------------------------------- part 2
    s += [PageBreak()]
    s += heading(2, 'Connect a staff PC')
    s += [Paragraph(
        'Repeat this on each staff computer. It takes about two minutes per '
        'machine.', LEAD), Spacer(1, 14)]

    for n, (t, b) in enumerate([
        ('Install AurumOS and open it',
         'The same setup screen appears. Complete Step 1 with the same licence '
         'key you used on the main PC.'),
        ('At Step 2, choose Client Node',
         'Pick the <b>Client Node</b> card on the right. An address box appears '
         'underneath it.'),
        ('Type the main PC address and press Check',
         'Enter the address you wrote down, for example 192.168.1.15, then '
         'press <b>Check</b>. Do not guess: the next step explains what the '
         'answer means.'),
        ('Confirm the shop name that comes back',
         'A green panel shows the name of the shop that answered. If that is '
         'your shop, you reached the right computer. If it names a different '
         'shop, you typed the address of the wrong machine.'),
        ('Continue and finish',
         'The staff PC joins the shop and appears on the owner\'s list of '
         'connected computers.'),
        ('Sign in',
         'The login screen shows a <b>Staff terminal</b> badge with your shop '
         'name. The staff member enters their own password, and the computer '
         'opens straight into the screen they are allowed to use.'),
    ], start=1):
        s += [Step(n, t, b), Spacer(1, 11)]

    s += [Spacer(1, 6), Paragraph('If the check does not go green', H2),
          Spacer(1, 8)]
    s += [table([
        ['What you see', 'What it means', 'What to do'],
        ['No answer from that address',
         'Nothing is listening there.',
         'Check the main PC is on and AurumOS is open. Then check the firewall '
         'command in Part 1 was run.'],
        ['Not running AurumOS',
         'Something answered, but it is another device.',
         'You have the address of the wrong machine. Run ipconfig on the main '
         'PC again.'],
        ['Could not be reached',
         'The two PCs cannot see each other.',
         'Put both on the same router. Guest Wi-Fi separates devices on '
         'purpose.'],
        ['A different shop name',
         'You reached an AurumOS PC, but not yours.',
         'Check each number of the address carefully.'],
        ['This licence connects N PCs',
         'Every seat is already taken.',
         'Remove a computer you no longer use on the main PC, or add a '
         'connection to your plan.'],
    ], [96, 108, CONTENT_W - 204])]

    # --------------------------------------------------------------- part 3
    s += [PageBreak()]
    s += heading(3, 'Day to day')
    s += [Paragraph('What a staff member sees', H2), Spacer(1, 8)]
    s += [Paragraph(
        'A staff computer shows only what that person has been given. Someone '
        'set up for billing opens the billing screen and sees nothing else in '
        'the sidebar. They cannot open settings, staff accounts, or year end, '
        'and they cannot delete a bill.', BODY), Spacer(1, 12)]

    s += [Paragraph('Turning a computer on and off', H2), Spacer(1, 8)]
    s += [Paragraph(
        'Start the main PC first and leave AurumOS open on it while the shop is '
        'trading. Staff computers can be started and shut down freely. At '
        'closing time, shut the staff computers down before the main PC so '
        'nothing is left mid-sale.', BODY), Spacer(1, 12)]

    s += [Callout('good', 'A short network drop is not a problem',
                  'If the network flickers, a staff computer holds what it is '
                  'doing and picks up again by itself once the connection '
                  'returns. Nobody has to sign in again.'), Spacer(1, 14)]

    s += [Paragraph('Common questions', H2), Spacer(1, 8)]
    s += [table([
        ['Question', 'Answer'],
        ['Can staff use the owner password?',
         'They can sign in with it, but the computer still gives them a staff '
         'session only. Full access exists on the main PC alone.'],
        ['Can two people bill at once?',
         'Yes. Each computer numbers its own bills, and the numbers never '
         'collide.'],
        ['What if the main PC breaks?',
         'Staff computers stop after a few minutes. Each holds its own copy of '
         'the data, so nothing recorded is lost.'],
        ['Does this work over the internet?',
         'No. It is designed for computers in one shop on one router.'],
        ['Can a staff PC print tags?',
         'Yes, on a printer attached to that computer.'],
    ], [128, CONTENT_W - 128])]

    # --------------------------------------------------------------- part 4
    s += [PageBreak()]
    s += heading(4, 'When something goes wrong', AMBER)
    s += [table([
        ['Symptom', 'Most likely cause', 'Fix'],
        ['Every staff PC stopped at once',
         'The main PC is off, asleep, or AurumOS was closed on it.',
         'Wake the main PC and open AurumOS. Set its power settings so it never '
         'sleeps during shop hours.'],
        ['One staff PC stopped, the rest are fine',
         'That computer lost the network.',
         'Check its cable or Wi-Fi. It reconnects on its own once the network '
         'returns.'],
        ['Worked yesterday, not today',
         'The main PC was given a different address by the router.',
         'Run ipconfig on the main PC and set the new address on each staff PC. '
         'Reserve a fixed address to stop it recurring.'],
        ['Staff PC connects but the password fails',
         'The account does not exist, or the password is wrong.',
         'Check under Staff on the main PC. Passwords live there, not on the '
         'staff computer.'],
        ['Cannot connect after five wrong tries',
         'Guessing protection has paused that computer.',
         'Wait five minutes and try again with the correct password.'],
        ['A new PC will not join',
         'All the seats your plan allows are in use.',
         'Remove an unused computer on the main PC, or add a connection to your '
         'plan.'],
    ], [104, 116, CONTENT_W - 220]), Spacer(1, 14)]

    s += [Callout('info', 'Before calling for help',
                  'Note which computers are affected, whether the main PC is on, '
                  'and the exact wording of the message on screen. Those three '
                  'things identify almost every case in the table above.')]

    # --------------------------------------------------------------- part 5
    s += [PageBreak()]
    s += heading(5, 'What is finished, and what is not', RED)
    s += [Paragraph(
        'This feature is <b>not complete</b>. The joining and sign-in flow in '
        'Parts 1 to 3 is built and tested. Three pieces are still outstanding, '
        'and one of them affects security. Read this before putting the guide '
        'in front of a customer.', LEAD), Spacer(1, 16)]

    s += [Paragraph('Built and tested', H2), Spacer(1, 8)]
    s += [table([
        ['Piece', 'State'],
        ['Checking the main PC address from setup', 'Done'],
        ['Joining a staff PC to the shop', 'Done'],
        ['Sign-in answered by the main PC', 'Done'],
        ['Seat limits for Pro and Enterprise', 'Done'],
        ['Blocking a computer from the main PC', 'Done, no screen for it yet'],
        ['Sidebar limited to assigned screens', 'Done'],
    ], [CONTENT_W - 150, 150]), Spacer(1, 16)]

    s += [Paragraph('Still to do', H2), Spacer(1, 8)]
    s += [table([
        ['Piece', 'Why it matters'],
        ['Enforce permissions inside the application',
         'The sidebar hides what a staff member may not use, but the underlying '
         'commands can still be reached by someone determined. Until this lands, '
         'treat the staff limits as tidiness, not security.'],
        ['A screen for assigning staff to a computer',
         'Assigning works underneath, but the owner has no page to do it from, '
         'so every staff PC currently gets the plan default.'],
        ['Remove the old networking code',
         'About 1,400 lines of the previous attempt are still in place and '
         'inert. Until they go, the new server runs on a different port to '
         'avoid a clash.'],
        ['Test across two real computers',
         'Everything so far was proved on one machine talking to itself. Real '
         'network conditions and Windows Firewall have not been exercised.'],
        ['Build and distribute a new installer',
         'The changes include a file that cannot be sent out as an update, so '
         'every computer needs a fresh install once.'],
    ], [150, CONTENT_W - 150]), Spacer(1, 16)]

    s += [Callout('critical', 'Not ready for a customer site',
                  'Because permissions are not yet enforced inside the '
                  'application, and nothing has run across two real computers, '
                  'this should stay on test machines until those two items are '
                  'finished.')]

    doc.build(s)
    return OUT


def _code(text):
    """A command, boxed so it can be copied without ambiguity."""
    t = Table([[Paragraph(f'<font face="Courier-Bold" size="8.4" '
                          f'color="#e2e8f0">{text}</font>',
                          style('code', leading=13))]],
              colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), INK),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 11),
        ('RIGHTPADDING', (0, 0), (-1, -1), 11),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    return t


if __name__ == '__main__':
    path = build()
    print(f'wrote {path}')
    sys.exit(0)
