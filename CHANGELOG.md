# Changelog

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
