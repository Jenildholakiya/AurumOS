# AurumOS — DPDP Act 2023 Compliance Implementation

## Overview

This document describes how AurumOS addresses all requirements of the **Digital Personal Data Protection Act, 2023** (India). The implementation closes **8 identified compliance gaps** through new code modules, database schema, UI pages, and integration with existing security systems.

---

## Compliance Architecture

```
┌─────────────────────────────────────────────────────┐
│                  DPDP Compliance Engine              │
│              core/dpdp_compliance.py                 │
├──────────┬──────────┬──────────┬────────────────────┤
│ Consent  │ Privacy  │  Rights  │  Retention         │
│ Manager  │ Notice   │ Handler  │  Scheduler         │
├──────────┼──────────┼──────────┼────────────────────┤
│ Breach   │   DPO    │ Cross-   │  Purpose           │
│ Notifier │ Registry │ Border   │  Limitation        │
└──────────┴──────────┴──────────┴────────────────────┘
        ↕               ↕               ↕
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │Bastion AI│   │ Geo-Fence│   │ Main API │
  │(Breach   │   │(Transfer │   │(UI calls)│
  │ detect)  │   │ control) │   │          │
  └──────────┘   └──────────┘   └──────────┘
```

---

## Gap-by-Gap Implementation

### Gap 1: Consent Management (Section 6)

**DPDP Requirement:** Clear informed consent before collecting personal data, option to withdraw, consent records.

**Implementation:**
- **`dpdp_consent` table** — Records consent per principal per purpose with timestamps, notice version, and IP
- **`record_consent()` / `record_bulk_consent()`** — Atomic consent recording with UPSERT
- **`withdraw_consent()`** — Instant withdrawal with timestamp
- **`has_consent()`** — Gate check before processing
- **UI: `agreement.html`** — Granular consent checkboxes (required vs optional) in Schedule B
- **UI: `dpdp_compliance.html`** — Toggle-based consent management page

**Consent Categories:**
| Purpose | Required | Legal Basis |
|---------|----------|-------------|
| License Validation | Yes | Contract |
| Security Monitoring | Yes | Legitimate Interest |
| Geo-Fence | Yes | Contract |
| Login Audit | Yes | Legitimate Interest |
| Telemetry | No | Consent |
| CRM Data | No | Consent |
| WhatsApp | No | Consent |
| Backups | No | Consent |

### Gap 2: Privacy Notice (Section 5)

**DPDP Requirement:** Notice of what data collected, why, how long, who to contact.

**Implementation:**
- **`get_privacy_notice()`** — Structured notice with all required fields
- **`get_privacy_notice_html()`** — Renderable HTML for embedding
- **Section 8.4 in `agreement.html`** — Full DPDP privacy notice with data table
- **DPDP Compliance Centre** — Viewable and downloadable privacy notice

### Gap 3: Data Principal Rights (Sections 11-14)

**DPDP Requirement:** Right to access, correction, erasure, grievance redressal.

**Implementation:**
- **`dpdp_rights_requests` table** — Tracks all requests with UUID, status, timestamps
- **`submit_rights_request()`** — Creates tracked request
- **`execute_access_request()`** — Gathers all personal data for a principal
- **`execute_erasure_request()`** — Deletes specified categories with audit log
- **`process_rights_request()`** — Admin processes and responds
- **UI** — Self-service form with request type dropdown and tracking table

**Request Flow:**
1. Data principal submits request via UI
2. Request logged with UUID (e.g., `DPDP-A1B2C3D4E5F6`)
3. Owner processes via admin dashboard
4. Response recorded with timestamp and processor identity
5. Principal can track status in real-time

### Gap 4: Purpose Limitation (Section 4)

**DPDP Requirement:** Data processed only for specified lawful purposes.

**Implementation:**
- **`dpdp_purpose_registry` table** — Pre-seeded with 10 data categories
- **`check_purpose_allowed()`** — Validates processing against registry
- Each category documents: purpose, legal basis, retention period, description

### Gap 5: Data Retention (Section 8(7))

**DPDP Requirement:** Auto-delete data when purpose fulfilled.

**Implementation:**
- **`run_retention_purge()`** — Daily automated purge based on registry
- **`dpdp_retention_log` table** — Audit trail of all deletions
- **Retention schedule** applied per category:
  - IP/Telemetry: 90 days
  - Login/Behavior logs: 180 days
  - Backup vaults: 365 days
  - CRM/Machine ID: Until deletion requested
- **Background thread** in `main.py` runs purge every 24 hours
- **Old `.vault` files** auto-deleted past retention period

### Gap 6: Breach Notification (Section 8(6))

**DPDP Requirement:** Notify Data Protection Board and affected principals.

**Implementation:**
- **`dpdp_breach_log` table** — Full breach lifecycle tracking
- **`report_breach()`** — Logs breach with severity, affected data, principal count
- **`_trigger_breach_notification()`** — Prepares notification payload for DPB
- **`mark_breach_notified()`** — Records when DPB and principals notified
- **Bastion AI integration** — Auto-reports breaches on SUSPEND and RESTRICT actions
- **`.breach_notification.json`** — Persisted for admin dashboard pickup

**Severity → Action:**
| Severity | Trigger | Action |
|----------|---------|--------|
| Critical | Score ≥ 90, db_tamper, exfiltration | Immediate notification prep |
| High | Score ≥ 75, unauthorized_access | Notification prep |
| Medium | Brute force, suspicious patterns | Logged for review |
| Low | Minor anomalies | Logged only |

### Gap 7: DPO & Grievance Redressal (Section 8)

**DPDP Requirement:** Designated Data Protection Officer, grievance contact.

**Implementation:**
- **`set_dpo_info()` / `get_dpo_info()`** — Stores DPO name, email, phone, org details in `app_config`
- **Section 8.5 in `agreement.html`** — DPO clause
- **DPDP Compliance Centre** — Shows DPO contact info
- **Grievance request type** in rights request system
- **Owner-only permission** for DPO configuration

### Gap 8: Cross-Border Transfer Controls (Section 16)

**DPDP Requirement:** Data transfer only to permitted jurisdictions.

**Implementation:**
- **`dpdp_transfer_policy` table** — Per-country allow/block with reasons
- **`is_transfer_allowed()`** — Checks policy before any cross-border transmission
- **Default policy:** India-only (IN=allowed, ALL=blocked)
- **Geo-fence integration** — Transfer check runs during country detection
- **Bastion Sync feature flags** — `dpdp_consent_enforcement` remotely toggleable

---

## Files Modified / Created

### New Files
| File | Purpose |
|------|---------|
| `core/dpdp_compliance.py` | Core DPDP compliance engine (all 8 gaps) |
| `migrations/0001_dpdp_tables.sql` | Database schema for DPDP tables |
| `ui/dpdp_compliance.html` | Self-service DPDP compliance centre UI |
| `docs/dpdp_compliance.md` | This documentation |

### Modified Files
| File | Change |
|------|--------|
| `main.py` | DPDP API methods + retention scheduler thread |
| `database/bastion_ai.py` | Breach detection hooks on SUSPEND/RESTRICT |
| `core/bastion_sync.py` | Added DPDP feature flags for remote control |
| `core/permissions.py` | DPDP permissions (owner-only + always) |
| `core/geo_fence.py` | Cross-border transfer check integration |
| `ui/agreement.html` | DPDP privacy notice, consent checkboxes, DPO clause |

---

## Permission Model

| Permission | Scope | Who |
|------------|-------|-----|
| `dpdp_get_privacy_notice` | View notice | Everyone |
| `dpdp_get_my_consents` | View own consents | Everyone |
| `dpdp_withdraw_consent` | Withdraw own consent | Everyone |
| `dpdp_submit_rights_request` | Submit data request | Everyone |
| `dpdp_get_my_requests` | View own requests | Everyone |
| `dpdp_get_dpo_info` | View DPO contact | Everyone |
| `dpdp_set_dpo_info` | Configure DPO | Owner only |
| `dpdp_process_rights_request` | Handle requests | Owner only |
| `dpdp_view_breaches` | View breach log | Owner only |
| `dpdp_run_retention_purge` | Manual purge | Owner only |
| `dpdp_export_personal_data` | Export all data | Owner only |
| `dpdp_execute_erasure` | Delete personal data | Owner only |

---

## Database Schema

All DPDP tables are created via `migrations/0001_dpdp_tables.sql` which runs automatically through DBManager's migration system on first startup.

### Tables
- **`dpdp_consent`** — Consent records (principal × purpose)
- **`dpdp_rights_requests`** — Data subject rights requests
- **`dpdp_purpose_registry`** — Lawful purpose documentation (pre-seeded)
- **`dpdp_retention_log`** — Deletion audit trail
- **`dpdp_breach_log`** — Breach incident management
- **`dpdp_transfer_policy`** — Cross-border transfer rules (pre-seeded)

---

## Regulatory References

| Section | Requirement | Implementation |
|---------|-------------|----------------|
| S.4 | Purpose limitation | Purpose registry |
| S.5 | Privacy notice | Notice in agreement + compliance centre |
| S.6 | Consent | Granular consent UI + DB records |
| S.6(6) | Withdraw consent | Toggle-based withdrawal |
| S.8(1) | DPO designation | Configurable DPO info |
| S.8(6) | Breach notification | Auto-detection + notification pipeline |
| S.8(7) | Data retention | Automated purge scheduler |
| S.11 | Right to access | Data export API |
| S.12 | Right to correction | Rights request system |
| S.13 | Right to erasure | Erasure execution + audit |
| S.14 | Grievance redressal | Request + DPO system |
| S.16 | Cross-border transfer | Transfer policy + geo-fence integration |
