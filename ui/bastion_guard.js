// AurumOS Bastion -- shared real-time suspension lock (all pages)
(function(){
  if (document.getElementById('bastion-screen')) return;
  var div = document.createElement('div');
  div.innerHTML = `<!-- BASTION FULL SCREEN POPUP (LIGHT THEME, ANIMATED) -->
<style>
@keyframes bastionFadeIn { from{opacity:0;} to{opacity:1;} }
@keyframes bastionSlideUp { from{opacity:0; transform:translateY(18px);} to{opacity:1; transform:translateY(0);} }
@keyframes bastionPulseRing { 0%{box-shadow:0 0 0 0 rgba(220,38,38,0.35);} 70%{box-shadow:0 0 0 18px rgba(220,38,38,0);} 100%{box-shadow:0 0 0 0 rgba(220,38,38,0);} }
@keyframes bastionSweep { 0%{background-position:-200% 0;} 100%{background-position:200% 0;} }
@keyframes bastionBlink { 0%,100%{opacity:1;} 50%{opacity:0.25;} }
#bastion-screen * { box-sizing:border-box; }
</style>
<div id="bastion-screen" style="
    display:none;
    position:fixed;
    inset:0;
    background:#faf8f3;
    z-index:999999;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    gap:18px;
    font-family:'DM Sans',sans-serif;
    padding:24px;
    box-sizing:border-box;
    animation:bastionFadeIn 0.35s ease;">

    <!-- Suspended icon ring -->
    <div style="
        width:64px; height:64px;
        border-radius:50%;
        background:#fef2f2;
        border:2px solid #fca5a5;
        display:flex; align-items:center; justify-content:center;
        animation:bastionSlideUp 0.4s ease, bastionPulseRing 2s ease-out infinite;
        margin-bottom:4px;">
        <span style="font-size:1.6rem; color:#dc2626; animation:bastionBlink 1.6s ease-in-out infinite;">&#9888;</span>
    </div>

    <!-- BASTION Logo -->
    <div style="text-align:center; animation:bastionSlideUp 0.4s ease;">
        <div style="
            font-size:0.6rem;
            letter-spacing:6px;
            text-transform:uppercase;
            color:#a87d1e;
            margin-bottom:8px;">
            &#9632; AurumOS BASTION Security
        </div>
        <div style="
            font-size:2rem;
            font-weight:800;
            color:#dc2626;
            letter-spacing:2px;">
            ACCOUNT SUSPENDED
        </div>
    </div>

    <!-- Red alert sweep bar -->
    <div style="
        width:100%;
        max-width:500px;
        height:3px;
        border-radius:2px;
        overflow:hidden;
        background:#fee2e2;
        position:relative;">
        <div style="
            position:absolute; inset:0;
            background:linear-gradient(90deg,transparent,#dc2626,transparent);
            background-size:200% 100%;
            animation:bastionSweep 2.2s linear infinite;">
        </div>
    </div>

    <!-- Attack detail card -->
    <div style="
        width:100%;
        max-width:500px;
        background:#ffffff;
        border:1px solid #f0ddc2;
        border-radius:14px;
        padding:22px 24px;
        box-shadow:0 8px 30px rgba(168,125,30,0.08);
        animation:bastionSlideUp 0.5s ease;">

        <div id="bastion-title" style="
            font-size:1.05rem;
            font-weight:700;
            color:#1f1a12;
            margin-bottom:6px;">
            Threat Detected
        </div>

        <div id="bastion-reason" style="
            font-size:0.85rem;
            color:#5c5347;
            line-height:1.55;
            margin-bottom:14px;">
        </div>

        <div id="bastion-detail" style="
            font-size:0.72rem;
            font-family:'DM Mono',monospace;
            color:#8a7a55;
            background:#faf6ea;
            border:1px solid #f0e6cc;
            border-radius:8px;
            padding:10px 12px;
            margin-bottom:14px;
            word-break:break-all;">
        </div>

        <div id="bastion-time" style="
            font-size:0.7rem;
            color:#a89878;
            text-align:right;">
        </div>
    </div>

    <!-- Lock code + unlock form -->
    <div style="
        width:100%;
        max-width:500px;
        background:#ffffff;
        border:1px solid #f0ddc2;
        border-radius:14px;
        padding:20px 24px;
        animation:bastionSlideUp 0.6s ease;">

        <div style="font-size:0.7rem; letter-spacing:1.5px; text-transform:uppercase; color:#a89878; margin-bottom:6px;">
            Lock code
        </div>
        <div id="bastion-lock-code" style="
            font-size:1.3rem;
            font-weight:700;
            font-family:'DM Mono',monospace;
            color:#a87d1e;
            letter-spacing:2px;
            margin-bottom:16px;">
        </div>

        <div style="font-size:0.7rem; letter-spacing:1.5px; text-transform:uppercase; color:#a89878; margin-bottom:8px;">
            Enter admin unlock key
        </div>
        <input id="bastion-unlock-input" type="text" placeholder="16-character key" style="
            width:100%;
            padding:12px 14px;
            border:1.5px solid #e8dcc0;
            border-radius:10px;
            font-family:'DM Mono',monospace;
            font-size:0.95rem;
            letter-spacing:2px;
            color:#1f1a12;
            background:#fdfcf8;
            margin-bottom:12px;
            outline:none;
            transition:border-color 0.2s ease, box-shadow 0.2s ease;">

        <button id="bastion-unlock-btn" style="
            width:100%;
            padding:12px;
            background:#1f1a12;
            color:#fff;
            border:none;
            border-radius:10px;
            font-size:0.85rem;
            font-weight:700;
            letter-spacing:0.5px;
            cursor:pointer;
            transition:background 0.2s ease, transform 0.1s ease;">
            Submit Admin Key
        </button>

        <div id="bastion-unlock-msg" style="
            font-size:0.78rem;
            margin-top:10px;
            text-align:center;
            min-height:18px;">
        </div>
    </div>

    <div style="font-size:0.68rem; color:#a89878; letter-spacing:0.5px;">
        Contact AurumOS Admin to restore access
    </div>
</div>`;
  document.body.appendChild(div);
})();

// ── Show BASTION screen ───────────────────────────────────────────
function bastionShow(record) {
    var el = document.getElementById('bastion-screen');
    if (!el) return;

    // Fill details
    var titles = {
        'session_tamper':       'Session Tampering Detected',
        'db_edit':              'Database Tampering Detected',
        'fingerprint_mismatch': 'Unauthorized PC Access Detected',
        'exe_tamper':           'EXE File Modified',
        'replay_attack':        'Replay Attack Detected',
    };
    var reasons = {
        'session_tamper':       'A session token mismatch was detected. Someone attempted to tamper with the running application or inject a forged session.',
        'db_edit':              'The AurumOS database was modified by an external tool while the application was running. This is a serious security violation.',
        'fingerprint_mismatch': 'This database was opened on a different PC than it was created on. Unauthorized data copying is strictly not permitted.',
        'exe_tamper':           'The AurumOS executable file has been modified or patched. This may be a hacking attempt. Contact AurumOS Admin immediately.',
        'replay_attack':        'An old or forged session token was detected. A hacking attempt was made against this installation.',
    };

    var atype = record.attack_type || 'unknown';
    var title = record.title || titles[atype] || 'Security Violation';
    var reason = record.reason || reasons[atype] || 'An unauthorized action was detected.';

    document.getElementById('bastion-title').innerText  = title;
    document.getElementById('bastion-reason').innerText = reason;
    document.getElementById('bastion-lock-code').innerText =
        (record.lock_code || '--------').toUpperCase();

    if (record.timestamp) {
        document.getElementById('bastion-time').innerText =
            'Suspended at: ' + record.timestamp;
    }

    if (record.detail) {
        var det = document.getElementById('bastion-detail');
        det.innerText = 'Detail: ' + record.detail;
        det.style.display = 'block';
    }

    el.style.display = 'flex';
}

// ── Submit admin unlock key ───────────────────────────────────────
function bastionSubmitUnlock() {
    var key = (document.getElementById('bastion-unlock-input').value || '').trim().toUpperCase();
    var lc  = (document.getElementById('bastion-lock-code').innerText || '').trim();
    var msg = document.getElementById('bastion-unlock-msg');

    msg.style.color = '#dc2626';
    msg.innerText = '';

    if (!key || key.length !== 16) {
        msg.innerText = 'BASTION key must be 16 characters';
        return;
    }

    if (!window.pywebview || !window.pywebview.api) {
        msg.innerText = 'API not ready';
        return;
    }

    msg.style.color = '#c9a227';
    msg.innerText   = 'Verifying...';

    window.pywebview.api.bastion_unlock(key, lc).then(function(r) {
        if (r && r.status === 'success') {
            msg.style.color = '#16a34a';
            msg.innerText   = 'Restored. Reloading...';
            setTimeout(function() { window.location.reload(); }, 1000);
        } else {
            msg.style.color = '#dc2626';
            msg.innerText   = 'Invalid key. Contact AurumOS Admin.';
            document.getElementById('bastion-unlock-input').value = '';
        }
    }).catch(function(e) {
        msg.style.color = '#dc2626';
        msg.innerText   = 'Error: ' + String(e);
    });
}

// ── Enter key support ─────────────────────────────────────────────
function bastionBindEvents() {
    var inp = document.getElementById('bastion-unlock-input');
    if (inp) {
        inp.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') bastionSubmitUnlock();
        });
    }
    var btn = document.getElementById('bastion-unlock-btn');
    if (btn) {
        btn.addEventListener('click', bastionSubmitUnlock);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bastionBindEvents);
} else {
    bastionBindEvents();
}

// ── Check BASTION status on every page load ───────────────────────
function bastionCheckOnLoad() {
    if (!window.pywebview || !window.pywebview.api) {
        setTimeout(bastionCheckOnLoad, 200);
        return;
    }
    window.pywebview.api.bastion_get_status().then(function(r) {
        if (r && r.suspended) {
            bastionShow(r);
        }
    }).catch(function() {});
}
bastionCheckOnLoad();

window.bastionShow = bastionShow;