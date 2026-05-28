import os, sys
import win32print
import win32ui
from PIL import Image, ImageDraw, ImageFont, ImageWin
import qrcode

# ================================================================
#  CONFIG BLOCK
# ================================================================
FONT_MAIN = 18  # Reduced slightly to handle ROW_GAP = 10 perfectly
FONT_GJ = 16
FONT_TAGID = 11
QR_SIZE = 60
CANVAS_W = 450
CANVAS_H = 96
OUTER_PAD = 10
QR_X = 140
ROW_GAP = 10
PRINTER_NAME = "TSC_Jewelry"
# ================================================================

WING_W = CANVAS_W // 2
CANVAS_W_HALF = CANVAS_W // 2

TEST_ITEMS = [
    {"tag_id": "9618698539", "gross_wt": "5.230", "touch": "91.6", "size": "18"},
    {"tag_id": "T002", "gross_wt": "2.400", "touch": "76", "size": "5"},
]


def load_fonts():
    base = os.path.dirname(os.path.abspath(__file__))
    for fd in [os.path.join(base, "fonts"), os.path.join(os.path.dirname(base), "fonts")]:
        if os.path.isdir(fd): font_dir = fd; break
    else:
        font_dir = base

    def ttf(name, size):
        p = os.path.join(font_dir, name)
        return ImageFont.truetype(p, size) if os.path.exists(p) else ImageFont.load_default()

    return {
        'main': ttf("JetBrainsMono-ExtraBold.ttf", FONT_MAIN),
        'gj': ttf("NotoSansGujarati-Bold.ttf", FONT_GJ),
        'tagid': ttf("JetBrainsMono-ExtraBold.ttf", FONT_TAGID),
    }


def calc_rows(fonts):
    def bbox_h(font, text):
        bb = font.getbbox(text)
        return bb[3] - bb[1], bb[1]

    h0, t0 = bbox_h(fonts['main'], "W:5.230")
    h1, t1 = bbox_h(fonts['gj'], "માપ:18")
    h2, t2 = bbox_h(fonts['main'], "T:91.6")
    total_text_h = h0 + ROW_GAP + h1 + ROW_GAP + h2
    text_top = (CANVAS_H - total_text_h) // 2
    y0 = text_top - t0
    y1 = text_top + h0 + ROW_GAP - t1
    y2 = text_top + h0 + ROW_GAP + h1 + ROW_GAP - t2
    return int(y0), int(y1), int(y2), h0, h1, h2


def make_qr(data):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=0)
    qr.add_data(data);
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert('RGB').resize((QR_SIZE, QR_SIZE),
                                                                                       Image.Resampling.NEAREST)


def text_at(draw, x, y, text, font, fill="black"):
    bb = font.getbbox(text)
    draw.text((x - bb[0], y), text, font=font, fill=fill)


def draw_wing(canvas, draw, fonts, wing_x, tag_id, qr_img, gross_wt, touch, size_val, row_y):
    y0, y1, y2 = row_y
    tx = wing_x + OUTER_PAD
    qx = wing_x + QR_X
    qx = min(qx, wing_x + WING_W - QR_SIZE - 1)
    qr_top = (CANVAS_H - QR_SIZE - FONT_TAGID) // 2

    text_at(draw, tx, y0, f"W:{gross_wt}", fonts['main'])
    has_size = size_val and size_val not in ('-', '', 'None', 'null', 'N/A')
    if has_size:
        text_at(draw, tx, y1, f"માપ:{size_val}", fonts['gj'])
        text_at(draw, tx, y2, f"T {touch}", fonts['main'])
    else:
        text_at(draw, tx, y1, f"T {touch}", fonts['main'])

    canvas.paste(qr_img, (qx, qr_top))
    short = tag_id[-10:] if len(tag_id) > 10 else tag_id
    id_w = int(draw.textlength(short, font=fonts['tagid']))
    id_x = qx + (QR_SIZE - id_w) // 2
    id_y = qr_top + QR_SIZE + 2
    text_at(draw, id_x, id_y, short, fonts['tagid'])


def generate_tag(fonts, item, row_y):
    canvas = Image.new('RGB', (CANVAS_W, CANVAS_H), 'white')
    draw = ImageDraw.Draw(canvas)
    qr = make_qr(str(item['tag_id']))
    kw = dict(gross_wt=str(item['gross_wt']), touch=str(item['touch']), size_val=str(item.get('size', '-')),
              row_y=row_y)
    draw_wing(canvas, draw, fonts, 0, item['tag_id'], qr, **kw)
    draw_wing(canvas, draw, fonts, CANVAS_W_HALF, item['tag_id'], qr, **kw)
    return canvas


# ─── NATIVE PRINTING FUNCTION ─────────────────────────────────
def send_to_printer(pil_image, printer_name):
    try:
        hprinter = win32print.OpenPrinter(printer_name)
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer_name)

        hdc.StartDoc("AurumOS Jewelry Tag")
        hdc.StartPage()

        # Draw image directly to the printable area canvas
        dib = ImageWin.Dib(pil_image)
        dib.draw(hdc.GetHandleOutput(), (0, 0, CANVAS_W, CANVAS_H))

        hdc.EndPage()
        hdc.EndDoc()
        win32print.ClosePrinter(hprinter)
        print(f"Sent successfully to {printer_name}")
    except Exception as e:
        print(f"Printing Error: {e}")


def main():
    fonts = load_fonts()
    row_y = calc_rows(fonts)[:3]

    for item in TEST_ITEMS:
        img = generate_tag(fonts, item, row_y)
        # Directly pipes runtime image stream to hardware spooler
        send_to_printer(img, PRINTER_NAME)


if __name__ == "__main__":
    main()
