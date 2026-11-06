# Contributing

- Run the unit tests before and after a change: `python3 -m unittest discover -s tests` (offline, a few seconds).
- One file, standard library only, Python 3.10+. Keep `ctx.py` short: a feature that needs state, a model call or
  a lock belongs in an agent rule, not here.
- A behaviour change comes with a test. Messages and docs in English.
- Open an issue before a large change; small fixes can go straight to a pull request.
