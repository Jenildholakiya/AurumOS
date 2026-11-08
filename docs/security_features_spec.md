# AurumOS — Security Features Specification

> Complete technical reference for every security layer implemented in the
> AurumOS desktop client, covering hardware binding, licensing, encryption,
> access control, network hardening, anti-tampering, and the Bastion AI engine.

---

## Table of Contents

1.  [Architecture Overview](#1-architecture-overview)
2.  [Hardware Identity & Machine Binding](#2-hardware-identity--machine-binding)
3.  [License Activation & Geo-Fencing](#3-license-activation--geo-fencing)
4.  [Auto-Revocation Engine](#4-auto-revocation-engine)
5.  [Software Transfer (PC-to-PC Migration)](#5-software-transfer-pc-to-pc-migration)
6.  [Encryption Systems](#6-encryption-systems)
7.  [Config Encryption & Secrets Management](#7-config-encryption--secrets-management)
8.  [Authentication & Session Security](#8-authentication--session-security)
9.  [Progressive Lockout](#9-progressive-lockout)
10. [TOTP Two-Factor Authentication](#10-totp-two-factor-authentication)
11. [Role-Based Access Control (RBAC)](#11-role-based-access-control-rbac)
12. [Security Lock & Unlock System](#12-security-lock--unlock-system)
13. [Bastion AI Engine](#13-bastion-ai-engine)
14. [Bastion Sync & Remote Commands](#14-bastion-sync--remote-commands)
15. [Anti-Tampering & Integrity Verification](#15-anti-tampering--integrity-verification)
16. [IO Safety & Atomic File Operations](#16-io-safety--atomic-file-operations)
17. [Backup & Recovery Vault](#17-backup--recovery-vault)
18. [Network & TLS Security](#18-network--tls-security)
19. [Windows Firewall Integration](#19-windows-firewall-integration)
20. [Cluster Security (Multi-Terminal)](#20-cluster-security-multi-terminal)
21. [IP Guard & Rate Limiting](#21-ip-guard--rate-limiting)
22. [Build-Time Security](#22-build-time-security)
23. [Natural-Language Report Whitelist](#23-natural-language-report-whitelist)
24. [Feature Tier Gating](#24-feature-tier-gating)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                     AurumOS Security Layers                         │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │  Hardware ID │  │  License &  │  │  Encryption  │  │  Network  │ │
│  │  & Machine  │  │  Geo-Fence  │  │  AES-256-GCM │  │  TLS/SSL  │ │
│  │  Binding    │  │  + Revoke   │  │  + XOR keys  │  │  + Firewall│ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘  └─────┬─────┘ │
│         │                │                │                 │       │
│         ▼                ▼                ▼                 ▼       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Bastion AI Engine                           │ │
│  │  60s monitoring cycle · escalation tiers · self-learning DB   │ │
│  │  session guard · DB integrity · sensitive-table watch         │ │
│  └────────────────────────┬──────────────────────────────────────┘ │
│                           │                                        │
│         ┌─────────────────┼─────────────────┐                      │
│         ▼                 ▼                 ▼                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐               │
│  │  Auth &     │  │  RBAC &     │  │  Anti-Tamper │               │
│  │  Session    │  │  Permissions│  │  & Integrity │               │
│  │  + Lockout  │  │  3-Layer    │  │  EXE hash +  │               │
│  │  + TOTP 2FA │  │  + Guard    │  │  Manifests   │               │
│  └─────────────┘  └─────────────┘  └──────────────┘               │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐               │
│  │  IO Safety  │  │  Backup     │  │  Cluster     │               │
│  │  Atomic Ops │  │  Vault AES  │  │  Multi-Term  │               │
│  │  + Protected│  │  + Recovery │  │  + Secrets   │               │
│  └─────────────┘  └─────────────┘  └──────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
```

### Defence-in-Depth Principle

Every request passes through multiple independent checks before it reaches
business logic. A compromised single layer cannot grant access — the remaining
layers still block unauthorised use.

---

## 2. Hardware Identity & Machine Binding

### 2.1 Hardware Fingerprint

**Code path:** `core/machine_id.py → get_machine_fingerprint()`

The client computes a **5-component hardware fingerprint** hashed with SHA-256:

| Component            | Source                        |
|----------------------|-------------------------------|
| Motherboard serial   | WMI `Win32_BaseBoard`         |
| CPU ID               | WMI `Win32_Processor`         |
| Disk serial          | WMI `Win32_DiskDrive`         |
| BIOS serial          | WMI `Win32_BIOS`              |
| MAC address          | `uuid.getnode()`              |

```
fingerprint = SHA256( mobo + cpu + disk + bios + mac )
```

### 2.2 Machine ID

**Code path:** `core/machine_id.py → get_machine_id()`

A secondary identifier derived from `uuid.getnode()` (MAC-based). Used for:
- E2E key derivation for encrypted temp passwords
- License binding checks against admin API
- Quick machine identification in logs

### 2.3 Hardware DNA Check

**Code path:** `database/aurum_health.py → check_hardware_dna()`

On every startup the client compares the current fingerprint against the
stored fingerprint in the database. A mismatch triggers:

```
  Startup
    │
    ▼
  check_hardware_dna()
    │
    ├── match ──► normal boot
    │
    └── mismatch
          │
          ├── check_whitelist() ──► whitelisted? ──► allow
          │
          └── not whitelisted ──► AUTO-FORMAT WIPE
                                   database wiped to blank state
                                   forces re-activation
```

### 2.4 Machine Whitelist

**Code path:** `database/aurum_health.py → check_whitelist()`

Admin API maintains a whitelist of allowed machines. A whitelisted machine
bypasses the hardware lock, enabling controlled migrations without data wipe.

---

## 3. License Activation & Geo-Fencing

### 3.1 Activation Flow

**Code path:** `main.py → license check on startup`, `subscription_manager.py`

```
  App start
    │
    ▼
  Read license key from encrypted store
    │
    ├── key absent ──► show activation screen
    │
    └── key present
          │
          ▼
        decrypt_license_key()        ← XOR cipher
          │
          ▼
        Validate with admin API (SSL/TLS verified)
          │
          ├── valid ──► check geo-fence ──► boot
          │
          └── invalid / expired ──► block + show reactivation UI
```

### 3.2 Geo-Fence

**Code path:** `core/geo_fence.py`

Each license is bound to a geographic region. On activation and periodically
thereafter, the client's IP-derived location is compared against the licensed
region. A mismatch triggers:
- Immediate revocation flag sent to admin API
- `country_mismatch` rule in auto-revocation engine
- Client displays geo-fence violation notice

### 3.3 Trial Management

**Code path:** `core/trial_manager.py`

Trial licenses have:
- Fixed duration (configurable per plan)
- Full feature access during trial period
- Automatic transition to expired state
- No geo-fence bypass during trial

### 3.4 SSE Real-Time Sync

**Code path:** `sse_listener.py`, `ui/sse_stream.js`

Server-Sent Events stream provides real-time license status updates:
- Subscription changes propagate instantly
- Remote deactivation commands delivered in < 1 second
- Plan upgrades/downgrades reflected without restart
- Connection heartbeat keeps sync alive

---

## 4. Auto-Revocation Engine

### 4.1 Rule Evaluation

**Code path:** `core/auto_revoke.py → evaluate()` (line ~459)

The engine runs a set of heuristic rules against every activation and
periodically against active sessions:

| Rule                 | Trigger                                           |
|----------------------|---------------------------------------------------|
| `rapid_activation`   | Multiple activations within short window          |
| `vpn_detection`      | Known VPN/proxy IP detected                       |
| `country_mismatch`   | IP country ≠ licensed country                     |
| `after_hours`        | Activation outside business hours (configurable)  |
| `machine_count`      | Too many distinct machines for license tier        |

### 4.2 Revocation Execution

**Code path:** `core/auto_revoke.py → _do_revoke()` (lines 522–555)

When a rule fires:
1. License status set to `revoked` locally
2. Revocation event sent to admin API
3. Cached revocation state stored in `.auto_revoke_cache.json`
4. Client displays revocation notice with reason code
5. Business data remains intact — only license is invalidated

```
  evaluate()
    │
    ▼
  for rule in RULES:
    rule.check(context)
      │
      ├── pass ──► next rule
      │
      └── fail ──► _do_revoke(reason=rule.name)
                     │
                     ├── set local status = REVOKED
                     ├── POST to admin API
                     ├── cache result
                     └── lock UI
```

---

## 5. Software Transfer (PC-to-PC Migration)

### 5.1 Transfer Flow

AurumOS uses a **deactivate-then-reactivate** model — there is no explicit
`move_license()` function. The process:

```
  OLD PC                                NEW PC
  ──────                                ──────
  1. Deactivate license                 
     (admin panel or auto-revoke)       
        │                               
        ▼                               
  2. License freed on server            
                                        3. Install AurumOS
                                           │
                                        4. Enter license key
                                           │
                                        5. Activate
                                           │
                                           ▼
                                        6. Server checks:
                                           - key valid?
                                           - previous deactivated?
                                           - rapid_activation rule?
                                           │
                                           ├── pass ──► activated
                                           │
                                           └── fail ──► blocked
                                                        (too fast / suspicious)
```

### 5.2 Database Transfer Protection

**Code path:** `database/db_manager.py` (line ~4119)

If a database file is physically copied to a new machine:

1. On startup, `check_hardware_dna()` detects fingerprint mismatch
2. If machine is **not** whitelisted → **auto-format wipe**
3. Database is reset to blank state
4. All business data is erased — prevents unauthorized data extraction
5. User must reactivate from scratch

This is the primary defence against simply copying the `.db` file to
steal business data.

### 5.3 Rapid-Activation Detection

When a license is deactivated and reactivated within a short window, the
`rapid_activation` rule fires a warning. If the pattern repeats, the license
is auto-revoked until admin review. This prevents:
- License sharing between multiple users
- Automated activation scripts
- Key-gen abuse

### 5.4 Controlled Migration (Whitelisted)

For legitimate transfers (e.g., hardware upgrade), an admin can:
1. Add the new machine's fingerprint to the whitelist
2. User installs and activates on new PC
3. Hardware DNA check passes via whitelist
4. No data wipe occurs — clean migration

---

## 6. Encryption Systems

### 6.1 AES-256-GCM (Primary)

**Used for:** Backups, recovery vaults, remote config, temp passwords, secure notes

**Code path:** `core/auto_backup.py`, `core/remote_config.py`,
`scripts/admin/secure_note.py`

```
  plaintext
    │
    ▼
  scrypt(password, salt, N=2^14, r=8, p=1) ──► 256-bit key
    │
    ▼
  AES-256-GCM(key, random_nonce)
    │
    ▼
  ciphertext + auth_tag + salt + nonce  ──► stored
```

Key derivation uses `scrypt` with high work factor. Every encryption
operation generates a fresh random nonce — no nonce reuse.

### 6.2 XOR Cipher (License Key Obfuscation)

**Code path:** `main.py → encrypt_license_key() / decrypt_license_key()`
(lines 514–584)

License keys stored on disk are obfuscated with an XOR cipher derived from
the machine ID. This ties the stored key to the hardware — copying the
key file to another machine produces garbage on decryption.

### 6.3 E2E Encrypted Temp Passwords

**Code path:** `main.py`, compatible with Web Crypto API

Temporary passwords shared between desktop and web portal use E2E encryption:
- Key derived from machine ID
- Compatible with browser Web Crypto API `SubtleCrypto`
- Server never sees plaintext password
- Time-limited validity

### 6.4 PDF Encryption

**Code path:** `database/bastion_report.py`, uses `pikepdf`

Security reports exported as PDF are encrypted with a password from the
`BASTION_PDF_PW` environment variable. Both owner and user passwords are set.

### 6.5 PyInstaller Build Cipher

**Code path:** `build.py → block_cipher`

The frozen EXE uses a PyInstaller block cipher to encrypt Python bytecode
within the bundle, adding a layer against casual reverse engineering.

---

## 7. Config Encryption & Secrets Management

### 7.1 Remote Config

**Code path:** `core/remote_config.py`

Remote configuration payloads are encrypted with:
- **HMAC-SHA256** key derivation from `CONFIG_ENCRYPTION_SECRET`
- **AES-256-GCM** for payload encryption
- Secret loaded from `.env` file (never hardcoded)

```
  Admin API
    │
    ▼
  encrypted config blob
    │
    ▼
  HMAC-SHA256(CONFIG_ENCRYPTION_SECRET) ──► derived key
    │
    ▼
  AES-GCM-decrypt(derived_key, blob) ──► config JSON
```

### 7.2 Environment Variable Secrets

All sensitive values are loaded from environment variables or `.env` file:

| Variable                    | Purpose                              |
|-----------------------------|--------------------------------------|
| `CONFIG_ENCRYPTION_SECRET`  | Remote config decryption             |
| `AURUM_BACKUP_PW`          | Backup vault encryption password     |
| `AURUM_ALERT_EMAIL_FROM`   | Alert email sender address           |
| `AURUM_ALERT_EMAIL_PASSWORD`| Alert email SMTP password           |
| `AURUM_GIST_ID`            | GitHub Gist for version manifest     |
| `AURUM_GIST_TOKEN`         | GitHub API token                     |
| `AURUM_HEALTH_URL`         | Health reporting endpoint            |
| `AURUM_ADMIN_SECRET`       | Admin API authentication             |
| `AURUM_CLIENT_ID`          | Client identification                |
| `BASTION_PDF_PW`           | PDF report encryption password       |

The `.env` file is in `.gitignore` — secrets never enter source control.

---

## 8. Authentication & Session Security

### 8.1 Login Flow

**Code path:** `main.py → login methods`

```
  User enters credentials
    │
    ▼
  Check lockout tier (progressive lockout table)
    │
    ├── locked ──► display lockout message + remaining time
    │
    └── not locked
          │
          ▼
        Verify PIN: bcrypt(salt + entered_pin) == stored_hash
          │
          ├── fail ──► increment attempt counter
          │            check next lockout tier
          │
          └── pass
                │
                ▼
              Check role
                │
                ├── owner/admin ──► TOTP gate (if enabled)
                │                     │
                │                     ├── valid code ──► session created
                │                     └── invalid ──► deny
                │
                └── staff ──► session created directly
```

### 8.2 Session Tokens

**Code path:** `hm_share.py` (portal sessions)

| Property             | Value                          |
|----------------------|--------------------------------|
| Token size           | 256 bits (random)              |
| Storage              | Database-backed                |
| TTL                  | 12 hours                       |
| Cookie flags         | `HttpOnly`, `Secure`, `SameSite` |
| PIN storage          | `bcrypt(salt + pin)`           |

### 8.3 Portal Tokens

**Code path:** `hm_share.py`

For web portal access:
- 30-day TTL
- Tied to specific machine
- Revocable from admin panel
- Separate from desktop session tokens

---

## 9. Progressive Lockout

**Code path:** `main.py` (lines ~3662–3755)

Failed login attempts trigger escalating lockout durations:

| Tier | Attempt Range | Lockout Duration     |
|------|--------------|----------------------|
| 1    | 3            | 30 seconds           |
| 2    | 4            | 1 minute             |
| 3    | 5            | 5 minutes            |
| 4    | 6–7          | 15 minutes           |
| 5    | 8–9          | 1 hour               |
| 6    | 10+          | **Permanent lock**   |

Tier 6 (permanent lock) requires admin intervention via
`admin_lockout_reset` — an `OWNER_ONLY` permission.

```
  Login attempt fails
    │
    ▼
  attempts += 1
    │
    ▼
  for tier in LOCKOUT_TIERS:
    if attempts >= tier.threshold:
      lock_until = now + tier.duration
      │
      ├── tier < 6 ──► temporary lock, show countdown
      │
      └── tier == 6 ──► PERMANENT LOCK
                         only admin_lockout_reset() can clear
```

---

## 10. TOTP Two-Factor Authentication

**Code path:** `main.py` (lines ~3390–3562)

### 10.1 Setup

- TOTP shared secret generated and stored encrypted
- QR code displayed for authenticator app pairing
- Backup codes generated (hashed before storage, one-time use)

### 10.2 Verification Gate

**Code path:** `main.py` (lines 3713–3721)

Applied to `owner` and `admin` roles after successful PIN entry:

```
  PIN verified
    │
    ▼
  role in (OWNER, ADMIN)?
    │
    ├── no ──► session granted
    │
    └── yes
          │
          ▼
        TOTP enabled for this user?
          │
          ├── no ──► session granted
          │
          └── yes ──► prompt TOTP code
                        │
                        ├── valid ──► session granted
                        ├── backup code ──► session granted
                        │                   (code consumed)
                        └── invalid ──► deny
```

### 10.3 TOTP Management

Owner-only operations (via RBAC):
- `totp_setup` — enable 2FA for a user
- `totp_disable` — remove 2FA
- `totp_verify` — validate a code
- `totp_backup_codes` — regenerate backup codes

---

## 11. Role-Based Access Control (RBAC)

### 11.1 Three-Layer Permission Model

**Code path:** `core/permissions.py → is_allowed()` (line ~354)

```
  API method call
    │
    ▼
  Layer 0: PRIVATE / INTERNAL?
    ├── yes ──► DENY (never exposed)
    │
    └── no
          │
          ▼
        Layer 1: OWNER_ONLY?
          ├── yes ──► caller is owner? ──► ALLOW / DENY
          │
          └── no
                │
                ▼
              Layer 2: ALWAYS (any session holder)
                ├── in ALWAYS set? ──► ALLOW
                │
                └── no ──► check BUNDLES (module-gated)
                             │
                             ├── plan includes module? ──► ALLOW
                             │
                             └── DENY
```

### 11.2 Owner-Only Methods

**Code path:** `core/permissions.py → OWNER_ONLY` frozenset

Critical operations restricted to the business owner:

- `bastion_unlock` — lift Bastion AI suspension
- `admin_lockout_reset` — clear permanent lockout
- `change_owner_password` — owner credential change
- `totp_setup` / `totp_disable` — 2FA management
- `security_lock` / `security_unlock` — system lock control
- `delete_staff` — remove staff accounts
- `view_audit_log` — security audit trail

### 11.3 Method Guard (install_guard)

**Code path:** `core/permissions.py → install_guard()` (lines 383–409)

A thread-local guard that intercepts every pywebview API method call:

```python
# Simplified flow
def install_guard():
    for method in api_methods:
        original = getattr(api, method)
        @wraps(original)
        def guarded(*args, **kwargs):
            if not is_allowed(current_session, method):
                raise PermissionDenied(method)
            return original(*args, **kwargs)
        setattr(api, method, guarded)
```

### 11.4 Seat Limits Per Plan

**Code path:** `core/permissions.py`

| Plan       | Default Seats | Max Seats |
|------------|--------------|-----------|
| Lite       | 0            | 50        |
| Pro        | 1            | 50        |
| Enterprise | 4            | 50        |

### 11.5 Frontend Role Enforcement

**Code path:** `ui/sidebar.js → _getRole(), _verifySessionAuthority()`

The sidebar filters navigation items based on the logged-in user's role.
`_SESSION_FREE_PAGES` defines pages accessible without an active session.
This is a UI-only layer — backend RBAC is the authoritative check.

---

## 12. Security Lock & Unlock System

### 12.1 Two-Tier Locking

**Code path:** `core/security_lock.py`

| Tier     | Code Length | Purpose                         |
|----------|------------|----------------------------------|
| Regular  | 12 chars   | Standard login lock              |
| Bastion  | 16 chars   | AI suspension lock               |

### 12.2 Nonce-Based Replay Prevention

**Code path:** `database/db_manager.py` (lines 6253–6336)

Each unlock attempt includes a server-generated nonce:

```
  Unlock request
    │
    ▼
  Verify nonce (one-time, time-limited)
    │
    ├── invalid/expired nonce ──► REJECT
    │
    └── valid
          │
          ▼
        Verify unlock code
          │
          ├── correct ──► consume nonce + unlock
          │
          └── wrong ──► consume nonce + deny
                         (nonce can't be reused)
```

This prevents replay attacks where an intercepted unlock request is
re-sent.

### 12.3 Admin Key Generation

**Code path:** `scripts/admin/generate_unlock_keys.py`

Offline script for generating unlock codes. Keys are never transmitted
over the network — they are generated locally and communicated
out-of-band to authorised personnel.

---

## 13. Bastion AI Engine

### 13.1 Overview

**Code path:** `database/bastion_ai.py`

The Bastion AI is an autonomous security monitor that runs a continuous
60-second cycle, checking for anomalies and threats across multiple
dimensions.

```
  ┌────────────────────────────────────────────────────┐
  │              Bastion AI — 60s Cycle                 │
  │                                                    │
  │  ┌──────────┐  ┌──────────┐  ┌─────────────────┐  │
  │  │ Session  │  │ DB       │  │ Sensitive Table │  │
  │  │ Guard    │  │ Integrity│  │ Monitor         │  │
  │  └────┬─────┘  └────┬─────┘  └───────┬─────────┘  │
  │       │              │                │            │
  │       ▼              ▼                ▼            │
  │  ┌──────────────────────────────────────────────┐  │
  │  │           Threat Scoring Engine              │  │
  │  │     _decide() — lines 972–1014              │  │
  │  └──────────────────┬───────────────────────────┘  │
  │                     │                              │
  │           ┌─────────┼─────────┐                    │
  │           ▼         ▼         ▼                    │
  │        WARN     RESTRICT   SUSPEND                 │
  │       (log)    (limit)    (lock out)               │
  │                                                    │
  │  ┌──────────────────────────────────────────────┐  │
  │  │        Self-Learning (line ~743)             │  │
  │  │   bastion_learning table stores patterns     │  │
  │  │   to reduce false positives over time        │  │
  │  └──────────────────────────────────────────────┘  │
  └────────────────────────────────────────────────────┘
```

### 13.2 Monitoring Checks

| Check                    | What It Detects                              |
|--------------------------|----------------------------------------------|
| Session Guard            | Expired / tampered / missing sessions        |
| DB Integrity             | `PRAGMA integrity_check` failure             |
| Sensitive Table Monitor  | Unauthorized changes to critical tables      |
| File Integrity           | Modified EXE or config files                 |
| Behaviour Anomalies      | Unusual access patterns (learned over time)  |

**Sensitive table monitoring:** lines 368–501 in `bastion_ai.py` — tracks
row counts and checksums on security-critical tables. A sudden change
(e.g., permissions table modified outside normal flow) triggers an alert.

### 13.3 Escalation Engine

**Code path:** `database/bastion_ai.py → _decide()` (lines 972–1014)

Threats escalate through severity levels:

```
  Threat detected
    │
    ▼
  severity == CRITICAL?
    ├── yes ──► IMMEDIATE SUSPEND (skip escalation)
    │
    └── no ──► escalation ladder:
                │
                WARN ──(repeated)──► RESTRICT ──(repeated)──► SUSPEND
                                                                │
                                                   SCORE_SUSPEND = 75
                                                   SUSPEND_LEVEL = 3
```

### 13.4 Auto-Suspend

**Code path:** `database/bastion_ai.py → _auto_suspend()` (lines 1087–1248)

When the threat score reaches `SCORE_SUSPEND` (75) or `SUSPEND_LEVEL` (3):

1. All active sessions terminated
2. UI replaced with Bastion lock screen (`ui/bastion_guard.js`)
3. 16-character Bastion unlock code required to resume
4. Admin notification sent
5. Event logged to bastion audit trail

### 13.5 Self-Learning

**Code path:** `database/bastion_ai.py` (line ~743)

The `bastion_learning` table stores:
- Known-good patterns (to suppress false positives)
- Confirmed threat signatures (to detect repeats faster)
- Time-weighted decay (old patterns lose influence)

### 13.6 Frontend Lock Screen

**Code path:** `ui/bastion_guard.js`

When Bastion suspends the system:
- Full-page overlay blocks all interaction
- Attack type displayed with human-readable description
- Only the Bastion unlock input is accessible
- No way to dismiss without the 16-char code

---

## 14. Bastion Sync & Remote Commands

### 14.1 Sync Polling

**Code path:** `core/bastion_sync.py`

The sync module polls the admin API every **30 seconds** for:
- Remote suspend/unsuspend commands
- Configuration updates
- Threat intelligence feeds
- License status changes

```
  Every 30s
    │
    ▼
  Poll admin API /bastion/sync
    │
    ▼
  Process commands:
    ├── SUSPEND ──► bastion_suspend()
    ├── UNSUSPEND ──► bastion_unsuspend()
    ├── CONFIG_UPDATE ──► apply remote config
    └── NOOP ──► sleep 30s
```

### 14.2 Admin Key Verification

**Code path:** `database/aurum_health.py` (lines 420–500)

Bastion admin commands require a key verified against the
`AURUM_ADMIN_SECRET` environment variable. Commands without a valid
key are silently dropped.

### 14.3 Bastion Security Reports

**Code path:** `database/bastion_report.py`

PDF reports generated on demand or on schedule:
- Password-encrypted (AES via pikepdf)
- Contains threat history, escalation events, learning data
- Exportable for compliance audits

---

## 15. Anti-Tampering & Integrity Verification

### 15.1 EXE Hash Verification

**Code path:** `main.py` (lines 1024–1035), `build.py → write_trusted_hash()`
(line 1137)

At build time, a SHA-256 hash of the final EXE is written to
`exe_trusted_hash.txt`. On startup:

```
  App start
    │
    ▼
  Read exe_trusted_hash.txt
    │
    ▼
  SHA256(current_exe) == trusted_hash?
    │
    ├── match ──► continue boot
    │
    └── mismatch ──► integrity violation
                      │
                      ├── log event
                      ├── notify Bastion AI
                      └── may trigger SUSPEND
```

### 15.2 Update Manifest Verification

**Code path:** `updater.py → verify_manifest_checksum()` (line ~260)

Every update file is verified against SHA-256 checksums in the manifest:

```
  Download update files
    │
    ▼
  For each file in manifest:
    SHA256(downloaded_file) == manifest.checksums[file]?
      │
      ├── all match ──► proceed with update
      │
      └── any mismatch ──► ABORT update
                            rollback to previous version
```

### 15.3 Protected Paths

**Code path:** `updater.py → PROTECTED_PATHS` (lines 48–49)

Certain files are excluded from updates to prevent overwriting local
configuration:
- User config files
- License data
- Local database

### 15.4 Database Integrity

**Code path:** `database/bastion_ai.py` (within 60s cycle)

SQLite `PRAGMA integrity_check` runs every Bastion cycle. A failure
indicates database corruption (accidental or malicious) and escalates
to a Bastion alert.

---

## 16. IO Safety & Atomic File Operations

### 16.1 Atomic Writes

**Code path:** `core/io_safety.py → atomic_write_bytes(), atomic_write_text(),
atomic_write_json()`

All critical file writes use a write-to-temp-then-rename pattern:

```
  Write request for target.json
    │
    ▼
  Write to target.json.tmp (same directory)
    │
    ▼
  fsync() — flush to disk
    │
    ▼
  os.replace(target.json.tmp, target.json)  ← atomic on Windows NTFS
    │
    ▼
  Success (or tmp file cleaned up on failure)
```

**Protected files using atomic writes:**
- `config.json` — application configuration
- License key files — activation data
- Flag files — feature/state flags

### 16.2 Why Atomic Writes Matter

A power failure or crash during a normal write can leave a file half-written
and unreadable. The atomic pattern ensures:
- The file is either fully old or fully new — never partial
- No data loss on crash
- No corruption from concurrent access

---

## 17. Backup & Recovery Vault

### 17.1 Silent Encrypted Backups

**Code path:** `core/auto_backup.py`

Automatic backups run every **5 minutes** in the background:

| Property          | Value                          |
|-------------------|--------------------------------|
| Interval          | 5 minutes                      |
| Encryption        | AES-256-GCM                    |
| Key derivation    | scrypt (password from env var) |
| File extension    | `.vault`                       |
| Password source   | `AURUM_BACKUP_PW` env var      |

### 17.2 Recovery Flow

**Code path:** `scripts/admin/restore_recovery.py`

```
  .vault file
    │
    ▼
  Read salt + nonce + ciphertext + tag
    │
    ▼
  scrypt(AURUM_BACKUP_PW, salt) ──► key
    │
    ▼
  AES-256-GCM-decrypt(key, nonce, ciphertext, tag)
    │
    ├── auth tag valid ──► restored database
    │
    └── auth tag invalid ──► REJECT (tampered or wrong password)
```

### 17.3 Bastion Auto-Healer

The Bastion AI monitors backup age. If backups stop being created
(indicating a possible attack on the backup system), Bastion triggers
a healing action to restart the backup process.

---

## 18. Network & TLS Security

### 18.1 SSL/TLS Configuration

**Code path:** `main.py → _ssl_context()` (lines 2119–2133)

| Connection Target       | Verification | Context                    |
|------------------------|--------------|----------------------------|
| Subscription manager   | **Verified** | Full cert chain validation |
| Admin API              | **Verified** | Full cert chain validation |
| SSE listener           | Fallback     | Best-effort TLS            |
| Remote config          | Fallback     | Best-effort TLS            |

The `subscription_manager.py` connection always uses a verified SSL context
because it handles license/payment data.

### 18.2 Authenticode Code Signing

**Code path:** `build.py` (lines 887–1098)

The distributed EXE is signed with an Authenticode certificate (OV or EV):
- Windows verifies the signature before execution
- SmartScreen reputation is established
- Tampered EXEs lose their valid signature
- Certificate pinning prevents impersonation

### 18.3 Cluster API Timing-Attack Prevention

**Code path:** `network/cluster_server.py`

The cluster API uses `hmac.compare_digest()` for secret comparison:
```python
hmac.compare_digest(provided_secret, expected_secret)
```
This is constant-time — an attacker cannot infer the correct secret
by measuring response times.

---

## 19. Windows Firewall Integration

### 19.1 Auto-Created Rules

**Code path:** `network/cluster_server.py → _ensure_firewall_rule()`
(line ~934)

When the cluster server starts, it automatically creates a Windows
Firewall inbound rule:

```
  Cluster server start (port 58903)
    │
    ▼
  _ensure_firewall_rule(58903)
    │
    ▼
  netsh advfirewall firewall add rule
    name="AurumOS Cluster"
    dir=in
    action=allow
    protocol=TCP
    localport=58903
```

The rule allows inbound TCP on the cluster port only. No outbound
rules are modified — the default Windows outbound policy applies.

---

## 20. Cluster Security (Multi-Terminal)

### 20.1 Terminal Secrets

**Code path:** `network/cluster_store.py`

Each terminal connection is authenticated with a secret generated via:
```python
secrets.token_hex(32)  # 256-bit random secret
```

### 20.2 Brute-Force Protection

**Code path:** `network/cluster_server.py`

| Parameter         | Value        |
|-------------------|-------------|
| `_MAX_ATTEMPTS`   | 5           |
| `_ATTEMPT_WINDOW` | 300 seconds |

After 5 failed authentication attempts within 5 minutes, the source
is blocked.

### 20.3 Lease-Based Authority

**Code path:** `network/cluster_server.py → LEASE_SECONDS = 300`

Staff terminal connections have a **5-minute lease**. After expiry,
the terminal must re-authenticate. This limits the damage window
if a terminal is left unattended.

### 20.4 Seat Enforcement

Cluster connections are capped at the plan's seat limit. Attempting
to connect beyond the limit is rejected at the server level, not just
the UI.

---

## 21. IP Guard & Rate Limiting

### 21.1 IP Guard

**Code path:** `core/ip_guard.py`

Monitors the client's public IP and enforces:
- Geo-fence compliance (cross-references with `geo_fence.py`)
- VPN/proxy detection
- IP change alerting
- Startup check + periodic re-check

### 21.2 Rate Limiting

API endpoints enforce per-IP rate limits to prevent:
- Brute-force login attempts
- Activation flooding
- API abuse

Rate limit state is checked at startup and refreshed periodically.

---

## 22. Build-Time Security

### 22.1 Trusted Hash Generation

**Code path:** `build.py → write_trusted_hash()` (line 1137)

After the EXE is compiled and signed, the build script:
1. Computes `SHA256(final.exe)`
2. Writes hash to `exe_trusted_hash.txt`
3. Bundles the hash file with the distribution

### 22.2 Payload Encryption

**Code path:** `build.py → _encrypt_payload()` (lines 729–793)

Sensitive build artifacts are encrypted before bundling to prevent
extraction of embedded secrets from the EXE.

### 22.3 Manifest Generation

**Code path:** `scripts/release/gen_version_manifest.py`

Release manifests include per-file SHA-256 checksums. The manifest
itself is signed and hosted on a trusted channel (GitHub Gist).

### 22.4 Authenticode Signing Pipeline

**Code path:** `build.py` (lines 887–1098)

```
  PyInstaller build
    │
    ▼
  EXE produced
    │
    ▼
  signtool sign /fd SHA256 /tr timestamp_url /td SHA256
    │
    ▼
  Signed EXE
    │
    ▼
  write_trusted_hash() ──► exe_trusted_hash.txt
    │
    ▼
  gen_version_manifest.py ──► version manifest with checksums
```

---

## 23. Natural-Language Report Whitelist

### 23.1 Query Restriction

**Code path:** `core/nl_report.py`

The natural-language report feature (AI-powered) restricts queries to
a **whitelist of table/column expressions**. This prevents:

- SQL injection via crafted natural-language prompts
- Unauthorized access to sensitive tables (e.g., passwords, keys)
- Data exfiltration through clever prompt engineering

Only pre-approved table and column combinations can appear in generated
SQL — anything outside the whitelist is rejected before execution.

---

## 24. Feature Tier Gating

### 24.1 Plan-Based Feature Access

**Code path:** `ui/boot.js`

Security features are gated by subscription plan:

| Feature              | Lite | Pro | Enterprise |
|----------------------|------|-----|------------|
| Basic Bastion        | ✓    | ✓   | ✓          |
| Enhanced Bastion     | —    | ✓   | ✓          |
| Bastion AI (ML)      | —    | —   | ✓          |
| Fleet Bastion        | —    | —   | ✓          |
| Multi-terminal       | —    | ✓   | ✓          |
| TOTP 2FA             | ✓    | ✓   | ✓          |
| Encrypted backups    | ✓    | ✓   | ✓          |

Tier checks are enforced both in the frontend (boot.js feature flags)
and backend (RBAC bundle checks in `permissions.py`).

---

## Appendix A — Security Event Flow (Full Lifecycle)

```
  ┌─────────────────────────────────────────────────────────────┐
  │                        APP START                            │
  │                                                             │
  │  1. EXE integrity check (SHA-256 vs trusted hash)          │
  │  2. Hardware DNA check (fingerprint vs stored)             │
  │  3. License decryption + validation                        │
  │  4. Geo-fence check                                        │
  │  5. Auto-revocation rule evaluation                        │
  │  6. IP guard startup check                                 │
  │  7. Bastion AI engine start (60s cycle begins)             │
  │  8. Bastion sync start (30s polling begins)                │
  │  9. SSE listener connects                                  │
  │ 10. Auto-backup scheduler starts (5-min interval)          │
  │ 11. Login screen presented                                 │
  │                                                             │
  │                     RUNTIME                                 │
  │                                                             │
  │  • Every API call: RBAC guard (install_guard)              │
  │  • Every 60s: Bastion AI full cycle                        │
  │  • Every 30s: Bastion sync poll                            │
  │  • Every 5m: Silent encrypted backup                       │
  │  • On login: progressive lockout + optional TOTP           │
  │  • On file write: atomic IO safety                         │
  │  • On cluster connect: secret + lease + seat check         │
  │                                                             │
  │                     SHUTDOWN                                │
  │                                                             │
  │  • Final backup created                                    │
  │  • Sessions invalidated                                    │
  │  • Bastion state persisted                                 │
  └─────────────────────────────────────────────────────────────┘
```

---

*Document generated for AurumOS internal reference. Covers all security
features as of v2.0.11.*
