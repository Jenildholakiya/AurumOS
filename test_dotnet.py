"""Pin what AurumOS tells a customer when it cannot start .NET.

Run from the project root:  python test_dotnet.py

The old behaviour was one message for every failure: "install .NET Desktop
Runtime 8.0". The app asks for the .NET Framework backend first, and .NET
Framework 4.x is part of Windows, so on nearly every shop PC that message
named something already installed. The customer went away, installed .NET 8,
came back, and got the identical dialog.

These tests fix the decision: what is said when nothing is installed, what is
said when something is installed and the load failed anyway, and that a
working machine is never blocked. The decision is a pure function over facts,
so none of this depends on what happens to be installed here.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core import dotnet                                        # noqa: E402

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


def check_that(label, ok, detail=''):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        if detail:
            print(f"         {detail}")
        FAIL.append(label)


print("-- reading versions --")
check("three parts", dotnet.parse_version('8.0.11'), (8, 0, 11))
check("two parts", dotnet.parse_version('8.0'), (8, 0, 0))
check("trailing junk is ignored", dotnet.parse_version('8.0.11-preview'), (8, 0, 11))
check("not a version", dotnet.parse_version('latest'), None)
check("nothing", dotnet.parse_version(''), None)
check("highest wins", dotnet.best_version(['6.0.9', '8.0.11', '7.0.2']), (8, 0, 11))
check("and it compares numerically, not as text",
      dotnet.best_version(['8.0.9', '8.0.11']), (8, 0, 11))
check("no versions", dotnet.best_version([]), None)

print("-- the Framework release number --")
check("4.8", dotnet.framework_version(528040), '4.8')
# The published table only goes so far; a build past the last threshold
# maps to that threshold, which is why framework_installed() prefers the
# registry Version string and keeps this mapping as the fallback.
check("past the last published threshold", dotnet.framework_version(533509), '4.8.1')
check("4.8.1", dotnet.framework_version(533320), '4.8.1')
check("4.7.2", dotnet.framework_version(461808), '4.7.2')
check("something ancient", dotnet.framework_version(1), '')
check("junk", dotnet.framework_version('x'), '')

print("-- nothing installed: the only case the old message suited --")
v = dotnet.decide({'framework': '', 'desktop': [], 'core': [], 'arch': 'AMD64'})
check("blocked", v['ok'], False)
check("named as missing", v['reason'], 'missing')
check_that("it says Desktop Runtime", 'Desktop Runtime' in v['message'])
check_that("and names the architecture", 'x64' in v['message'])
check_that("and gives the download", 'dotnet.microsoft.com' in v['message'])

print("-- the wrong .NET installed, which looks identical to the customer --")
v = dotnet.decide({'framework': '', 'desktop': [], 'core': ['8.0.11'], 'arch': 'AMD64'})
check("still blocked", v['ok'], False)
check_that("but it explains they are two different downloads",
           'separate downloads' in v['message'])
check_that("and that the Desktop one is needed", 'Desktop' in v['message'])

print("-- installed and working: never block --")
v = dotnet.decide({'framework': '4.8', 'desktop': [], 'core': [], 'arch': 'AMD64'})
check_that("Framework alone is enough to proceed", v['ok'])
v = dotnet.decide({'framework': '', 'desktop': ['8.0.11'], 'core': [], 'arch': 'AMD64'})
check_that("so is the Desktop Runtime alone", v['ok'])
v = dotnet.decide({'framework': '4.8', 'desktop': ['8.0.11'], 'core': [], 'arch': 'AMD64'})
check_that("and both together", v['ok'])
check("with nothing to show the user", v['message'], '')

print("-- installed but it would not start: the case that wasted everyone's time --")
v = dotnet.decide({'framework': '4.8', 'desktop': [], 'core': [],
                   'arch': 'AMD64', 'clr_error': 'Failed to resolve Python.Runtime'})
check("blocked", v['ok'], False)
check("but for a different reason", v['reason'], 'broken')
check_that("it says what IS installed", '.NET Framework 4.8' in v['message'])
check_that("and that this is not a missing download",
           'not a missing download' in v['message'])
# The whole point: stop sending someone to install what they already have.
check_that("so it does not send them to the download page",
           'dotnet.microsoft.com' not in v['message'])
check_that("it points at the likely cause instead", '_internal' in v['message'])
check_that("and at the log", 'aurumos.log' in v['message'])
check_that("the real error is kept for support", 'Python.Runtime' in v['detail'])

v = dotnet.decide({'framework': '', 'desktop': ['8.0.11'], 'core': [],
                   'arch': 'AMD64', 'clr_error': 'hostfxr missing'})
check_that("the Desktop Runtime version is named back to them",
           '8.0.11' in v['message'])

print("-- old Framework versions still count --")
v = dotnet.decide({'framework': '4.7.2', 'desktop': [], 'core': [], 'arch': 'AMD64'})
check_that("4.7.2 is not treated as absent", v['ok'])

print("-- a desktop runtime older than we need is not enough --")
v = dotnet.decide({'framework': '', 'desktop': ['6.0.20'], 'core': [], 'arch': 'AMD64'})
check("blocked", v['ok'], False)
check("as missing", v['reason'], 'missing')

print("-- architecture is named so they pick the right installer --")
for arch, label in (('AMD64', 'x64'), ('ARM64', 'Arm64'), ('x86', 'x86')):
    v = dotnet.decide({'framework': '', 'desktop': [], 'core': [], 'arch': arch})
    check_that(f"{arch} -> {label}", label in v['message'])

print("-- reading this machine does not raise --")
facts = dotnet.gather()
check_that("gather returns the expected keys",
           set(facts) >= {'framework', 'desktop', 'core', 'arch', 'dotnet_root'})
verdict = dotnet.diagnose()
check_that("diagnose returns a verdict", 'ok' in verdict and 'reason' in verdict)
check_that("and a log line", dotnet.summary_line(facts).startswith('[.NET]'))
print(f"         (this machine: {dotnet.summary_line(facts)})")

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
