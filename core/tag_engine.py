import qrcode
import os
import math
import time
import threading
import subprocess
from PIL import Image, ImageDraw, ImageFont
import win32print

# ═══════════════════════════════════════════════════════════════════
#  TSC THERMAL PRINTER — MSPAINT /pt METHOD (PROVEN WORKING)
#
#  mspaint /pt <file> <printer> <driver> <port>
#  ✅ Confirmed working on this machine.
#
#  Fix for multiple labels: set BMP DPI metadata to match printer DPI
#  so mspaint knows NOT to scale the image.
# ═══════════════════════════════════════════════════════════════════


def _print_bmp(printer_name: str, bmp_path: str, copies: int = 1):
    h = win32print.OpenPrinter(printer_name)
    try:
        info   = win32print.GetPrinter(h, 2)
        driver = info['pDriverName']
        port   = info['pPortName']
    finally:
        win32print.ClosePrinter(h)

    print(f"🖨️  [MSPAINT] '{printer_name}' | driver='{driver}' | port='{port}'")

    for i in range(copies):
        print(f"🖨️  [MSPAINT] Sending copy {i+1}/{copies}...")
        result = subprocess.run(
            ["mspaint", "/pt", bmp_path, printer_name, driver, port],
            timeout=20,
            capture_output=True
        )
        print(f"✅  [MSPAINT] rc={result.returncode}")
        if copies > 1 and i < copies - 1:
            time.sleep(2)


# ═══════════════════════════════════════════════════════════════════
#  TAG FACTORY
# ═══════════════════════════════════════════════════════════════════

class TagFactory:

    LABEL_W_MM = 81
    LABEL_H_MM = 12
    DPI        = 203

    # Canvas dimensions exactly matching label at 203 DPI
    # 81mm * 203/25.4 = 647.9 → 648px
    # 12mm * 203/25.4 = 95.9  →  96px
    CANVAS_W   = 630
    CANVAS_H   = 100

    LEFT_WING_X  = 5
    RIGHT_WING_X = 220

    QR_SIZE    = 50
    QR_GAP     = 20
    PAD_LEFT   = 5

    FONT_MAIN_PX  = int(3.1 * DPI / 25.4)
    FONT_GJ_PX    = int(3.1 * DPI / 25.4)
    FONT_TAGID_PX = int(1.8 * DPI / 25.4)

    def __init__(self, dpi: int = 203):
        self.dpi          = dpi
        self.printer_name = "TSC_Jewelry"

        base_dir = os.path.dirname(os.path.abspath(__file__))
        font_dir = os.path.join(os.path.dirname(base_dir), "fonts")

        def _ttf(n, s):
            return ImageFont.truetype(os.path.join(font_dir, n), s)

        try:
            self.font_main   = _ttf("JetBrainsMono-ExtraBold.ttf", self.FONT_MAIN_PX)
            self.font_gj     = _ttf("NotoSansGujarati-Bold.ttf",   self.FONT_GJ_PX)
            self.font_tag_id = _ttf("JetBrainsMono-ExtraBold.ttf", self.FONT_TAGID_PX)
        except Exception:
            self.font_main = self.font_gj = self.font_tag_id = ImageFont.load_default()

        self._qr_y         = (self.CANVAS_H - self.QR_SIZE) // 4
        self._tagid_y      = self._qr_y + self.QR_SIZE + 8
        self._row_y        = [10, 38, 66]
        self._left_qr_max  = self.RIGHT_WING_X - 2 - self.QR_SIZE
        self._right_qr_max = self.CANVAS_W - 2 - self.QR_SIZE

        self._tmp_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ui", "temp"
        )
        os.makedirs(self._tmp_dir, exist_ok=True)

    # ─── PRINTER STATUS ──────────────────────────────────────────
    def check_printer_status(self):
        STATUS = {
            0x00000001:"Paused", 0x00000002:"Error",
            0x00000008:"Paper Jam", 0x00000010:"Paper/Ribbon Out",
            0x00000100:"Offline", 0x00000800:"Busy",
        }
        try:
            h = win32print.OpenPrinter(self.printer_name)
            try:
                s = win32print.GetPrinter(h, 2)['Status']
                return (True,"Online & Ready") if s==0 else (False,STATUS.get(s,f"Code {s}"))
            finally:
                win32print.ClosePrinter(h)
        except Exception as e:
            return False, f"Driver error: {e}"

    # ─── QR CODE ─────────────────────────────────────────────────
    def _make_qr(self, data, size_px):
        qr = qrcode.QRCode(version=1,
                           error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=1, border=0)
        qr.add_data(data)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white") \
                 .convert('RGB').resize((size_px, size_px), Image.Resampling.NEAREST)

    # ─── DRAW WING ───────────────────────────────────────────────
    def _draw_wing(self, canvas, draw, wing_x, qr_max_x,
                   tag_id, qr_img, gross_wt, touch, size_val):
        tx   = wing_x + self.PAD_LEFT
        row0 = f"W: {gross_wt}"
        has  = size_val and size_val not in ('-','','None','null','N/A')
        row1 = f"માપ: {size_val}" if has else f"માપ: {touch}"
        row2 = f"T {touch}"
        tw   = max(int(draw.textlength(r, font=f))
                   for r, f in [(row0,self.font_main),(row1,self.font_gj),(row2,self.font_main)])
        qx   = min(tx + tw + self.QR_GAP, qr_max_x)
        draw.text((tx, self._row_y[0]), row0, fill="black", font=self.font_main)
        draw.text((tx, self._row_y[1]), row1, fill="black", font=self.font_gj)
        draw.text((tx, self._row_y[2]), row2, fill="black", font=self.font_main)
        canvas.paste(qr_img, (qx, self._qr_y))
        did = tag_id[-10:] if len(tag_id) > 10 else tag_id
        dw  = int(draw.textlength(did, font=self.font_tag_id))
        dx  = min(qx + (self.QR_SIZE - dw) // 2, self.CANVAS_W - dw - 2)
        if self._tagid_y + self.FONT_TAGID_PX <= self.CANVAS_H:
            draw.text((dx, self._tagid_y), did, fill="black", font=self.font_tag_id)

    # ─── GENERATE TAG IMAGE ──────────────────────────────────────
    def generate_tag_image(self, item_data: dict) -> Image.Image:
        tag_id   = str(item_data.get('tag_id',   'N/A'))
        gross_wt = str(item_data.get('gross_wt', '0.000'))
        touch    = str(item_data.get('touch',    '0'))
        size_val = str(item_data.get('size',     '-'))

        canvas = Image.new('RGB', (self.CANVAS_W, self.CANVAS_H), 'white')
        draw   = ImageDraw.Draw(canvas)
        qr_img = self._make_qr(tag_id, self.QR_SIZE)

        self._draw_wing(canvas, draw, self.LEFT_WING_X,  self._left_qr_max,
                        tag_id, qr_img, gross_wt, touch, size_val)
        self._draw_wing(canvas, draw, self.RIGHT_WING_X, self._right_qr_max,
                        tag_id, qr_img, gross_wt, touch, size_val)
        return canvas

    # ─── PREVIEW ─────────────────────────────────────────────────
    def generate_preview(self, item_data: dict) -> str:
        os.makedirs(self._tmp_dir, exist_ok=True)
        path = os.path.join(self._tmp_dir, "tag_preview.png")
        img  = self.generate_tag_image(item_data)
        img.resize((img.width * 5, img.height * 5), Image.Resampling.NEAREST).save(path)
        return f"temp/tag_preview.png?v={int(time.time())}"

    # ─── PRINT ───────────────────────────────────────────────────
    def print_to_thermal_printer(self, tag_image: Image.Image, copies: int = 1) -> bool:
        is_ready, msg = self.check_printer_status()
        print(f"🖨️  [PRINTER]: {msg}")
        if not is_ready:
            raise Exception(f"Printer not ready — {msg}")

        # ── Scale to exact label pixel size ──────────────────────
        label_w = int(round(self.LABEL_W_MM * self.DPI / 25.4))  # 648
        label_h = int(round(self.LABEL_H_MM * self.DPI / 25.4))  # 96

        # Resize canvas to exact label dots
        print_img = tag_image.resize(
            (label_w, label_h), Image.Resampling.LANCZOS
        ).convert('RGB')

        # ── Save BMP with correct DPI metadata ───────────────────
        # Setting DPI in BMP tells mspaint the physical size:
        # 203 DPI → mspaint knows 648px = 81mm → no scaling needed
        bmp_path = os.path.join(self._tmp_dir, f"tag_{int(time.time())}.bmp")

        print_img.save(
            bmp_path,
            format='BMP',
            dpi=(self.DPI, self.DPI)   # ← KEY: tells mspaint exact physical size
        )

        print(f"🖨️  [PRINT] BMP {label_w}×{label_h}px @ {self.DPI}dpi → {bmp_path}")

        try:
            _print_bmp(self.printer_name, bmp_path, copies)
        finally:
            # Clean up temp file after 20 seconds
            def _del():
                time.sleep(20)
                try:
                    os.remove(bmp_path)
                except Exception:
                    pass
            threading.Thread(target=_del, daemon=True).start()

        return True