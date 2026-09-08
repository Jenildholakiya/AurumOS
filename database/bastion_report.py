# -*- coding: utf-8 -*-
"""
AurumOS BASTION AI — Forensic Investigation Report Generator
"""

import os
import sys
import threading
import traceback
from datetime import datetime


# ── COLOUR PALETTE ───────────────────────────────────────────────
C_GOLD      = (0.788, 0.635, 0.153)
C_GOLD_DARK = (0.659, 0.498, 0.039)
C_GOLD_LITE = (0.980, 0.965, 0.910)
C_CREAM     = (0.980, 0.973, 0.953)
C_CREAM2    = (0.949, 0.937, 0.906)
C_CREAM3    = (0.910, 0.894, 0.859)
C_INK       = (0.055, 0.047, 0.035)
C_INK2      = (0.165, 0.149, 0.122)
C_INK3      = (0.361, 0.353, 0.322)
C_MUTED     = (0.478, 0.447, 0.408)
C_MUTED2    = (0.620, 0.596, 0.565)
C_RED       = (0.725, 0.110, 0.110)
C_RED_BG    = (0.996, 0.941, 0.941)
C_RED_DARK  = (0.498, 0.063, 0.063)
C_GREEN     = (0.082, 0.502, 0.239)
C_GREEN_BG  = (0.941, 0.988, 0.957)
C_AMBER     = (0.706, 0.325, 0.035)
C_AMBER_BG  = (0.996, 0.969, 0.910)
C_BLUE      = (0.114, 0.306, 0.847)
C_BLUE_BG   = (0.937, 0.949, 0.996)
C_WHITE     = (1.0,   1.0,   1.0  )
C_RULE      = (0.878, 0.859, 0.820)
C_RULE2     = (0.910, 0.898, 0.875)

# ── EMBEDDED ACCESS PASSWORD ──────────────────────────────────────
# NOTE: real PDF encryption needs the plaintext password. A SHA256 hash
# cannot be used to encrypt/open the document. Prefer reading from an
# environment variable so the password isn't hard-coded in source.
_PDF_USER_PASSWORD  = os.environ.get('BASTION_PDF_PW',       'Jenil@8305')        # required to OPEN
_PDF_OWNER_PASSWORD = os.environ.get('BASTION_PDF_OWNER_PW', 'Jenil@8305_admin')  # controls permissions


def _safe(val, default='—'):
    try:
        s = str(val or '').strip()
        return s if s else default
    except Exception:
        return default


def _collect_data(db, attack_type, detail, timestamp, lock_code, suspend_record):
    data = {
        'attack_type':    attack_type,
        'detail':         detail,
        'timestamp':      timestamp,
        'lock_code':      lock_code,
        'suspend_record': suspend_record or {},
        'incident_id':    f"BST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }

    try:
        with db._get_connection() as conn:
            cfg = {r['key']: r['value'] for r in conn.execute(
                "SELECT key, value FROM app_config"
            ).fetchall()}
        data['biz_name']   = cfg.get('business_name', '—')
        data['owner_name'] = cfg.get('owner_name', '—')
        data['shop_id']    = cfg.get('shop_id', '—')
        data['fin_year']   = cfg.get('financial_year', '—')
        data['setup_date'] = cfg.get('setup_date', '—')
        data['fp_stored']  = cfg.get('machine_fingerprint', '—')
    except Exception:
        data.update({'biz_name':'—','owner_name':'—','shop_id':'—',
                     'fin_year':'—','setup_date':'—','fp_stored':'—'})

    try:
        data['device_id'] = db.get_or_create_device_id()
    except Exception:
        data['device_id'] = '—'

    try:
        data['fp_live'] = db._machine_fingerprint()
    except Exception:
        data['fp_live'] = '—'

    try:
        import socket
        data['hostname'] = socket.gethostname()
    except Exception:
        data['hostname'] = os.environ.get('COMPUTERNAME', '—')

    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT ts, event_type, severity, score, detail, action_taken "
                "FROM bastion_events ORDER BY id DESC LIMIT 30"
            ).fetchall()
        data['events'] = [dict(r) for r in rows]
    except Exception:
        data['events'] = []

    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT ts, username, action, detail, category "
                "FROM audit_log ORDER BY id DESC LIMIT 10"
            ).fetchall()
        data['audit'] = [dict(r) for r in rows]
    except Exception:
        data['audit'] = []

    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT username, role, login_time FROM login_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        data['last_login'] = dict(row) if row else {}
    except Exception:
        data['last_login'] = {}

    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT vch_id, customer, status, date, total_amount "
                "FROM sales_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
        data['last_bill'] = dict(row) if row else {}
    except Exception:
        data['last_bill'] = {}

    try:
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT it_code, it_name, tag_id, gr_wt, touch, entry_date "
                "FROM stock_inventory ORDER BY id DESC LIMIT 1"
            ).fetchone()
        data['last_stock'] = dict(row) if row else {}
    except Exception:
        data['last_stock'] = {}

    counts = {}
    for tbl in ['sales_history', 'stock_inventory', 'katti_vouchers',
                'clients_master', 'admin_creds', 'credit_ledger']:
        try:
            with db._get_connection() as conn:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            counts[tbl] = n
        except Exception:
            counts[tbl] = '?'
    data['counts'] = counts

    data['ram_token']  = '✓ Present' if getattr(db, '_session_token', None) else '✗ Missing'
    data['token_file'] = getattr(db, '_session_token_file', None) or '—'
    try:
        import winreg as _wr
        key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\InputMethod\AOS')
        _wr.QueryValueEx(key, 'SessionCache')
        _wr.CloseKey(key)
        data['reg_token'] = '✓ Present'
    except Exception:
        data['reg_token'] = '✗ Missing'

    return data


def _plain_english_summary(attack_type, detail):
    summaries = {
        'db_edit': (
            "AurumOS detected that its database file was modified by an external program "
            "while the app was running. Something outside of AurumOS directly changed "
            "transaction records, stock entries, or configuration data without going through "
            "the app's secure write path. This is a serious integrity violation that could "
            "indicate data manipulation, fraud, or a software attack."
        ),
        'session_tamper': (
            "A session security token was found missing or modified during an active session. "
            "These tokens are stored in three places and all must match for any transaction "
            "to proceed. A mismatch indicates a possible session hijack or replay attempt."
        ),
        'exe_tamper': (
            "The AurumOS executable was found to have a different cryptographic hash than "
            "the trusted version recorded at build time. This means the EXE was modified "
            "after installation — possibly by malware or deliberate tampering."
        ),
        'fingerprint_mismatch': (
            "The hardware fingerprint stored in the database does not match this machine. "
            "The database was likely copied from another PC, which is strictly not permitted "
            "under AurumOS security policy."
        ),
        'replay_attack': (
            "An old or duplicate session authentication token was detected. AurumOS's "
            "rolling session token system identified and blocked this replay attempt immediately."
        ),
    }
    return summaries.get(attack_type,
        f"BASTION AI detected a security anomaly classified as '{attack_type}'. "
        f"Technical detail: {detail}"
    )


def generate_bastion_report(db, attack_type, detail, timestamp,
                             lock_code, suspend_record) -> str:

    # ── STEP 1: Import reportlab ──────────────────────────────────
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, PageBreak, KeepTogether
        )
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    except ImportError:
        print("[BASTION_REPORT] reportlab not installed — pip install reportlab")
        return ''

    # Encryption support (optional — fall back gracefully if unavailable)
    try:
        from reportlab.lib.pdfencrypt import StandardEncryption
        _ENC_AVAILABLE = True
    except Exception:
        _ENC_AVAILABLE = False

    # ── STEP 2: Resolve output path ───────────────────────────────
    if getattr(sys, 'frozen', False):
        _root = os.path.dirname(sys.executable)
    else:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    report_dir = os.path.join(_root, 'Reports')
    try:
        os.makedirs(report_dir, exist_ok=True)
    except Exception as e:
        print(f"[BASTION_REPORT] makedirs failed: {e}, using cwd")
        report_dir = os.getcwd()

    safe_ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
    pdf_path = os.path.join(report_dir, f'BASTION_REPORT_{safe_ts}.pdf')

    print(f"[BASTION_REPORT] Root     : {_root}")
    print(f"[BASTION_REPORT] Save dir : {report_dir}")
    print(f"[BASTION_REPORT] PDF path : {pdf_path}")

    # ── STEP 3: Collect data FIRST ────────────────────────────────
    try:
        data = _collect_data(db, attack_type, detail, timestamp, lock_code, suspend_record)
    except Exception as e:
        print(f"[BASTION_REPORT] data collection error: {e}")
        data = {
            'attack_type':    attack_type,
            'detail':         detail,
            'timestamp':      timestamp,
            'lock_code':      lock_code,
            'incident_id':    f"BST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'biz_name':       '—', 'owner_name': '—', 'shop_id':    '—',
            'device_id':      '—', 'hostname':   '—', 'fp_live':    '—',
            'fp_stored':      '—', 'fin_year':   '—', 'setup_date': '—',
            'events': [], 'audit': [], 'last_login': {}, 'last_bill': {},
            'last_stock': {}, 'counts': {}, 'ram_token': '—',
            'reg_token': '—', 'token_file': '—', 'suspend_record': suspend_record or {},
        }

    # ── STEP 4: Setup (data is defined — safe from here) ─────────
    PAGE_W, PAGE_H = A4
    MARGIN  = 18 * mm
    CONTENT = PAGE_W - 2 * MARGIN

    # ReportLab colours
    def RC(*c): return colors.Color(*c)

    RL_GOLD      = RC(*C_GOLD)
    RL_GOLD_DARK = RC(*C_GOLD_DARK)
    RL_GOLD_LITE = RC(*C_GOLD_LITE)
    RL_CREAM     = RC(*C_CREAM)
    RL_CREAM2    = RC(*C_CREAM2)
    RL_CREAM3    = RC(*C_CREAM3)
    RL_INK       = RC(*C_INK)
    RL_INK2      = RC(*C_INK2)
    RL_INK3      = RC(*C_INK3)
    RL_MUTED     = RC(*C_MUTED)
    RL_MUTED2    = RC(*C_MUTED2)
    RL_RED       = RC(*C_RED)
    RL_RED_BG    = RC(*C_RED_BG)
    RL_RED_DARK  = RC(*C_RED_DARK)
    RL_GREEN     = RC(*C_GREEN)
    RL_GREEN_BG  = RC(*C_GREEN_BG)
    RL_AMBER     = RC(*C_AMBER)
    RL_AMBER_BG  = RC(*C_AMBER_BG)
    RL_BLUE      = RC(*C_BLUE)
    RL_BLUE_BG   = RC(*C_BLUE_BG)
    RL_WHITE     = colors.white
    RL_RULE      = RC(*C_RULE)
    RL_RULE2     = RC(*C_RULE2)
    RL_BLACK     = colors.black

    incident_id = data['incident_id']
    gen_time    = datetime.now().strftime('%d %b %Y  %I:%M %p')

    # ── STEP 5: Page background + header/footer callback ─────────
    def _on_page(canv, doc):
        canv.saveState()

        # Full page cream background
        canv.setFillColor(RL_CREAM)
        canv.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        # ── TOP HEADER BAND ───────────────────────────────────────
        # Dark ink band
        canv.setFillColor(RL_INK)
        canv.rect(0, PAGE_H - 20*mm, PAGE_W, 20*mm, fill=1, stroke=0)
        # Gold gradient bar at very top (3 stripes for gradient feel)
        canv.setFillColor(RL_GOLD)
        canv.rect(0, PAGE_H - 2.5*mm, PAGE_W, 2.5*mm, fill=1, stroke=0)
        canv.setFillColor(RC(0.850, 0.700, 0.200))
        canv.rect(0, PAGE_H - 3.5*mm, PAGE_W, 1*mm, fill=1, stroke=0)

        # Left: logo text
        canv.setFont('Helvetica-Bold', 9)
        canv.setFillColor(RL_GOLD)
        canv.drawString(MARGIN, PAGE_H - 11*mm, 'AurumOS')
        canv.setFont('Helvetica', 9)
        canv.setFillColor(RC(0.7, 0.65, 0.55))
        canv.drawString(MARGIN + 38, PAGE_H - 11*mm, 'BASTION AI  ·  Forensic Security Report')

        # Right: incident ID pill
        iw = 72*mm
        ix = PAGE_W - MARGIN - iw
        canv.setFillColor(RC(0.12, 0.10, 0.08))
        canv.roundRect(ix, PAGE_H - 14.5*mm, iw, 7*mm, 2, fill=1, stroke=0)
        canv.setFont('Courier-Bold', 7)
        canv.setFillColor(RL_GOLD)
        canv.drawCentredString(ix + iw/2, PAGE_H - 11*mm, incident_id)

        # ── BOTTOM FOOTER BAND ────────────────────────────────────
        canv.setFillColor(RL_INK)
        canv.rect(0, 0, PAGE_W, 13*mm, fill=1, stroke=0)
        canv.setFillColor(RL_GOLD)
        canv.rect(0, 12.5*mm, PAGE_W, 0.5*mm, fill=1, stroke=0)

        canv.setFont('Helvetica', 6.5)
        canv.setFillColor(RC(0.55, 0.52, 0.46))
        canv.drawString(MARGIN, 4.5*mm,
            f'Generated: {gen_time}  ·  {data["biz_name"]}  ·  CONFIDENTIAL')
        canv.setFont('Helvetica-Bold', 7)
        canv.setFillColor(RL_GOLD)
        canv.drawRightString(PAGE_W - MARGIN, 4.5*mm, f'Page {doc.page}')

        # ── VERY SUBTLE WATERMARK ────────────────────────────────
        canv.setFont('Helvetica-Bold', 68)
        canv.setFillColor(colors.Color(0.93, 0.91, 0.87, alpha=0.18))
        canv.saveState()
        canv.translate(PAGE_W / 2, PAGE_H / 2)
        canv.rotate(42)
        canv.drawCentredString(0, 0, 'CONFIDENTIAL')
        canv.restoreState()

        # ── SIDE ACCENT LINE ─────────────────────────────────────
        canv.setFillColor(RL_GOLD)
        canv.rect(0, 13*mm, 2.5, PAGE_H - 33*mm, fill=1, stroke=0)

        canv.restoreState()

    # ── STEP 6: Paragraph styles ──────────────────────────────────
    def S(name, **kw):
        base = dict(fontName='Helvetica', fontSize=10, textColor=RL_INK,
                    leading=15, spaceAfter=4, spaceBefore=0)
        base.update(kw)
        return ParagraphStyle(name, **base)

    sty_h1       = S('h1',  fontName='Helvetica-Bold', fontSize=26, textColor=RL_INK,      leading=32)
    sty_h2       = S('h2',  fontName='Helvetica-Bold', fontSize=12, textColor=RL_WHITE,     leading=18)
    sty_h3       = S('h3',  fontName='Helvetica-Bold', fontSize=9.5,textColor=RL_INK2,     leading=14, spaceAfter=3)
    sty_body     = S('body',fontSize=9.5, textColor=RL_INK,  leading=16, spaceAfter=4)
    sty_body_sm  = S('bsm', fontSize=8.5, textColor=RL_INK3, leading=13)
    sty_mono     = S('mono',fontName='Courier', fontSize=8.5, textColor=RL_INK, leading=13)
    sty_label    = S('lbl', fontName='Helvetica-Bold', fontSize=7,   textColor=RL_MUTED,
                     leading=10, spaceAfter=1, textTransform='uppercase')
    sty_center   = S('ctr', fontSize=8.5, textColor=RL_MUTED2, leading=13, alignment=TA_CENTER)
    sty_badge_wh = S('bwh', fontName='Helvetica-Bold', fontSize=8.5,
                     textColor=RL_WHITE, leading=13, alignment=TA_CENTER)
    sty_cover_biz= S('cbz', fontName='Helvetica-Bold', fontSize=13, textColor=RL_INK,
                     leading=18, alignment=TA_CENTER)
    sty_gold_sm  = S('gsm', fontName='Helvetica-Bold', fontSize=7.5, textColor=RL_GOLD_DARK,
                     leading=11, alignment=TA_CENTER)

    # ── STEP 7: Helper builders ───────────────────────────────────
    sp = lambda n=6: Spacer(1, n)
    hr = lambda c=None: HRFlowable(width='100%', thickness=0.5,
                                    color=c or RL_RULE, spaceAfter=6, spaceBefore=2)

    def section_header(num, title, color=None):
        bg = color or RL_INK
        tbl = Table(
            [[ Paragraph(f'{num}', S('snum', fontName='Helvetica-Bold', fontSize=9,
                                      textColor=RL_GOLD, leading=13, alignment=TA_CENTER)),
               Paragraph(title, sty_h2) ]],
            colWidths=[9*mm, CONTENT - 9*mm]
        )
        tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (0,-1), RL_GOLD),
            ('BACKGROUND',    (1,0), (1,-1), bg),
            ('TOPPADDING',    (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING',   (0,0), (0,-1),  0),
            ('LEFTPADDING',   (1,0), (1,-1),  10),
            ('RIGHTPADDING',  (0,0), (-1,-1), 8),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN',         (0,0), (0,-1),  'CENTER'),
        ]))
        return tbl

    def kv_table(pairs, col1=58*mm):
        col2 = CONTENT - col1
        rows = []
        for k, v in pairs:
            v_str = _safe(v)
            v_sty = S('kv_v', fontName='Courier' if len(v_str) > 30 else 'Helvetica',
                       fontSize=8.5, textColor=RL_INK, leading=13)
            rows.append([Paragraph(k, sty_label), Paragraph(v_str, v_sty)])
        tbl = Table(rows, colWidths=[col1, col2])
        style = [
            ('LEFTPADDING',   (0,0), (-1,-1), 8),
            ('RIGHTPADDING',  (0,0), (-1,-1), 8),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('VALIGN',        (0,0), (-1,-1), 'TOP'),
            ('LINEBELOW',     (0,0), (-1,-2), 0.4, RL_RULE2),
            ('BACKGROUND',    (0,0), (0,-1),  RL_CREAM2),
        ]
        for i in range(0, len(rows), 2):
            style.append(('BACKGROUND', (1,i), (1,i), RL_WHITE))
        for i in range(1, len(rows), 2):
            style.append(('BACKGROUND', (1,i), (1,i), RL_CREAM))
        tbl.setStyle(TableStyle(style))
        return tbl

    def stat_cards(items):
        # items = list of (label, value, color)
        cells  = []
        styles = []
        for label, val, col in items:
            cells.append(
                Table(
                    [[Paragraph(val,   S('sv', fontName='Helvetica-Bold', fontSize=16,
                                          textColor=col, leading=20, alignment=TA_CENTER))],
                     [Paragraph(label, S('sl', fontSize=7, textColor=RL_MUTED,
                                          leading=10, alignment=TA_CENTER))]],
                    colWidths=[CONTENT / len(items) - 4*mm]
                )
            )
        card_w = CONTENT / len(items)
        tbl = Table([cells], colWidths=[card_w]*len(items))
        ts  = [
            ('TOPPADDING',    (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING',   (0,0), (-1,-1), 4),
            ('RIGHTPADDING',  (0,0), (-1,-1), 4),
            ('ALIGN',         (0,0), (-1,-1), 'CENTER'),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ]
        for i, (_, _, col) in enumerate(items):
            ts.append(('BACKGROUND', (i,0), (i,-1), RL_WHITE))
            ts.append(('LINEABOVE',  (i,0), (i,0),  2, col))
        tbl.setStyle(TableStyle(ts))
        return tbl

    def alert_box(text, bg, border, text_color, icon='⚠'):
        content = f'{icon}  {text}'
        tbl = Table(
            [[Paragraph(content, S('ab', fontSize=9.5, textColor=text_color,
                                    leading=16, fontName='Helvetica'))
            ]],
            colWidths=[CONTENT]
        )
        tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,-1), bg),
            ('LINEBEFORE',    (0,0), (0,-1),  5, border),
            ('LINEABOVE',     (0,0), (-1,0),  0.5, border),
            ('LINEBELOW',     (0,-1),(-1,-1), 0.5, border),
            ('LINEAFTER',     (0,0), (-1,-1), 0.5, border),
            ('LEFTPADDING',   (0,0), (-1,-1), 14),
            ('RIGHTPADDING',  (0,0), (-1,-1), 12),
            ('TOPPADDING',    (0,0), (-1,-1), 11),
            ('BOTTOMPADDING', (0,0), (-1,-1), 11),
        ]))
        return tbl

    def two_col(left_items, right_items):
        """Two side-by-side kv blocks."""
        lw = (CONTENT - 6*mm) / 2
        lt = kv_table(left_items,  col1=28*mm)
        rt = kv_table(right_items, col1=28*mm)
        tbl = Table([[lt, Spacer(6*mm, 1), rt]], colWidths=[lw, 6*mm, lw])
        tbl.setStyle(TableStyle([
            ('VALIGN',       (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING',  (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING',   (0,0), (-1,-1), 0),
            ('BOTTOMPADDING',(0,0), (-1,-1), 0),
        ]))
        return tbl

    # ── STEP 8: Build story ───────────────────────────────────────
    story = []

    # ════════════════════════════════════════════════════════════
    # COVER PAGE
    # ════════════════════════════════════════════════════════════
    story.append(Spacer(1, 18*mm))

    # Centered gold logo mark
    logo_tbl = Table(
        [[Paragraph('⬡', S('lico', fontName='Helvetica-Bold', fontSize=42,
                              textColor=RL_GOLD, leading=50, alignment=TA_CENTER))]],
        colWidths=[CONTENT]
    )
    logo_tbl.setStyle(TableStyle([
        ('ALIGN',  (0,0),(-1,-1),'CENTER'),
        ('VALIGN', (0,0),(-1,-1),'MIDDLE'),
    ]))
    story.append(logo_tbl)
    story.append(sp(6))

    # BASTION AI title
    story.append(Paragraph('BASTION AI', S('bt', fontName='Helvetica-Bold',
                                             fontSize=42, textColor=RL_INK,
                                             leading=48, alignment=TA_CENTER,
                                             spaceAfter=0)))
    story.append(Paragraph('FORENSIC INVESTIGATION REPORT',
                            S('bts', fontName='Helvetica', fontSize=11,
                              textColor=RL_MUTED, leading=16,
                              alignment=TA_CENTER, spaceAfter=0,
                              letterSpacing=3)))
    story.append(sp(8))

    # Gold rule
    story.append(HRFlowable(width='60%', thickness=1.5, color=RL_GOLD,
                             spaceAfter=10, spaceBefore=4, hAlign='CENTER'))

    # Severity badge
    sev_col = RL_RED if attack_type in ('exe_tamper','replay_attack','db_edit') else RL_AMBER
    badge = Table(
        [[Paragraph('● &nbsp; CRITICAL SECURITY INCIDENT', sty_badge_wh)]],
        colWidths=[90*mm]
    )
    badge.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), sev_col),
        ('LEFTPADDING',   (0,0),(-1,-1), 16),
        ('RIGHTPADDING',  (0,0),(-1,-1), 16),
        ('TOPPADDING',    (0,0),(-1,-1), 8),
        ('BOTTOMPADDING', (0,0),(-1,-1), 8),
        ('ALIGN',         (0,0),(-1,-1), 'CENTER'),
        ('ROUNDEDCORNERS',(0,0),(-1,-1), [4,4,4,4]),
    ]))
    w_badge = Table([[badge]], colWidths=[CONTENT])
    w_badge.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER')]))
    story.append(w_badge)
    story.append(sp(14))

    # Business name hero
    story.append(Paragraph(data['biz_name'], sty_cover_biz))
    story.append(sp(3))
    story.append(Paragraph(
        f'Attack Type: {attack_type.replace("_"," ").upper()}',
        S('cat', fontName='Helvetica-Bold', fontSize=9, textColor=sev_col,
          leading=13, alignment=TA_CENTER, letterSpacing=1)
    ))
    story.append(sp(16))

    # Cover info grid — 2 columns
    cover_l = [
        ('Incident ID',  data['incident_id']),
        ('Business',     data['biz_name']),
        ('Owner',        data['owner_name']),
        ('Financial Yr', data['fin_year']),
    ]
    cover_r = [
        ('PC / Host',    data['hostname']),
        ('Date & Time',  _safe(timestamp)),
        ('Lock Code',    lock_code),
        ('Shop ID',      data['shop_id']),
    ]
    story.append(two_col(cover_l, cover_r))
    story.append(sp(20))

    # Access notice — this PDF is password protected
    pw_tbl = Table(
        [[Paragraph(
            'This document is password protected. Contact the administrator for access.',
            S('ph', fontSize=6.5, textColor=RC(0.88, 0.87, 0.85),
              leading=10, alignment=TA_CENTER)
        )]],
        colWidths=[CONTENT]
    )
    pw_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), RC(0.96, 0.95, 0.93)),
        ('LINEABOVE',     (0,0),(-1,0),  0.3, RL_RULE2),
        ('LINEBELOW',     (0,-1),(-1,-1),0.3, RL_RULE2),
        ('TOPPADDING',    (0,0),(-1,-1), 4),
        ('BOTTOMPADDING', (0,0),(-1,-1), 4),
    ]))
    story.append(pw_tbl)
    story.append(sp(10))

    story.append(Paragraph(
        'This document is automatically generated by AurumOS BASTION AI security system. '
        'It is strictly confidential and intended solely for the authorised administrator. '
        'Unauthorised disclosure is prohibited.',
        S('disc', fontSize=8, textColor=RL_MUTED2, leading=13, alignment=TA_CENTER)
    ))
    story.append(PageBreak())

    # ════════════════════════════════════════════════════════════
    # SECTION 1 — INCIDENT SUMMARY
    # ════════════════════════════════════════════════════════════
    story.append(section_header('1', 'Incident Summary', RL_RED_DARK))
    story.append(sp(10))

    # Stat cards
    ev_total  = len(data['events'])
    ev_high   = sum(1 for e in data['events'] if e.get('severity') in ('HIGH','CRITICAL'))
    ev_healed = sum(1 for e in data['events'] if e.get('auto_healed'))
    story.append(stat_cards([
        ('Total Events (30)',    str(ev_total),  RL_BLUE),
        ('High / Critical',     str(ev_high),   RL_RED),
        ('Auto-Healed',         str(ev_healed), RL_GREEN),
        ('Threat Score',        '100/100',      RL_RED),
    ]))
    story.append(sp(12))

    story.append(alert_box(
        _plain_english_summary(attack_type, detail),
        RL_RED_BG, RL_RED, RL_RED_DARK, icon='🔴'
    ))
    story.append(sp(10))

    story.append(Paragraph('What Was Detected', sty_h3))
    story.append(sp(4))
    story.append(Paragraph(_safe(detail), sty_body))
    story.append(sp(10))

    story.append(Paragraph('Immediate Action Taken', sty_h3))
    story.append(sp(4))
    story.append(alert_box(
        'BASTION AI automatically suspended ALL database writes and locked the AurumOS '
        'account. The BASTION lock screen is now displayed on every page. No transactions '
        'can be recorded until an administrator provides the correct unlock key.',
        RL_AMBER_BG, RL_AMBER, RL_AMBER, icon='🔒'
    ))
    story.append(sp(14))

    # ════════════════════════════════════════════════════════════
    # SECTION 2 — TECHNICAL EVIDENCE
    # ════════════════════════════════════════════════════════════
    story.append(section_header('2', 'Technical Evidence'))
    story.append(sp(10))

    story.append(Paragraph('Hardware & Identity', sty_h3))
    story.append(sp(4))
    fp_match = '✓ MATCH' if (data['fp_live'] == data['fp_stored'] and data['fp_live'] != '—') else '✗ MISMATCH'
    fp_col   = RL_GREEN if fp_match.startswith('✓') else RL_RED
    story.append(two_col(
        [('Device ID',   data['device_id']),
         ('Shop ID',     data['shop_id']),
         ('PC Hostname', data['hostname']),
         ('Lock Code',   lock_code)],
        [('FP (live)',   data['fp_live']),
         ('FP (stored)', data['fp_stored']),
         ('FP Match',    fp_match),
         ('Setup Date',  data['setup_date'])]
    ))
    story.append(sp(10))

    story.append(Paragraph('Session Token State', sty_h3))
    story.append(sp(4))
    story.append(kv_table([
        ('RAM Token',       data['ram_token']),
        ('Registry Token',  data['reg_token']),
        ('Token File Path', data['token_file']),
    ]))
    story.append(sp(10))

    story.append(Paragraph('Database Record Counts at Time of Suspension', sty_h3))
    story.append(sp(4))
    cnt_pairs = [(t.replace('_',' ').title(), str(n)) for t,n in data['counts'].items()]
    mid = len(cnt_pairs)//2
    story.append(two_col(cnt_pairs[:mid], cnt_pairs[mid:]))
    story.append(sp(14))

    # ════════════════════════════════════════════════════════════
    # SECTION 3 — EVENT TIMELINE
    # ════════════════════════════════════════════════════════════
    story.append(section_header('3', 'BASTION Event Timeline  (last 30)'))
    story.append(sp(10))

    if data['events']:
        hdr_sty = S('eth', fontName='Helvetica-Bold', fontSize=7,
                     textColor=RL_GOLD, leading=11)
        ev_header = [
            Paragraph('Timestamp',  hdr_sty),
            Paragraph('Event',      hdr_sty),
            Paragraph('Severity',   hdr_sty),
            Paragraph('Score',      hdr_sty),
            Paragraph('Action',     hdr_sty),
            Paragraph('Detail',     hdr_sty),
        ]
        ev_rows = [ev_header]
        for ev in data['events']:
            sev     = _safe(ev.get('severity','?'))
            sev_col = RL_RED   if sev in ('CRITICAL',)           else \
                      RL_AMBER if sev in ('HIGH','MEDIUM')        else RL_GREEN
            ev_rows.append([
                Paragraph(_safe(ev.get('ts',''))[:16],
                          S('ec1', fontName='Courier', fontSize=7, textColor=RL_INK3, leading=10)),
                Paragraph(_safe(ev.get('event_type','')).replace('_',' '),
                          S('ec2', fontSize=7.5, textColor=RL_INK, leading=11)),
                Paragraph(sev,
                          S('ec3', fontName='Helvetica-Bold', fontSize=7, textColor=sev_col, leading=10)),
                Paragraph(str(ev.get('score',0)),
                          S('ec4', fontName='Courier', fontSize=7, textColor=RL_INK3,
                            leading=10, alignment=TA_CENTER)),
                Paragraph(_safe(ev.get('action_taken','')),
                          S('ec5', fontSize=7, textColor=RL_INK3, leading=10)),
                Paragraph(_safe(ev.get('detail',''))[:70],
                          S('ec6', fontSize=6.5, textColor=RL_INK3, leading=10)),
            ])

        cw = CONTENT
        ev_tbl = Table(ev_rows, colWidths=[30*mm, 36*mm, 18*mm, 12*mm, 20*mm, cw-116*mm])
        ev_tbl.setStyle(TableStyle([
            ('BACKGROUND',     (0,0),  (-1,0),  RL_INK),
            ('TOPPADDING',     (0,0),  (-1,-1), 4),
            ('BOTTOMPADDING',  (0,0),  (-1,-1), 4),
            ('LEFTPADDING',    (0,0),  (-1,-1), 5),
            ('RIGHTPADDING',   (0,0),  (-1,-1), 5),
            ('VALIGN',         (0,0),  (-1,-1), 'TOP'),
            ('LINEBELOW',      (0,0),  (-1,-1), 0.3, RL_RULE2),
            ('ROWBACKGROUNDS', (0,1),  (-1,-1), [RL_WHITE, RL_CREAM]),
        ]))
        story.append(ev_tbl)
    else:
        story.append(alert_box('No events recorded in bastion_events table.',
                                RL_CREAM2, RL_RULE, RL_MUTED, icon='ℹ'))

    story.append(sp(14))

    # ════════════════════════════════════════════════════════════
    # SECTION 4 — SYSTEM STATE
    # ════════════════════════════════════════════════════════════
    story.append(section_header('4', 'System State at Time of Suspension'))
    story.append(sp(10))

    ll = data['last_login']
    lb = data['last_bill']
    ls = data['last_stock']

    story.append(Paragraph('Last Successful Login', sty_h3))
    story.append(sp(4))
    story.append(kv_table([
        ('Username',   ll.get('username','—')),
        ('Role',       ll.get('role','—')),
        ('Login Time', ll.get('login_time','—')),
    ]))
    story.append(sp(10))

    story.append(Paragraph('Last Recorded Sales Bill', sty_h3))
    story.append(sp(4))
    story.append(two_col(
        [('Voucher ID', lb.get('vch_id','—')),
         ('Customer',   lb.get('customer','—')),
         ('Status',     lb.get('status','—'))],
        [('Date',       lb.get('date','—')),
         ('Amount',     f"Rs. {lb.get('total_amount','—')}"),
         ('', '')]
    ))
    story.append(sp(10))

    story.append(Paragraph('Last Stock Entry', sty_h3))
    story.append(sp(4))
    story.append(two_col(
        [('IT Code',    ls.get('it_code','—')),
         ('Item Name',  ls.get('it_name','—')),
         ('Tag ID',     ls.get('tag_id','—'))],
        [('Weight',     f"{ls.get('gr_wt','—')} g"),
         ('Touch',      f"{ls.get('touch','—')} %"),
         ('Entry Date', ls.get('entry_date','—'))]
    ))
    story.append(sp(14))

    # ════════════════════════════════════════════════════════════
    # SECTION 5 — UNLOCK INSTRUCTIONS
    # ════════════════════════════════════════════════════════════
    story.append(section_header('5', 'Unlock Instructions', RC(0.08, 0.24, 0.08)))
    story.append(sp(10))

    story.append(alert_box(
        'Do NOT share this Lock Code or this report publicly. '
        'The Lock Code is derived from this machine\'s hardware and is unique to this PC.',
        RL_AMBER_BG, RL_AMBER, RL_AMBER, icon='⚠'
    ))
    story.append(sp(10))

    story.append(kv_table([
        ('Lock Code',   lock_code),
        ('Incident ID', data['incident_id']),
        ('PC Hostname', data['hostname']),
        ('Generated',   gen_time),
    ]))
    story.append(sp(10))

    for i, (step, desc) in enumerate([
        ('Step 1', 'Contact AurumOS support. Provide the Lock Code and Incident ID above.'),
        ('Step 2', 'Support will run unlock_keygen.py (BASTION mode 2) to generate a '
                   '16-character unlock key valid for today only.'),
        ('Step 3', 'On the BASTION lock screen inside AurumOS, click "Enter Admin Unlock Key" '
                   'and type the 16-character key exactly as provided.'),
        ('Step 4', 'Before unlocking, review this report. If data tampering is confirmed, '
                   'restore from backup at C:\\ProgramData\\AurumOS\\aurum_backup.db first.'),
        ('Step 5', 'After unlocking, immediately change the AurumOS owner password from Settings.'),
    ], 1):
        row = Table(
            [[Paragraph(f'{i}', S('sn', fontName='Helvetica-Bold', fontSize=11,
                                   textColor=RL_GOLD, leading=16, alignment=TA_CENTER)),
              Paragraph(f'<b>{step}</b> — {desc}', sty_body)]],
            colWidths=[10*mm, CONTENT - 10*mm]
        )
        row.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (0,-1), RL_INK),
            ('BACKGROUND',    (1,0), (1,-1), RL_WHITE),
            ('LINEBELOW',     (0,0), (-1,-1), 0.4, RL_RULE2),
            ('LINEBEFORE',    (1,0), (1,-1),  2,   RL_GOLD),
            ('TOPPADDING',    (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING',   (0,0), (0,-1),  0),
            ('LEFTPADDING',   (1,0), (1,-1),  10),
            ('RIGHTPADDING',  (0,0), (-1,-1), 8),
            ('ALIGN',         (0,0), (0,-1),  'CENTER'),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(row)
        story.append(sp(3))

    story.append(sp(16))
    story.append(hr(RL_GOLD))
    story.append(sp(4))
    story.append(Paragraph(
        'AurumOS BASTION AI  ·  Automated Forensic Security System  ·  '
        f'Report ID: {incident_id}',
        sty_center
    ))

    # ── STEP 9: Build PDF (with password encryption) ──────────────
    enc = None
    if _ENC_AVAILABLE:
        try:
            enc = StandardEncryption(
                userPassword=_PDF_USER_PASSWORD,    # prompts on OPEN
                ownerPassword=_PDF_OWNER_PASSWORD,  # full-permission password
                canPrint=1,
                canModify=0,
                canCopy=0,
                canAnnotate=0,
                strength=128,                       # 128-bit
            )
        except Exception as e:
            print(f"[BASTION_REPORT] encryption setup failed, building unencrypted: {e}")
            enc = None
    else:
        print("[BASTION_REPORT] StandardEncryption unavailable — building unencrypted PDF")

    try:
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            leftMargin=MARGIN, rightMargin=MARGIN,
            topMargin=24*mm, bottomMargin=17*mm,
            title=f'BASTION Forensic Report — {incident_id}',
            author='AurumOS BASTION AI',
            subject='Security Incident Report',
            creator='AurumOS v2',
            encrypt=enc,                            # ← locks the PDF with a password
        )
        doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
        if enc is not None:
            print(f"[BASTION_REPORT] ✓ PDF saved (encrypted): {pdf_path}")
        else:
            print(f"[BASTION_REPORT] ✓ PDF saved (NOT encrypted): {pdf_path}")
    except Exception as e:
        print(f"[BASTION_REPORT] ✗ PDF build failed: {e}")
        traceback.print_exc()
        return ''

    # Store path in app_config
    try:
        with db._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_config(key,value) "
                "VALUES('last_bastion_report_path',?)", (pdf_path,)
            )
            conn.commit()
    except Exception:
        pass

    return pdf_path


def generate_bastion_report_async(db, attack_type, detail, timestamp,
                                   lock_code, suspend_record):
    threading.Thread(
        target=generate_bastion_report,
        args=(db, attack_type, detail, timestamp, lock_code, suspend_record),
        daemon=True,
        name='BASTION-ReportGen'
    ).start()
