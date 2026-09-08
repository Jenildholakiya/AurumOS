# -*- coding: utf-8 -*-
"""AurumOS AI Support Assistant — BASTION AI.

A lightweight, self-contained bridge to OpenAI's ChatGPT that lets the shop
owner ask "how do I ... ?" in any language and get step-by-step help that is
specific to HOW AURUMOS ACTUALLY WORKS.

Design notes
------------
* A working OpenAI API key is shipped with the app (EMBEDDED_KEY) so the
  assistant works out of the box. The owner may still paste their OWN key from
  the in-app Help panel; a stored key overrides the embedded one and is kept
  locally in config.json (next to the EXE / project root).
* We call the provider with the standard library only (urllib) so there is no
  extra dependency to bundle into the frozen EXE.
* The "knowledge" of how the software works lives in SYSTEM_PROMPT below. It is
  intentionally a single, easy-to-edit block so it can grow as the app grows.
"""

import os
import sys
import json
import urllib.request
import urllib.error

# ── Provider endpoints ─────────────────────────────────────────────────────
# Groq (OpenAI-compatible Chat Completions API) — FREE, no credit card.
# The key is sent as a Bearer header. Get a free key at console.groq.com/keys.
OPENAI_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
REQUEST_TIMEOUT = 90  # seconds

# No key ships with the app — the free Groq key is entered once by the owner
# (in-app Help panel / config.json). A saved key is always used.
EMBEDDED_KEY = ""

# ── The assistant's knowledge of AurumOS ────────────────────────────────────
# Edit / extend this block to teach the assistant about new screens & flows.
SYSTEM_PROMPT = """You are **BASTION AI** — the built-in, friendly, patient support assistant for \
AurumOS, a jewelry shop management desktop application used by shop owners and their staff \
(primarily in India).

CRITICAL LANGUAGE RULE — auto-detect the language of EACH user message and reply ONLY in that same \
language, on every message, even if it changes mid-conversation. Never mix languages. Specifically:
- An English message -> reply in English.
- A message written in Gujarati script -> reply in Gujarati script.
- "Gujlish" (Gujarati written with English/Latin letters, e.g. "bill kai rite banavu?") -> understand \
  it and reply in Gujarati script (ગુજરાતી).
- "Hinglish" (Hindi in Latin letters) -> reply in Hindi script (हिन्दी); likewise Marathi and other \
  Indian languages -> reply in their native script.

Your job: help the user use AurumOS. When they ask "how do I ...?", give a clear, numbered, \
step-by-step answer that names the ACTUAL buttons, fields, toggles and menus in the app — never \
generic advice. Be concise but complete. If a step depends on a prior setup (e.g. gold rate must \
be set first), say so.

## Core principles of the AurumOS UI
- The app has a left **Sidebar** with icons. Click any item to open that screen.
- Most screens auto-load their data; if something looks empty, wait a second or refresh.
- Numbers use Indian weight units: grams (g) and "touch" / "fine" (purity), "tola", "bhari".
  Metal purity is tracked as Touch (%) and Fine (pure gold weight).
- Money is in Indian Rupees (Rs.).
- Many actions confirm with a modal "Confirm / Cancel" dialog before saving.

## Main modules (sidebar)
- **Dashboard**: overview of the day — sales, outstanding, quick stats.
- **Create Bill / Billing** (wholesale) and **POS / Billing** (retail): make a sale / invoice.
- **Inventory**: manage stock items, tags, HUID, opening stock.
- **Karigar**: artisans who make/job jewellery. Track orders, in/out, balances.
- **Katti / Uchak / Stock Ledger**: internal stock movement and manufacturing batches.
- **History / Voucher History**: browse past bills and vouchers; reprint or view details.
- **Accounting / Cash & Bank / Chart of Accounts / Ledger / Party**: the finance books.
- **Customers / Old Gold**: retail customer accounts and exchange of old gold.
- **Gold Rate**: set today's gold rate (required before billing can price metal).
- **Reports**: business reports.
- **Staff**: manage staff logins and permissions.
- **Network**: connect multiple shop computers (Host Core + Client nodes) on the LAN.
- **Settings**: business name, owner, and app configuration.

## HOW TO MAKE A BILL (Create Bill — wholesale flow)
This is the most common request. Walk the owner through it exactly:

1. Open the **sidebar** and click **Create Bill** (or **POS / Billing** on retail mode).
2. **Voucher ID** is auto-filled (e.g. VCH-001). The **Date** and **Time** are filled automatically.
3. In the **customer search box** ("Search or Type"), type the party/customer name. Pick the \
   matching customer from the dropdown. (If the customer is new you can usually type and create one.)
4. Fill the header purity fields:
   - **Overall Touch** (overall purity %) and **Wastage** for the bill.
   - **Tag ID**: scan or type a tag to pull a pre-tagged item, if applicable.
5. Turn on the relevant toggles if needed:
   - **Uchak** toggle — for uchak (manufacturing) billing.
   - **Credit** toggle — if the customer is buying on credit; the live outstanding balance shows.
   - **Online Paid** toggle — if payment was received online.
6. **Add items** using the item-entry row (Step 3 card):
   - In **IT Code / Touch**, type the item code or touch value (e.g. `68`) and pick from the dropdown.
   - Enter **Weight** (grams). You can capture weight automatically from a connected **scale** \
     (the scale panel lets you connect a COM port and reads live weight).
   - **Wastage** and **Fine** auto-calculate; Fine is read-only.
   - Press the **+** button (or Enter) to add the line to the items table.
   - Repeat for each item. The table shows NO, IT Code, Weight, Para, Less, Touch, Fine.
7. In the **settlement** card (Step 4): review **Total Gross Fine**. Enter **Collect Fine** (metal \
   taken back), and if applicable fill the **995 Wt / Touch / Fine** and **Dhal Wt / Touch / Fine** \
   rows, plus the **Gold Rate**. The app computes the final metal and cash settlement live.
8. Click the **billing / save / commit** action to commit the transaction (the button opens a \
   **print selector** modal — choose number of copies: 1 / 2 / 3, then **Confirm** to print the bill).
9. The bill is saved; you can later find it under **History** or **Voucher History** to reprint.

If anything errors (e.g. "Touch value not in master list"), the app shows a modal — read it and \
adjust the value, or add the touch to the master list in Settings / Inventory.

## HOW TO ADD INVENTORY / OPENING STOCK
Open **Inventory** → use **Opening Stock** (or the add-item form) to enter items with tag, HUID, \
weight, touch, and rate. Save; the item then appears when you bill by tag/IT code.

## HOW TO SET THE GOLD RATE
Open **Gold Rate** from the sidebar, enter today's rate, and save. Billing uses this to price metal.

## HOW TO CONNECT MULTIPLE COMPUTERS (Network)
Open **Network** from the sidebar. One PC is the **Host Core** (runs the sync server on port 7272); \
other PCs are **Client nodes** that point at the Host's IP. Set the mode in Settings / provisioning. \
Once connected, data syncs across the shop.

## Tone
Be warm, plain-spoken, and encouraging — the user may not be technical. Use the real field/button \
names. If you are unsure of an exact detail, say "In most versions of AurumOS you'll find this under \
<module> — look for <field>" rather than inventing specifics. Never ask the user for their API key or \
password. Keep answers focused on using the software.
"""


def _resolve_config_path():
    """config.json lives next to the EXE (frozen) or in the project root."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.abspath('.')
    return os.path.join(base, 'config.json')


class AISupport:
    """Owns the LLM configuration and the ask() call."""

    def __init__(self, config_path=None):
        self.config_path = config_path or _resolve_config_path()
        self.provider = "groq"
        self.api_key = ""
        self.model = DEFAULT_MODEL
        self._load()

    # ── config persistence ────────────────────────────────────────────────
    def _load(self):
        try:
            if os.path.exists(self.config_path):
                try:
                    from core.io_safety import safe_read_json
                    cfg = safe_read_json(self.config_path) or {}
                except Exception:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        cfg = json.load(f) or {}
                # OpenAI is the only backend wired up, so always treat the
                # stored provider as openai (ignores any legacy value).
                self.provider = 'groq'
                self.api_key = cfg.get('ai_api_key', '') or ''
                self.model = cfg.get('ai_model', DEFAULT_MODEL) or DEFAULT_MODEL
        except Exception as e:
            print(f"[AI] config load error: {e}")

    def _effective_key(self):
        """Owner-supplied key wins; otherwise fall back to the shipped key."""
        return (self.api_key or EMBEDDED_KEY or "").strip()

    def save_config(self, provider, api_key, model):
        """Merge AI keys into config.json (atomic) and reload in memory."""
        provider = (provider or 'groq').strip()
        api_key = (api_key or '').strip()
        model = (model or DEFAULT_MODEL).strip()
        # Read existing config (don't clobber other keys like mode/server_ip).
        cfg = {}
        if os.path.exists(self.config_path):
            try:
                from core.io_safety import safe_read_json
                cfg = safe_read_json(self.config_path) or {}
            except Exception:
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        cfg = json.load(f) or {}
                except Exception:
                    cfg = {}
        cfg['ai_provider'] = provider
        cfg['ai_api_key'] = api_key
        cfg['ai_model'] = model
        try:
            from core.io_safety import atomic_write_json
            atomic_write_json(self.config_path, cfg, indent=2)
        except Exception:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
        # Reload in memory
        self.provider = provider
        self.api_key = api_key
        self.model = model

    def is_configured(self):
        # Always configured — an embedded key ships with the app.
        return bool(self._effective_key())

    def get_status(self):
        """Return config state WITHOUT exposing the raw key."""
        # Only reflect an owner-supplied key in the masked preview; the shipped
        # key stays hidden so the settings box shows an empty field.
        masked = ""
        if self.api_key:
            if len(self.api_key) > 8:
                masked = self.api_key[:4] + "…" + self.api_key[-4:]
            else:
                masked = "•" * len(self.api_key)
        return {
            "provider": self.provider,
            "model": self.model,
            "configured": self.is_configured(),
            "key_masked": masked,
        }

    # ── the ask() call ─────────────────────────────────────────────────────
    def ask(self, question, history=None):
        """Send a question to ChatGPT and return a dict the UI can render.

        Returns: {"status": "success", "answer": "..."} or
                 {"status": "error", "message": "..."}
        """
        question = (question or "").strip()
        if not question:
            return {"status": "error", "message": "Please type your question."}
        key = self._effective_key()
        if not key:
            return {
                "status": "no_key",
                "message": "No AI key configured. Open the ⚙ settings in this Help panel and paste your OpenAI API key.",
            }

        # Build the OpenAI `messages` list: a system turn, a small recent
        # history window, then the new user question.
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history and isinstance(history, list):
            for turn in history[-8:]:
                role = turn.get("role")
                text = turn.get("content", "")
                if not text:
                    continue
                if role == "user":
                    messages.append({"role": "user", "content": text})
                elif role == "assistant":
                    messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": question})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1200,
            "temperature": 0.4,
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            OPENAI_URL,
            data=data,
            headers={
                "content-type": "application/json",
                "authorization": "Bearer " + key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode('utf-8')
            body = json.loads(raw)

            # OpenAI error payloads look like {"error": {...}}
            if "error" in body and isinstance(body.get("error"), dict):
                return {
                    "status": "error",
                    "message": "OpenAI error: " + str(body["error"].get("message", "unknown error")),
                }

            choices = body.get("choices") or []
            text = ""
            if choices:
                text = (choices[0].get("message", {}) or {}).get("content", "") or ""
            if not text:
                finish = (choices[0].get("finish_reason") if choices else "") or ""
                if finish and finish.lower() not in ("stop", ""):
                    return {"status": "error",
                            "message": f"The assistant stopped early ({finish}). Please rephrase your question."}
                return {"status": "error", "message": "The assistant returned an empty reply."}
            return {"status": "success", "answer": text}
        except urllib.error.HTTPError as he:
            detail = he.read().decode('utf-8', 'replace') if he.fp else ""
            # Surface the human-readable OpenAI message when present.
            try:
                err_json = json.loads(detail) if detail else {}
                inner = err_json.get("error", {}).get("message", "")
            except Exception:
                inner = ""
            msg = inner or detail[:300]
            if he.code == 400:
                return {"status": "error", "message": f"Bad request (400): {msg}"}
            if he.code == 401:
                return {"status": "error",
                        "message": "API key rejected (401). Check the OpenAI key in BASTION AI ⚙ settings."}
            if he.code == 403:
                return {"status": "error",
                        "message": "Access denied (403). Verify your OpenAI key and that the model is enabled."}
            if he.code == 404:
                return {"status": "error",
                        "message": f"Model '{self.model}' not found (404). Open BASTION AI ⚙ settings and set a valid OpenAI model id."}
            if he.code == 429:
                return {"status": "error",
                        "message": "Rate limited or quota exceeded (429). Try again shortly."}
            return {"status": "error", "message": f"API error {he.code}: {msg}"}
        except urllib.error.URLError as ue:
            return {"status": "error",
                    "message": f"Could not reach OpenAI. Check your internet connection. ({ue})"}
        except Exception as e:
            return {"status": "error", "message": f"Unexpected error: {e}"}
