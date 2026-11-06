# Contributing

- Run the unit tests before and after a change: `python3 -m unittest discover -s tests` (offline, a few seconds).
- One file, standard library only, Python 3.10+. Keep `ctx.py` short: a feature that needs state, a model call or
  a lock belongs in an agent rule, not here.
- A behaviour change comes with a test and a line in `CHANGELOG.md` under the next version. Messages and docs in
  English. Lines of 120 characters at most.
- Open an issue before a large change; small fixes can go straight to a pull request. Fork, branch from `main`,
  open the pull request against `main`; the template lists the checks.
- Be kind: see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
