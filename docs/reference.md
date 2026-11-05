# ctx reference

The short intro is the [README](../README.md); design and failure handling are in [how-it-works.md](how-it-works.md).
This page is the one place for files, size caps, configuration and environment.

## Files

- `<folder>/CONTEXT.md`: a summary (max 300 characters) and the sections, in this order, "Status" (max 20 lines,
  800 characters), "Next" (max 5 lines, 300 characters), "Decisions" (optional, absent = empty; owned by code, max 15 lines and 1000 characters),
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

- **Node marker.** A node is a folder whose `CONTEXT.md` ctx wrote on this machine (its state in `~/.ctx/state`
  records the last version; a moved folder whose file is still that version stays a node). The `ctx: 1` header
  that `ctx init` writes is not enough: a cloned or committed `CONTEXT.md` carries it too. Any other `CONTEXT.md`
  is foreign: ignored everywhere, never injected nor rewritten, listed by `ctx doctor` on an `info:` line (not a
  problem). `ctx init` adopts a foreign file that carries `ctx: 1` (its Notes are kept, the rest starts anew,
  the old file goes to history) and refuses any other until you move or rename it.
  A container without child nodes yet is fine. Folders between two nodes need not be nodes: a node reports to the
  closest node above it that lists it (`~/projects/tools/cli` to `~/projects`, listed there as `tools/cli/`; a
  parent looks at most 3 levels down and never into hidden folders). Only a node with no node at all above it gets an `info:` line from `doctor` (not a problem), with
  the `ctx init --no-absorb <root>` that would collect it.
- **Unrouted files.** A file no node takes (under a container, or in no node at all) is logged as `unrouted`
  with its folders and the `ctx init <folder>` hint, never dropped silently; ctx never creates a node by itself.
- **Lost header.** A `CONTEXT.md` ctx wrote before stays a node even if an agent or an editor removed its
  `ctx: 1` header; the next sync puts the header back.
- **Injection.** Only the summary and the five known sections are injected, inside a `<ctx-data>` frame, Decisions
  first (right after the summary, before Status); other header keys and sections are dropped, and so are empty or
  placeholder sections and a computed summary (it only repeats the Subfolders lines). The text starts with one
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
- **Private.** `private: true` in a node's header keeps its summary and its decision count out of its parent:
  the parent's Subfolders line shows `(private)`, a computed summary skips it, `ctx show --tree` shows `(private)`.
  The node itself is injected and rewritten as usual.
- **Summaries.** A node never rewritten at its own level (placeholder or `summary: auto`) shows a computed summary,
  no model call: its non-private children, most recently updated first, as `<child>: <summary>` (cut on a word boundary,
  to share the room, at least 120 characters each) joined with ` · ` while they fit in 300 characters, then `also: <names>`. A session
  rewrite keeps its own summary, which bubble-up never overwrites.
- **`.ctxignore`.** Optional file in the root, one glob per line (`#` comments): matching folders are never nodes or
  scanned. The root is `CTX_ROOT` if set, else your home.

## Configuration and environment

- `~/.ctx/config`: `model` (default `haiku`; a value starting with `-` is refused) and `rewriter` (only `claude`;
  anything else is reported by `ctx doctor` and fails the sync with a clear message). There is no timeout key: a
  model call is cut after 100 s (one retry at most); a sync waits at most `2 x 100 + 30` seconds for another sync
  of the same node, and 30 seconds for a parent's lock.
- `CTX_OFF=1`: every hook is a no-op (any value except empty, `0`, `false`, `no`, `off`).
- `CTX_HOME`: use another directory instead of your home (state in `$CTX_HOME/.ctx`); `CTX_ROOT`: root of the node
  tree and of `.ctxignore`. Both are mostly for tests.
- `CTX_INNER=1` is set by ctx on its own model call to prevent hook loops; do not set it.

## Behaviour details

- **Work detection.** A session that routed no file to a node gets no model call, except the first time
  (placeholder summary), so a read-only "take stock" session can fill a new node. Instead (**sync-light**, log line
  `sync-light`, or `skip` when there is nothing), the `decision: ...` lines of its reports go to Decisions and a
  `next: ...` (or `prochaine étape: ...`) line replaces Next, through the same filters; Status and the summary stay.
- **Edits through Bash.** Hooks do not see them: at sync, files of the routed (or launch) node modified since the
  session start, and not in `.ctxignore`, `.git` or a child node, are added (log line `bash-scan`). The heuristic can
  also pick up a file edited by another process in the same window.
- **Liveness.** A session is over when its end hook wrote the `ended` marker (a resumed session clears it), or
  after 6 hours without a hook. The next session start syncs the sessions that are over; a running one syncs at its
  own end. agy has no end hook: its sessions are synced after 6 hours without a hook, or by `ctx sync`.
- **Trimming.** Over-long model output is trimmed, not rejected: a summary is cut at the last space to 300 characters
  with `…`, a section keeps its first whole lines within its caps (log line `trimmed`). A rewrite that copies a
  subfolder's own summary line is rejected and retried once, then the old file is kept.
- **Catch-up.** A session already synced with only new reports never reappears; new files bring it back. A sync
  that failed, or that could not get its node's lock (`busy`), is retried from the session's offsets.

## Log and doctor

`~/.ctx/log` has one line per event, `ISO-time event key=value ...`. The main events: `session-start` (session,
client, cwd, node, characters injected), `session-end` (session, `tool_events` = number of tool hooks, `files`),
`sync` (writer, tokens, durations), `sync-light`, `sync-fail` (cause), `sync-busy`, `sync-conflict`, `skip`,
`unrouted`, `state-restored`, `trimmed`, `filtered`, `catchup`, `session-purged`, `bash-scan`, `hook-error`,
`hook-timeout`, `rotate`. There is no line per tool call; lines written while syncing carry `session=`.

`ctx log [-n N] [--session ID] [--fail]` prints its last lines, the failures, or a whole session (oldest first);
grep the file for a node or a time. `ctx doctor` checks caps, ghost lines, failed and abandoned syncs, pending sessions, the last sync per node, sync-tool conflict copies and the rewriter command (foreign `CONTEXT.md` files are `info:` lines); it prints
the size of `~/.ctx`. `ctx doctor --fix` removes the state of folders gone for
30 days.

`ctx install-hooks --remove` writes the config back without the ctx hooks and deletes the `.ctx-bak` made at install
(a re-install keeps the first backup) when the result equals it; otherwise the backup is kept and its path printed. A
config that did not exist is deleted again, and the empty folder install created is left; a config that was `{}` comes
back as `{}` while that backup exists, otherwise the then-empty file is deleted. Without a terminal
the confirmation counts as "no" (nothing written): pass `--yes`.
