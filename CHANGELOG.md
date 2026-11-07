# Changelog

## 2.1.0 - 2026-11-07

From a first install on another computer: less defense, an install that writes, a README that reads once.

- `install --write [--settings PATH]` merges the hook into `~/.claude/settings.json`: the file as it was before the
  first run is saved once as `settings.json.bak-ctx` (mode kept, never refreshed, a link there is not followed), a
  second run changes nothing, a stale ctx command (moved clone, other Python) is replaced rather than duplicated,
  and invalid JSON, a duplicated key or absurd nesting is refused without writing. Only a command of the form
  `<python> <path>/ctx.py hook session-start` counts as ctx's; a hand-edited one is left alone.
- `install` alone prints only the JSON block on stdout (the hint goes to stderr), so it can be piped.
- Redaction keeps known token formats and `user:password@` in URLs only. The heuristics are gone: `password=`,
  `token=`, camelCase keys, `bearer`, 40-character base64 keys and long letter-and-digit runs now pass. Keep secrets
  out of `STATE.md`, as the rule says.
- Invisible marks are a single character class; other combining marks (Thai, Devanagari) are kept. The `[ctx]`
  look-alike check also catches combining marks around its letters (`[c̲tx]`).
- README rewritten in short sentences; the scan detail moves to `docs/scan.md`. The rule splits into a core rule
  and an optional block for git worktrees and monorepos. `docs/measures.md` says the 2.0 scripts were lost and
  gives the protocol.

## 2.0.4 - 2026-11-06

Second adversarial review round on 2.0.3.

- `<` and `>` are escaped in every quoted string: a `STATE.md` line can no longer close the tag a host wraps the
  context in (`</system-reminder>` came out verbatim). The footer reads "Details: read STATE.md in the folder."
- A `STATE.md` with more than one name (a hard link, which could be any file of yours) is not read.
- Redaction: runs of 40 letters and digits after `_` (`gsk_...`, `dop_v1_...`), `r8_` keys, camelCase names
  (`clientSecret=`, `apiToken:`; `accessKey=`, not any `...Key=`), `mot de passe` and `mdp`.
- A scan stopped by the folder cap is complete when the folders left have no sub-folder.
- `install` explains the merge for each case (no file, no `hooks`, no `SessionStart`, a list already there) and
  warns that a second `hooks` key silently drops the first.
- Tests: 101, one example per secret pattern, secrets straddling the 60- and 120-character caps of names, notices,
  hard link, tag escape, quoted interpreter; tests that are not about time no longer depend on the real one-second
  budget, and the fake clock patches ctx only.
- Rule: shorten line 1 rather than cut it; "Waiting: ..." when the next step waits on a person or an answer; no
  `STATE.md` created after only reading a folder. README: the price of the rule in tokens and in the course chain.

## 2.0.3 - 2026-11-06

From an adversarial review on four angles (injection, concept, new user, robustness and tests).

- A first line within 200 characters as written is no longer cut because normalization lengthened it (`…` becomes
  `...`): the cap allows what normalization added, up to 20 characters. Real lines lost the end of their Next.
- Redaction: URL passwords that hold `/` or `@`, `password:` / `token=` / `api_key=` followed by letters and digits,
  and 40-character base64 keys (AWS secret keys) are redacted. Mongolian variation selectors are removed.
- A tree of exactly 20000 folders is no longer reported as cut short.
- `install` writes the Python that ran it instead of `python3`, and says how to merge the block.
- Tests: 94 (from 75), one per defense: fake clock for each deadline check, folder cap below the top level, secrets
  in folder names and across the cap, read size, sort ties and order, cap bounds; the install test passes in a
  path with spaces.
- README: most of the measured gain is the rule's, the hook saves turns from a parent folder; how to merge the
  hook block; the hook also runs on resume, `/clear` and compaction; limits for archives, huge folders and a moved
  clone. Rule: Next is never "confirm the state".

## 2.0.2 - 2026-11-06

- The 200-character cap holds: text is normalized (NFKC) before redaction and capping, so expanding characters no
  longer grow a line past it, and full-width tokens are redacted.
- One folder cannot starve the others: the folders directly below the start folder are walked in turns, one
  folder read each per round; a read that would pass the 20000-folder cap takes half of what is left; the time
  budget is checked before each folder read and at each folder visited (15000 slow folders took 41 s in 2.0.1, so
  the hook timed out; now 1 s).
- Outside git, entries are grouped one or two levels below the start folder (`projects/<p>`), so twelve projects
  without a repository at their root are all shown.
- An epoch date is a date (no longer "future date"); a front matter block is skipped; capping keeps the text when
  the line has no early space; `host:port@` in a URL is not redacted; more look-alikes of `[ctx]` are neutralized.
- Rule: update `STATE.md` when the folder's state changes (not after every file change); line 1 puts a blocker
  first.
- README: quick start, uninstall in full, real sample output, scan details as a list; measures moved to
  `docs/measures.md` with the cases where ctx did worse.

## 2.0.1 - 2026-11-06

- Rule: update `STATE.md` only when Status, Next or Decisions change; a touch-up (typo, comment, docstring,
  formatting) does not need it. Measured on copies of real folders: 6 of 6 touch-ups left `STATE.md` alone (2.0.0:
  updated almost every time, at about twice the tokens of no rule), 8 of 8 state-changing tasks still updated it.
- Rule: line 1 names any blocker, since it is the only line shown in parent folders.

## 2.0.0 - 2026-11-06

ctx lite. Each folder's state is a `STATE.md` written by the agent itself, asked by a rule in `AGENTS.md`; ctx is
one file that surfaces the first line of the `STATE.md` files below the session's folder at session start.

- Scan: up to 4 levels below the start folder, at most 20000 folders and one second; a scan cut short says so.
  Hidden folders, dependencies and build output are skipped; symlinked folders are not entered; only regular
  `STATE.md` files owned by the current user are read, opened without following links or blocking.
- Output: 30 lines, at most 8 per group (git repository below the start folder, or folder directly below it
  outside git), shared in turn between the folders directly below the start folder; left-out entries are named;
  newest first, a future date sorts last, a date from another year shows the year.
- Safety: folder names and first lines are flattened to one line without control or known invisible characters,
  shown as JSON strings, with `[ctx]`-like markers neutralized; known token formats, URL credentials and long
  random runs are redacted. No program runs and `.git` is never opened.
- `install` prints the hook block with a quoted path; the hook resolves a relative `cwd`; `#77` keeps its `#`.
- Removed: the model rewrite, `CONTEXT.md` nodes, decisions kept by code, history and undo, locks and queues, all
  commands but `hook session-start`, `tree` and `install` (which now only prints). The 1.x hooks other than
  session-start are accepted and do nothing, so an old install keeps working until it is cleaned up.
- 1.x stays on branch `v1` (tag `v1.2.0`).
- Community files: code of conduct, issue and pull request templates.

## 1.2.0 - 2026-11-06

The 1.x entries describe branch `v1`, not this tree.

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
