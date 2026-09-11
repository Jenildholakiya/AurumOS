# The customer screen, in plain words

This is the CRM screen at `ui/crm.html`, reached from **CRM** in the sidebar.
It is separate from **Customers**, which is the record card for one person.
This one answers a different question: *what should I do today?*

---

## What is on the screen

**Six counters across the top.** To call, Promised, In hand, Owed, Plans
behind, Customers. Each one is a button. Clicking it scrolls to the card it
counts, so the number is a way in rather than decoration.

**Left — the one place you type something, then what to do today.**

| Card | What it is |
|---|---|
| Do something | Four tabs: Follow-up, Order, Announce, Send piece. Everything you might write down or send is here, so it is one card rather than four stacked forms. |
| Call today | Birthdays and anniversaries in the next 30 days |
| Promised | Things you said you would do, oldest first |
| Orders and repairs | Work in hand, overdue in red |

**Right — money and people.**

| Card | What it lists |
|---|---|
| Money owed | Four age buckets, then who owes what and for how long |
| Savings plans | Enrolments, the ones that have fallen behind on top |
| Gone quiet | Nobody bought in a year |
| Best customers | By lifetime spend |

The two sides are split so they end at about the same place. A third column
was tried and removed: three stacks cannot fill two cells, so on any window
too narrow for three the last one dropped to a second row with a hole above
it the height of the tallest column.

Below about 1120 pixels the two sides stack into one. The form goes to the
bottom there, because on a narrow screen the lists matter more than the
place you type.

---

## It keeps itself current

There is a refresh button, but you should not need it.

- The whole screen reloads every **60 seconds**.
- It reloads again the moment you **come back to the window**.
- The dot by the title says **live**, **just now**, or how long since the
  last successful load. If it goes grey and says *not updating*, something
  is wrong and the cards will say what.

**It will not reload over the top of you.** If the cursor is in a field
that has something typed in it, that tick is skipped. Rebuilding the lists
under a half-written follow-up would throw away what was typed, which is
exactly what makes auto-refresh worse than a button.

A search you have typed is re-applied after each reload, so filtering does
not come undone every minute.

---

## Where the numbers come from

One call. `crm_dashboard(30, 365)` on the Python side reads the database
once and hands back every list on the screen together — occasions, dues,
plans, follow-ups, orders, lapsed, top customers and the counters. Seven
separate queries firing on a timer would be seven chances to show a screen
that half agrees with itself.

The arithmetic is not in the page. It is in `core/crm.py`, which takes
today's date as an argument rather than reading the clock, so the same
input always gives the same answer and it can be tested. That module works
out days to a birthday (including 29 February), which age bucket a debt
falls in, whether a savings plan is behind, and which segment a customer is
in.

---

## WhatsApp

### Why it asks you to press send

WhatsApp has no way for a desktop program to send a message on its own
without a paid Business API account. Anything that automates the normal app
gets the shop's number banned, usually within a day or two of the first
bulk run. So AurumOS does the work up to the last step: it picks the
customer, writes the message, opens the chat with the text already in it.
You read it and press send.

### The one-tap reminders

Some rows have a green button, because the row already knows everything the
message needs:

- **Wish** on a birthday or anniversary, but only on the day, and only if
  there is a phone number.
- **Remind** on a debt, which quotes the amount.
- **Ready** on an order, which names the piece.
- **Remind** on a savings plan that has slipped, which quotes the
  instalment and the date it was due.

### Announce, for many customers

The **Announce** tab sends one message to a group: a new arrival, a shop
announcement, or a festival greeting. Choose who — everyone, VIPs, regulars,
or those who have gone quiet — and press *Prepare messages*.

That does not blast anything. It builds a **queue** and shows you one
customer at a time with the message written out. You send it or you skip
it, and it moves on. The pace is deliberate: roughly 40 an hour with a gap
between, which is the difference between a shop talking to its customers
and a number that looks like a spam source.

Anyone marked opted out is dropped from the queue before you see them, and
anyone already sent this kind of message recently is skipped, so nobody
gets the same announcement twice.

Every send is written to a log, so you can see what went to whom.

---

## Photographs of stock

### Putting a picture on a piece

**Send piece** tab, choose the piece, press **Add photo**. A normal Windows
file picker opens. The picture is copied into the shop's own photo folder
and renamed after the tag, so it is `T-1041.jpg` rather than `IMG_0001.jpg`
and still means something in a year. JPEG, PNG and WebP up to 8 MB.

### Sending it

Press **Send**. Three things happen at once:

1. A caption is written from the stock record — the name, the karat, the
   weight, the price, plus anything you typed in *Anything to add*. It is
   deliberately a retail caption: no touch, no fine weight, none of the
   wholesale arithmetic a customer should not see. Rupees are grouped the
   Indian way, so 2,85,000 and not 285,000.
2. The picture is put on the **clipboard as a file**, the same as if you had
   copied it in Explorer.
3. WhatsApp opens on that customer's chat with the caption already typed.

Press **Ctrl+V** and the picture attaches under the caption. Then send.

That paste step is not laziness. A `wa.me` link can carry text and nothing
else — no image, ever. The clipboard is the only way to get a picture into
that chat without an API account.

If the copy fails for any reason, the screen says so and offers to show you
the file so you can drag it in instead.

---

## When a card says something is wrong

| What it says | What it means |
|---|---|
| connecting, cards on Loading | Normal, for about a second after opening. The screen is waiting to be handed the program. |
| Waiting for the app to restart | The screen updated but AurumOS is still running the older program underneath. Close it and open it again. |
| Not connected | The screen never reached AurumOS at all. Close the program and open it again. |
| Could not load, with a reason | The database refused the request. The reason is the database's own. |
| Nobody to call, Nothing outstanding, and so on | Genuinely empty. Nothing is wrong. |

The first line matters. pywebview hands a page its connection to the program
a moment **after** the page has been drawn, so a screen that asks for data on
its first line always finds nothing there. This one waits to be handed the
connection before it decides anything is wrong.

The first one is the common one, and it is worth understanding why.
Screen files update themselves when you push. The Python underneath is
frozen inside `AurumOS.exe` and only changes when the program is rebuilt
and restarted. So a new screen can arrive and find an old program behind
it. Closing and reopening AurumOS is the whole fix.

---

## What updates on its own, and what does not

| Changed | Reaches the shop |
|---|---|
| Anything under `ui/` | On the next update check. No rebuild. |
| `main.py`, `core/`, `database/` | Only in a rebuilt `AurumOS.exe`. |

This is why a screen can be ahead of the program, and never the other way
round.
