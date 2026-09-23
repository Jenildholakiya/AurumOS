# AurumOS Client

Jewellery retail + wholesale management for Windows. Billing, inventory with tag printing,
karigar job-work, hallmarking lots, CRM with savings schemes, dead stock, hallmark centre
portal, multi-PC shop networking, and Bastion security monitoring — served as local HTML
screens through pywebview, backed by SQLite.

Wholesale keys start `AU-`, retail keys start `AR-`.

## Run from source

Requires Python 3.12 on Windows.

```bat
pip install pywebview pywin32 pyserial cryptography reportlab pillow qrcode requests
python main.py
```

First launch asks where to keep the database, then walks through setup (licence key,
business profile, owner PIN).

## Build the EXE

```bat
python build.py
```

Output: `dist\AurumOS.exe` (+ `dist\_internal\`). The build stamps the highest `vX.Y.Z`
git tag into the version, obfuscates the frozen modules, writes
`dist\exe_trusted_hash.txt` (Bastion's tamper baseline), and signs when
`sign_config.json` points at a certificate (see `sign_config.json.example`).

## Tests

```bat
python test_permissions.py       # staff access matrix + bridge guard
python test_karigar.py           # karigar register end to end
python test_subscription_logic.py# licence reason handling, no fabricated days
python test_plan_tiers.py        # plan tiers agree across py/js/screens
python test_release_plan.py      # release planner rules
python test_ui_syntax.py         # every page script parses
python test_retail_inventory.py  # retail stock flows
python test_crm.py test_gst.py test_hallmark.py test_deadstock.py
```

CI (`.github/workflows/tests.yml`) runs these on every push.

## Updates

- `ui/*` ships over the air: downloaded screens take effect on next open.
- `migrations/*.sql` ships schema fixes the same way — each file runs once per
  shop via `DBManager.apply_migrations`, in filename order. New files must be
  named `NNNN_what.sql` and idempotent (`CREATE TABLE IF NOT EXISTS`, conditional
  `ALTER`). The live `database/` folder itself is never overwritten.
- `main.py`, `updater.py`, `core/`, `database/*.py`, `network/` are frozen into the
  EXE: changing them needs `python build.py` + reinstall.

## Configuration

`config.json` is per-machine (licence terminal secrets, LAN IPs) and never committed —
copy `config.example.json` to start. Environment overrides:

| Variable | Purpose |
|---|---|
| `AURUM_DB_PATH` / `AURUM_DB_DIR` | database location |
| `AURUM_ALERT_EMAIL_FROM` / `AURUM_ALERT_EMAIL_PASSWORD` / `AURUM_ALERT_EMAIL_TO` | Bastion alert mailbox (Gmail app password). Unset = alerts queue locally |
| `BASTION_PDF_PW` / `BASTION_PDF_OWNER_PW` | forensic PDF passwords (default: derived per-machine) |
| `AURUM_HEALTH_URL` / `AURUM_ADMIN_SECRET` / `AURUM_CLIENT_ID` | health dashboard push |
| `AURUM_SHIP_BACKEND` | list backend `.py` in `version.json` (source-run copies only) |
| `AURUM_FORCE` | mark a release as version-reset |

## Layout

- `main.py` — pywebview bridge (`AurumAPI`, ~280 methods)
- `ui/` — screens; `boot.js` injects `subscription.js`/`sse_stream.js`/`urgent.js` everywhere, `sidebar.js` loads `ai_support.js`
- `core/` — permissions matrix, cluster API, CRM rules, GST, hallmark, WhatsApp
- `core/tag_engine.py` — ships obfuscated (source lost to an unrestored build); interface re-mapped from bytecode, hash pinned in `core/tag_engine.sha256` and verified at startup
- `database/` — `db_manager.py` (schema + migrations), `bastion_ai.py`, `cluster_store.py`
- `network/` — cluster server/client, LAN scan, hallmark share
- `docs/` — setup guide, update/CRM/hallmark notes
