# A/B runner: structure vs state vs nothing

`run_ab_real_folders.py` compares, per folder, three `CONTEXT.md` arms on the same resume task (3 runs per arm, blind Sonnet judge): **structure** (hand-written file listing), **state** (content rules: state with file path, `decision:` lines, next step with file) and **nothing** (`CTX_OFF=1`). Output: quality, input tokens, `cache_read_input_tokens`, variance, and a cache check (a drop of `cache_read` with state versus nothing is flagged, with the recommendation: fixed section order, `updated` at the end of the header).

Per-folder specs `DIR/<folder name>.json`: see `variants-template.json` and `variants-mirror/`. The runner writes only the node's `CONTEXT.md` and restores it byte-identical. The judge also reads the folder's Claude Code auto-memory (`~/.claude/projects/<slug>/memory/*.md`) if present; it is never saved.

Command (real folders):

```
python3 tests/integration/run_ab_real_folders.py ~/courses/ml-intro ~/courses/challenge --variants-dir DIR --runs 3 --out /tmp/ctx-ab
```

On a test tree: prefix `CTX_HOME=<test home>` and add `--mirror-settings <hooks file>`.

## Benchmark: with ctx vs without

`run_benchmark.py` reuses this runner: two tasks per folder (`benchmark-tasks.json`, generic labels), with ctx vs `CTX_OFF=1`, 5 runs per arm in one shuffled order, blind judge, paired bootstrap CI. Method and results: `docs/benchmark.md`.
