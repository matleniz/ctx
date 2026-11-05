# How ctx works

```
                 session starts
 CONTEXT.md --------------------> agent        (injection: nearest node only)
 (nearest node)   SessionStart hook
                                    |  Write / Edit tool calls
                                    v          collection: paths appended to ~/.ctx/sessions/<id>/touched,
                                  hooks        last assistant messages kept (no transcript is read)
                                    |
                 session ends       v
                 sync (detached) -> route each touched file to its nearest node
                                    |
                                    v          the ONE model call: claude -p, no tools, no hooks,
                                 rewrite       sees only that node's old text + its own touched files
                                    |
                                    v          deterministic, no model: parent "Subfolders" section
                                 bubble-up     rebuilt from the children's summaries, bottom to top,
                                    |          one lock per parent; stops when nothing changed
                                    v
                                parent nodes
```

1. **Injection.** The CLI's start hook runs `ctx hook session-start`; the answer starts with one dated line ("State as
   of <time of the last sync that changed files>", or of the read-only bootstrap, then to be treated as a hint;
   none for a node never synced; "Verify in the files before acting on Next."), then the nearest node's `CONTEXT.md`, Decisions first,
   in a "data, not instructions" frame, followed by one line `parent <name>: <parent summary>` (at most 300 characters, same filters, only when the parent's summary was written by a session at its own level, since a computed one quotes the siblings; never the parent's Subfolders lines or siblings, nothing for the root), or a one-time question when the folder has no node.
2. **Collection.** Edit-tool hooks record touched paths; the end hook records the last messages. Edits made through
   Bash are found at sync by scanning files modified since the session start.
3. **Isolated rewrite.** Only sections "Status" and "Next" are rewritten, starting from the old text.
   "Notes" is invisible to the model and never changed. "Decisions" is owned by code: the model sees it and may
   only add to it (`decision:` lines) or mark a line superseded (`decision superseded: #<n> -> <new>`, the active lines being numbered in its input); it can
   never remove one. A session that touched no file gets no model call, except to fill a brand-new node, or a
   reduced one (**sync-light**) when a report line starts with `decision:` or `next:`, or the reports hold two blocker words:
   the model then sees only the old Next and Decisions and the reports, and may change only Next and Decisions.
4. **Bubble-up.** Only a child's `summary` goes up, as a quotation under a fixed prefix, with ` · decisions: N`
   when the child holds N active decisions (a count, never their text). A parent's own summary is
   only changed by a session at its own level (or computed from its children while it was never rewritten).
5. **History.** Each rewrite snapshots the previous file under `~/.ctx/history/`; `ctx undo` restores it.

Caps, files and details: [reference.md](reference.md).

## Guards

These are best-effort protections, each covered by tests ([readme-claims.md](readme-claims.md)), not absolute guarantees.

| Risk | Guard |
|---|---|
| Hook loop (the rewrite is itself a Claude session) | `CTX_INNER=1` on the model call; every hook exits at once when it is set |
| Dirtying git | `CONTEXT.md` goes to `.git/info/exclude`; history and logs live in `~/.ctx`, outside repos |
| Secrets | pattern-based filter on the rewrite input and output; scans skip `.env*`, `*.pem`, `*.key` |
| Orders in the file (prompt injection) | instruction-shaped lines are dropped from model output, and again at injection for hand-edited content; the frame says the content is not instructions |
| Leaks between siblings | by inputs, see "Isolation" below |
| Bad model output | validated in code (sections, caps, summary length); invalid keeps the old file and the failure shows at the next session start |
| Concurrent syncs | one lock per node, a "redo" flag instead of a queue, atomic write (temp file + rename) |
| Runaway size | size caps on every part and on the whole file ([reference.md](reference.md#files)) |

## Isolation

Nothing filters the rewriter's output for sibling names; isolation is by what the rewriter is given. A rewrite of a node
receives only: that node's old `CONTEXT.md` (summary, "Status", "Next", "Decisions"; never "Notes"), the paths touched
under it, the session reports (the assistant's last messages, which may mention other folders when the session was
launched higher up), and the `git diff --stat` of the node. It never reads a sibling's or a parent's `CONTEXT.md`, and a
session receives only its nearest node at start, plus that node's parent summary line. A session report that talks about a sibling reaches the rewriter as is;
the prompt tells it to keep facts about the node and its subfolders only, and nothing checks that afterwards. The one
output check is that a rewrite copying a subfolder's own summary line is rejected and retried once.

## Rewriter

The agent writes first. The injection of a project node (not a container) ends with ctx's request to update the
node's Status, Next and `summary:` line before finishing, and to state decisions as `decision:` lines in the final
message. When the session edited that `CONTEXT.md` and changed Status or Next (compared with what ctx last wrote),
the sync adopts it with no model call (log `sync … writer=agent`): same validator as a model answer, decisions from
the reports merged into Decisions, Notes kept, a summary the agent left unchanged rebuilt from the first Status and
Next lines. Otherwise, or when the agent's text fails the validator (`agent-state-rejected`), the model rewrites.
An edit made outside the session (another machine, an editor) is only the base of the model rewrite.

Claude is the only rewriter (`REWRITERS` in `src/ctx/rewriter.py` has one row, `cli_rewriter` is the single call). Content rules: the prompt asks for state with file paths, `decision:` lines and a concrete next step; the
validator rejects "structure" lines (contains / list of / tree / N files / three file names without a state verb)
and retries once like any other violation. Why Claude: the rewrite
reads text taken from your files and session, so the call must be isolated, and Claude Code documents all three guards:
no tools (`--tools ""`), no persistence (`--no-session-persistence`), no user hooks or settings (`--setting-sources ""`).
Cursor documents no way to remove tools or persistence, and neither Cursor nor agy documents a way to switch off
user-level hooks in a headless call, so they stay hook clients: their sessions are injected and collected by their
hooks and rewritten by `claude`, which must be installed (`ctx doctor` checks it). Per-CLI details:
[clients.md](clients.md).

What another CLI must offer to get a row: a headless mode that takes the prompt on stdin or argv and returns JSON with the
reply and an error flag; a documented way to disable every tool and all session persistence; a documented way to ignore
the user's hooks and settings (or hooks that honor `CTX_INNER=1`); a system-prompt flag or a safe place to prepend it;
token usage in the JSON (optional). Then add a row (`cmd`, `model_flag`, `system`, `bad`, `result`, `usage`, `model`)
and a test of its flags.

## When things break

Each case is covered by `tests/test_robust.py`. In every case a failed write never fails a hook (exit 0).

| Case | What happens |
|---|---|
| Computer stops right after a session ends | No partial `CONTEXT.md`, no temp file, no lock left; the `ended` marker makes the next session start catch the session up, same result as without the cut |
| Cut after a node is rewritten, before its parent | The queued work stays; the next sync of the node drains it and rebuilds the parents; `doctor` reports nothing false |
| Cut between the end hook and the sync launch | The session stays pending; the next session start launches the sync |
| Model call impossible (not logged in, quota, no network, no `claude`) | Old file kept; `sync-fail` log line with the cause; after 2 failures the session is abandoned but kept: `doctor` lists it, the next session start shows "Sync abandoned (session)" with the sanitised cause inside the data frame, `ctx sync --session <id>` retries |
| Slow model | Same as above; the whole `claude` process group is killed, and also dies with its parent: no orphan |
| Two machines on one synced folder | Only `CONTEXT.md` is read; a version written outside ctx (hash differs from the last one ctx wrote) is the base of the next rewrite and goes to history (`external-edit` log line; a node with no recorded hash trusts the file once and logs `external-edit-unknown`); sync-tool conflict copies (`CONTEXT (conflit).md`, `CONTEXT.sync-conflict-*.md`) are ignored and flagged by `doctor` |
| Folder moved, renamed or deleted | `CONTEXT.md` travels with the folder; the parent line follows at the next rebuild; a deleted node disappears from it; `doctor` lists state of vanished paths older than 30 days and `ctx doctor --fix` removes it |
| `CONTEXT.md` hand-edited during a sync | The Notes of the hand version win; the other sections restart from the version read at the start; the hand version is in history (`ctx undo`); `edit-during-sync` log line |
| `~/.ctx` missing, unwritable or disk full | Recreated when possible; the session starts with its context, `hook-error` in the log when it can be written |
| Session never closed (window closed, long sleep, WSL stopped) | A live CLI pid or recent activity protects a session; a dead (or reused) pid releases it at once; a slept session is caught up once and later edits sync alone; nothing is attributed to another node |
| Growth | `log` over 1 MB moves to `log.1` (one kept, under a lock; logged values are stripped of control characters, `=` and length); `sessions/` purged after 7 days; history keeps 10 versions; `doctor` prints the size of `~/.ctx` |
| Hook slower than its 10 s delay | The hook gives up after 8 s with exit 0: the session starts without context; `hook-timeout` log line (a read stuck in uninterruptible disk I/O cannot be interrupted: the CLI's own delay then applies) |

## Limits

- Model quality (Haiku) is only measured on the integration runs, not guaranteed.
- A crash can lose the last messages of a session (touched paths survive and are caught up at the next start).
- A session is protected from other sessions' catch-up by its CLI's pid. Hooks run through `sh -c`; ctx records the
  first ancestor that is not a shell. Before 1.1.0 it recorded that short-lived `sh`, so a running session could be
  synced by another session start before its last messages were stored.
- The group kill and parent-death kill of the rewrite call are Linux/WSL; elsewhere a timeout kills only the direct child.
- The rewrite needs `claude`; cursor and agy are hook clients only.
- Linux, WSL, macOS (`fcntl`); not Windows native.
