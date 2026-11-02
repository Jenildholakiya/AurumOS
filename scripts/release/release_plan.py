# -*- coding: utf-8 -*-
"""
Decide what the next AurumOS release is.

The GitHub workflow runs this on every push to main and on every vX.Y.Z tag.
It prints ``key=value`` lines for $GITHUB_OUTPUT on stdout, a readable
summary on stderr, and writes three files next to it:

    release_plan.json        everything below, for gen_version_manifest.py
    release_notes.md         the GitHub release body
    release_changelog.txt    one line per change, used as the tag message

    python release_plan.py                 push to main: bump from the highest tag
    python release_plan.py --tag v1.2.0    a hand-made tag: use that exact number

NUMBERING
    The next number is always one patch above the highest vX.Y.Z tag that
    exists anywhere in the repo, not above the most recent release. Installed
    copies only accept a number higher than the one they last saw, so a
    release below the historical maximum would sit on GitHub as "latest" and
    never install on the PCs that saw the higher one.

COMMIT MESSAGE MARKERS (the pushed commit only)
    [minor]          1.1.7 -> 1.2.0
    [major]          1.1.7 -> 2.0.0
    [force]          install even on copies whose number is higher
    [skip release]   push without releasing (also [no release], [skip ci])

WHAT NEEDS A REBUILD
    Only ui/ reaches installed copies through the release. Every Python file
    is frozen inside AurumOS.exe, so a push that touches one is flagged
    ``needs_rebuild`` in the manifest, the release notes and the job summary:
    run build.py and reinstall before that change reaches a shop.
"""

import json
import os
import re
import subprocess
import sys

VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)(?:-(beta|rc)\.(\d+))?$')
STABLE_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')

CHANNELS = ('stable', 'rc', 'beta')

# Everything outside ui/ that ends up inside the frozen EXE or its bundle.
FROZEN_FILES = {
    'main.py', 'updater.py', 'build.py', 'subscription_manager.py',
    'sse_listener.py', 'AurumOS.ico',
}
FROZEN_PREFIXES = ('core/', 'database/', 'network/', 'fonts/', 'assets/')
FROZEN_PATTERNS = (re.compile(r'^hook_[^/]+\.py$'), re.compile(r'^rthook_[^/]+\.py$'))
FROZEN_EXTS = {'.py'}          # under core/ database/ network/ only code counts

CHANGELOG_LIMIT = 20


# ── pure helpers (covered by test_release_plan.py) ─────────────────────────

def parse_version(text):
    """'v1.2.3' or '1.2.3' -> (1, 2, 3); anything else -> None.
    Also accepts '1.2.3-beta.1' but returns only the base triple."""
    m = VERSION_RE.match(str(text or '').strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def fmt(version):
    return '.'.join(str(x) for x in version)


def highest(tags):
    """The largest vX.Y.Z among tag names, or None."""
    parsed = [v for v in (parse_version(t) for t in tags) if v]
    return max(parsed) if parsed else None


def bump(version, message):
    """Next number after `version`, driven by [major]/[minor] in `message`."""
    text = (message or '').lower()
    if version is None:
        return (1, 0, 0)
    major, minor, patch = version
    if '[major]' in text:
        return (major + 1, 0, 0)
    if '[minor]' in text:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def wants_skip(message):
    text = (message or '').lower()
    return any(m in text for m in ('[skip release]', '[no release]', '[skip ci]'))


def wants_force(message):
    return '[force]' in (message or '').lower()


def detect_channel(message):
    """Return 'beta', 'rc', or 'stable' based on commit markers."""
    text = (message or '').lower()
    if '[beta]' in text:
        return 'beta'
    if '[rc]' in text:
        return 'rc'
    return 'stable'


def read_min_version():
    """Read MIN_VERSION file from repo root, or None."""
    try:
        p = os.path.join(os.getcwd(), 'MIN_VERSION')
        if os.path.isfile(p):
            v = open(p, encoding='utf-8').read().strip()
            if STABLE_RE.match(v):
                return v
    except Exception:
        pass
    return None


def format_channel_version(base_version, channel, pre_num=1):
    """Format version with channel suffix: '1.2.0-beta.1' or '1.2.0'."""
    v = fmt(base_version) if isinstance(base_version, tuple) else str(base_version)
    if channel in ('beta', 'rc'):
        return f'{v}-{channel}.{pre_num}'
    return v


def changelog_lines(subjects):
    """Commit subjects -> release notes lines: no merges, no repeats, capped."""
    seen, out = set(), []
    for s in subjects:
        s = ' '.join(str(s or '').split())
        if not s or s.lower().startswith('merge '):
            continue
        s = re.sub(r'\s*\[(major|minor|force|skip release|no release|skip ci|beta|rc)\]', '', s, flags=re.I).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    if len(out) > CHANGELOG_LIMIT:
        extra = len(out) - CHANGELOG_LIMIT
        out = out[:CHANGELOG_LIMIT] + [f'... and {extra} more change{"s" if extra != 1 else ""}']
    return out or ['Bug fixes and improvements']


def is_frozen(path):
    """True when a changed repo path lives inside the EXE rather than ui/."""
    p = str(path or '').replace('\\', '/').lstrip('./')
    if not p or p.startswith('ui/'):
        return False
    if p in FROZEN_FILES or any(rx.match(p) for rx in FROZEN_PATTERNS):
        return True
    for prefix in FROZEN_PREFIXES:
        if p.startswith(prefix):
            if prefix in ('core/', 'database/', 'network/'):
                return os.path.splitext(p)[1].lower() in FROZEN_EXTS
            return True
    return False


def rebuild_files(changed_paths):
    return sorted({p.replace('\\', '/') for p in changed_paths if is_frozen(p)})


def release_notes(version, changelog, rebuild, commit=''):
    lines = [f'## AurumOS v{version}', '']
    lines += [f'- {c}' for c in changelog]
    if rebuild:
        lines += ['', '### Rebuild needed before this reaches shops',
                  'Only `ui/` ships through the updater. These files are frozen inside '
                  'AurumOS.exe, so run `python build.py` and reinstall:', '']
        lines += [f'- `{p}`' for p in rebuild]
    if commit:
        lines += ['', f'Built from `{commit[:7]}`.']
    return '\n'.join(lines) + '\n'


# ── git ─────────────────────────────────────────────────────────────────────

def git(*args, default=''):
    try:
        out = subprocess.run(['git', *args], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=60)
        return out.stdout.strip() if out.returncode == 0 else default
    except Exception:
        return default


def all_tags():
    return [t for t in git('tag', '-l', 'v*').split('\n') if t.strip()]


def tags_at_head():
    return [t for t in git('tag', '--points-at', 'HEAD').split('\n') if parse_version(t)]


def head_message():
    return git('log', '-1', '--format=%B')


def head_sha():
    return git('rev-parse', 'HEAD')


def tag_message(tag):
    return git('tag', '-l', '--format=%(contents)', tag)


def subjects_since(prev_tag):
    rng = f'{prev_tag}..HEAD' if prev_tag else 'HEAD'
    args = ['log', '--format=%s', rng] if prev_tag else ['log', '-1', '--format=%s']
    return [s for s in git(*args).split('\n') if s.strip()]


def changed_since(prev_tag):
    if not prev_tag:
        return []
    return [p for p in git('diff', '--name-only', prev_tag, 'HEAD').split('\n') if p.strip()]


# ── the plan ────────────────────────────────────────────────────────────────

def plan(tag=None):
    tags = all_tags()
    top = highest(tags)
    message = head_message()

    if tag:
        version = parse_version(tag)
        if not version:
            return {'skip': True, 'reason': f'{tag} is not a vX.Y.Z tag'}
        below = [v for v in (parse_version(t) for t in tags) if v and v < version]
        prev = f'v{fmt(max(below))}' if below else None
        notes = tag_message(f'v{fmt(version)}').strip()
        subjects = [ln.lstrip('-* ').strip() for ln in notes.split('\n')] if notes \
            else subjects_since(prev)
        force = wants_force(notes) or wants_force(message)
    else:
        if wants_skip(message):
            return {'skip': True, 'reason': 'commit message asks to skip the release'}
        already = tags_at_head()
        if already:
            return {'skip': True,
                    'reason': f'this commit is already released as {already[0]}'}
        version = bump(top, message)
        prev = f'v{fmt(top)}' if top else None
        subjects = subjects_since(prev)
        force = wants_force(message)

    existing = f'v{fmt(version)}' in tags
    if existing and not tag:
        return {'skip': True, 'reason': f'v{fmt(version)} already exists'}

    channel = detect_channel(message)
    min_ver = read_min_version()
    ver_str = format_channel_version(version, channel)
    tag_str = f'v{ver_str}'

    changelog = changelog_lines(subjects)
    rebuild = rebuild_files(changed_since(prev))
    commit = head_sha()
    return {
        'skip': False,
        'reason': '',
        'version': ver_str,
        'base_version': fmt(version),
        'tag': tag_str,
        'prev_tag': prev or '',
        'channel': channel,
        'min_version': min_ver,
        'force': force,
        'needs_rebuild': bool(rebuild),
        'rebuild_files': rebuild,
        'changelog': changelog,
        'commit': commit,
        'notes': release_notes(ver_str, changelog, rebuild, commit),
    }


def write_files(p, out_dir='.'):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'release_plan.json'), 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=2)
    with open(os.path.join(out_dir, 'release_notes.md'), 'w', encoding='utf-8') as f:
        f.write(p.get('notes', ''))
    with open(os.path.join(out_dir, 'release_changelog.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(p.get('changelog', [])) + '\n')


def main(argv):
    tag = None
    out_dir = '.'
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == '--tag' and args:
            tag = args.pop(0)
        elif a == '--out' and args:
            out_dir = args.pop(0)
        else:
            print(f'unknown argument {a!r}', file=sys.stderr)
            return 2

    p = plan(tag)

    # stdout is copied straight into $GITHUB_OUTPUT: scalars only.
    print(f"skip={'true' if p['skip'] else 'false'}")
    print(f"reason={p.get('reason', '')}")
    if not p['skip']:
        print(f"version={p['version']}")
        print(f"tag={p['tag']}")
        print(f"prev_tag={p['prev_tag']}")
        print(f"channel={p.get('channel', 'stable')}")
        print(f"min_version={p.get('min_version') or ''}")
        print(f"force={'true' if p['force'] else 'false'}")
        print(f"needs_rebuild={'true' if p['needs_rebuild'] else 'false'}")
        print(f"rebuild_count={len(p['rebuild_files'])}")
        write_files(p, out_dir)

    if p['skip']:
        print(f"release: skipped -- {p['reason']}", file=sys.stderr)
    else:
        print(f"release: {p['tag']} (after {p['prev_tag'] or 'no earlier tag'})", file=sys.stderr)
        for c in p['changelog']:
            print(f'  - {c}', file=sys.stderr)
        if p['needs_rebuild']:
            print('  REBUILD NEEDED: ' + ', '.join(p['rebuild_files']), file=sys.stderr)
        if p['force']:
            print('  forced: installs on every copy regardless of number', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
