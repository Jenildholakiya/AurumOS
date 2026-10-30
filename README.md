# AurumOS Client

Jewellery retail + wholesale management for Windows. Billing, inventory with tag printing,
karigar job-work, hallmarking lots, CRM with savings schemes, dead stock, hallmark centre
portal, multi-PC shop networking, and Bastion security monitoring — served as local HTML
screens through pywebview, backed by SQLite.

> AurumOS is built and maintained by **Pentarch**.

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
`sign_config.json` points at a certificate (see `config/sign_config.json.example`).

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
copy `config/config.example.json` to `config.json` in the project root to start. Environment overrides:

| Variable | Purpose |
|---|---|
| `AURUM_DB_PATH` / `AURUM_DB_DIR` | database location |
| `AURUM_ALERT_EMAIL_FROM` / `AURUM_ALERT_EMAIL_PASSWORD` / `AURUM_ALERT_EMAIL_TO` | Bastion alert mailbox (Gmail app password). Unset = alerts queue locally |
| `BASTION_PDF_PW` / `BASTION_PDF_OWNER_PW` | forensic PDF passwords (default: derived per-machine) |
| `AURUM_HEALTH_URL` / `AURUM_ADMIN_SECRET` / `AURUM_CLIENT_ID` | health dashboard push |
| `AURUM_SHIP_BACKEND` | list backend `.py` in `version.json` (source-run copies only) |
| `AURUM_FORCE` | mark a release as version-reset |

## Layout

```
AurumOS_Client/
├── main.py                  pywebview bridge (AurumAPI) and app entry point
├── build.py                 one-command EXE build
├── updater.py               over-the-air update client
├── subscription_manager.py  plan / licence checks
├── sse_listener.py          live licence events
├── AurumOS.ico
│
├── core/          pure rules: permissions, cluster API, CRM, GST, hallmark, WhatsApp
├── database/      db_manager.py (schema + ensure_* migrations), bastion_ai.py, cluster_store.py
├── network/       cluster server/client, LAN scan, hallmark share
├── ui/            screens, shipped over the air (path is part of the update manifest)
├── migrations/    NNNN_what.sql, shipped over the air
├── assets/  fonts/
│
├── config/        config.example.json, sign_config.json.example
├── scripts/
│   ├── release/   release_plan.py, gen_version_manifest.py, stage_release_assets.py (CI)
│   ├── dev/       run_dev.bat, seed_demo_stock.py, dev_connect_staff.py, diag_keys.py
│   └── admin/     generate_unlock_keys.py
├── tools/         PowerShell helpers copied to dist\tools\ (firewall, unblock files, USB prep)
├── docs/          setup guide, update / CRM / hallmark notes
└── .github/workflows/
```

Run from the project root: `scripts\dev\run_dev.bat` for a dev instance next to an
installed copy, `python scripts\dev\seed_demo_stock.py` for demo stock.

Generated and per-machine files are git-ignored: `config.json`, `db_path.txt`,
`AurumOS.spec`, `hook_*.py` / `rthook_*.py`, `version_info.txt`, `build/`, `dist/`,
`dist-client/`, `exports/`.

`core/tag_engine.py` ships obfuscated (source lost to an unrestored build); its interface
was re-mapped from bytecode, and its hash is pinned in `core/tag_engine.sha256` and
verified at startup.
