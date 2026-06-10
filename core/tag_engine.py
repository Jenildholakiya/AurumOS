"""
AurumOS Tag Engine — core/tag_engine.py

Four variations:
  RING     → W / માપ / T
  PARA     → Left: W/P/N + QR only | Right: tagID + T + QR
  KATTI    → W / T(touch+wastage=total) / F(fine)
  STANDARD → W / T  (with extra row gap)
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    import win32print
    import win32ui
    from PIL import ImageWin
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# ── CONFIG ────────────────────────────────────────────────────────────────────
FONT_RING     = 24
FONT_PARA     = 24
FONT_KATTI    = 24
FONT_GJ       = 22
FONT_TAGID    = 14

QR_SIZE          = 60
CANVAS_W         = 450
LABEL_H          = 96
OUTER_PAD        = 12
QR_X             = 150
ROW_GAP          = 6
ROW_GAP_PARA     = 10   # vertical gap between W/P/N rows
ROW_GAP_STANDARD = 20
ID_GAP           = 12
TOP_OFFSET       = 0
ID_X_OFFSET      = 10
WING_W           = CANVAS_W // 2
PRINTER_NAME     = "TSC_Jewelry"


# ── FONT LOADER ───────────────────────────────────────────────────────────────
def _get_font_dir() -> Path:
    here = Path(__file__).parent
    root = here.parent
    for candidate in [root / "fonts", here / "fonts", root / "ui" / "fonts"]:
        if candidate.is_dir():
            return candidate
    return root


def _load_fonts():
    fd = _get_font_dir()

    def ttf(name, size):
        p = fd / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
        alt = Path(os.path.abspath(".")) / "fonts" / name
        if alt.exists():
            return ImageFont.truetype(str(alt), size)
        return ImageFont.load_default()

    return {
        'ring':  ttf("JetBrainsMono-ExtraBold.ttf", FONT_RING),
        'para':  ttf("JetBrainsMono-ExtraBold.ttf", FONT_PARA),
        'katti': ttf("JetBrainsMono-ExtraBold.ttf", FONT_KATTI),
        'gj':    ttf("NotoSansGujarati-Bold.ttf",    FONT_GJ),
        'tagid': ttf("JetBrainsMono-ExtraBold.ttf",  FONT_TAGID),
    }


# ── QR CODE ───────────────────────────────────────────────────────────────────
def _make_qr(data: str) -> Image.Image:
    if not HAS_QR:
        return Image.new('RGB', (QR_SIZE, QR_SIZE), 'white')
    qr = qrcode.QRCode(version=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=1, border=0)
    qr.add_data(data)
    qr.make(fit=True)
    return (qr.make_image(fill_color="black", back_color="white")
               .convert('RGB')
               .resize((QR_SIZE, QR_SIZE), Image.Resampling.NEAREST))


# ── LAYOUT ENGINE ─────────────────────────────────────────────────────────────
def _row_metrics(font, text):
    b = font.getbbox(text)
    return b[3] - b[1], b[1]


def _compute_layout(fonts: dict, item: dict) -> dict:
    variation = str(item.get('variation', 'RING')).upper().strip()
    gross_wt  = str(item.get('gross_wt', item.get('gr_wt', '0.000')))
    touch     = str(item.get('touch', '91.6'))
    tag_id    = str(item.get('tag_id', ''))
    short     = tag_id[-10:] if len(tag_id) > 10 else tag_id

    if variation == 'RING':
        mf       = fonts['ring']
        gap      = ROW_GAP
        size_val = str(item.get('size', item.get('ring_size', '')))
        h0, t0   = _row_metrics(mf,          f"W {gross_wt}")
        h1, t1   = _row_metrics(fonts['gj'], f"માપ {size_val}")
        h2, t2   = _row_metrics(mf,          f"T {touch}")
        rows         = (h0, t0, h1, t1, h2, t2, 0, 0)
        total_text_h = h0 + gap + h1 + gap + h2
        extra        = dict(size_val=size_val)

    elif variation == 'PARA':
        mf      = fonts['para']
        gap     = ROW_GAP_PARA
        para_wt = str(item.get('para_wt', item.get('para_stone_wt', '0.000')))
        net_wt  = str(item.get('net_wt',  item.get('nt_wt', '0.000')))
        h0, t0  = _row_metrics(mf, f"W: {gross_wt}")
        h1, t1  = _row_metrics(mf, f"P: {para_wt}")
        h2, t2  = _row_metrics(mf, f"N: {net_wt}")
        h3, t3  = _row_metrics(mf, f"T {touch}")
        rows         = (h0, t0, h1, t1, h2, t2, h3, t3)
        total_text_h = h0 + gap + h1 + gap + h2
        extra        = dict(para_wt=para_wt, net_wt=net_wt)

    elif variation == 'KATTI':
        mf  = fonts['katti']
        gap = ROW_GAP
        _MISSING = object()
        def _get_wastage(d):
            for key in ('wastage', 'wastage_pct', 'wst', 'wstg', 'waste'):
                v = d.get(key, _MISSING)
                if v is not _MISSING:
                    return v
            return 0
        _wraw = str(_get_wastage(item)).strip()
        try:
            w_val = float(_wraw) if _wraw not in ('', '-', 'None', 'null', 'none') else 0.0
        except Exception:
            w_val = 0.0
        wastage = str(int(w_val)) if w_val == int(w_val) else str(w_val)
        try:
            t_val  = float(touch)
            g_val  = float(gross_wt)
            total  = t_val + w_val
            fine   = round(total / 100 * g_val, 3)
        except Exception:
            total = 0.0
            fine  = 0.0
        fine_str  = f"{fine:.3f}"
        total_str = str(int(total)) if total == int(total) else str(total)
        touch_row = f"T {touch}+{wastage}={total_str}" if w_val > 0 else f"T {touch}"
        fine_row  = f"F {fine_str}"
        h0, t0 = _row_metrics(mf, f"W {gross_wt}")
        h1, t1 = _row_metrics(mf, touch_row)
        h2, t2 = _row_metrics(mf, fine_row)
        rows         = (h0, t0, h1, t1, h2, t2, 0, 0)
        total_text_h = h0 + gap + h1 + gap + h2
        extra        = dict(wastage=wastage, fine_str=fine_str,
                            touch_row=touch_row, fine_row=fine_row)

    elif variation == 'STANDARD':
        mf     = fonts['ring']
        gap    = ROW_GAP_STANDARD
        h0, t0 = _row_metrics(mf, f"W {gross_wt}")
        h1, t1 = _row_metrics(mf, f"T {touch}")
        rows         = (h0, t0, h1, t1, 0, 0, 0, 0)
        total_text_h = h0 + gap + h1
        extra        = {}

    else:
        print(f"[TAG ENGINE] Unknown variation '{variation}', using STANDARD")
        mf     = fonts['ring']
        gap    = ROW_GAP_STANDARD
        h0, t0 = _row_metrics(mf, f"W {gross_wt}")
        h1, t1 = _row_metrics(mf, f"T {touch}")
        rows         = (h0, t0, h1, t1, 0, 0, 0, 0)
        total_text_h = h0 + gap + h1
        extra        = {}
        variation    = 'STANDARD'

    bb_id = fonts['tagid'].getbbox(short)
    id_h  = bb_id[3] - bb_id[1]
    content_top = max((LABEL_H - total_text_h) // 2 + TOP_OFFSET, 1)
    if content_top + total_text_h > LABEL_H - 1:
        content_top = max(LABEL_H - total_text_h - 1, 1)

    return dict(
        variation=variation, gross_wt=gross_wt, touch=touch,
        short=short, rows=rows, gap=gap, mf=mf,
        total_text_h=total_text_h,
        id_h=id_h, bb_id=bb_id,
        content_top=content_top,
        **extra,
    )


# ── PARA: LEFT WING — W/P/N + QR only, NO tag ID, NO T row ──────────────────
def _draw_para_left(canvas, draw, fonts, layout, qr_img):
    """
    Left wing of PARA tag:
      W: 0.530
      P: 0.200   [QR]
      N: 1.613
    No tag ID. No T row.
    """
    mf  = layout['mf']
    gap = layout['gap']
    h0, t0, h1, t1, h2, t2, h3, t3 = layout['rows']
    ct  = layout['content_top']
    tx  = OUTER_PAD + 8   # left wing text indent

    draw.text((tx, ct - t0),
              f"W: {layout['gross_wt']}", font=mf, fill="black")
    draw.text((tx, ct + h0 + gap - t1),
              f"P: {layout['para_wt']}",  font=mf, fill="black")
    draw.text((tx, ct + h0 + gap + h1 + gap - t2),
              f"N: {layout['net_wt']}",   font=mf, fill="black")

    # QR flush to right edge of left wing, vertically centred
    qx     = WING_W - QR_SIZE - 2
    qr_top = (LABEL_H - QR_SIZE) // 2
    canvas.paste(qr_img, (qx, qr_top))


# ── PARA: RIGHT WING — tag ID + T row (left col) | QR (right col) ────────────
def _draw_para_right(canvas, draw, fonts, layout, qr_img):
    """
    Right wing of PARA tag:
      tag_id        [QR]
      T 76          [  ]
    """
    mf    = layout['mf']
    short = layout['short']
    bb_id = layout['bb_id']

    # QR: right column, flush to right edge
    qx     = WING_W + WING_W - QR_SIZE - 14   # QR closer to text
    qr_top = (LABEL_H - QR_SIZE) // 2
    canvas.paste(qr_img, (qx, qr_top))

    # Left column: same indent as left wing (OUTER_PAD + 4)
    col_x = WING_W + OUTER_PAD + 8   # tighter gap between text and QR

    # Measure tag_id and T row
    id_h = bb_id[3] - bb_id[1]
    id_t = bb_id[1]
    t_str = f"T {layout['touch']}"
    t_bb  = mf.getbbox(t_str)
    t_h   = t_bb[3] - t_bb[1]
    t_t   = t_bb[1]

    inner_gap = 14   # vertical gap between tag_id and T row
    col_h     = id_h + inner_gap + t_h
    col_top   = qr_top + (QR_SIZE - col_h) // 2   # centre with QR

    # tag_id row
    draw.text((col_x - bb_id[0], col_top - id_t),
              short, font=fonts['tagid'], fill="black")

    # T row
    draw.text((col_x - t_bb[0], col_top + id_h + inner_gap - t_t),
              t_str, font=mf, fill="black")


# ── STANDARD WING ─────────────────────────────────────────────────────────────
def _draw_wing(canvas, draw, fonts, wing_x, layout, qr_img):
    tx        = wing_x + OUTER_PAD
    qx        = min(wing_x + QR_X, wing_x + WING_W - QR_SIZE - OUTER_PAD)
    ct        = layout['content_top']
    variation = layout['variation']
    mf        = layout['mf']
    gap       = layout['gap']
    h0, t0, h1, t1, h2, t2, h3, t3 = layout['rows']

    if variation == 'KATTI':
        qx = min(wing_x + QR_X + 30, wing_x + WING_W - QR_SIZE - OUTER_PAD)

    if variation == 'RING':
        draw.text((tx, ct - t0),
                  f"W {layout['gross_wt']}", font=mf, fill="black")
        draw.text((tx, ct + h0 + gap - t1),
                  f"માપ {layout['size_val']}", font=fonts['gj'], fill="black")
        draw.text((tx, ct + h0 + gap + h1 + gap - t2),
                  f"T {layout['touch']}", font=mf, fill="black")

    elif variation == 'KATTI':
        draw.text((tx, ct - t0),
                  f"W {layout['gross_wt']}", font=mf, fill="black")
        draw.text((tx, ct + h0 + gap - t1),
                  layout['touch_row'], font=mf, fill="black")
        draw.text((tx, ct + h0 + gap + h1 + gap - t2),
                  layout['fine_row'], font=mf, fill="black")

    elif variation == 'STANDARD':
        draw.text((tx, ct - t0),
                  f"W {layout['gross_wt']}", font=mf, fill="black")
        draw.text((tx, ct + h0 + gap - t1),
                  f"T {layout['touch']}", font=mf, fill="black")

    # QR + tag ID
    total_qr_h = QR_SIZE + ID_GAP + layout['id_h']
    qr_top     = (LABEL_H - total_qr_h) // 2
    canvas.paste(qr_img, (qx, qr_top))
    short = layout['short']
    id_w  = int(draw.textlength(short, font=fonts['tagid']))
    id_x  = (qx + (QR_SIZE - id_w) // 2) - ID_X_OFFSET
    id_y  = qr_top + QR_SIZE + ID_GAP - layout['bb_id'][1]
    draw.text((id_x - layout['bb_id'][0], id_y), short,
              font=fonts['tagid'], fill="black")


def _generate(fonts, item: dict) -> Image.Image:
    layout = _compute_layout(fonts, item)
    canvas = Image.new('RGB', (CANVAS_W, LABEL_H), 'white')
    draw   = ImageDraw.Draw(canvas)
    qr     = _make_qr(str(item.get('tag_id', '')))

    if layout['variation'] == 'PARA':
        _draw_para_left(canvas, draw, fonts, layout, qr)
        _draw_para_right(canvas, draw, fonts, layout, qr)
    else:
        _draw_wing(canvas, draw, fonts, 0,      layout, qr)
        _draw_wing(canvas, draw, fonts, WING_W, layout, qr)
    return canvas


# ── TAG FACTORY ───────────────────────────────────────────────────────────────
class TagFactory:
    PRINTER_NAME = PRINTER_NAME

    def __init__(self):
        self._fonts = None

    def _get_fonts(self):
        if self._fonts is None:
            self._fonts = _load_fonts()
        return self._fonts

    def generate_tag_image(self, item_data: dict) -> Image.Image:
        return _generate(self._get_fonts(), item_data)

    def generate_preview(self, item_data: dict) -> str:
        img     = self.generate_tag_image(item_data)
        out_dir = Path(os.path.abspath(".")) / "ui" / "temp"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "tag_preview.png"
        img.resize((img.width * 2, img.height * 2),
                   Image.Resampling.NEAREST).save(str(out_path))
        return out_path.as_uri()

    def print_to_thermal_printer(self, pil_image: Image.Image,
                                  printer_name: str = None) -> tuple:
        name = printer_name or self.PRINTER_NAME
        if not HAS_WIN32:
            return False, "win32print not available"
        try:
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(name)
            w, h = pil_image.size
            hdc.StartDoc("AurumOS Tag")
            hdc.StartPage()
            ImageWin.Dib(pil_image).draw(hdc.GetHandleOutput(), (0, 0, w, h))
            hdc.EndPage()
            hdc.EndDoc()
            hdc.DeleteDC()
            return True, f"Tag sent to {name}"
        except Exception as e:
            return False, str(e)

    def check_printer_status(self) -> tuple:
        if not HAS_WIN32:
            return False, "Printing not supported on this OS"
        try:
            printers = [p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
            if self.PRINTER_NAME in printers:
                return True, f"Printer '{self.PRINTER_NAME}' is ready"
            return False, (
                f"'{self.PRINTER_NAME}' not found. "
                f"Available: {', '.join(printers) or 'none'}"
            )
        except Exception as e:
            return False, f"Printer check failed: {e}"

    def set_printer(self, name: str):
        self.PRINTER_NAME = name
        print(f"[TAG] Printer set to: {name}")