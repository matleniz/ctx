# Contributing

- Run the unit tests before any change and after it: `python3 -m unittest discover -s tests` (offline, about two
  minutes), and `python3 -m unittest discover -s bench` when you touch `bench/`.
  `pip install pyflakes` to run the style check that CI enforces.
- Integration runners in `tests/integration/` are optional: they call real CLIs (`claude`, `cursor-agent`, `agy`) on a
  throwaway test tree and cost model calls. Run them only when you change hook or rewrite behaviour.
- One package, no dependency: the program is `src/ctx/` (Python 3.10+ standard library only); `ctx.py` stays a thin
  launcher at the root, since installed hooks and `~/.local/bin/ctx` call it. Module map and import order: below.
  No circular import.
- In tests, patch a name in the module that uses it (`mock.patch.object(ctx.sync, "write_doc")`), never on `ctx`.
- A behaviour change comes with a test; a README claim comes with a row in `docs/readme-claims.md`.
- Keep lines of `ctx.py` and `src/ctx/*.py` at 100 characters or fewer; write messages and docs in English.
- Open an issue before a large change; small fixes can go straight to a pull request.

## Internals

### Code layout

`ctx.py` at the root of the clone is only the launcher: the installed hooks and `~/.local/bin/ctx` run it, it puts
`src/` (found next to its own real path) on `sys.path` and calls `ctx.cli.main()`; `python3 -m ctx` does the same
with `src/` on `PYTHONPATH`. The program is the package `src/ctx/`, standard library only. Modules, in import order
(each imports only from those above it):

- `paths.py`: constants, state dir, log, atomic writes, directory locks, hook deadline, launcher path.
- `filters.py`: secret, instruction-shaped and structure-line filters.
- `doc.py`: `Doc`, parse and render; history snapshots, per-node state.
- `tree.py`: `.ctxignore`, node discovery, declined folders, the Subfolders section, bubble-up, git exclude.
- `sessions.py`: hook collection, pending work, session liveness, catch-up.
- `rewriter.py`: prompt, rewriter table, config, the model call, output parsing.
- `sync.py`: node lock, rewrite, sync-light, session sync, bash scan.
- `hooks.py`: client table, session-start injection, hook dispatch.
- `install.py`: hook entries in the CLIs' configs (merge, review, removal).
- `doctor.py`: the checks and fixes behind `ctx doctor`.
- `cli.py`: commands, argument parser, log reading, `main()`.

The package re-exports nothing: tests import `ctx.cli` (which loads every module) and patch a name in the module
that uses it (`ctx.sync.write_doc`).

### Tests

- Unit suite, offline: `python3 -m unittest discover -s tests`.
- Integration runners in `tests/integration/` use a real `claude` (or `cursor-agent`, `agy`) on a throwaway test tree
  (`CTX_HOME`) and write their evidence to `$CTX_EVIDENCE_DIR` (default: a `ctx-evidence` folder in the system temp
  dir): `run_readme.py` (the user journey), `run_core_battery.py`, `run_regressions.py`, `run_resilience.py` (longer
  batteries; see `SUMMARY.txt` in each output folder), `run_loop_guard.py`, `run_cursor.py`, `run_agy.py`. They pass
  `--setting-sources project,local` so only the test tree's hooks run when your own are installed.
- Benchmarks in `bench/` (see `bench/README.md`); their pure parts: `python3 -m unittest discover -s bench`.
- Hidden option used by the runners: `install-hooks --settings-file FILE` (with `--yes`, writes a hooks-only config to a new file).
