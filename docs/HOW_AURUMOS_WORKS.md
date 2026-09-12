# How AurumOS works

A brief for somebody who has to work on it, or explain it.

AurumOS is a Windows desktop program for a jewellery shop. It is one
executable, one database file, and a folder of screens. It runs on the
counter PC with no internet needed for anything a shop does in a day.

---

## The shape of it

Three layers, and every feature crosses all three.

| Layer | What it is | Where |
|---|---|---|
| The screens | 49 HTML pages with their own JavaScript. No framework, no build step. | `ui/` |
| The bridge | One Python object with 259 methods. The only way a screen reaches data. | `main.py` |
| The store | SQLite, 45 tables, one file. | `database/db_manager.py` |

The window is a Chromium view hosted by pywebview. A screen calls Python by
name, `window.pywebview.api.crm_dashboard(30, 365)`, and gets a promise
back. The screens are loaded from files on disk, not served, so the bridge
itself has no port and nothing outside the program can call into it. The one
listener in the software is the shop network server, described below, and it
only runs on the PC that serves.

Pure rules live in `core/` as plain functions with no database and no clock:
hallmarking, the CRM arithmetic, GST, WhatsApp, photographs, machine
identity. They take today's date as an argument. That is what makes the
calculations a shop argues about testable on their own.

---

## Starting up

`run_aur_os()` in `main.py`, in order:

1. **Open the database.** The file is chosen in this order: the
   `AURUM_DB_PATH` variable, then `db_path.txt` beside the executable, then
   `database/aurum_local.db` beside the executable. It runs in WAL mode so a
   read never waits for a write.
2. **Migrate it.** Every `ensure_*` routine runs on every launch: retail
   columns, CRM tables, hallmarking tables. Each is `IF NOT EXISTS` and adds
   columns only where missing, so an old shop's file grows into the new
   version without a migration step anyone has to run.
3. **Decide which screen opens.** Setup, login, or a licence screen. This is
   worth its own section, below.
4. **Open the window** and hand the bridge to it.
5. **Start the background work**: the shop network, the subscription check,
   the update check, Bastion.

### Which screen opens

`is_setup_done()` answers three questions and never destroys anything:

- Is there an account anybody can log in with?
- Did a setup run to completion?
- Is this the machine the shop was set up on?

The third is the one that used to go wrong. Identity is recorded with the
name of the reader that produced it, `machineguid:1a2b…`, and a later launch
re-checks that same reader. If that reader cannot answer, the verdict is
**unknown**, never **different**. Silence about hardware is a statement
about Windows, not about whose computer this is.

The readers, best first: MachineGuid from the registry, the computer system
product UUID, the baseboard serial, the BIOS serial, the disk volume serial,
and last of all the network card. An identity is traded up when a steadier
reader appears and never traded down.

Three outcomes, and none of them deletes a row: the setup wizard, the login,
or a flag saying the file came from another machine. See `core/machine_id.py`.

---

## Retail or wholesale

One program, two personalities, decided by the licence key prefix. `AR-`
means retail; anything else means wholesale.

Python reads it from the key. The screens read it from browser storage,
which **falls back to wholesale when the value is missing or unreadable**.
That fallback is why a menu entry has to exist in both navigation lists, not
just the one you think applies. Two features have been invisible for exactly
this reason.

Retail gets the point of sale, hallmarking, the CRM, savings plans. Wholesale
gets vouchers, the karigar workflow, the ledger and accounting. Both get
inventory, customers and settings.

---

## Who may do what

`core/permissions.py` wraps every bridge method on the class at startup and
checks the policy before any of them runs. Three tiers:

- **Owner only.** Never callable by a staff account, whatever else they hold.
- **Always.** Anything a logged-in person needs.
- **Bundles.** Modules an owner hands to a terminal: billing, inventory,
  karigar, history, customers, hallmarking.

A plan maps to bundles, and a staff login builds its menu from the plan
rather than the full list. A bundle belonging to no plan is a screen nobody
on staff can ever reach, which is now a test failure rather than a surprise.

---

## What a sale actually does

Worth following once, because most of the software is downstream of it.

1. The counter screen assembles the bill: items by tag, making charge, GST
   at the shop's rates, a discount, loyalty points redeemed, old gold taken
   in against it.
2. It sends one object to Python with the shop's own details attached, so a
   reprint years later shows what the shop was registered as then, not
   whatever a browser had cached.
3. Python writes `sales_history`, awards loyalty, and deducts the stock.
4. Deduction is by tag for a tagged piece, and by purity for bulk metal.
   The item code is matched against a real stock row first and only read as
   a purity if nothing matched, because the other order once made a piece
   coded "75" deduct from 75-touch bulk gold. It refuses and logs a
   shortfall rather than inventing stock.
5. The bill prints as a tax invoice or a plain one, from the same data.

---

## More than one PC

One shop PC serves; the rest are terminals. A terminal starts no server and
only ever calls out, so two machines can never both believe they are the
shop.

The server listens on port 58903 and speaks a small JSON protocol: enrol,
login, sync. `database/cluster_store.py` on the owner's PC records which
machines are allowed to connect. `network/lan_scan.py` shows every device on
the network, not only the ones that joined, so an owner can see a PC that
has not been enrolled yet.

Each terminal keeps its own database, so a router hiccup does not stop a
sale, and what it writes is merged back afterwards.

---

## Licence and subscription

The key identifies the shop and chooses retail or wholesale. A lock code is
cut from the machine fingerprint and is now **recorded once per install and
reused**, because a lock code that moves locks a paying customer out of
software they bought.

`subscription_manager.py` checks the plan against a server at startup and
every thirty minutes, five minutes when offline. It caches for 24 hours and
then degrades to the smallest plan rather than stopping work. Live plan
changes arrive over a server-sent event stream.

---

## Bastion

A self-monitoring layer that watches the program rather than the shop: file
integrity, session behaviour, a tamper check on the executable against a
recorded hash, and a learner that builds a picture of this shop's normal
usage. It queues alerts and sends them when there is internet. The tag
engine ships obfuscated as part of the same posture.

---

## Updates, and the one rule that matters

| Changed | Reaches a shop |
|---|---|
| Anything under `ui/` | On the next update check. No rebuild. |
| `main.py`, `core/`, `database/` | Only in a rebuilt executable. |

This is why a screen can be ahead of the program and never the other way
round, and why a new screen that needs a new bridge method shows "close and
reopen AurumOS" until the rebuild lands.

An update never overwrites the database, the logs, the config, the version
lock or the executable itself. Every push to `main` cuts a release; the
version number is the highest git tag plus one, stamped into the bundle at
build time rather than typed anywhere.

---

## Building

`build.py` runs PyInstaller in one-directory mode, so the result is
`AurumOS.exe` beside an `_internal` folder. It stamps a Windows version
resource, records a trusted hash so the tamper check has something to
compare against, and signs the executable if a certificate is configured.
Unsigned, a downloaded copy gets a SmartScreen warning; copied from a USB
stick it does not, because Windows only tags files that came from the
internet.

---

## Tests

Twenty-seven suites, run with plain `python test_*.py`. No framework.

Two kinds. The pure rules are tested directly against `core/`. The screens
are tested by pulling their JavaScript out of the HTML and running it
against a stub browser in node, so a test breaks when the page breaks rather
than when a selector is renamed. A few drive a real database in the system
temp directory. The shop's own file is never opened.

---

## Four things that have gone wrong more than once

Written down because each one cost a day.

**A menu entry in one list only.** The software type falls back to
wholesale, so an entry that lives only in the retail list disappears for
reasons unrelated to the licence.

**Asking for the bridge too early.** pywebview hands a page its connection a
moment after the page is drawn. A screen that asks on its first line always
finds nothing and reports the build as too old. `boot.js` knows how to wait;
use it.

**Treating a silent hardware reader as evidence.** It is not. It cost a shop
its books once already.

**Forgetting that only `ui/` auto-updates.** A feature that works on your
machine and not on a customer's is usually a rebuild that never happened.
