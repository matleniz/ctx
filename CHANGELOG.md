# Changelog

## 1.2.0 - 2026-11-05

Real use showed work under containers getting lost and containers injecting noise.

- A file no node takes goes to the root of its git repository, made a node on the spot (`auto-init`), unless
  ignored, declined or foreign; anything left is logged as `unrouted` instead of being dropped silently.
- A node reports to the closest node above it, across plain folders (`tools/cli/` in its container's Subfolders).
- Computed summaries: children most recently updated first, each cut on a word boundary, then `also: <names>`
  (was: children in name order, cut mid-word at 300 characters).
- Injection drops empty and placeholder sections and computed summaries; the Subfolders section is no longer
  injected twice.

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
