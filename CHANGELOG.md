# Changelog

## 2.0.0 - 2026-11-06

ctx lite. Each folder's state is a `STATE.md` written by the agent itself, asked by a rule in `AGENTS.md`; ctx is
one file that surfaces the first line of the `STATE.md` files below the session's folder at session start.

- Scan: up to 4 levels, 30 lines, at most 8 per git repository below the start folder; a `STATE.md` that is empty,
  a symlink or not a regular file does not hide the folders under it; only folders count toward the 20000 cap;
  a scan cut short says so even when it found nothing; dates in the future count as now.
- Folder names and first lines are flattened to one line without control characters (a folder name with a newline
  could forge a line); a `#` is removed only as a Markdown heading mark (`#77` stays).
- `install` quotes the path of `ctx.py`; the hook resolves a relative `cwd`.
- A `STATE.md` tracked by git is not shown (it came with a repository, maybe from someone else); every `STATE.md`
  below is listed, a folder's own state no longer hides those under it; lines are shown in quotes; known
  invisible characters are removed; folder paths are capped.
- Removed: the model rewrite, `CONTEXT.md` nodes, decisions kept by code, history and undo, locks and queues, all
  commands but `hook session-start`, `tree` and `install` (which now only prints). The 1.x hooks other than
  session-start are accepted and do nothing, so an old install keeps working until it is cleaned up.
- 1.x stays on branch `v1` (tag `v1.2.0`).
- Community files: code of conduct, issue and pull request templates.

## 1.2.0 - 2026-11-06

Last release of the full design (1.x). Real use showed work under containers getting lost and containers
injecting noise; three reviews then hardened and simplified the code.

- A file no node takes is logged as `unrouted` (with the `ctx init` hint) instead of being dropped silently.
- A node reports to the closest node above it that lists it, across plain folders (`tools/cli/` in its
  container's Subfolders; at most 3 levels, no hidden folder).
- Computed summaries: children most recently updated first, each cut on a word boundary, then `also: <names>`
  (was: children in name order, cut mid-word at 300 characters).
- The agent writes its own state: a project node's injection asks the agent to update Status, Next and the
  summary of its `CONTEXT.md` before finishing; when the session did, the sync adopts it without a model call
  (`writer=agent`), through the same validator. Only summary, Status and Next are taken: the header, Notes and
  Decisions start from ctx's last version, so an agent cannot remove them; a node whose header was removed stays a
  node. A session cannot change another node's state (`state-restored`). The model rewrite stays as the fallback.
- Next names the next unfinished work, never a re-check of done work (rewriter rule).
- Injection drops empty and placeholder sections and computed summaries; the Subfolders section is no longer
  injected twice.

Security and correctness:

- A node is a folder whose `CONTEXT.md` ctx wrote on this machine. A cloned or committed file with `ctx: 1` is
  foreign: never injected nor rewritten; `ctx init` adopts it (its Notes are kept, the rest starts anew). A moved
  node whose file is still ctx's last version stays a node. On a second machine sharing a folder, run `ctx init`.
- The instruction filter also drops "please", policy lines starting with always/never, "before editing anything"
  preconditions, lines addressed to the assistant and downloads piped into a shell or an interpreter.
- An edit made while a sync waits for the model (a person, another session's agent) is never overwritten: the sync
  is redone from the new file (`sync-conflict`). An edit made outside any session is the base as it is, Status and
  Next included (before, ctx's last version won).
- Secrets: `AWS_SECRET_ACCESS_KEY=`-style names, credentials in URLs, private-key blocks across lines. `~/.ctx` is
  private (0700, files 0600); `CONTEXT.md` keeps its mode.
- Session start no longer walks the parent's tree (3 s with a wide home before).
- Fixes: a BOM at file start, a `## Next` heading inside Notes, `ctx sync --session` now exits 1 on failure, a
  resumed session clears its end marker, purged sessions with pending work are logged (`session-purged`).

Simpler:

- Sync-light is done by code: a session that changed no file has its reports' `decision:` lines added to Decisions
  and a `next:` line written to Next, with no model call. The `light_words` config key and the blocker-word rule are
  gone.
- One lock per node instead of a work queue: a second sync of a node waits for the first (`sync-busy` if the wait
  runs out; the session is retried, not counted as failed). `~/.ctx/pending/` is no longer used.
- A session is over at its end hook, or after 1 hour without a hook (no CLI pid tracking any more). agy, which has
  no end hook, is synced 1 hour after its last hook or by `ctx sync`.
- Removed: the "summary drops a key figure" retry (a second model call), `ctx log --node` and `--since` (grep the
  log), doctor's retry/trim counters, the `external-edit` and `edit-during-sync` log lines, the recognition of hook
  entries installed before the `CTX_HOOK=1` marker (pre-1.0), the hidden `install-hooks --emit`.
- Benchmarks move to `bench/`.

## 1.1.0 - 2026-11-05

Continuity over long chains of sessions (the three weaknesses of the long-run benchmark).

- New `Decisions` section, owned by code: the rewriter's `decision:` lines are appended (a line with the same
  words is a duplicate); `decision superseded: #<n> -> <new>` (or the old text, single best match) marks one
  line; the model can never remove a decision. At most 15 lines and 1000 characters: hand edits are kept as
  written, but over the cap the oldest lines go first, hand-written ones included. `ctx undo` restores the whole
  previous file, Decisions included. Injected right after the summary (capped like Notes); the parent's
  Subfolders line shows ` · decisions: N`. Files without the section need no migration. The whole-file cap is
  now 5000 characters.
- Dated, cautious injection: the injected text starts with the time of the last sync that changed files (or of
  the read-only bootstrap, flagged as a hint; nothing for a node never synced) and asks to verify in the files
  before acting on `Next`.
- Sync-light: a session that changed no file but whose reports have a line starting with `decision:`, `next:`
  or `prochaine étape:`, or at least two blocker words (`light_words` in `~/.ctx/config`), gets a reduced
  rewrite of Next and Decisions only (log line `sync-light`); Status and summary are untouched.
- Fix: on systems where /bin/sh is dash (Debian, Ubuntu, WSL), a running session could be synced by another
  session's start before its last messages were recorded; the CLI pid is now resolved through the shell wrapper
  and a live pid keeps the session alive.

## 1.0.0 - 2026-11-05

First public release.

- Per-folder `CONTEXT.md` (Status, Next, Notes, Subfolders), marked `ctx: 1`, injected at session start into Claude
  Code, Cursor (`cursor-agent`) and Antigravity (`agy`) through their hooks.
- Rewritten after each session by one `claude -p` call (Haiku by default) from the files the session touched; Notes
  are never rewritten; each folder's summary bubbles up into its parent.
- Commands: `init`, `sync`, `show`, `undo`, `doctor`, `log`, `install-hooks` (diff and confirmation before any write,
  `--remove` restores the configs).
- Guards: injection cap, secret and instruction filters, isolation from sibling folders, hooks that always exit 0,
  crash-safe writes with history (`ctx undo`), `CTX_OFF=1`.
- Python 3.10+ standard library, no dependency; Linux, WSL and macOS.
- `ctx.py` at the clone root is the entry point hooks and the `ctx` link call; the code lives in `src/ctx`
  (11 modules). `python3 -m ctx` also runs it.
