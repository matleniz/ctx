"""ctx: per-folder context (CONTEXT.md) for coding agents. Standard library only, no dependency.

A node is a folder whose CONTEXT.md carries the `ctx: 1` header. Its context is injected into agent
sessions started under it (CLI hooks) and rewritten by a model after those sessions.
See README.md and docs/. Layout of the state dir (~/.ctx):
  log  declined  sessions/<id>/  history/<hash>/  state/<hash>.json  pending/<hash>/

Modules, in import order (a module imports only from those above it; no cycle):
   paths      constants, state dir, log, atomic I/O, locks, deadline, launcher path
   filters    secrets, instruction-shaped and structure lines
   doc        Doc, parse, render; history snapshots, per-node state, log reading
   tree       ignore rules, node discovery, declined folders, Subfolders, bubble-up, git
   sessions   hook collection, pending work, liveness, catch-up
   rewriter   prompt, rewriter table, config, model call, output parsing
   sync       per-node queue, rewrite, session sync, bash scan
   hooks      client table, session-start injection, hook dispatch
   install    hook entries in the CLIs' configs
   doctor     checks and fixes behind `ctx doctor`
   cli        commands and argument parser
The launcher `ctx.py` at the root of the clone (what hooks and ~/.local/bin/ctx run) puts src/ on
sys.path and calls cli.main(); `python3 -m ctx` does the same through __main__.

Exceptions: `except BaseException` appears only where the SIGALRM deadline (Alarm), an interrupt
or a SystemExit must be seen: cli_rewriter and sync_node catch it only to clean up or log, then
re-raise; guarded_hook alone swallows it (after logging), because a hook must always exit 0.

The names below are re-exported for callers and tests. They are copies: to patch a name, patch it
in the module that uses it (`mock.patch.object(ctx.sync, "write_doc")`), never on `ctx` itself.
"""
from .paths import (
    BUBBLE_LOCK_WAIT, DEFAULT_SUMMARY, FULL_ENTRIES, HISTORY_KEEP, HOOK_DEADLINE, LIVE_WINDOW,
    LOG_MAX, MAX_ENTRIES, MAX_INJECT, MAX_NEXT, MAX_NEXT_LINES, MAX_NOTES, MAX_STATUS,
    MAX_STATUS_LINES, MAX_SUBS, MAX_SUMMARY, MAX_TOTAL, MODEL_TIMEOUT, ORDER, PLACEHOLDERS,
    SCAN_MAX, SEC_NEXT, SEC_NOTES, SEC_STATUS, SEC_SUBS, SHORT_WIDTH, SYNC_MARGIN, __version__,
    atomic_write, base, clean, dirlock, human_cmd, log, now, path_key, read_json, real, self_cmd,
    stamp, state_dir,
)
from .filters import instruction_shaped, redact, structure_line
from .doc import (
    Doc, get_status, history_dir, orphans, parse, read_doc, read_log, render, set_status,
    status_line, valid_shape, write_doc,
)
from .tree import (
    accepts, all_nodes, bubble, child_nodes, git_info_dir, has_ctx, is_declined, nearest_node,
    refresh_subs, remove_declined,
)
from .sessions import (
    abandoned, catch_up, collect_start, collect_stop, collect_tool, pid_dead, proc_start,
    session_dir_of, session_live, session_pending,
)
from .rewriter import (
    REWRITERS, SYSTEM_PROMPT, build_prompt, cli_rewriter, config_warnings, copied_sub,
    dropped_figure, effective_rewriter, parse_model_output, read_config,
)
from .sync import (
    ask_model, drain, enqueue, has_pending, scan_changed, sync_deadline, sync_node, sync_session,
)
from .hooks import CLIENTS, build_injection, discovery_text, hook_guarded, run_hook
from .install import (
    hook_ours, hooks_block, is_ctx_hook_command, merged_settings, ours_entry, unmerged,
)
from .cli import main

__all__ = [
    "BUBBLE_LOCK_WAIT", "DEFAULT_SUMMARY", "FULL_ENTRIES", "HISTORY_KEEP", "HOOK_DEADLINE",
    "LIVE_WINDOW", "LOG_MAX", "MAX_ENTRIES", "MAX_INJECT", "MAX_NEXT", "MAX_NEXT_LINES",
    "MAX_NOTES", "MAX_STATUS", "MAX_STATUS_LINES", "MAX_SUBS", "MAX_SUMMARY", "MAX_TOTAL",
    "MODEL_TIMEOUT", "ORDER", "PLACEHOLDERS", "SCAN_MAX", "SEC_NEXT", "SEC_NOTES", "SEC_STATUS",
    "SEC_SUBS", "SHORT_WIDTH", "SYNC_MARGIN", "__version__", "atomic_write", "base", "clean",
    "dirlock", "human_cmd", "log", "now", "path_key", "read_json", "real", "self_cmd", "stamp",
    "state_dir", "instruction_shaped", "redact", "structure_line", "Doc", "get_status",
    "history_dir", "orphans", "parse", "read_doc", "read_log", "render", "set_status",
    "status_line", "valid_shape", "write_doc", "accepts", "all_nodes", "bubble", "child_nodes",
    "git_info_dir", "has_ctx", "is_declined", "nearest_node", "refresh_subs", "remove_declined",
    "abandoned", "catch_up", "collect_start", "collect_stop", "collect_tool", "pid_dead",
    "proc_start", "session_dir_of", "session_live", "session_pending", "REWRITERS", "SYSTEM_PROMPT",
    "build_prompt", "cli_rewriter", "config_warnings", "copied_sub", "dropped_figure",
    "effective_rewriter", "parse_model_output", "read_config", "ask_model", "drain", "enqueue",
    "has_pending", "scan_changed", "sync_deadline", "sync_node", "sync_session", "CLIENTS",
    "build_injection", "discovery_text", "hook_guarded", "run_hook", "hook_ours", "hooks_block",
    "is_ctx_hook_command", "merged_settings", "ours_entry", "unmerged", "main",
]
