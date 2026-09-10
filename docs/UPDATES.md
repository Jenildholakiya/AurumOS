# Shipping an AurumOS update

```bash
git push origin main
```

That is the whole procedure. Every push to `main` publishes a release, and
shop PCs pick it up on their own within half an hour. Nobody clicks anything,
nobody downloads an installer, and the version number on screen moves to the
new one by itself.

The one thing to know: **only `ui/` travels this way.** Everything written in
Python is sealed inside `AurumOS.exe`. Read [When a rebuild is
needed](#when-a-rebuild-is-needed) before assuming a change has shipped.

---

## What a push does

GitHub Actions runs `.github/workflows/release.yml`, which:

1. Reads every `vX.Y.Z` tag and takes the number after the **highest** one.
   `v1.1.7` present anywhere means the next release is `v1.1.8`, even if the
   most recently published release was `v1.0.5`.
2. Writes `version.json`: every file in `ui/`, with its SHA-256 and the URL it
   can be downloaded from.
3. Tags the commit and publishes the GitHub release, using the commit subjects
   since the last release as the notes.
4. Uploads `version.json` plus every `ui/` file as release assets.

An installed copy then, on its own:

- checks about every 30 minutes, and again shortly after the dashboard opens;
- compares each file's hash against what it already has;
- downloads only what changed, straight into the folder the app serves screens
  from, so the new version of a screen appears the next time it is opened;
- writes `version.lock`, which is what the sidebar, the sign-in box and
  Settings read.

Nothing is downloaded twice, and a shop that was switched off for a week
catches up on the next launch.

### Why the number goes up from the highest tag

An installed copy only accepts a release **numbered higher than the one it
already has**. A release numbered below the historical maximum would sit on
GitHub marked "Latest" and quietly install nowhere. Numbering from the highest
tag makes that impossible to do by accident.

To deliberately go backwards, put `[force]` in the commit message. The release
is then marked forced and installs regardless of number.

---

## Controlling a release from the commit message

Markers go in the message of the commit you push.

| Marker | Effect |
|---|---|
| *(none)* | Patch bump: `1.1.7` → `1.1.8` |
| `[minor]` | `1.1.7` → `1.2.0` |
| `[major]` | `1.1.7` → `2.0.0` |
| `[force]` | Installs even on copies with a higher number |
| `[skip release]` | Push without publishing anything |

`[no release]` and `[skip ci]` also skip.

Two other ways in:

```bash
git tag v2.0.0 -m "Rewrite the billing screen" && git push origin v2.0.0
```

uses that exact number and that message as the notes. And the **Run workflow**
button on the repository's Actions tab releases on demand, optionally at a
version you type.

---

## When a rebuild is needed

`ui/` is the only folder that reaches a shop through a release. `main.py`,
`updater.py`, everything in `core/`, `database/` and `network/`, the fonts and
the runtime hooks are all frozen inside the EXE. The frozen importer serves
those modules from inside the executable, so a loose `.py` written next to it
is never read — a change to one of them has not shipped until a new EXE is
installed on the PC.

The pipeline says so rather than leaving it to memory. When a push touches any
of those files, the release notes carry a **Rebuild needed** section listing
them, and the workflow run's summary page repeats the warning.

To ship one:

```bash
python build.py
```

The build stamps the highest git tag into `build_version.txt` inside the
bundle, so the new EXE reports the right number without anybody editing a
version string. Then copy `dist\` to each PC as usual.

Build from a checkout that has the tags (`git fetch --tags`). Without them the
build falls back to `FALLBACK_VERSION` in `updater.py`, and an EXE that
under-reports its version re-downloads an update it already has.

---

## Where the version is shown

| Place | Shows |
|---|---|
| Sidebar footer | Installed version, plus **UPDATE READY** while a newer one is coming down |
| Sign-in screen | Installed version |
| Settings → System Information | Installed version, latest published version, release date, what changed, and a **Check now** button |
| Dashboard | A banner and a toast when a version lands |

All of it comes from one place: `AurumOS.version` in `ui/boot.js`, which every
screen loads. It reads the installed number from the app and the newest
published number from `api.github.com`, and repaints anything tagged
`data-aurum-version`, `data-aurum-latest` or `data-aurum-update`. Offline, the
installed number stays on screen and the latest column shows a dash.

It asks GitHub's API rather than reading `version.json` directly because
release-asset downloads send no CORS header, so a page cannot fetch them. The
app's Python side has no such limit and does the actual downloading.

Keeping this in `ui/` is deliberate: it means the version display can be fixed
by a push, on machines whose EXE is months old.

---

## Checking a release before you push

```bash
python release_plan.py
```

Prints the number it would pick, the notes it would write, and whether a
rebuild is needed. It changes nothing.

```bash
python test_release_plan.py
```

Covers the numbering rules, the commit markers, which files count as frozen,
and what an installed copy accepts — including against a throwaway git
repository built for the test.

---

## If an update does not arrive

- **The app shows an old number.** Look at `version.lock` next to
  `AurumOS.exe`. It is what the screens read. Missing or older than the EXE's
  own number means the EXE's number wins.
- **The release published but nothing downloaded.** Open the release on GitHub
  and check the assets are there, `version.json` included. `stage_release_assets.py`
  fails the build rather than publishing a manifest whose files would 404.
- **The number went down.** GitHub marks the most recently *published* release
  as "Latest", regardless of its number. That is why numbering runs from the
  highest tag; if the line is genuinely being reset, use `[force]`.
- **A Python change seems ignored.** It is. See
  [When a rebuild is needed](#when-a-rebuild-is-needed).
