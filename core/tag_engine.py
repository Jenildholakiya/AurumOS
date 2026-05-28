import qrcode
import os
import math
import time
import threading
import subprocess
from PIL import Image, ImageDraw, ImageFont
import win32print

# ================================================================
#  AURUM OS — JEWELRY TAG ENGINE
#  Label: 81mm × 12mm  →  648 × 96 px at 203 DPI
#
#  THE PRINTER CUTS ~30px FROM THE TOP — so all content
#  must start at Y=30 or lower to actually appear on label.
#
#  TWO IDENTICAL WINGS:
#    LEFT  wing: x =   0 → 323
#    RIGHT wing: x = 324 → 647
#
#  CONTENT LAYOUT (starts at y=28 to avoid top cut):
#
#    y=28  ──  W: 5.230
#    y=48  ──  માપ:18
#    y=68  ──  T 91.6
#    y=83  ──  [tag ID below QR]
#
#  QR block: vertically spans y=22 → y=82 (60px tall)
#
# ── TWEAK THESE TO ADJUST LAYOUT ────────────────────────────
TEXT_X     = 2    # px — text left edge from wing left
QR_X       = 170  # px — QR left edge from wing left

QR_SIZE    = 60   # px — QR height/width

# Row positions — start at 28 to clear the top print margin
ROW0_Y     = 28   # "W: xxx"
ROW1_Y     = 48   # "માપ:xx"
ROW2_Y     = 68   # "T xx.x"

FONT_MAIN  = 22   # px — W: and T rows (smaller = all 3 rows fit)
FONT_GJ    = 20   # px — Gujarati row
FONT_TAGID = 10   # px — tag ID below QR
# ────────────────────────────────────────────────────────────

# Fixed
CANVAS_W = 648
CANVAS_H = 96
WING_W   = 324
QR_TOP   = (CANVAS_H - QR_SIZE) // 2   # = 18  (vertically centred)
ID_Y     = QR_TOP + QR_SIZE + 2        # = 80


def _print_bmp(printer_name: str, bmp_path: str, copies: int = 1):
    h = win32print.OpenPrinter(printer_name)
    try:
        info   = win32print.GetPrinter(h, 2)
        driver = info['pDriverName']
        port   = info['pPortName']
    finally:
        win32print.ClosePrinter(h)
    print(f"[PRINT] {printer_name} | {driver} | {port}")
    for i in range(copies):
        print(f"[PRINT] copy {i+1}/{copies}")
        r = subprocess.run(
            ["mspaint", "/pt", bmp_path, printer_name, driver, port],
            timeout=20, capture_output=True
        )
        print(f"[PRINT] rc={r.returncode}")
        if copies > 1 and i < copies - 1:
            time.sleep(2)


class TagFactory:

    LABEL_W_MM = 81
    LABEL_H_MM = 12
    DPI        = 203

    def __init__(self):
        self.printer_name = "TSC_Jewelry"

        base = os.path.dirname(os.path.abspath(__file__))
        font_dir = None
        for fd in [
            os.path.join(os.path.dirname(base), "fonts"),
            os.path.join(base, "fonts"),
            os.path.join(base, "..", "fonts"),
        ]:
            if os.path.isdir(fd):
                font_dir = fd
                break
        if not font_dir:
            font_dir = base

        def load(name, size):
            p = os.path.join(font_dir, name)
            return ImageFont.truetype(p, size) if os.path.exists(p) \
                   else ImageFont.load_default()

        self.f_main  = load("JetBrainsMono-ExtraBold.ttf", FONT_MAIN)
        self.f_gj    = load("NotoSansGujarati-Bold.ttf",   FONT_GJ)
        self.f_tagid = load("JetBrainsMono-ExtraBold.ttf", FONT_TAGID)

        self._tmp = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ui", "temp"
        )
        os.makedirs(self._tmp, exist_ok=True)

    def check_printer_status(self):
        CODES = {
            0x00000001: "Paused",     0x00000002: "Error",
            0x00000008: "Paper Jam",  0x00000010: "Ribbon Out",
            0x00000100: "Offline",    0x00000800: "Busy",
            0x00200000: "Needs Attention", 0x00800000: "Door Open",
        }
        try:
            h = win32print.OpenPrinter(self.printer_name)
            try:
                s = win32print.GetPrinter(h, 2)['Status']
                return (True, "Ready") if s == 0 \
                       else (False, CODES.get(s, f"Status {s}"))
            finally:
                win32print.ClosePrinter(h)
        except Exception as e:
            return False, f"Error: {e}"

    def _make_qr(self, data: str) -> Image.Image:
        qr = qrcode.QRCode(version=1,
                           error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=1, border=0)
        qr.add_data(data)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white") \
                 .convert('RGB') \
                 .resize((QR_SIZE, QR_SIZE), Image.Resampling.NEAREST)

    def _draw_wing(self, canvas, draw, wing_x,
                   tag_id, qr_img, gross_wt, touch, size_val):
        tx = wing_x + TEXT_X
        qx = min(wing_x + QR_X, CANVAS_W - QR_SIZE - 1)

        # Row 0 — Weight ALWAYS
        draw.text((tx, ROW0_Y), f"W: {gross_wt}", fill="black", font=self.f_main)

        # Row 1 — Size (Gujarati) or Touch
        has_size = size_val and size_val not in ('-', '', 'None', 'null', 'N/A')
        if has_size:
            draw.text((tx, ROW1_Y), f"માપ:{size_val}", fill="black", font=self.f_gj)
            draw.text((tx, ROW2_Y), f"T {touch}",      fill="black", font=self.f_main)
        else:
            draw.text((tx, ROW1_Y), f"T {touch}",      fill="black", font=self.f_main)

        # QR
        canvas.paste(qr_img, (qx, QR_TOP))

        # Tag ID below QR
        short = tag_id[-10:] if len(tag_id) > 10 else tag_id
        id_w  = int(draw.textlength(short, font=self.f_tagid))
        id_x  = qx + (QR_SIZE - id_w) // 2
        id_x  = max(wing_x, min(id_x, CANVAS_W - id_w - 1))
        if ID_Y + FONT_TAGID <= CANVAS_H:
            draw.text((id_x, ID_Y), short, fill="black", font=self.f_tagid)

    def generate_tag_image(self, item_data: dict) -> Image.Image:
        tag_id   = str(item_data.get('tag_id',   'N/A'))
        gross_wt = str(item_data.get('gross_wt', '0.000'))
        touch    = str(item_data.get('touch',    '0'))
        size_val = str(item_data.get('size',     '-'))

        canvas = Image.new('RGB', (CANVAS_W, CANVAS_H), 'white')
        draw   = ImageDraw.Draw(canvas)
        qr     = self._make_qr(tag_id)

        self._draw_wing(canvas, draw, 0,      tag_id, qr, gross_wt, touch, size_val)
        self._draw_wing(canvas, draw, WING_W, tag_id, qr, gross_wt, touch, size_val)
        return canvas

    def generate_preview(self, item_data: dict) -> str:
        path = os.path.join(self._tmp, "tag_preview.png")
        img  = self.generate_tag_image(item_data)
        img.resize((img.width * 5, img.height * 5),
                   Image.Resampling.NEAREST).save(path)
        return f"temp/tag_preview.png?v={int(time.time())}"

    def print_to_thermal_printer(self, tag_image: Image.Image,
                                 copies: int = 1) -> bool:
        ok, msg = self.check_printer_status()
        print(f"[PRINTER] {msg}")
        if not ok:
            raise Exception(f"Printer not ready — {msg}")

        bmp = os.path.join(self._tmp, f"tag_{int(time.time())}.bmp")
        tag_image.convert('RGB').save(bmp, 'BMP')
        print(f"[PRINT] {tag_image.width}×{tag_image.height}px → {bmp}")

        try:
            _print_bmp(self.printer_name, bmp, copies)
        finally:
            def _cleanup():
                time.sleep(25)
                try: os.remove(bmp)
                except: pass
            threading.Thread(target=_cleanup, daemon=True).start()
        return True