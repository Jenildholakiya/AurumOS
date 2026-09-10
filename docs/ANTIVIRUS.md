# When Windows blocks AurumOS

Two different things get blamed on "the antivirus", and they have different
causes and different fixes. Work out which one you are looking at first.

| What the customer sees | What it is | Fixed by |
|---|---|---|
| "Windows protected your PC", blue box, **Don't run** | SmartScreen. The file is unsigned or too new to have a reputation. | [A code-signing certificate](#the-certificate) |
| The file vanishes, or "Threat found", "Trojan:Win32/Wacatac" | Antivirus heuristics. Nothing is actually wrong; the file simply looks like the kind of thing they stop. | [Everything under Reducing suspicion](#reducing-suspicion), then [report the false positive](#reporting-a-false-positive) |
| Staff PCs cannot connect to the main PC | Windows Firewall. Not antivirus at all. | [The firewall rule](#the-firewall-rule) |

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
`sign_config.json.example`.

You also need `signtool.exe` from the Windows SDK, which is **not currently
installed on the build machine**:
<https://developer.microsoft.com/windows/downloads/windows-sdk/>

With that in place `python build.py` signs and verifies automatically. Without
it, the build says so in plain words rather than quietly producing an unsigned
file.

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

Run this once on the main PC, as Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\allow_through_firewall.ps1
```

It ships in `dist\tools\`. It clears any stale block rule first, then adds an
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
