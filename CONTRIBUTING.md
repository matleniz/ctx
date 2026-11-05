# Contributing

- Run the unit tests before any change and after it: `python3 -m unittest discover -s tests` (offline, about a minute).
  `pip install pyflakes` to run the style check that CI enforces.
- Integration runners in `tests/integration/` are optional: they call real CLIs (`claude`, `cursor-agent`, `agy`) on a
  throwaway test tree and cost model calls. Run them only when you change hook or rewrite behaviour.
- One package, no dependency: the program is `src/ctx/` (Python 3.10+ standard library only); `ctx.py` stays a thin
  launcher at the root, since installed hooks and `~/.local/bin/ctx` call it. Module map and import order:
  [docs/reference.md](docs/reference.md#code-layout). No circular import.
- In tests, patch a name in the module that uses it (`mock.patch.object(ctx.sync, "write_doc")`), never on `ctx`.
- A behaviour change comes with a test; a README claim comes with a row in `docs/readme-claims.md`.
- Keep lines of `ctx.py` and `src/ctx/*.py` at 100 characters or fewer; write messages and docs in English.
- Open an issue before a large change; small fixes can go straight to a pull request.
