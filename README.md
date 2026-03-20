# project-move

Seamlessly move Claude Code projects without losing sessions, memories, or state.

When you move a Claude Code project directory, **12 things silently break**. Claude Code ties all state to the absolute path — sessions, memories, permissions, plugin scopes, file history, and more. A simple `mv` or Finder drag-and-drop orphans everything.

`project-move` finds and fixes all of them.

## The problem

Claude Code stores state in `~/.claude/` using [path-encoded directory names](https://gist.github.com/gwpl/e0b78a711b4a6b2fc4b594c9b9fa2c4c). When you rename or move a project, every reference to the old path becomes stale:

| What breaks | Where it lives |
|---|---|
| Session transcripts | `~/.claude/projects/-encoded-path/` |
| Project memories | `~/.claude/projects/-encoded-path/memory/` |
| File change history | `~/.claude/file-history/-encoded-path/` |
| Todos | `~/.claude/todos/-encoded-path/` |
| Shell snapshots | `~/.claude/shell-snapshots/-encoded-path/` |
| Debug logs | `~/.claude/debug/-encoded-path/` |
| Session index | `~/.claude/history.jsonl` |
| Permission entries | `~/.claude.json` |
| Plugin scopes | `~/.claude/plugins/installed_plugins.json` |
| Project settings | `.claude/settings.local.json` |
| Cross-project references | Other projects' `settings.local.json` |
| Memory file content | `~/.claude/projects/*/memory/*.md` |

This tool builds on [Chase Adams' `claude-mv`](https://curiouslychase.com/posts/rescuing-your-claude-conversations-when-you-rename-projects/), which solved the core problem — renaming the 5 path-encoded directories and updating session files. `project-move` extends that foundation to cover the full Claude Code ecosystem (plugins, permissions, cross-project references, memory files) and adds safety features like dry-run mode and prefix overlap detection.

## Install

```bash
curl -o ~/.local/bin/project-move \
  https://raw.githubusercontent.com/JaviOFC/project-move/main/project-move
chmod +x ~/.local/bin/project-move
```

Or clone and symlink:

```bash
git clone https://github.com/JaviOFC/project-move.git
ln -s "$(pwd)/project-move/project-move" ~/.local/bin/project-move
```

**Requirements:** Python 3.11+ (no dependencies — stdlib only).

## Usage

### Dry-run (default)

Shows everything that would change without touching anything:

```bash
project-move ~/projects/my-app ~/projects/archive/my-app
```

```
============================================================
 PROJECT-MOVE — DRY RUN
============================================================

  From: /Users/you/projects/my-app
    To: /Users/you/projects/archive/my-app

  8 action(s) planned:

  [ 1] encoded-dir/projects
       Rename projects/-Users-you-projects-my-app → ...

  [ 2] session-content
       Update 18 session .jsonl file(s)

  [ 3] history.jsonl
       Update 43 line(s)

  [ 4] claude.json
       Update 2 reference(s) in ~/.claude.json

  [ 5] plugins
       Update 1 projectPath entry in installed_plugins.json

  [ 6] own-settings
       Update 3 reference(s) in project's settings.local.json

  [ 7] cross-project-settings
       Update 2 other project(s)' settings.local.json

  [ 8] memory-files
       Update 1 memory file(s) with path references

  + Move directory

============================================================
  To execute: project-move /Users/you/projects/my-app /Users/you/projects/archive/my-app --execute
============================================================
```

### Execute

```bash
project-move ~/projects/my-app ~/projects/archive/my-app --execute
```

### Context-only mode

When the directory has already been moved (e.g., a parent directory was renamed), update only Claude Code's internal state:

```bash
project-move --context-only ~/old-path/my-app ~/new-path/my-app --execute
```

### Flags

| Flag | Description |
|---|---|
| `--execute` | Actually perform the move (default: dry-run) |
| `--context-only` | Update Claude state only — directory already moved |
| `--no-backup` | Skip `history.jsonl` backup |

## Safety features

### Prefix overlap detection

If the old path is a prefix of the new path (e.g., `/Users/you` → `/Users/you/projects`), a naive string replacement would corrupt every path. `project-move` detects this and refuses:

```
ERROR: Prefix overlap detected!
  Old: /Users/you
  New: /Users/you/projects
  A sed replacement of '/Users/you' would also match '/Users/you/projects',
  causing double-substitution.
```

### Tilde-form handling

Claude Code state files use both absolute (`/Users/you/...`) and tilde (`~/...`) path forms. `project-move` replaces both.

### Merge conflict resolution

When the destination already exists, `--execute` mode offers interactive options:

```
Destination already exists: /Users/you/projects/archive/my-app
  Claude context exists at destination (5 sessions)

  Options:
    [c] Clean — delete destination dir + its Claude context, then move
    [m] Merge — move source files into destination, merge Claude context
    [n] Abort (default)
```

### JSON validation

Plugin file updates are validated as JSON before writing — if the replacement would produce invalid JSON, the write is skipped.

## What this adds beyond claude-mv

Chase Adams' [`claude-mv`](https://github.com/curiouslychase/dotfiles/blob/main/scripts/claude-mv) handles the core move: renaming the 5 path-encoded directories, updating session file contents, and rewriting `history.jsonl`. It also introduced `--context-only` mode and merge conflict resolution — both of which `project-move` carries forward.

`project-move` extends that with:

- **`~/.claude.json` permissions** — old permission entries get rekeyed to the new path
- **`installed_plugins.json`** — plugin scope paths (`projectPath`) are updated
- **`settings.local.json`** — both the project's own settings and other projects that reference it
- **Memory file content** — paths inside `~/.claude/projects/*/memory/*.md`
- **Dry-run mode** — default behavior shows everything before touching anything
- **Prefix overlap detection** — refuses moves where the old path is a prefix of the new path (prevents silent data corruption)
- **Tilde-form handling** — replaces both `/Users/you/...` and `~/...` forms

## How Claude Code encodes paths

Claude Code converts absolute paths to directory names by replacing `/`, `.`, `_`, and spaces with `-`:

```
/Users/you/projects/my-app  →  -Users-you-projects-my-app
```

This encoded name is used as a directory name under `~/.claude/projects/`, `~/.claude/file-history/`, `~/.claude/todos/`, `~/.claude/shell-snapshots/`, and `~/.claude/debug/`.

## Background

Built after executing a full dev environment migration that moved 51 projects across 7 locations. The core move (using `claude-mv`) worked well, but a post-migration audit found 62 additional stale references in plugin registries, permission files, cross-project settings, and memory files. `project-move` was built to catch all of them in one pass.

As of March 2026, Claude Code has no built-in support for project moves ([#1516](https://github.com/anthropics/claude-code/issues/1516), [#19483](https://github.com/anthropics/claude-code/issues/19483), [#16417](https://github.com/anthropics/claude-code/issues/16417)).

## Acknowledgments

- [Chase Adams](https://curiouslychase.com/posts/rescuing-your-claude-conversations-when-you-rename-projects/) for `claude-mv`, which solved the core problem and inspired this tool
- [gwpl](https://gist.github.com/gwpl/e0b78a711b4a6b2fc4b594c9b9fa2c4c) for documenting Claude Code's internal state structure for the community

## License

MIT
