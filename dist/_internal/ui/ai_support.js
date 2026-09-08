/* BASTION AI Support — floating help assistant.
 * Self-contained: injects its own CSS, renders a floating button + chat panel,
 * and calls OpenAI ChatGPT DIRECTLY (no Python bridge, no rebuild needed).
 * OpenAI returns permissive CORS so the file:// page can reach it.
 * Loaded from sidebar.js on every sidebar page. */
(function () {
  "use strict";

  if (window.__aurumAiWidget) return;
  window.__aurumAiWidget = true;

  /* ── FREE chat config — Groq (self-contained; no Python bridge, no rebuild) ─
   * The widget calls Groq directly. Groq is FREE (no credit card) and returns
   * permissive CORS (Access-Control-Allow-Origin: *) so a file:// page reaches
   * it. It is OpenAI-compatible (same request/response shape).
   * Get a free key at https://console.groq.com/keys and paste it in ⚙ Settings.
   * A key/model saved in Settings (localStorage) is used for all calls. */
  var API_URL      = "https://api.groq.com/openai/v1/chat/completions";
  var DEFAULT_MODEL = "llama-3.3-70b-versatile";
  // No key ships with the app — the free Groq key is entered once in Settings.
  var EMBEDDED_KEY = "";

  var SYSTEM_PROMPT =
    "You are BASTION AI, the built-in support assistant for AurumOS, a jewelry shop " +
    "management desktop app used mainly in India. " +
    "CRITICAL LANGUAGE RULE — auto-detect the language of EACH user message and reply ONLY in that " +
    "same language, on every message, even if it changes mid-chat. Never mix languages. Specifically: " +
    "(a) English message -> reply in English. " +
    "(b) Message written in Gujarati script -> reply in Gujarati script. " +
    "(c) 'Gujlish' = Gujarati written with English/Latin letters (e.g. 'bill kai rite banavu?') -> " +
    "understand it and reply in Gujarati script (ગુજરાતી). " +
    "(d) 'Hinglish' = Hindi in Latin letters -> reply in Hindi script (हिन्दी); likewise Marathi and " +
    "other Indian languages -> reply in their native script. " +
    "Give clear, numbered, step-by-step help that names the ACTUAL buttons, fields, toggles and " +
    "sidebar screens in AurumOS (Dashboard, Create Bill/Billing, POS, Inventory, Karigar, Stock " +
    "Ledger, History/Voucher History, Accounting/Cash & Bank/Ledger, Customers/Old Gold, Gold Rate, " +
    "Reports, Staff, Network, Settings). Weights use grams and 'touch'/'fine' (purity); money is in " +
    "Rupees. To make a bill: open Create Bill, pick the customer, set Overall Touch/Wastage, add items " +
    "in the item row (IT Code/Touch + Weight, press +), review settlement (Collect Fine, Gold Rate), " +
    "then save and choose copies in the print dialog. Set the Gold Rate screen before billing. Be warm, " +
    "concise and plain-spoken; never ask for the user's API key or password.";

  function aiGetKey()   { try { return (localStorage.getItem("aurum_ai_key")   || "").trim() || EMBEDDED_KEY; } catch (e) { return EMBEDDED_KEY; } }
  function aiGetModel() { try { return (localStorage.getItem("aurum_ai_model") || "").trim() || DEFAULT_MODEL; } catch (e) { return DEFAULT_MODEL; } }

  // Direct call to Groq (OpenAI-compatible). Returns {status, answer|message}.
  function askChatGPT(question, history) {
    var key = aiGetKey();
    if (!key) {
      return Promise.resolve({
        status: "no_key",
        message: "BASTION AI needs a FREE key. Open the ⚙ settings, then get a free key (no credit card) at console.groq.com/keys and paste it in."
      });
    }

    var messages = [{ role: "system", content: SYSTEM_PROMPT }];
    if (history && history.length) {
      history.slice(-8).forEach(function (t) {
        if (!t || !t.content) return;
        if (t.role === "user") messages.push({ role: "user", content: t.content });
        else if (t.role === "assistant") messages.push({ role: "assistant", content: t.content });
      });
    }
    messages.push({ role: "user", content: question });

    return fetch(API_URL, {
      method: "POST",
      headers: { "content-type": "application/json", "authorization": "Bearer " + key },
      body: JSON.stringify({ model: aiGetModel(), messages: messages, max_tokens: 1200, temperature: 0.4 })
    }).then(function (resp) {
      return resp.json().then(function (body) {
        if (body && body.error) {
          var m = (body.error && body.error.message) || "unknown error";
          if (resp.status === 401) return { status: "error", message: "Key rejected (401). Open ⚙ settings and paste a valid free Groq key from console.groq.com/keys." };
          if (resp.status === 429) return { status: "error", message: "Free rate limit hit (429). Wait a few seconds and try again." };
          if (resp.status === 404) return { status: "error", message: "Model not found (404). In ⚙ settings set a valid model, e.g. llama-3.3-70b-versatile." };
          return { status: "error", message: "AI error: " + m };
        }
        var text = "";
        if (body && body.choices && body.choices[0] && body.choices[0].message) {
          text = body.choices[0].message.content || "";
        }
        if (!text) return { status: "error", message: "The assistant returned an empty reply. Please try again." };
        return { status: "success", answer: text };
      });
    }).catch(function (e) {
      return { status: "error", message: "Could not reach the AI service. Check your internet connection. (" + (e && e.message ? e.message : e) + ")" };
    });
  }

  /* ── Icons (inline SVG) ─────────────────────────────────────────────── */
  var ICON_SPARK = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 14l.7 1.8L21.5 16.5 19.7 17.2 19 19l-.7-1.8L16.5 16.5l1.8-.7z"/></svg>';
  var ICON_QUESTION = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.2 9.3a2.8 2.8 0 0 1 5.4 1c0 1.9-2.8 2.5-2.8 4"/><line x1="12" y1="17" x2="12" y2="17"/></svg>';
  var ICON_SEND = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  var ICON_GEAR = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';

  /* ── CSS ───────────────────────────────────────────────────────────── */
  var CSS = [
    ":root{--ai-cream:#faf8f3;--ai-cream2:#f2efe7;--ai-ink:#0e0c09;--ai-ink2:#2a2620;",
    "--ai-gold:#a87d1e;--ai-gold2:#c9a227;--ai-gold3:#8a6c0a;--ai-muted:#7a7268;",
    "--ai-green:#15803d;--ai-red:#b91c1c;}",
    "#aurum-ai-fab{position:fixed;right:22px;bottom:22px;z-index:2147483600;",
    "width:58px;height:58px;border-radius:50%;border:none;cursor:pointer;",
    "background:linear-gradient(135deg,#a87d1e,#e0bd46);color:#fff;",
    "box-shadow:0 8px 26px rgba(168,125,30,.5);display:flex;align-items:center;justify-content:center;",
    "transition:transform .18s cubic-bezier(.2,.8,.3,1),box-shadow .18s;",
    "animation:aiPulse 2.6s ease-in-out infinite;}",
    "#aurum-ai-fab:hover{transform:scale(1.08) rotate(4deg);box-shadow:0 12px 32px rgba(168,125,30,.6);}",
    "#aurum-ai-fab:active{transform:scale(.96);}",
    "@keyframes aiPulse{0%,100%{box-shadow:0 8px 26px rgba(168,125,30,.5);}",
    "50%{box-shadow:0 8px 30px rgba(168,125,30,.78);}}",
    "#aurum-ai-panel{position:fixed;right:22px;bottom:92px;z-index:2147483601;",
    "width:400px;max-width:calc(100vw - 36px);height:600px;max-height:calc(100vh - 120px);",
    "background:var(--ai-cream);border-radius:20px;overflow:hidden;display:none;",
    "flex-direction:column;font-family:'DM Sans',system-ui,sans-serif;color:var(--ai-ink);",
    "border:1px solid rgba(168,125,30,.28);",
    "box-shadow:0 20px 60px rgba(14,12,9,.32),0 0 0 1px rgba(255,255,255,.4) inset;",
    "transform-origin:bottom right;}",
    "#aurum-ai-panel.open{display:flex;animation:aiPop .22s cubic-bezier(.2,.9,.3,1.1);}",
    "@keyframes aiPop{from{opacity:0;transform:translateY(14px) scale(.96);}",
    "to{opacity:1;transform:translateY(0) scale(1);}}",
    ".ai-head{padding:16px 16px 14px;position:relative;color:#fff;",
    "background:radial-gradient(120% 140% at 0% 0%,#c9a227 0%,#a87d1e 55%,#8a6c0a 100%);}",
    ".ai-head::after{content:'';position:absolute;left:0;right:0;bottom:0;height:1px;",
    "background:linear-gradient(90deg,transparent,rgba(255,255,255,.6),transparent);}",
    ".ai-head-row{display:flex;align-items:center;gap:11px;}",
    ".ai-avatar{width:40px;height:40px;border-radius:12px;display:flex;align-items:center;justify-content:center;",
    "background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.3);flex-shrink:0;}",
    ".ai-head .ai-title{font-family:'DM Serif Display',Georgia,serif;font-size:1.12rem;line-height:1;flex:1;}",
    ".ai-head .ai-sub{font-size:.72rem;opacity:.85;margin-top:3px;font-weight:500;}",
    ".ai-head button{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.22);color:#fff;",
    "width:32px;height:32px;border-radius:9px;cursor:pointer;display:flex;align-items:center;justify-content:center;",
    "transition:background .15s;}",
    ".ai-head button:hover{background:rgba(255,255,255,.32);}",
    ".ai-body{flex:1;overflow-y:auto;padding:18px 16px;display:flex;flex-direction:column;gap:14px;",
    "background:linear-gradient(180deg,#faf8f3 0%,#f4f0e6 100%);}",
    ".ai-body::-webkit-scrollbar{width:8px;}",
    ".ai-body::-webkit-scrollbar-thumb{background:rgba(168,125,30,.3);border-radius:8px;}",
    ".ai-body::-webkit-scrollbar-thumb:hover{background:rgba(168,125,30,.5);}",
    ".ai-msg{max-width:86%;padding:11px 14px;border-radius:14px;font-size:.9rem;line-height:1.55;",
    "white-space:pre-wrap;word-wrap:break-word;box-shadow:0 1px 2px rgba(14,12,9,.05);}",
    ".ai-msg.user{align-self:flex-end;background:linear-gradient(135deg,#1c1813,#0e0c09);color:#fff;",
    "border-bottom-right-radius:5px;}",
    ".ai-msg.bot{align-self:flex-start;background:#fff;border:1px solid rgba(14,12,9,.08);",
    "border-bottom-left-radius:5px;}",
    ".ai-msg.bot h1,.ai-msg.bot h2,.ai-msg.bot h3{font-size:.98rem;margin:.35em 0 .25em;color:var(--ai-ink);}",
    ".ai-msg.bot h1{font-size:1.05rem;}",
    ".ai-msg.bot ol,.ai-msg.bot ul{margin:.3em 0 .3em 1.25em;padding:0;}",
    ".ai-msg.bot li{margin:.28em 0;}",
    ".ai-msg.bot code{background:rgba(168,125,30,.12);padding:1px 6px;border-radius:5px;font-size:.85em;",
    "font-family:'DM Mono',monospace;}",
    ".ai-msg.bot strong{color:var(--ai-gold3);}",
    ".ai-msg.bot a{color:var(--ai-gold);}",
    ".ai-typing{display:inline-flex;gap:4px;align-items:center;}",
    ".ai-typing i{width:7px;height:7px;border-radius:50%;background:var(--ai-gold);opacity:.5;",
    "animation:aiBounce 1.2s infinite ease-in-out;}",
    ".ai-typing i:nth-child(2){animation-delay:.18s;}",
    ".ai-typing i:nth-child(3){animation-delay:.36s;}",
    "@keyframes aiBounce{0%,80%,100%{transform:translateY(0);opacity:.4;}",
    "40%{transform:translateY(-5px);opacity:1;}}",
    ".ai-welcome{text-align:center;padding:14px 6px 4px;}",
    ".ai-welcome .ai-big{width:64px;height:64px;margin:0 auto 12px;border-radius:20px;",
    "background:linear-gradient(135deg,#a87d1e,#e0bd46);display:flex;align-items:center;justify-content:center;",
    "color:#fff;box-shadow:0 10px 26px rgba(168,125,30,.4);}",
    ".ai-welcome h3{font-family:'DM Serif Display',Georgia,serif;font-size:1.2rem;margin-bottom:6px;}",
    ".ai-welcome p{font-size:.82rem;color:var(--ai-muted);line-height:1.5;margin:0 auto;max-width:280px;}",
    ".ai-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:16px;}",
    ".ai-chip{background:#fff;border:1px solid rgba(168,125,30,.3);color:var(--ai-gold3);",
    "padding:7px 13px;border-radius:20px;font-size:.78rem;cursor:pointer;transition:all .15s;font-weight:500;}",
    ".ai-chip:hover{background:var(--ai-gold);color:#fff;border-color:var(--ai-gold);transform:translateY(-1px);}",
    ".ai-foot{flex-shrink:0;border-top:1px solid rgba(14,12,9,.08);padding:11px;display:flex;gap:9px;",
    "background:#fff;align-items:flex-end;}",
    ".ai-foot textarea{flex:1;resize:none;height:44px;max-height:96px;border:1px solid rgba(14,12,9,.16);",
    "border-radius:13px;padding:11px 13px;font-family:inherit;font-size:.9rem;outline:none;line-height:1.4;",
    "transition:border-color .15s,box-shadow .15s;}",
    ".ai-foot textarea:focus{border-color:var(--ai-gold);box-shadow:0 0 0 3px rgba(168,125,30,.12);}",
    ".ai-send{background:linear-gradient(135deg,#a87d1e,#c9a227);color:#fff;border:none;border-radius:13px;",
    "width:46px;height:44px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;",
    "transition:transform .15s,opacity .15s;}",
    ".ai-send:hover{transform:scale(1.05);}",
    ".ai-send:disabled{opacity:.5;cursor:default;transform:none;}",
    ".ai-config{padding:20px 18px;display:flex;flex-direction:column;gap:15px;overflow-y:auto;",
    "background:linear-gradient(180deg,#faf8f3,#f4f0e6);}",
    ".ai-config::-webkit-scrollbar{width:8px;}",
    ".ai-config::-webkit-scrollbar-thumb{background:rgba(168,125,30,.3);border-radius:8px;}",
    ".ai-config .ai-cfg-head{display:flex;align-items:center;gap:10px;}",
    ".ai-config .ai-cfg-head .ai-avatar{width:36px;height:36px;border-radius:10px;}",
    ".ai-config h4{font-family:'DM Serif Display',Georgia,serif;font-size:1.15rem;margin:0;}",
    ".ai-config .ai-cfg-sub{font-size:.74rem;color:var(--ai-muted);margin:-4px 0 0;}",
    ".ai-config p{margin:0;font-size:.82rem;color:var(--ai-ink2);line-height:1.5;}",
    ".ai-config label{font-size:.76rem;font-weight:600;color:var(--ai-ink2);display:block;margin-bottom:6px;}",
    ".ai-config input,.ai-config select{width:100%;padding:10px 12px;border:1px solid rgba(14,12,9,.18);",
    "border-radius:11px;font-family:inherit;font-size:.88rem;outline:none;box-sizing:border-box;",
    "background:#fff;transition:border-color .15s,box-shadow .15s;}",
    ".ai-config input:focus,.ai-config select:focus{border-color:var(--ai-gold);box-shadow:0 0 0 3px rgba(168,125,30,.12);}",
    ".ai-config .ai-save{background:linear-gradient(135deg,#15803d,#1a9a49);color:#fff;border:none;border-radius:12px;",
    "padding:12px;cursor:pointer;font-weight:600;font-size:.92rem;transition:transform .15s,opacity .15s;}",
    ".ai-config .ai-save:hover{transform:translateY(-1px);}",
    ".ai-config .ai-save:disabled{opacity:.6;cursor:default;transform:none;}",
    ".ai-config .ai-hint{font-size:.76rem;color:var(--ai-muted);line-height:1.5;}",
    ".ai-config .ai-hint a{color:var(--ai-gold);text-decoration:none;font-weight:600;}",
    ".ai-config .ai-hint a:hover{text-decoration:underline;}",
    ".ai-secure{display:flex;align-items:center;gap:7px;font-size:.74rem;color:var(--ai-muted);",
    "background:rgba(168,125,30,.07);border:1px solid rgba(168,125,30,.15);padding:9px 11px;border-radius:10px;}"
  ].join("");

  function injectCSS() {
    var s = document.createElement("style");
    s.id = "aurum-ai-style";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (html != null) e.innerHTML = html;
    return e;
  }

  function esc(t) {
    return String(t).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Small markdown-ish renderer: **bold**, `code`, numbered/bulleted lists, headings.
  function renderMD(text) {
    var lines = String(text).replace(/\r/g, "").split("\n");
    var out = [], inList = null;
    function closeList() { if (inList) { out.push("</" + inList + ">"); inList = null; } }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i], m;
      if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
        closeList(); var lvl = m[1].length;
        out.push("<h" + lvl + ">" + inline(m[2]) + "</h" + lvl + ">");
      } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
        if (inList !== "ol") { closeList(); out.push("<ol>"); inList = "ol"; }
        out.push("<li>" + inline(m[1]) + "</li>");
      } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
        if (inList !== "ul") { closeList(); out.push("<ul>"); inList = "ul"; }
        out.push("<li>" + inline(m[1]) + "</li>");
      } else if (line.trim() === "") { closeList();
      } else { closeList(); out.push("<p>" + inline(line) + "</p>"); }
    }
    closeList();
    return out.join("");
  }
  function inline(s) {
    return esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function buildWidget() {
    injectCSS();

    var fab = el("button", { id: "aurum-ai-fab", title: "BASTION AI Help", "aria-label": "BASTION AI Help" }, ICON_SPARK);
    document.body.appendChild(fab);

    var panel = el("div", { id: "aurum-ai-panel" });
    var head = el("div", { class: "ai-head" },
      '<div class="ai-head-row">' +
      '<div class="ai-avatar">' + ICON_SPARK + '</div>' +
      '<div style="flex:1"><div class="ai-title">BASTION AI</div>' +
      '<div class="ai-sub">Ask anything, in any language</div></div>' +
      '<button class="ai-gear" title="Settings">' + ICON_GEAR + '</button>' +
      '<button class="ai-close" title="Close">&times;</button>' +
      '</div>');
    panel.appendChild(head);
    var body = el("div", { class: "ai-body" });
    panel.appendChild(body);
    var foot = el("div", { class: "ai-foot" },
      '<textarea placeholder="Type your question…" rows="1"></textarea>' +
      '<button class="ai-send" title="Send">' + ICON_SEND + '</button>');
    panel.appendChild(foot);
    document.body.appendChild(panel);

    var gearBtn = head.querySelector(".ai-gear");
    var closeBtn = head.querySelector(".ai-close");
    var ta = foot.querySelector("textarea");
    var sendBtn = foot.querySelector(".ai-send");
    var history = [];

    fab.addEventListener("click", function () {
      panel.classList.toggle("open");
      if (panel.classList.contains("open")) {
        if (!body.dataset.init) { body.dataset.init = "1"; showWelcome(); }
        setTimeout(function () { ta.focus(); }, 60);
      }
    });
    closeBtn.addEventListener("click", function () { panel.classList.remove("open"); });
    gearBtn.addEventListener("click", function () { showConfig(); });

    function pushMsg(role, html) {
      var m = el("div", { class: "ai-msg " + role }, html);
      body.appendChild(m);
      body.scrollTop = body.scrollHeight;
      return m;
    }

    function showWelcome() {
      body.innerHTML = "";
      var w = el("div", { class: "ai-welcome" },
        '<div class="ai-big">' + ICON_QUESTION + '</div>' +
        '<h3>How can BASTION AI help?</h3>' +
        '<p>I know how AurumOS works. Ask me to do something, or pick a common task below.</p>' +
        '<div class="ai-chips">' +
        '<span class="ai-chip">How do I make a bill?</span>' +
        '<span class="ai-chip">How to add inventory?</span>' +
        '<span class="ai-chip">Set the gold rate</span>' +
        '<span class="ai-chip">Connect two computers</span>' +
        '</div>');
      body.appendChild(w);
      Array.prototype.forEach.call(w.querySelectorAll(".ai-chip"), function (chip) {
        chip.addEventListener("click", function () { ask(chip.textContent); });
      });
    }

    function initAfterConfig() {
      history = [];
      showWelcome();
    }

    function showConfig() {
      body.innerHTML = "";
      var box = el("div", { class: "ai-config" });
      box.innerHTML =
        '<div class="ai-cfg-head"><div class="ai-avatar">' + ICON_GEAR + '</div>' +
        '<div><h4>BASTION AI Settings</h4><p class="ai-cfg-sub">Connect your FREE Groq key</p></div></div>' +
        '<p>BASTION AI is powered by Groq — it is <strong>free</strong> and needs <strong>no credit card</strong>. ' +
        'Get a free key, paste it below, and Save. The key is saved only on this computer and is used only to ' +
        'power BASTION AI.</p>' +
        '<div><label>Provider</label><select id="ai-prov">' +
        '<option value="groq">Groq (free)</option></select></div>' +
        '<div><label>API Key</label><input id="ai-key" type="password" placeholder="gsk_… (free Groq key)" autocomplete="off"></div>' +
        '<div><label>Model</label><input id="ai-model" value="llama-3.3-70b-versatile" placeholder="llama-3.3-70b-versatile"></div>' +
        '<button class="ai-save">Save &amp; Start</button>' +
        '<div class="ai-secure">' + ICON_GEAR + '<span>Only you can see this key. Groq is free to use.</span></div>' +
        '<p class="ai-hint">Get a free key (no credit card) at ' +
        '<a href="https://console.groq.com/keys" target="_blank" rel="noopener">console.groq.com/keys</a> — sign in with Google, click “Create API Key”, copy it here.</p>';
      body.appendChild(box);

      // Prefill from any saved values (stored locally).
      try {
        var savedModel = localStorage.getItem("aurum_ai_model");
        if (savedModel) box.querySelector("#ai-model").value = savedModel;
      } catch (e) {}

      box.querySelector(".ai-save").addEventListener("click", function () {
        var key = box.querySelector("#ai-key").value.trim();
        var model = box.querySelector("#ai-model").value.trim() || "llama-3.3-70b-versatile";
        try {
          if (key) localStorage.setItem("aurum_ai_key", key);
          localStorage.setItem("aurum_ai_model", model);
        } catch (e) {}
        initAfterConfig();
      });
    }

    function ask(q) {
      q = (q || ta.value).trim();
      if (!q) return;
      ta.value = "";
      autoSize();
      pushMsg("user", esc(q));
      history.push({ role: "user", content: q });
      var typing = pushMsg("bot", '<span class="ai-typing"><i></i><i></i><i></i></span>');
      sendBtn.disabled = true;
      askChatGPT(q, history).then(function (r) {
        if (typing.parentNode) body.removeChild(typing);
        if (r && r.status === "success") {
          pushMsg("bot", renderMD(r.answer || ""));
          history.push({ role: "assistant", content: r.answer || "" });
        } else if (r && r.status === "no_key") {
          pushMsg("bot", esc(r.message || "Add your free key in the ⚙ settings."));
          showConfig();
        } else {
          pushMsg("bot", esc((r && r.message) || "Something went wrong. Please try again."));
        }
      }).catch(function (e) {
        if (typing.parentNode) body.removeChild(typing);
        pushMsg("bot", esc("Error: " + (e && e.message ? e.message : e)));
      }).then(function () {
        sendBtn.disabled = false;
        ta.focus();
      });
    }

    function autoSize() {
      ta.style.height = "44px";
      ta.style.height = Math.min(ta.scrollHeight, 96) + "px";
    }

    sendBtn.addEventListener("click", function () { ask(); });
    ta.addEventListener("input", autoSize);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); }
    });
  }

  // Build the UI immediately — it does not need the bridge. apiCall() handles
  // bridge readiness on its own (polling). This avoids the start-up race.
  if (document.body) buildWidget();
  else document.addEventListener("DOMContentLoaded", buildWidget);
})();
