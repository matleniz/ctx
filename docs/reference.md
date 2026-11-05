# ctx reference

The short intro is the [README](../README.md); design and failure handling are in [how-it-works.md](how-it-works.md).
This page is the one place for files, size caps, configuration and environment.

## Files

- `<folder>/CONTEXT.md`: a summary (max 300 characters) and the sections, in this order, "Status" (max 20 lines),
  "Next" (max 5), "Decisions" (optional, absent = empty; owned by code, max 15 lines and 1000 characters),
  "Notes" (yours, never rewritten; 800 characters max injected) and "Subfolders".
  The file never exceeds 5000 characters. In a git repository it is added to `.git/info/exclude`, so it is not
  committed by accident.
- `~/.ctx/`: `log` (one line per event, rotated at 1 MB to `log.1`), `declined`, `sessions/`, `history/`
  (last 10 versions of each node, for `ctx undo`), `state/` and `config`.
- Each CLI's hook config: see [clients.md](clients.md). `ctx install-hooks --remove` restores it.

A session receives the nearest node's `CONTEXT.md` only, at most 9500 characters. A parent keeps its own summary and
shows its children's summaries as quotes. With 8 children or fewer each is shown whole; beyond 8 each is cut to
80 characters; beyond 20, a count.

## Section titles

The titles are English (`Status`, `Next`, `Decisions`, `Notes`, `Subfolders`); the content follows the language of your sessions.

## Decisions

The model never writes this section. After each rewrite, every `decision: <text>` line of its output is appended
(as `- <text>`) and removed from Status, unless an active line has the same words (token sets without stop words,
Jaccard similarity of at least 0.8). The model sees the active lines numbered `#1`, `#2`...;
`decision superseded: #<n> -> <new>` marks line n `(superseded)` and adds the new one. With the old text instead
of `#<n>`, only the single best active match is marked, and only when its similarity is at least 0.6. Over 15
lines or 1000 characters, the oldest superseded line goes first, then the oldest active one (log line
`decision-dropped`), hand-written lines included. You may edit the section by hand: it is kept as is, like Notes.
`ctx undo` restores the whole previous file, Decisions included. The parent's Subfolders line of the node ends
with ` · decisions: N` when N active decisions exist (a count, never their text).

## Nodes

- **Node marker.** A node is a `CONTEXT.md` whose header carries `ctx: 1` (written by `ctx init`). Any other
  `CONTEXT.md` is foreign, even one in ctx's own format: ignored everywhere, never rewritten, listed by `ctx doctor` on an `info:` line (not a problem);
  `ctx init` refuses that folder until you move or rename the file.
  A container without child nodes yet is fine. A node whose parent folder is not a node is fine too: its summary does
  not bubble above it, and `doctor` says so on an `info:` line (not a problem) with the `ctx init --no-absorb <parent>` that would
  collect it.
- **Injection.** Only the summary and the five known sections are injected, inside a `<ctx-data>` frame, Decisions
  first (right after the summary, before Status); other header keys and sections are dropped. The text starts with one
  line giving the time of the last sync that changed files (`worked` in the node's state): "written after the
  last session that changed files here", or, while only read-only bootstraps happened, the bootstrap time and
  "bootstrapped from a read-only look, never corrected by work: treat as a hint"; then "Verify in the files
  before acting on Next." A node never synced gets no such line. Decisions is capped at 1000 characters there,
  like Notes at 800.
- **Absorb.** A new node takes all work below it, up to the next node: a session in a subfolder without a node
  receives that node's context. `ctx init --no-absorb` (for containers such as `~` or `projects`) restricts it to its
  own folder; subfolders without a node under such a container get the "create a context?" question (never asked under
  an absorbing node). The header key is `absorb`; a header without it means true. `ctx show --tree` marks
  `[absorb:false]` nodes.
- **Summaries.** A node never rewritten at its own level (placeholder or `summary: auto`) shows a computed summary:
  `<child> : <summary>` for each non-private child joined with `; `, cut at 300 characters, no model call. A session
  rewrite keeps its own summary, which bubble-up never overwrites.
- **`.ctxignore`.** Optional file in the root, one glob per line (`#` comments): matching folders are never nodes or
  scanned. The root is `CTX_ROOT` if set, else your home.

## Configuration and environment

- `~/.ctx/config`: `light_words` (comma-separated blocker words; a session without file changes gets a
  sync-light when its reports hold at least two of them as whole words; default
  `blocked,failed,cannot,bloqué,échec,impossible,refused`; a line starting with `decision:`, `next:` or
  `prochaine étape:` always counts), `model` (default `haiku`; a value starting with `-` is refused) and `rewriter` (only `claude`;
  anything else is reported by `ctx doctor` and fails the sync with a clear message). There is no timeout key: a
  model call is cut after 100 s, and a node's sync deadline is `2 x 100 + 30` seconds (two calls and a margin).
- `CTX_OFF=1`: every hook is a no-op (any value except empty, `0`, `false`, `no`, `off`).
- `CTX_HOME`: use another directory instead of your home (state in `$CTX_HOME/.ctx`); `CTX_ROOT`: root of the node
  tree and of `.ctxignore`. Both are mostly for tests.
- `CTX_INNER=1` is set by ctx on its own model call to prevent hook loops; do not set it.

## Behaviour details

- **Work detection.** A session that routed no file to a node does not rewrite it (log line `skip`), except the first
  time (placeholder summary), so a read-only "take stock" session can fill a new node, and except when its reports
  hold a line starting with `decision:`, `next:` or `prochaine étape:`, or two `light_words` words: then a reduced rewrite (log line
  `sync-light`) sees only the old Next and Decisions and the reports, and may change only Next and Decisions.
- **Edits through Bash.** Hooks do not see them: at sync, files of the routed (or launch) node modified since the
  session start, and not in `.ctxignore`, `.git` or a child node, are added (log line `bash-scan`). The heuristic can
  also pick up a file edited by another process in the same window.
- **Liveness.** Hooks record the CLI's pid (past the `sh -c` that runs the hook command) and start time in
  `~/.ctx/sessions/<id>/pid`; the next session start syncs at
  once a session whose process is gone. A session whose CLI process is alive (same start time) counts as running
  whatever its activity, for at most 12 hours idle. Without a pid file, a session active in the last 600 seconds counts as running.
- **Trimming.** Over-long model output is trimmed, not rejected: a summary is cut at the last space to 300 characters
  with `…`, a section keeps its first whole lines within its caps (log line `trimmed`). A rewrite that copies a
  subfolder's own summary line is rejected and retried once, then the old file is kept.
- **Catch-up.** A session without pid is caught up once, then closed; a session already synced with only new reports
  never reappears.

## Log and doctor

`~/.ctx/log` has one line per event, `ISO-time event key=value ...`: `session-start` (session, client, cwd, node,
characters injected), `session-end` (session, `tool_events` = number of tool hooks, `files`), `sync` (tokens,
durations), `sync-fail` (cause), `skip`, `trimmed`, `catchup`, `bash-scan`, `hook-error`, `hook-timeout`, `rotate`.
There is no line per tool call; lines written while syncing carry `session=`.

`ctx log [-n N] [--node DIR] [--session ID] [--since 30m|2h|1d] [--fail]` filters it (`--session` prints a whole
session, oldest first). `ctx doctor` checks caps, ghost lines, failed and abandoned syncs, pending sessions, the last sync per node, sync-tool conflict copies and the rewriter command (foreign `CONTEXT.md` files are `info:` lines); it prints
the size of `~/.ctx` and rewrite-retry, trimmed and sync counts. `ctx doctor --fix` removes the state of folders gone for
30 days.

`ctx install-hooks --remove` restores the pre-install bytes from the `.ctx-bak` made at install (a re-install keeps the
first backup). A config that did not exist is deleted again, and the empty folder install created is left; a config that
was `{}` comes back as `{}` only while that backup exists, otherwise the then-empty file is deleted. Without a terminal
the confirmation counts as "no" (nothing written): pass `--yes`.

## Code layout

`ctx.py` at the root of the clone is only the launcher: the installed hooks and `~/.local/bin/ctx` run it, it puts
`src/` (found next to its own real path) on `sys.path` and calls `ctx.cli.main()`; `python3 -m ctx` does the same
with `src/` on `PYTHONPATH`. The program is the package `src/ctx/`, standard library only. Modules, in import order
(each imports only from those above it):

- `paths.py`: constants, state dir, log, atomic writes, directory locks, deadline, launcher path.
- `filters.py`: secret, instruction-shaped and structure-line filters.
- `doc.py`: `Doc`, parse and render; history snapshots, per-node state, log reading.
- `tree.py`: `.ctxignore`, node discovery, declined folders, the Subfolders section, bubble-up, git exclude.
- `sessions.py`: hook collection, pending work, session liveness, catch-up.
- `rewriter.py`: prompt, rewriter table, config, the model call, output parsing.
- `sync.py`: per-node queue, rewrite, session sync, bash scan, deadlines.
- `hooks.py`: client table, session-start injection, hook dispatch.
- `install.py`: hook entries in the CLIs' configs (merge, review, removal).
- `doctor.py`: the checks and fixes behind `ctx doctor`.
- `cli.py`: commands, argument parser, `main()`.

Tests patch a name in the module that uses it (`ctx.sync.write_doc`), not on `ctx`: the package only re-exports
copies.

## Tests

- Unit suite, offline: `python3 -m unittest discover -s tests`.
- Integration runners in `tests/integration/` use a real `claude` on a throwaway test tree (`CTX_HOME`) and write their
  evidence to `$CTX_EVIDENCE_DIR` (default: a `ctx-evidence` folder in the system temp dir): `run_readme.py` (the user journey), `run_install.py` (install and uninstall in a temporary
  HOME, no model), `run_core_battery.py`, `run_resilience.py`, `run_usefulness.py` (longer batteries; see `SUMMARY.txt` in each
  output folder). They pass `--setting-sources project,local` so only the test tree's hooks run when your own are
  installed.
- Hidden options used by the runners: `install-hooks --emit FILE` and `--settings-file FILE`.
