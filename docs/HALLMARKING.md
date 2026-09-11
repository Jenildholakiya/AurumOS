# Hallmarking, in plain words

This is the **Hallmarking** screen in the retail sidebar. It exists because
of one fact: gold jewellery cannot legally be sold in India without a
hallmark. Stock you have bought and paid for is not sellable stock until it
comes back from an assaying centre with a number struck on it.

That gap is money sitting still, and nothing in the software could see it
before. The number itself was stored against an item and nothing else: no
record of what went, when, what it cost, or what came home.

---

## The vocabulary

**Assaying and Hallmarking Centre**, usually just "the centre". The place
that tests the metal and marks it. A shop sends lots and gets them back.

**Fineness.** Parts per thousand of pure metal. 22K is 916, 18K is 750.
A piece is marked with its grade, written as `22K916`.

**HUID.** Hallmark Unique Identification. Six characters, letters and
digits, struck on the piece. It belongs to that one piece for ever.

**Failed.** The assay came back below what was claimed. The piece is not
marked. It comes home to be melted again, not sold.

---

## What is on the screen

**Five counters across the top.** Each one is a button that scrolls to what
it counts.

| Counter | What it means |
|---|---|
| Waiting to go | Tagged stock with no number on it, still in the shop |
| At the centre | Pieces away right now, and how many lots |
| Overdue | Lots past the day they were due back |
| Failed assay | Pieces that came back unmarked, to be melted again |
| Cannot be sold | Waiting plus away. The one number an owner wants. |

**Left: Waiting to go.** Every piece with no number on it. Tick the ones
you are sending, choose a centre, press Send lot.

**Right: At the centre**, worst first, because the late one is the one to
ring about. Then **Trace a piece**, then **Finished lots**.

---

## Sending a lot

Tick pieces. The line above the button keeps a running count and what the
centre will charge.

**It will tell you when a lot is too small.** Centres charge per piece with
a minimum on the lot. Below that minimum you are paying for pieces you did
not send, so the line says how many more would ride along for nothing. Shops
lose real money by sending four pieces at a time.

**It refuses some pieces rather than packing them.** A piece with no weight
recorded, no tag, or a purity BIS will not mark. In that last case it names
the grade the piece would have to go as instead, always rounding down: a
shop may under-claim purity, never over-claim it.

**A piece already at the centre is never offered twice.** That is a live
question against the register rather than a flag on the item, so cancelling
a lot releases its pieces with nothing left to tidy up.

Once sent, the lot is frozen. Each piece's weight is copied in as it stood
that day, so editing the stock record later cannot rewrite what was sent.

---

## Booking a lot back in

Press **Book in** on a lot. One row per piece still out.

- **Type the number** struck on the piece. That marks it passed.
- **Tick failed** for a piece the centre would not mark. The number field
  closes and empties.
- **Leave a row blank** to deal with it later. The lot stays open.

Press Book in. Every passed piece gets its number written onto its stock
record, which is what makes it sellable. A failed piece gets nothing and
goes back to the waiting list, where you can see it.

### Three things it will not let you do

**A failed piece cannot keep a number.** This is the whole risk the screen
exists to stop: a number recorded against an unmarked piece would quietly
make it sellable stock.

**A number already on another piece is refused.** Two pieces cannot share
one. It checks against stock and against every other lot.

**Nothing is half saved.** Every row is checked before any of them is
written, so one mistyped number cannot leave a lot recorded in pieces.

### One thing it volunteers

A number containing **O, 0, I or 1** is flagged for a second look. Those are
read off a loupe and get mistyped for each other, which is how a shop ends
up ringing the centre a week later. It is a warning, never a block: a real
number may contain any of them.

---

## Tracing a piece

Type a number or a tag in the search box at the top. It shows which lot the
piece travelled in, which centre marked it, and when it came back. This is
the question a BIS inspector asks, and it is why finished lots are kept
rather than cleared away.

---

## Centres

The **Centres** button holds the places you send to, what each charges per
piece, its minimum for a lot, and how many days it usually takes. That last
number sets the due date, which is what makes a lot show as overdue.

The shop's own BIS registration lives here too.

Charges default to the published rates and can be changed per centre,
because BIS has changed them before.

---

## Where the pieces live

| Part | What it does |
|---|---|
| `core/hallmark.py` | The rules. Grades, numbers, charges, whether a lot is late. Pure functions with the date passed in, so the arithmetic a shop argues about is checkable on its own. |
| `database/db_manager.py` | Three tables: centres, lots, and the pieces inside them. |
| `main.py` | Ten methods on the bridge. The panel loads in one call. |
| `ui/hallmark.html` | The screen. |

Three test suites cover it: the rules, the storage against a real database,
and the screen against a stub browser.

---

## What it does not do yet

- It does not talk to the BIS portal. Numbers are typed in from the pieces.
- It does not print a challan to send with the lot.
- Silver is not handled. Hallmarking silver is voluntary and every item in
  this software is gold.
- A failed piece goes back to the waiting list, but there is no melting
  record to say what became of the metal.
