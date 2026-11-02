"""Pin the rules of the push-to-update pipeline.

Run from the project root:  python test_release_plan.py   (needs git on PATH)

Two halves. The first checks the pure decisions in release_plan.py and
updater.py with plain values: how the next number is chosen, which commit
markers do what, which changed files mean a rebuild, and when an installed
copy accepts a release. The second builds a throwaway git repository in a
temp folder and runs the real planner against it, so the git plumbing
(tags, ranges, diffs) is exercised the way the workflow uses it.

Nothing here touches the network, the project's own git history, or dist/.
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'scripts', 'release'))

try:
    import release_plan as rp                                # noqa: E402
    import updater                                           # noqa: E402
except ImportError as _exc:
    print(f"SKIP: {_exc}  (module not yet present)")
    sys.exit(0)

FAIL = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         expected {want!r}")
        print(f"         got      {got!r}")
        FAIL.append(label)


print("-- version numbers --")
check("parse v1.2.3", rp.parse_version("v1.2.3"), (1, 2, 3))
check("parse 1.2.3 without v", rp.parse_version("1.2.3"), (1, 2, 3))
check("parse rejects junk", rp.parse_version("release-1"), None)
check("highest tag wins, not newest", rp.highest(["v1.0.5", "v1.1.7", "v1.0.1", "junk"]), (1, 1, 7))
check("no tags -> None", rp.highest([]), None)
check("patch bump by default", rp.bump((1, 1, 7), "Fix totals"), (1, 1, 8))
check("[minor] bumps the middle", rp.bump((1, 1, 7), "New screen [minor]"), (1, 2, 0))
check("[major] bumps the first", rp.bump((1, 1, 7), "[MAJOR] rewrite"), (2, 0, 0))
check("first ever release is 1.0.0", rp.bump(None, "hello"), (1, 0, 0))

print("-- commit markers --")
check("[skip release]", rp.wants_skip("docs only [skip release]"), True)
check("[no release]", rp.wants_skip("[no release] typo"), True)
check("[skip ci] also skips", rp.wants_skip("chore [skip ci]"), True)
check("plain message releases", rp.wants_skip("Fix totals"), False)
check("[force] detected", rp.wants_force("Reset line [force]"), True)
check("no [force]", rp.wants_force("Reset line"), False)

print("-- release notes --")
check("merges dropped, markers stripped, repeats collapsed",
      rp.changelog_lines(["Merge branch 'x'", "Fix totals [minor]", "Fix totals", "", "Add screen"]),
      ["Fix totals", "Add screen"])
check("empty history gets a stock line", rp.changelog_lines([]), ["Bug fixes and improvements"])
many = rp.changelog_lines([f"change {i}" for i in range(25)])
check("long history is capped", (len(many), many[-1]), (rp.CHANGELOG_LIMIT + 1, "... and 5 more changes"))

print("-- what needs a rebuild --")
check("ui/ ships live", rp.is_frozen("ui/dashboard.html"), False)
check("main.py is frozen", rp.is_frozen("main.py"), True)
check("updater.py is frozen", rp.is_frozen("updater.py"), True)
check("core/*.py is frozen", rp.is_frozen("core/cluster_api.py"), True)
check("network/*.py is frozen", rp.is_frozen("network/lan_scan.py"), True)
check("database data files are not code", rp.is_frozen("database/.subscription_cache"), False)
check("runtime hooks are frozen", rp.is_frozen("rthook_pythonnet.py"), True)
check("fonts are bundled", rp.is_frozen("fonts/DMSans.ttf"), True)
check("docs and tests are neither", rp.is_frozen("docs/UPDATES.md"), False)
check("workflow file is neither", rp.is_frozen(".github/workflows/release.yml"), False)
check("rebuild list is sorted and deduplicated",
      rp.rebuild_files(["ui/a.html", "main.py", "core/x.py", "main.py", "README.md"]),
      ["core/x.py", "main.py"])
notes = rp.release_notes("1.1.8", ["Fix totals"], ["main.py"], "abcdef1234")
check("notes carry the rebuild warning", "Rebuild needed" in notes and "`main.py`" in notes, True)
check("notes name the commit", "abcdef1" in notes, True)

print("-- release channels --")
check("detect_channel stable", rp.detect_channel("Fix totals"), "stable")
check("detect_channel beta", rp.detect_channel("New feature [beta]"), "beta")
check("detect_channel rc", rp.detect_channel("Prep [rc] for release"), "rc")
check("format stable version", rp.format_channel_version((1, 2, 0), "stable"), "1.2.0")
check("format beta version", rp.format_channel_version((1, 2, 0), "beta", 1), "1.2.0-beta.1")
check("format rc version", rp.format_channel_version((1, 2, 0), "rc", 3), "1.2.0-rc.3")
check("parse version with beta suffix", rp.parse_version("1.2.0-beta.1"), (1, 2, 0))
check("parse version with rc suffix", rp.parse_version("v1.2.0-rc.2"), (1, 2, 0))
check("changelog strips [beta] marker",
      rp.changelog_lines(["New screen [beta]"]), ["New screen"])
check("changelog strips [rc] marker",
      rp.changelog_lines(["Prep [rc]"]), ["Prep"])

print("-- manifest integrity --")
import hashlib, json
sample_manifest = {"version": "1.2.0", "channel": "stable", "files": []}
body = json.dumps(sample_manifest, sort_keys=True, ensure_ascii=False).encode('utf-8')
sample_manifest["manifest_checksum"] = hashlib.sha256(body).hexdigest()
check("valid checksum passes", updater.verify_manifest_checksum(sample_manifest), True)
tampered = dict(sample_manifest)
tampered["version"] = "9.9.9"
check("tampered manifest fails", updater.verify_manifest_checksum(tampered), False)
check("manifest without checksum passes", updater.verify_manifest_checksum({"version": "1.0.0"}), True)

print("-- minimum version enforcement --")
min_manifest = {"version": "2.0.0", "min_version": "1.5.0", "files": []}
check("below min_version forces update",
      updater.decide(min_manifest, "1.1.7") is not None, True)
check("above min_version still needs newer",
      updater.decide({"version": "1.1.7", "min_version": "1.0.0"}, "1.1.7"), None)

print("-- channel filtering --")
check("stable client accepts stable", updater._channel_accepts("stable", "stable"), True)
check("stable client rejects beta", updater._channel_accepts("stable", "beta"), False)
check("beta client accepts stable", updater._channel_accepts("beta", "stable"), True)
check("beta client accepts beta", updater._channel_accepts("beta", "beta"), True)
check("rc client accepts rc", updater._channel_accepts("rc", "rc"), True)
check("rc client rejects beta", updater._channel_accepts("rc", "beta"), False)

print("-- what an installed copy accepts --")
check("version key", updater._version_key("v1.2.3"), (1, 2, 3))
check("newer installs", updater.decide({"version": "1.1.8", "files": [1, 2]}, "1.1.7") is not None, True)
check("same is ignored", updater.decide({"version": "1.1.7"}, "1.1.7"), None)
check("older is ignored", updater.decide({"version": "1.0.5"}, "1.1.7"), None)
check("older installs when forced", updater.decide({"version": "1.0.1", "force": True}, "1.1.7") is not None, True)
check("junk version ignored", updater.decide({"version": "latest"}, "1.1.7"), None)
d = updater.decide({"version": "1.1.8", "files": [1], "changelog": ["a", "b"]}, "1.1.7")
check("decision carries what the pages read",
      (d["available"], d["current"], d["file_count"], d["_changelog"]), (True, "1.1.7", 1, "a\nb"))
check("baked version is X.Y.Z", bool(updater.VERSION_RE.match(updater.CURRENT_VERSION)), True)


# ── the real planner on a throwaway repository ───────────────────────────────

def sh(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def commit(cwd, path, text, message):
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    sh("add", "-A", cwd=cwd)
    sh("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message, cwd=cwd)


def plan_in(cwd, tag=None):
    here = os.getcwd()
    os.chdir(cwd)
    try:
        return rp.plan(tag)
    finally:
        os.chdir(here)


print("-- planner against a real (temporary) git repository --")
work = tempfile.mkdtemp(prefix="aurum_release_")
try:
    sh("init", "-q", "-b", "main", cwd=work)
    commit(work, "ui/dashboard.html", "<html>1</html>", "Initial")
    commit(work, "main.py", "print(1)", "Some python")
    sh("-c", "user.name=t", "-c", "user.email=t@example.com",
       "tag", "-a", "v1.1.7", "-m", "Silent auto-update release", cwd=work)
    sh("-c", "user.name=t", "-c", "user.email=t@example.com",
       "tag", "-a", "v1.0.5", "-m", "Old line", cwd=work)          # lower number, made later

    p = plan_in(work)
    check("commit already tagged -> skip", p["skip"], True)

    commit(work, "ui/dashboard.html", "<html>2</html>", "Dashboard: show totals")
    p = plan_in(work)
    check("next number is above the HIGHEST tag, not the newest", p["version"], "1.1.8")
    check("previous release recorded", p["prev_tag"], "v1.1.7")
    check("notes are the commits since then", p["changelog"], ["Dashboard: show totals"])
    check("ui-only push needs no rebuild", (p["needs_rebuild"], p["rebuild_files"]), (False, []))
    check("not forced", p["force"], False)

    commit(work, "main.py", "print(2)", "Fix the bridge [force]")
    p = plan_in(work)
    check("python change flags a rebuild", (p["needs_rebuild"], p["rebuild_files"]), (True, ["main.py"]))
    check("[force] on the head commit", p["force"], True)
    check("both commits in the notes, marker stripped",
          p["changelog"], ["Fix the bridge", "Dashboard: show totals"])
    check("notes file mentions the rebuild", "Rebuild needed" in p["notes"], True)

    commit(work, "ui/login.html", "<html/>", "Login polish [minor]")
    p = plan_in(work)
    check("[minor] -> 1.2.0", p["version"], "1.2.0")

    commit(work, "README.md", "hi", "Docs [skip release]")
    p = plan_in(work)
    check("[skip release] skips", (p["skip"], "skip" in p["reason"]), (True, True))

    p = plan_in(work, tag="v3.0.0")
    check("hand-made tag uses that number", p["version"], "3.0.0")
    check("hand-made tag's previous is the highest below it", p["prev_tag"], "v1.1.7")
    check("hand-made tag with no message falls back to commits", len(p["changelog"]) >= 1, True)

    p = plan_in(work, tag="v1.1.7")
    check("existing tag re-run: exact number, notes from tag message",
          (p["version"], p["changelog"]), ("1.1.7", ["Silent auto-update release"]))
    check("existing tag: previous is the highest below it", p["prev_tag"], "v1.0.5")

    p = plan_in(work, tag="nightly")
    check("non-semver tag is ignored", p["skip"], True)

    # Output format the workflow copies into $GITHUB_OUTPUT.
    here = os.getcwd()
    os.chdir(work)
    try:
        out = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "release", "release_plan.py"), "--out", "plan_out"],
                             capture_output=True, text=True)
    finally:
        os.chdir(here)
    lines = dict(ln.split("=", 1) for ln in out.stdout.strip().split("\n") if "=" in ln)
    check("stdout is key=value only", set(lines) >= {"skip", "reason"}, True)
    check("skip output for the [skip release] head", lines.get("skip"), "true")
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASS")
