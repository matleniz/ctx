# Benchmarks

Measurement harnesses, not tests: each run calls real models and costs tokens. Results and methods are in
[docs/benchmark.md](../docs/benchmark.md) and [docs/benchmark-longrun.md](../docs/benchmark-longrun.md).

- `run_benchmark.py`: with / without ctx on real folders, blind judge (tasks in `benchmark-tasks.json`).
- `run_longrun.py`: 10-session chains on the synthetic projects of `longrun/`.
- `run_ab_real_folders.py`: three-arm A/B on real folders (see `README-ab.md`, `variants-*`).

Their pure parts (statistics, parsing, checks) are unit-tested offline: `python3 -m unittest discover -s bench`.
