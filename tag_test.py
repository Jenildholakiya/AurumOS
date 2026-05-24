"""
AurumOS Tag Engine — Visual Test & Print Tool
Run from project root:
    .\.venv\Scripts\python.exe tag_test.py
"""

import os
import sys

# ── Make sure core/ is importable ────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.tag_engine import TagFactory

# ══════════════════════════════════════════════════════════════════
#  TEST DATA — change these values to match a real item
# ══════════════════════════════════════════════════════════════════
TEST_ITEMS = [
    {
        "tag_id":   "9618698539",
        "gross_wt": "5.230",
        "touch":    "91.6",
        "size":     "18",
    },
    {
        "tag_id":   "T002",
        "gross_wt": "2.400",
        "touch":    "76",
        "size":     "5",
    },
    {
        "tag_id":   "T003",
        "gross_wt": "10.500",
        "touch":    "91.6",
        "size":     "-",      # no size
    },
]


def save_preview(tf: TagFactory, item: dict, index: int) -> str:
    """Save a 5x scaled preview PNG and return the path."""
    img  = tf.generate_tag_image(item)
    big  = img.resize((img.width * 5, img.height * 5))
    path = f"tag_preview_{index+1}.png"
    big.save(path)
    print(f"  ✅ Preview saved → {path}  ({img.width}×{img.height}px raw)")
    return path


def main():
    print("=" * 60)
    print("  AurumOS Tag Engine — Test Tool")
    print("=" * 60)

    tf = TagFactory()
    print(f"\n📐 Canvas  : {tf.CANVAS_W}×{tf.CANVAS_H}px")
    print(f"📏 Label   : {tf.LABEL_W_MM}×{tf.LABEL_H_MM}mm @ {tf.DPI}dpi")
    print(f"🔤 Font    : {tf.FONT_MAIN_PX}px main / {tf.FONT_TAGID_PX}px tagid")
    print(f"📦 QR size : {tf.QR_SIZE}px")
    print(f"📍 Wings   : left_x={tf.LEFT_WING_X}  right_x={tf.RIGHT_WING_X}")
    print()

    # ── Step 1: Save all previews ─────────────────────────────────
    print("STEP 1 — Saving preview images...")
    previews = []
    for i, item in enumerate(TEST_ITEMS):
        print(f"\n  Item {i+1}: tag_id={item['tag_id']} W={item['gross_wt']} T={item['touch']} S={item['size']}")
        path = save_preview(tf, item, i)
        previews.append(path)

    print(f"\n✅ All {len(previews)} previews saved in project root.")
    print("   Open them to check layout before printing.\n")

    # ── Step 2: Open previews automatically ──────────────────────
    ans = input("Open preview images now? (y/n): ").strip().lower()
    if ans == 'y':
        for p in previews:
            os.startfile(p)

    # ── Step 3: Print test ────────────────────────────────────────
    print()
    ans = input("Print test label? (y/n): ").strip().lower()
    if ans != 'y':
        print("Skipped printing.")
        return

    print("\nWhich item to print?")
    for i, item in enumerate(TEST_ITEMS):
        print(f"  {i+1}. tag_id={item['tag_id']} W={item['gross_wt']} T={item['touch']} S={item['size']}")
    print(f"  {len(TEST_ITEMS)+1}. Print ALL")

    try:
        choice = int(input("Choice: ").strip())
    except ValueError:
        print("Invalid choice — exiting.")
        return

    # ── Check printer status ──────────────────────────────────────
    ok, msg = tf.check_printer_status()
    print(f"\n🖨️  Printer: {msg}")
    if not ok:
        print(f"❌ Printer not ready: {msg}")
        return

    # ── Print ─────────────────────────────────────────────────────
    try:
        if choice == len(TEST_ITEMS) + 1:
            for i, item in enumerate(TEST_ITEMS):
                print(f"\n  Printing item {i+1}...")
                img = tf.generate_tag_image(item)
                tf.print_to_thermal_printer(img)
        elif 1 <= choice <= len(TEST_ITEMS):
            item = TEST_ITEMS[choice - 1]
            print(f"\n  Printing: {item}")
            img = tf.generate_tag_image(item)
            tf.print_to_thermal_printer(img)
        else:
            print("Invalid choice.")
            return

        print("\n✅ Print job sent successfully!")

    except Exception as e:
        print(f"\n❌ Print error: {e}")


if __name__ == "__main__":
    main()