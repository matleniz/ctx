"""ctx: per-folder context (CONTEXT.md) for coding agents. Standard library only, no dependency.

A node is a folder whose CONTEXT.md ctx wrote on this machine (header `ctx: 1`). Its context is
injected into agent sessions started under it (CLI hooks) and updated after those sessions.
See README.md and docs/. Layout of the state dir (~/.ctx):
  log  declined  sessions/<id>/  history/<hash>/  state/<hash>.json  pending/<hash>/

Modules, in import order (a module imports only from those above it; no cycle):
   paths      constants, state dir, log, atomic I/O, locks, deadline, launcher path
   filters    secrets, instruction-shaped and structure lines
   doc        Doc, parse, render; history snapshots, per-node state
   tree       ignore rules, node discovery, declined folders, Subfolders, bubble-up, git
   sessions   hook collection, pending work, liveness, catch-up
   rewriter   prompt, rewriter table, config, model call, output parsing
   sync       per-node queue, rewrite, session sync, bash scan
   hooks      client table, session-start injection, hook dispatch
   install    hook entries in the CLIs' configs
   doctor     checks and fixes behind `ctx doctor`
   cli        commands, argument parser, log reading
The launcher `ctx.py` at the root of the clone (what hooks and ~/.local/bin/ctx run) puts src/ on
sys.path and calls cli.main(); `python3 -m ctx` does the same through __main__.

Exceptions: `except BaseException` appears only where the SIGALRM deadline (Alarm), an interrupt
or a SystemExit must be seen: cli_rewriter and sync_node catch it only to clean up or log, then
re-raise; guarded_hook alone swallows it (after logging), because a hook must always exit 0.

Nothing is re-exported here: import the module that defines a name (`ctx.sync.sync_node`), and
patch a name in the module that uses it (`mock.patch.object(ctx.sync, "write_doc")`).
"""
