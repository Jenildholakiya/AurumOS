# When Windows blocks AurumOS

Several different things get blamed on "the antivirus", and they have different
causes and different fixes. Work out which one you are looking at first.

| What the customer sees | What it is | Fixed by |
|---|---|---|
| "Windows protected your PC", blue box, **Don't run** | SmartScreen. The file is unsigned or too new to have a reputation. | [A code-signing certificate](#the-certificate) |
| The file vanishes, or "Threat found", "Trojan:Win32/Wacatac" | Antivirus heuristics. Nothing is actually wrong; the file simply looks like the kind of thing they stop. | [Everything under Reducing suspicion](#reducing-suspicion), then [report the false positive](#reporting-a-false-positive) |
| Staff PCs cannot connect to the main PC | Windows Firewall. Not antivirus at all. | [The firewall rule](#the-firewall-rule) |
| "Smart App Control blocked an app that may be unsafe", with no Run anyway | Smart App Control. A different feature from SmartScreen, and a harder one. | [Smart App Control](#smart-app-control) |

---

## What was wrong, and what changed

The shipped `AurumOS.exe` was inspected. It carried **no publisher information
at all** — Properties showed an empty Details tab: no CompanyName, no
ProductName, no FileDescription, no FileVersion — and it was unsigned.

That combination is the problem. On its own, "unsigned" is common. But an
unsigned executable, with no publisher details, built by a tool that packs an
interpreter and its bytecode into one binary, which then opens a listening
socket on the local network, is a close description of what heuristic engines
exist to catch. Each factor adds to a score. None of them individually is
damning, and together they are enough.

Three of those factors are now gone:

- **Version resource.** The build writes `version_info.txt` from
  `build_version.txt` and stamps it in, so Properties shows the publisher, the
  product, the description and the version. Set at the top of `build.py`.
- **No packer.** `upx=True` was set in the spec. UPX compresses an executable
  and, because that is what malware uses to hide, scanners treat a packed file
  as packed. It happened to be doing nothing, because UPX is not installed on
  the build machine — meaning the first build on a machine that *had* UPX
  would have started getting flagged, with nothing in the diff to explain it.
  Now `upx=False`, said out loud.
- **Signature verification.** The signing step used to trust signtool's exit
  code. It now runs `signtool verify /pa` afterwards, because a signature that
  does not chain on a clean machine is worse than none: it fails only at the
  customer, where you cannot see it.

The one that remains is the certificate.

---

## The certificate

This is the only thing that removes the SmartScreen warning outright. Nothing
else substitutes for it.

A code-signing certificate is issued to a registered business, costs roughly
what a year of hosting does, and takes a few days of identity checks. Sectigo,
DigiCert and GlobalSign all sell them.

**Standard (OV) or Extended Validation (EV)?** EV clears SmartScreen from the
first download. Standard has to build reputation first, so early customers may
still see the warning for a few weeks until enough installs accumulate. If
customers are being handed the file directly, EV is worth the difference.

Before you buy, set `COMPANY` at the top of `build.py` to the exact registered
name the certificate will be issued to. The name in the file's Properties and
the name in the SmartScreen dialog should match; if they disagree, a cautious
customer notices.

Then either put the `.pfx` path in `sign_config.json`, or — for any OV/EV
certificate issued since June 2023, whose key lives on a hardware token and
has no `.pfx` — put its thumbprint there instead. See
`config/sign_config.json.example`.

**You do not need the Windows SDK.** The build used to require `signtool.exe`
from it, which is a large download and was not installed, so the build kept
producing unsigned files for a reason that had nothing to do with the
certificate. Signing now falls back to Windows' own
`Set-AuthenticodeSignature`, which is present on every Windows machine.
`signtool` is still used when it happens to be installed.

**The cheapest route is Azure Trusted Signing.** Microsoft runs it as a
subscription billed monthly rather than a few hundred up front, and the
certificates it issues are trusted the same as any other. It requires an
organisation that can show trading history. If that fits, it is the least
expensive way to stop this problem for good.

Otherwise Sectigo, DigiCert and GlobalSign sell the usual one- to three-year
certificates.

With any of them configured, `python build.py` signs and verifies
automatically, and says plainly when it could not.

---

## Reducing suspicion

Beyond the above, one thing in the build still works against you, and it is a
deliberate trade rather than a mistake.

`build.py` protects four source files by replacing each with a stub that
decrypts a payload at import and runs it:

```python
P = bytes(b ^ k for b, k in zip(D, S[:len(D)]))
exec(marshal.loads(zlib.decompress(P)))
```

Decrypt-in-memory-then-execute is the defining behaviour of a dropper. That is
not an accusation about the intent — it is what the pattern matches, and
behavioural engines match on the pattern. If AurumOS keeps getting flagged
after it is signed, this is the next thing to look at.

It is protecting real intellectual property, so removing it is a product
decision, not a technical one. Worth knowing the trade exists:

- A signed binary is judged far less harshly, so signing may well settle it.
- Compiling to `.pyc` and shipping that, without the encrypt/exec layer, gives
  most of the practical protection with none of the signature.
- A commercial obfuscator such as PyArmor is recognised by scanners and
  whitelisted in a way a hand-rolled XOR stub never will be.

---

## Reporting a false positive

If a specific vendor flags a signed release, tell them. These are free, take a
few minutes, and turnaround is usually one to three days. A fixed
false positive stays fixed for every customer on that product.

| Vendor | Where |
|---|---|
| Microsoft Defender | <https://www.microsoft.com/wdsi/filesubmission> |
| Avast / AVG | <https://www.avast.com/false-positive-file-form.php> |
| Kaspersky | <https://opentip.kaspersky.com/> |
| Norton | <https://submit.norton.com/> |
| McAfee | <https://www.mcafee.com/enterprise/en-us/threat-center/threat-library/sample-submission.html> |
| Bitdefender | <https://www.bitdefender.com/submit/> |

Check a release before customers do, with all engines at once:
<https://www.virustotal.com/>. A handful of obscure engines flagging a
PyInstaller binary is normal and not worth chasing. Defender, Kaspersky,
Avast, Norton or Bitdefender flagging it is worth a submission.

Submit the exact file you shipped. A rebuilt binary has a different hash, and
the whitelist is by hash.

---

## The firewall rule

Not an antivirus problem, though it gets reported as one, because the symptom
is "the software doesn't work".

The main PC listens on TCP **58903** so staff PCs can reach it. On first
launch Windows asks "Allow AurumOS to communicate on these networks?". If
whoever is at the counter clicks Cancel, Windows records a **block** rule that
persists, and a later allow rule does not override it. Staff PCs then fail to
connect, with nothing in the log, because the packets never arrive.

Run this once on the main PC, as Administrator.

An Administrator prompt opens in `C:\Windows\System32`, not in the AurumOS
folder, so a relative path finds nothing. Change directory first:

```powershell
cd "C:\AurumOS"
powershell -ExecutionPolicy Bypass -File .\tools\allow_through_firewall.ps1
```

Or give the full path, and name the EXE the rule should be tied to:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\AurumOS\tools\allow_through_firewall.ps1" -ExePath "C:\AurumOS\AurumOS.exe"
```

Substitute wherever AurumOS is actually installed.

It ships in `dist\tools\`, which means it only exists in a folder produced by
a build that included it. A copy installed before then has no `tools` folder,
and the command reports that the file does not exist — which is what that
error means, rather than anything being wrong with the firewall.

It clears any stale block rule first, then adds an
inbound TCP allow rule for that port, tied to `AurumOS.exe`, on **Private and
Domain networks only**. Never Public: Windows treats any unfamiliar network as
Public, and a till should not be reachable from a hotel or airport network.

`-Remove` undoes it. Staff PCs need nothing — they only make outgoing
connections, which Windows already permits.

The application does not add this rule itself, on purpose. It would need to
elevate, and a program that silently rewrites firewall configuration at
startup is doing the exact thing the rest of this document is about.

If it still will not connect, the script prints what to check. The usual
answer is that Windows has the shop's network marked Public:

```powershell
Get-NetConnectionProfile
```

A third-party security suite may also run its own firewall, separate from
Windows, which needs the same rule.

---

## Smart App Control

This is **not** SmartScreen, and the difference matters. SmartScreen shows a
box with **More info → Run anyway** behind it. Smart App Control shows a box
whose only buttons are **Okay** and **Get apps from the Store**. There is no
way past it, for you or for a customer, and there is no per-app exception list
to add anything to.

It blocks anything it cannot attribute to a publisher it trusts. An unsigned
executable never runs on a machine where it is enforcing, however that file
arrived. Copying it from a USB stick does not help, because this is not driven
by the download tag.

### Who has it on

Only Windows 11, and only where Windows decided to enable it. It is switched
on during a **clean install**, after a period in evaluation mode where Windows
watches what the machine runs and decides. A PC upgraded from Windows 10, or
one that has been used for a while, usually ends up with it off.

So most shop PCs will not have it. New ones increasingly will, and that share
only grows.

Check any machine:

```powershell
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy').VerifiedAndReputablePolicyState
```

| Value | Meaning |
|---|---|
| 0 | Off |
| 1 | On and enforcing. An unsigned build cannot run |
| 2 | Evaluation. Windows is still deciding |

`tools\check_release.ps1` reports this along with everything else.

### Turning it off on your own machine

Windows Security → App & browser control → Smart App Control settings → Off.

**This is one-way.** Once off, it cannot be switched back on without
reinstalling Windows. On a machine you build and test software on that is the
right call, because you will otherwise be blocked from running your own
unsigned builds forever. Make that decision deliberately, and not on a
customer's PC.

### What actually fixes it

A code-signing certificate, and nothing else. Smart App Control honours a
valid signature from a trusted CA, so a signed build runs. This is the reason
signing stops being optional: SmartScreen can be clicked past by a determined
customer, and Smart App Control cannot be.

---

## SmartScreen: the red box on first run

This is the one customers ring you about. It is not antivirus and it is not a
detection. SmartScreen checks whether Windows has seen this exact file before
and whether a trusted publisher signed it. An unsigned file nobody has run
before gets **"Windows protected your PC"**, and the button that runs it is
hidden behind **More info**.

### What decides whether it appears

Three things, checked before every release by:

```bash
powershell -ExecutionPolicy Bypass -File tools\check_release.ps1
```

**Signature.** A certificate from a trusted CA is the only thing that removes
the box outright. The build signs automatically once one is configured, and
the build report now says plainly when it did not.

**Reputation.** A standard (OV) certificate does not clear SmartScreen on day
one. Microsoft accrues reputation as installs happen, typically over a few
weeks. An EV certificate skips that wait and clears it from the first
download. If you are handing the file to customers yourself, EV is worth the
difference precisely because there is no early grace period to survive.

**Mark of the Web.** Windows tags anything that arrives from the internet with
a hidden stream, and that tag is what raises the box. This is why the same
build behaves differently depending on how it reaches the shop:

| How the shop gets it | What happens |
|---|---|
| Downloaded from a website or a cloud link | Tagged, and every file inside a downloaded ZIP inherits the tag. Red box. |
| Copied from a USB stick | Not tagged. No box. |
| Copied from a local network share | Not tagged. No box. |

Checked on the current build: seven files in `dist\` already carry that tag
from having been extracted out of `AurumOS_Client.zip`. A customer extracting
a downloaded ZIP gets it on `AurumOS.exe` too, which is exactly the case that
produces the red box.

So until a certificate is in place, **how you deliver the file changes what
the customer sees more than anything else you can do**. Handing over a USB
stick, or copying from a share during installation, avoids the prompt
entirely.

### Clearing the tag on a copy you are installing

```bash
powershell -ExecutionPolicy Bypass -File tools\check_release.ps1 -Unblock
```

This is the same action as ticking **Unblock** on the file's Properties tab.
It removes the "came from the internet" tag from a folder you have just
downloaded and are about to install. It disables no scanning: antivirus still
inspects every file. It only clears the tag that drives the prompt, on that
one copy, on that one machine.

Do this as part of your own installation routine. It is not something to talk
a shopkeeper through over the phone, and it is not a substitute for signing.

### What to tell a customer who is looking at the box right now

Click **More info**, then **Run anyway**. It appears once per machine for a
given file. If the person is unwilling, and they are right to be careful, the
honest answer is that the software is not yet signed, which is a thing for you
to fix rather than something for them to work around.
