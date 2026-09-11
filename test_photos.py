"""Pin the rules for stock photographs and their captions.

Run from the project root:  python test_photos.py

A wa.me link cannot carry an image, so the photo goes to the customer via the
clipboard and a person pressing Ctrl+V. What this module decides is what may
be stored, what the stored copy is called, and what is written under the
picture.

The naming matters more than it looks. A camera calls everything IMG_0001.jpg,
and a shop photographing forty rings in an afternoon would overwrite the same
file forty times. Naming after the tag makes that impossible and makes the
photo findable from the piece.

Nothing here touches the disk, the clipboard or WhatsApp.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core import photos                                        # noqa: E402

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


def check_that(label, ok, detail=''):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        if detail:
            print(f"         {detail}")
        FAIL.append(label)


print("-- what counts as a picture --")
for good in ('ring.jpg', 'RING.JPG', 'chain.jpeg', 'set.png', 'new.webp'):
    check_that(good, photos.is_allowed(good))
for bad in ('notes.pdf', 'video.mp4', 'sheet.xlsx', 'ring', 'ring.jpg.exe'):
    check_that('not ' + bad, not photos.is_allowed(bad))

print("-- and what is refused, with something a shopkeeper can act on --")
ok, why = photos.check('ring.jpg', 250000)
check("an ordinary photo", (ok, why), (True, ''))
ok, why = photos.check('', 100)
check("nothing chosen", (ok, why), (False, 'No file chosen.'))
ok, why = photos.check('invoice.pdf', 1000)
check_that("a document says which types are wanted",
           not ok and 'JPG' in why)
ok, why = photos.check('ring.jpg', 0)
check("an empty file", (ok, why), (False, 'That file is empty.'))
ok, why = photos.check('huge.jpg', 40 * 1024 * 1024)
check_that("one straight off a camera is refused", not ok)
check_that("and it says how big it was and what the limit is",
           '40.0 MB' in why and '8 MB' in why)
ok, why = photos.check('ring.jpg', 'not a number')
check("junk for a size", (ok, why), (False, 'Could not read that file.'))
check_that("right on the limit is allowed",
           photos.check('ring.jpg', photos.MAX_BYTES)[0])

print("-- naming the stored copy --")
# A camera calls everything IMG_0001.jpg. Forty rings photographed in an
# afternoon must not become one file overwritten forty times.
check("named after the tag", photos.safe_name('TAG0000001', 'IMG_0001.jpg'), 'TAG0000001.jpg')
check("keeps the real extension", photos.safe_name('TAG1', 'photo.PNG'), 'TAG1.png')
check("a second photo of the same piece", photos.safe_name('TAG1', 'a.jpg', 2), 'TAG1-2.jpg')
check("a third", photos.safe_name('TAG1', 'a.jpg', 3), 'TAG1-3.jpg')
check_that("two different pieces never collide",
           photos.safe_name('TAG1', 'IMG_0001.jpg') != photos.safe_name('TAG2', 'IMG_0001.jpg'))
check("a tag with punctuation is cleaned", photos.safe_name('TAG/../1', 'a.jpg'), 'TAG1.jpg')
check_that("and a path cannot escape the folder",
           '..' not in photos.safe_name('../../etc/passwd', 'a.jpg')
           and '/' not in photos.safe_name('../../etc/passwd', 'a.jpg'))
check("no tag at all still gets a name", photos.safe_name('', 'a.jpg'), 'ITEM.jpg')
check("an odd extension falls back to jpg", photos.safe_name('TAG1', 'a.tiff'), 'TAG1.jpg')

print("-- where they are kept --")
check("beside the data, not inside the program",
      photos.folder('C:/AurumOS'), os.path.join('C:/AurumOS', 'photos'))
check("a full path", photos.path_for('C:/AurumOS', 'TAG1.jpg'),
      os.path.join('C:/AurumOS', 'photos', 'TAG1.jpg'))
check("no file, no path", photos.path_for('C:/AurumOS', ''), '')

print("-- what is written under the picture --")
item = {'it_name': 'Temple Necklace', 'karat': '22K', 'gr_wt': 32.5,
        'category': 'Necklaces', 'sale_price': 285000}
cap = photos.caption(item, shop='DJ Jewellers')
check_that("names the piece", 'Temple Necklace' in cap)
check_that("gives the karat and weight", '22K' in cap and '32.500 g' in cap)
check_that("and the price, grouped the Indian way", 'Rs 2,85,000' in cap)
check_that("signed off by the shop", 'DJ Jewellers' in cap)
# Touch and fine weight are wholesale language. A walk-in buyer does not
# know what 91.6 means and should not be shown it.
check_that("no wholesale jargon", 'touch' not in cap.lower() and 'fine' not in cap.lower())

bare = photos.caption({'it_name': 'Plain Band'}, shop='DJ')
check_that("a piece with nothing recorded still gets a caption", 'Plain Band' in bare)
check_that("and no empty price line", 'Rs' not in bare)
check_that("nothing at all still names something",
           photos.caption({}, shop='DJ').startswith('*This piece*'))
withnote = photos.caption(item, shop='DJ', note='Can be made to order in 18K.')
check_that("a note is carried through", 'made to order' in withnote)

print("-- one line naming a piece --")
check("full description", photos.describe(item), 'Temple Necklace (22K, Necklaces, 32.500 g)')
check("just a name", photos.describe({'it_name': 'Bangle'}), 'Bangle')
check("nothing", photos.describe({}), '')
check_that("junk weight does not raise",
           photos.describe({'it_name': 'X', 'gr_wt': 'heavy'}) == 'X')

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
