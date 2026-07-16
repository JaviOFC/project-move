# project-move

Move a Claude Code project directory without losing its sessions, memories, or
other state — on Windows, macOS, or Linux.

Claude Code ties a lot of state to a project's absolute path. A plain `mv` /
drag-and-drop / PowerShell `Move-Item` orphans all of it. `project-move` finds
every reference it can and updates it, or tells you exactly why it won't.

**Always run a dry-run first (the default — see below) and read the plan
before passing `--execute`.**

**Fully close Claude Code (and any other process that might touch the same
project or `~/.claude`) before running `--execute`.** This tool detects — and
refuses to overwrite — a state file that changed after it was scanned, but
that's a safety net, not a substitute for not racing a live Claude Code
session in the first place.

## The problem

Claude Code stores state in `~/.claude/` keyed to the project's path — some of
it as path-encoded directory names, some of it as literal path strings inside
JSON/JSONL files:

| What breaks | Where it lives |
|---|---|
| Session transcripts | `~/.claude/projects/<encoded-path>/` |
| Project memories | `~/.claude/projects/<encoded-path>/memory/` |
| File change history, todos, shell snapshots, debug logs | `~/.claude/file-history/`, `~/.claude/todos/`, `~/.claude/shell-snapshots/`, `~/.claude/debug/` (path-encoded on at least some Claude Code versions — see caveat below) |
| Session index | `~/.claude/history.jsonl` |
| Permission entries (keyed **by** the literal path) | `~/.claude.json` |
| Plugin scope entries | `~/.claude/plugins/installed_plugins.json` (where a `projectPath` field is present) |
| Project settings | `<project>/.claude/settings.local.json` |
| Cross-project references | other projects' `.claude/settings.local.json` |
| Memory file content | `~/.claude/projects/*/memory/*.md` |

This tool builds on [Chase Adams' `claude-mv`](https://curiouslychase.com/posts/rescuing-your-claude-conversations-when-you-rename-projects/)
and [gwpl's documentation](https://gist.github.com/gwpl/e0b78a711b4a6b2fc4b594c9b9fa2c4c)
of Claude Code's state layout, extended to cover Windows correctly and to add
real backup/rollback and dry-run guarantees.

## Install

```bash
git clone https://github.com/JaviOFC/project-move.git
```

Don't pipe a downloaded script straight into an interpreter (`curl ... | python`).
Download it, read it if you like, then run it from disk.

**Requirements:** Python 3.10+, standard library only (no dependencies).

On macOS/Linux:
```bash
chmod +x project-move
./project-move ~/projects/my-app ~/projects/archive/my-app
```

On Windows, run it with the `py` launcher or `python` (there is no `.py`
extension on the script, but both run a file directly regardless of
extension):

```powershell
py project-move "C:\Development\my-app" "C:\Development\Archive\my-app"
```

or

```powershell
python project-move C:\Development\my-app C:\Development\Archive\my-app
```

## Usage

### Dry-run (default — always do this first)

```bash
project-move ~/projects/my-app ~/projects/archive/my-app
```

Shows the platform detected, both paths in normalized form, the Claude path
encoding used for each, every file/directory that would change with an exact
reference count, every conflict, the backup scope, and the move strategy —
without creating, deleting, renaming, or backing up anything.

### Execute

```bash
project-move ~/projects/my-app ~/projects/archive/my-app --execute
```

Windows example:

```powershell
py project-move "C:\Development\my-app" "C:\Development\Archive\my-app" --execute
```

### Context-only mode

If the directory has already been moved (e.g. a parent directory was renamed
by something else) and only Claude Code's internal state needs updating:

```bash
project-move --context-only ~/old-path/my-app ~/new-path/my-app --execute
```

### Non-interactive / scripted runs

Merging into an existing, conflict-free destination normally requires typing
the destination path back at a confirmation prompt (see below). Pass `--yes`
to skip that prompt in scripts or CI — you still get the same dry-run/backup
guarantees.

### Flags

| Flag | Description |
|---|---|
| `--execute` | Actually perform the move (default: dry-run) |
| `--context-only` | Update Claude state only — directory already moved |
| `--no-backup` | Skip backup for a plain, non-merge move. **Ignored** (and reported) when a merge is happening — backups are mandatory there. |
| `--yes`, `-y` | Skip the interactive merge confirmation prompt |

## How Claude Code encodes paths

This was verified empirically against a real Windows Claude Code install by
comparing real `~/.claude/projects/<name>` directory names against real paths
recorded in `~/.claude.json` — not assumed from the POSIX behavior.

- **Windows:** every character outside `[A-Za-z0-9-]` — colons, backslashes,
  forward slashes, spaces, and non-ASCII characters — becomes `-`. Case and
  separator style are preserved exactly as given; Claude Code does not
  normalize before encoding.

  ```
  C:\Development\Arduino\sketches        ->  C--Development-Arduino-sketches
  c:/Development/CPlusPlus/Shell         ->  c--Development-CPlusPlus-Shell
  C:\Development\Apps\Elkjøp Support Tool -> C--Development-Apps-Elkj-p-Support-Tool
  ```

- **POSIX (macOS/Linux):** unchanged from the original behavior — only `/`,
  `.`, `_`, and space become `-`.

  ```
  /Users/you/projects/my-app  ->  -Users-you-projects-my-app
  ```

**Caveat:** on at least one current Windows Claude Code install, only
`projects/` was reliably path-encoded — `file-history` was keyed by session
UUID and `shell-snapshots` was flat, not path-encoded, and `todos`/`debug`
didn't exist at all in that install. `project-move` still checks all five
documented subdirectories defensively (harmless no-ops where a directory
doesn't exist in that form), but don't assume all five are populated on every
Claude Code version.

**Also observed:** at least one real install additionally had a duplicate,
undocumented path-encoded directory directly under `~/.claude/<encoded>/`
(outside `projects/`), with content mirroring the `projects/<encoded>/` one.
`project-move` detects this, includes it in the dry-run plan, and backs it up
before any execute — but **never renames, merges, or deletes it**, since its
purpose and lifecycle aren't documented anywhere. If you have one of these,
verify what it is before touching it by hand.

## Windows path scenarios

Explicitly handled, with tests:

- Drive-letter paths (`C:\...`), including mixed drive-letter casing (`C:` vs `c:`) — NTFS is case-insensitive, so these are treated as the same location.
- Paths with spaces, dots, and underscores.
- UNC paths (`\\server\share\...`).
- Sibling paths with similar prefixes (`App` vs `App2`) — relationship checks are component-wise, never a naive string-prefix check, so this is never mistaken for nesting.
- **Case-only renames** (`MyApp` -> `myapp`) — detected as same-file-different-case rather than "destination already exists," and performed through a safe temporary intermediate name.
- **Cross-volume moves** — detected via actual device IDs (not drive-letter/anchor comparison, which is unreliable especially on POSIX). Handled transactionally: copy to a uniquely-named staging directory beside the real destination (on the destination volume), verify the staging copy **file-for-file by content hash** (not just size — two same-size files with different bytes are caught), then atomically rename the verified staging directory to the real destination, and only then remove the source. If anything fails at any point, the source is left completely untouched and the real destination path is never created; see "If something fails partway" below for what happens to a partial/unverified staging copy.

Explicitly refused, clearly, with no guessing:

- Source and destination are the same path.
- Destination is inside the source directory (would move a directory into its own subtree).
- Destination is an existing ancestor of the source (refuses to merge a directory into its own ancestor).

## Safety features

### Dry-run is genuinely read-only

The default dry-run mode never creates, renames, deletes, or backs up
anything — this is enforced by tests that snapshot the filesystem before and
after a dry-run and assert nothing changed.

### Conflict scanning and merges

If the destination already exists, `project-move` does a **complete,
recursive conflict scan** — of the project directory itself and of every
path-encoded Claude state directory — before anything is written.

**A conflict is ANY relative path that exists in both the source and the
destination tree — including two directories with the same name.** For
example, if both source and destination have a `shared/` subdirectory, that
is a conflict even if the files inside `shared/` don't collide by name.
`project-move` deliberately does not attempt a recursive, file-by-file merge
of two directories that share a name — the merge only proceeds when the
source and destination trees are **completely disjoint** at every level, so
there is never a partial, guessed, or silently-overwriting merge.

- **Any conflict anywhere aborts the whole operation before a single byte is
  written.** Every conflict is listed, both in the dry-run plan and before
  `--execute` proceeds.
- If there are no conflicts, the merge is safe by construction: every file in
  the source has a free slot in the destination, so the merge cannot skip a
  file, silently overwrite one, or need to delete a non-empty source
  directory to "finish."
- There is no "Clean" (delete-the-destination) option. Deleting an existing
  destination reliably, with real backup and rollback, would mean being able
  to duplicate a possibly-huge destination tree — the same problem this tool
  avoids for the source. If you want the destination gone, remove it
  yourself first and re-run.
- Merging always requires either an interactive typed confirmation (you must
  type the destination path back exactly) or `--yes`. `--no-backup` is
  ignored for merges — a backup is mandatory, and you'll see a note saying so.

### If something fails partway through `--execute`

Execution happens in stages: create backup, rename Claude state directories,
write state files (history, claude.json, plugins, settings, memory, session
content), then move the project directory itself. **An error at any stage
stops every later stage** — in particular, the project directory is never
moved after a failed state-directory rename or a failed state-file write.

This tool does not attempt automatic rollback of a partially-completed stage:
reliably undoing a partial rename or a partial set of file writes isn't
something it can guarantee without risking further damage. Instead it stops
immediately, leaves both the source and whatever has already changed exactly
as they are, and points you at the backup (with `MANIFEST.txt` describing
exactly what to restore and where). Re-run once you've resolved whatever
caused the failure.

### Backup and rollback

Before any write, `--execute` backs up **every file and directory it found a
genuine reference in**, including files the original version of this tool
missed:

- Session `.jsonl` files (previously not backed up at all).
- Every path-encoded Claude state directory being renamed/merged (full
  `shutil.copytree` snapshot).
- `history.jsonl`, `~/.claude.json`, `installed_plugins.json`.
- The project's own and cross-project `settings.local.json` files.
- Memory `.md` files.
- Any detected undocumented root-mirror state directory (see above).

```
~/.claude/backups/project-move-20260320-121500-123456/
├── MANIFEST.txt
├── files/...            (exact copies, one per backed-up file)
└── dirs/...              (exact copies, one per backed-up directory tree)
```

`MANIFEST.txt` lists, for every item: its original location, its backup
location, and the restore step (copy it back).

**The project directory itself is not duplicated into the backup** — for a
same-volume move this is a single atomic rename (nothing to roll back: either
it happened or it didn't), and for a cross-volume move the untouched source
*is* the rollback, since it is only removed after the copy is verified
**file-for-file by content hash** against it. If a backup cannot be created,
`--execute` aborts before making any change.

### Cross-volume moves are transactional

When source and destination are on different volumes, a plain rename isn't
possible, so `--execute` does the following instead:

1. Allocates a uniquely-named staging directory beside the real destination
   (same parent, so it's on the destination volume) — e.g.
   `.myapp.project-move-staging-<random-id>`. An existing path with that
   exact name is never reused or overwritten; a fresh name is generated
   instead.
2. Copies the source into the staging directory (symlinks are preserved as
   symlinks, matching what a same-volume rename does — see below).
3. Verifies the staging copy against the source **by streaming content
   hash**, not just by comparing relative paths and file sizes — two
   same-size files with different bytes are caught.
4. Only once verification passes, atomically renames the staging directory
   to the real destination path (same-volume rename, since staging sits
   beside it).
5. Only once the destination is established, removes the source.

If the copy or the verification fails at any point, the source is left
completely untouched, the real destination path is never created, and the
(possibly partial) staging copy is preserved at its staging path rather than
silently deleted — the output names the exact staging path so you can
inspect or clean it up. Once any staging content has actually been created
on disk, that's treated as a real mutation for exit-code purposes (see
below), even though the real destination was never touched.

Symlinks: the source tree is copied with `symlinks=True`, so a symlink stays
a symlink at the destination (its target text is copied, not the content it
points to) — the same behavior a same-volume rename already has, since
renaming never follows or transforms symlinks. Verification compares symlink
target text directly rather than following the link, since content on the
other end of a symlink may live entirely outside the tree being moved.

### Concurrent-modification protection

Before writing each file, `project-move` re-reads it and compares a hash of
its current content against a hash taken when it was scanned. If they don't
match — something else (most likely Claude Code, if it wasn't fully closed)
wrote to the file in the meantime — that file is **not** overwritten. This is
recorded as an error, which (per the stage-stop behavior above) prevents the
project directory from being moved. Re-run project-move once the other
process is no longer touching the same state.

This is an optimistic check, not a lock: it protects against clobbering a
concurrent write it can detect, but the right practice is still to close
Claude Code before running `--execute` in the first place.

### Post-move verification

After every write, the tool re-scans **every item it planned to change** —
session files, history.jsonl, claude.json, plugins, the project's own and
cross-project settings.local.json, and memory files, re-mapped to wherever
they ended up after the directory/state moves — for any reference to the old
path that should have been updated but wasn't, and reports each one
individually rather than printing "Done" unconditionally. A case-only rename
is verified with a case-sensitive check (the normal check would otherwise
"find" the old path inside the correctly-updated new-cased content, since
they're the same text modulo case). An undocumented root-mirror directory
(see above) being present never suppresses checking the real, documented
state directories — they're unrelated locations.

### Conservative text replacement

Claude Code's JSON files store Windows paths JSON-escaped (backslashes
doubled). `project-move` accounts for this, plus forward-slash and Git-Bash
(`/c/Users/...`) path forms actually observed in real `settings.local.json`
files. Where a file parses as JSON, the tool cross-checks the number of
structural (parsed) references against the number of raw-text replacements
it's about to make; if they don't match, the file is left untouched and
reported rather than guessed at.

Every replacement is boundary-guarded so `App` is never replaced inside
`App2`, and a reference to a subpath (`.../App/subfolder/file.txt`) is
replaced correctly, keeping the subfolder suffix intact.

### JSON validation

Every JSON file this tool writes is validated with `json.loads()` immediately
before the write, and writes go through a temp-file-plus-atomic-replace in
the same directory — an interrupted or failed write never leaves a
partially-written or corrupted original in place.

### Error handling

Scan failures (unreadable files, permission errors) are collected, not
silently swallowed. `--execute` refuses to run if a required location
couldn't be scanned. The final report distinguishes successful updates,
warnings, notes (informational — e.g. "backup was forced on for this merge"),
stale references found during verification, and errors, and uses a distinct
message for each outcome — clean success prints `Done. Moved ...`; warnings
alone print `FINISHED WITH WARNINGS ...`; errors or unresolved stale
references print `FINISHED WITH PROBLEMS ...`. `Done.` never appears next to
a non-zero exit code.

The exit code itself is based on whether any *project or Claude-state*
mutation actually succeeded — a state directory was renamed/merged, a state
file was atomically replaced, the project directory was renamed or merged,
or (for a cross-volume move) content was actually created in the staging
directory, even if the final destination was never established — not merely
on whether an error was recorded, and not on whether a backup was created:

- `0` — completed, fully verified, no warnings.
- `1` — **aborted before any project/Claude-state mutation**: preflight
  failure, unresolved conflicts, a declined/missing merge confirmation, a
  failed backup, or the very first mutating step itself failing before it
  changed anything. **A safety backup may still have been created** in this
  case (e.g. the backup succeeded but the subsequent project-directory
  rename then failed) — creating a backup is not, by itself, a project or
  Claude-state mutation, so it doesn't change the exit code. If a backup was
  created, the output mentions it regardless of the exit code.
- `2` — **an error or unresolved stale reference occurred after at least one
  project/Claude-state mutation already succeeded** (a state directory was
  renamed, a state file was written, or the project directory was
  moved/merged/copied). This includes the concurrent-modification and
  stage-stop cases above — some real state changed, so treat the operation
  as partially complete and check the backup.

## Testing

```bash
py -m unittest discover -s tests -v
```

All tests run against isolated temporary directories injected via `ClaudeEnv`
— none of them touch your real home directory, `~/.claude`, or any real
project.

The suite is designed to pass on both Windows and Linux. Pure path-logic
tests (encoding, ancestor/relationship checks, the replacement engine) select
`PureWindowsPath`/`PurePosixPath` explicitly from an injected `windows=`
flag rather than the host OS, so a Windows-specific scenario gives the same
result whether the test runs on Windows or Linux. Filesystem integration
tests exercise the real host filesystem and default to the host's actual
platform. CI runs the suite on both `windows-latest` and `ubuntu-latest`
(see `.github/workflows/tests.yml`).

## What this adds beyond claude-mv

Chase Adams' [`claude-mv`](https://github.com/curiouslychase/dotfiles/blob/main/scripts/claude-mv)
handles the core move on macOS/Linux: renaming the path-encoded directories,
updating session file contents, and rewriting `history.jsonl`. It also
introduced `--context-only` mode, which `project-move` carries forward.

`project-move` adds:

- Correct, empirically-verified Windows path encoding and case/UNC/cross-volume handling.
- `~/.claude.json` permission entries (including the fact that they're keyed *by* the path).
- `installed_plugins.json` plugin scope paths, where present.
- Project and cross-project `settings.local.json` updates, including Git-Bash-style path forms.
- Memory file content updates.
- A genuinely read-only dry-run mode with exact per-file, per-form replacement counts.
- Full conflict scanning before any write, requiring completely disjoint trees, with no partial/silent merges.
- Real backup and rollback design, atomic validated writes, and honest exit codes tied to whether a mutation actually happened.
- Optimistic concurrency protection against a state file changing between scan and write.
- Execution stops immediately after any stage fails — a state-directory or state-file error always prevents the project directory from being moved.

## Background

Built after executing a full dev environment migration that moved 51 projects
across 7 locations. The core move (using `claude-mv`) worked well on
macOS/Linux, but a later Windows audit found the original path-encoding logic,
raw-text scanning, and destination-conflict handling were all unsafe there —
this version was rewritten to fix that while keeping POSIX behavior intact.

As of 2026, Claude Code has no built-in support for project moves
([#1516](https://github.com/anthropics/claude-code/issues/1516),
[#19483](https://github.com/anthropics/claude-code/issues/19483),
[#16417](https://github.com/anthropics/claude-code/issues/16417)).

## Acknowledgments

- [Chase Adams](https://curiouslychase.com/posts/rescuing-your-claude-conversations-when-you-rename-projects/) for `claude-mv`, which solved the core problem and inspired this tool
- [gwpl](https://gist.github.com/gwpl/e0b78a711b4a6b2fc4b594c9b9fa2c4c) for documenting Claude Code's internal state structure for the community

## License

MIT
