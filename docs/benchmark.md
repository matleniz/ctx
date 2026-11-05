# Benchmark: with ctx vs without, on real folders

Measured once, on the night of 2026-11-04, on 8 of the author's own folders. The question: when you come back to
a folder and ask an agent where things stand, does the injected `CONTEXT.md` give a better answer, or the same
answer for less work? Everything is reported, including the results that do not favour ctx. The numbers cannot
be reproduced without these private folders; the method and the runner can (see Reproduce).

## Headline

Run 2 (primary): the with arm of run 1 against a new without arm in which the folder's `CONTEXT.md` was hidden
during each session. Haiku agent, Sonnet judge blind to the arm (Opus judge as a check). The unit is the folder
(n = 8; its two tasks share one `CONTEXT.md`). Quality is in points on 0-9; the other effects are ratios of means
(mean over folders of the with level divided by the same for without, minus 1). CIs: cluster bootstrap by folder
(5 000 draws, runs resampled within each arm of each cell) and paired t on the 8 folder differences (7 df); sign
test over folders. "CI excludes 0" refers to the bootstrap CI.

| metric | judge | tasks | folders | without | with | effect | 95 % CI, folder bootstrap | 95 % CI, paired t | folders higher / lower (sign test p) | CI excludes 0 |
|---|---|---|---|---|---|---|---|---|---|---|
| quality (0-9, points) | sonnet | all | 8 | 6.42 | 6.30 | -0.12 | [-0.62, +0.47] | [-0.74, +0.49] | 1 / 5 (p = 0.219) | no |
| quality (0-9, points) | sonnet | resume | 8 | 5.47 | 5.12 | -0.35 | [-0.97, +0.28] | [-1.04, +0.34] | 2 / 6 (p = 0.289) | no |
| quality (0-9, points) | sonnet | specific | 8 | 7.38 | 7.47 | +0.10 | [-0.50, +0.80] | [-0.57, +0.77] | 2 / 5 (p = 0.453) | no |
| quality (0-9, points) | opus | all | 8 | 6.85 | 6.59 | -0.26 | [-0.76, +0.24] | [-0.82, +0.31] | 3 / 5 (p = 0.727) | no |
| quality (0-9, points) | mean | all | 8 | 6.69 | 6.49 | -0.20 | [-0.59, +0.20] | [-0.66, +0.26] | 3 / 5 (p = 0.727) | no |
| input tokens incl. cache (%) | - | all | 8 | 457 128 | 369 763 | -19.1 % | [-27.0 %, -10.5 %] | [-27.9 %, -10.3 %] | 0 / 8 (p = 0.008) | yes |
| input tokens incl. cache (%) | - | resume | 8 | 422 390 | 312 902 | -25.9 % | [-44.5 %, -6.7 %] | [-52.0 %, +0.2 %] | 0 / 8 (p = 0.008) | yes |
| input tokens incl. cache (%) | - | specific | 8 | 491 866 | 426 624 | -13.3 % | [-29.6 %, +11.5 %] | [-39.5 %, +12.9 %] | 3 / 5 (p = 0.727) | no |
| turns (%) | - | all | 8 | 8.9 | 6.8 | -23.1 % | [-31.9 %, -13.9 %] | [-33.1 %, -13.2 %] | 0 / 8 (p = 0.008) | yes |
| seconds (%) | - | all | 8 | 24.1 | 19.9 | -17.2 % | [-27.1 %, -6.9 %] | [-27.9 %, -6.5 %] | 0 / 8 (p = 0.008) | yes |
| cost USD (%) | - | all | 8 | 0.0636 | 0.0594 | -6.6 % | [-31.8 %, +24.0 %] | [-32.7 %, +19.4 %] | 4 / 4 (p = 1.000) | no |

Largest quality loss in one (folder, task) cell: -1.2 points (course-labs, resume).

In one sentence: with ctx the agent did less work (fewer input tokens, turns and seconds, in all 8 folders) for
an answer of about the same quality; at the folder level a loss of more than about 0.7 point or a gain of more
than about 0.5 is unlikely, smaller changes are not excluded. Cost did not move once the without arm was rerun (it follows the
prompt-cache state more than the work done).

## Method

- **Folders**: 8 real working folders of one person, not prepared for the test: two course folders of lab
  notebooks (`course-notebooks-A`, `course-notebooks-B`), a folder of lab assignments (`course-labs`), a course
  folder with a graded mini-project (`course-with-project`), a course git repository with sub-projects
  (`course-monorepo`), the git repository of a prediction challenge (`ml-challenge`), the git repository of a
  static web site (`static-site`) and the git monorepo of a writing tool (`writing-project`). Three were nodes
  with a `CONTEXT.md` already shaped by earlier sessions; five were made nodes that night and their first
  `CONTEXT.md` was written by the real hooks after one read-only session ("fais le point").
- **Tasks** (`tests/integration/benchmark-tasks.json`), in French, as typed when resuming work: a *resume* task
  ("where are we and what next, 3 concrete actions with the files"), the same in every folder, and a *specific*
  question whose answer is in the files (open `# Todo` cells, best submission and its scores, checks before a
  deploy, open known bugs, ...).
- **Arms**: *with* = the real global hooks (`CONTEXT.md` injected at session start, sync at the end); *without* =
  the same command with `CTX_OFF=1`. Agent: `claude -p --model haiku --output-format json`, prompt on stdin,
  tools `Read,Glob,Grep` only, 180 s timeout, one retry. 5 runs per arm and task, in one shuffled order.
- **Run 1** (secondary): both arms the same night, 160 sessions. `CONTEXT.md` stayed on disk in the without
  arm and 11 of its 80 sessions read it with the Read tool, which biases the comparison towards no difference.
- **Run 2** (primary): the 80 without sessions again, with every `CONTEXT.md` of the folder renamed to
  `.CONTEXT.md.hidden` for the duration of each session (atomic rename, restored in a `finally`, checked by the
  manifests). The with arm is the one of run 1.
- **Judges**: `claude -p`, no tools, one call per answer, answers of both arms shuffled together with no arm
  label. Ground truth: the folder's readable text files, a dated file listing, the Claude Code auto-memory of the
  folder when it exists, the task's pointer files (notebook summaries: executed cells, `# Todo` cells), read-only
  `git log`/`git status`, and for the specific task a reference answer written from the files. Scores 0-3 on
  exactness, actionability (resume: the 3 actions; specific: the question answered) and no-invention; quality =
  their sum. Sonnet judged every answer. Opus judged the run 2 answers once, until a cost cap stopped it after
  111 of 160 (a random subset: the order was shuffled); its rows use those runs only.
- **Integrity**: sha256 manifest of every folder before and after, size/mtime check after every session: in all
  8 folders only `CONTEXT.md` changed, and every hidden file was back in place. No session failed.

## Judge agreement

Pearson r between the Sonnet and Opus quality scores: 0.91 over the 32 (folder, task, arm) cell means, 0.77 over the 111 runs both judged. Opus scores higher on average (6.66 vs 6.39) but ranks the cells largely the same way; the headline gives the quality effect under each judge and under their mean (runs judged by both).

## Sensitivity

Percentages above are ratios of means. Geometric mean of the per-folder ratios (t interval, 7 df):

- input tokens incl. cache (%), all: ratio of means -19.1 %, geometric mean of the folder ratios -18.5 % [-24.4 %, -12.1 %] (t, n - 1 df)
- input tokens incl. cache (%), resume: ratio of means -25.9 %, geometric mean of the folder ratios -26.6 % [-45.0 %, -2.1 %] (t, n - 1 df)
- input tokens incl. cache (%), specific: ratio of means -13.3 %, geometric mean of the folder ratios -9.2 % [-29.9 %, +17.6 %] (t, n - 1 df)
- turns (%), all: ratio of means -23.1 %, geometric mean of the folder ratios -23.5 % [-31.7 %, -14.4 %] (t, n - 1 df)
- seconds (%), all: ratio of means -17.2 %, geometric mean of the folder ratios -17.0 % [-26.1 %, -6.8 %] (t, n - 1 df)
- cost USD (%), all: ratio of means -6.6 %, geometric mean of the folder ratios -8.9 % [-36.2 %, +30.3 %] (t, n - 1 df)

Hiding the file did not keep every agent away from it: in 18 of the 80 run 2 without sessions the agent found
`.CONTEXT.md.hidden` (Glob lists dot files) and read it or got its lines from Grep. Without those 18 runs:

| metric | judge | tasks | folders | without | with | effect | 95 % CI, folder bootstrap | 95 % CI, paired t | folders higher / lower (sign test p) | CI excludes 0 |
|---|---|---|---|---|---|---|---|---|---|---|
| quality (0-9, points) | sonnet | all | 8 | 6.41 | 6.44 | +0.03 | [-0.61, +0.84] | [-0.82, +0.88] | 3 / 5 (p = 0.727) | no |
| quality (0-9, points) | sonnet | resume | 7 | 5.37 | 5.09 | -0.29 | [-0.99, +0.44] | [-1.12, +0.55] | 2 / 5 (p = 0.453) | no |
| quality (0-9, points) | sonnet | specific | 8 | 7.24 | 7.47 | +0.24 | [-0.53, +1.41] | [-0.85, +1.32] | 2 / 6 (p = 0.289) | no |
| quality (0-9, points) | opus | all | 8 | 6.83 | 6.64 | -0.19 | [-0.82, +0.46] | [-0.93, +0.54] | 3 / 5 (p = 0.727) | no |
| quality (0-9, points) | mean | all | 8 | 6.71 | 6.58 | -0.13 | [-0.63, +0.44] | [-0.75, +0.49] | 3 / 4 (p = 1.000) | no |
| input tokens incl. cache (%) | - | all | 8 | 455 449 | 381 274 | -16.3 % | [-26.5 %, -3.8 %] | [-28.7 %, -3.9 %] | 1 / 7 (p = 0.070) | yes |
| input tokens incl. cache (%) | - | resume | 7 | 419 562 | 317 385 | -24.4 % | [-45.8 %, -3.4 %] | [-54.8 %, +6.1 %] | 0 / 7 (p = 0.016) | yes |
| input tokens incl. cache (%) | - | specific | 8 | 491 123 | 426 624 | -13.1 % | [-30.9 %, +11.6 %] | [-39.5 %, +13.2 %] | 3 / 5 (p = 0.727) | no |
| turns (%) | - | all | 8 | 8.4 | 7.0 | -17.4 % | [-28.1 %, -5.4 %] | [-30.4 %, -4.5 %] | 1 / 7 (p = 0.070) | yes |
| seconds (%) | - | all | 8 | 23.7 | 20.3 | -14.5 % | [-26.5 %, +0.0 %] | [-30.0 %, +0.9 %] | 2 / 6 (p = 0.289) | no |
| cost USD (%) | - | all | 8 | 0.0586 | 0.0597 | +2.0 % | [-26.3 %, +40.0 %] | [-25.7 %, +29.7 %] | 5 / 3 (p = 0.727) | no |

Largest quality loss in one (folder, task) cell: -1.2 points (course-labs, resume).

## Run 1 (secondary)

Same with arm, without arm from the same night with `CONTEXT.md` left on disk:

| metric | judge | tasks | folders | without | with | effect | 95 % CI, folder bootstrap | 95 % CI, paired t | folders higher / lower (sign test p) | CI excludes 0 |
|---|---|---|---|---|---|---|---|---|---|---|
| quality (0-9, points) | sonnet | all | 8 | 6.50 | 6.30 | -0.20 | [-0.91, +0.69] | [-1.15, +0.75] | 3 / 5 (p = 0.727) | no |
| quality (0-9, points) | sonnet | resume | 8 | 5.72 | 5.12 | -0.60 | [-1.52, +0.20] | [-1.60, +0.40] | 2 / 5 (p = 0.453) | no |
| quality (0-9, points) | sonnet | specific | 8 | 7.28 | 7.47 | +0.20 | [-0.65, +1.27] | [-0.95, +1.35] | 3 / 4 (p = 1.000) | no |
| input tokens incl. cache (%) | - | all | 8 | 442 185 | 369 763 | -16.4 % | [-26.3 %, -5.5 %] | [-28.0 %, -4.8 %] | 1 / 7 (p = 0.070) | yes |
| input tokens incl. cache (%) | - | resume | 8 | 418 704 | 312 902 | -25.3 % | [-43.2 %, -6.8 %] | [-49.2 %, -1.3 %] | 0 / 8 (p = 0.008) | yes |
| input tokens incl. cache (%) | - | specific | 8 | 465 666 | 426 624 | -8.4 % | [-24.4 %, +11.9 %] | [-29.6 %, +12.8 %] | 2 / 6 (p = 0.289) | no |
| turns (%) | - | all | 8 | 8.9 | 6.8 | -23.3 % | [-34.5 %, -10.1 %] | [-40.1 %, -6.6 %] | 1 / 7 (p = 0.070) | yes |
| seconds (%) | - | all | 8 | 25.4 | 19.9 | -21.4 % | [-33.1 %, -9.9 %] | [-35.5 %, -7.2 %] | 1 / 7 (p = 0.070) | yes |
| cost USD (%) | - | all | 8 | 0.0882 | 0.0594 | -32.6 % | [-51.2 %, -10.0 %] | [-55.0 %, -10.2 %] | 1 / 7 (p = 0.070) | yes |

Largest quality loss in one (folder, task) cell: -2.8 points (course-notebooks-B, resume).

## Per folder and task (run 2)

Quality mean over 5 runs (S = Sonnet judge, O = Opus judge, on the runs it judged), input tokens as means.

| folder | task | quality without (S / O) | quality with (S / O) | tokens without | tokens with | secondary: quality without (S) |
|---|---|---|---|---|---|---|
| course-labs | resume | 7.0 / 7.2 | 5.8 / 6.0 | 595k | 555k | 6.8 |
| course-labs | specific | 7.6 / 6.8 | 7.2 / 6.8 | 647k | 462k | 7.8 |
| course-monorepo | resume | 6.2 / 6.2 | 6.6 / 6.5 | 326k | 242k | 6.2 |
| course-monorepo | specific | 8.0 / 8.0 | 7.6 / 8.0 | 446k | 408k | 7.8 |
| course-notebooks-A | resume | 4.0 / 4.2 | 5.2 / 5.6 | 389k | 370k | 4.0 |
| course-notebooks-A | specific | 6.0 / 8.0 | 7.8 / 8.0 | 688k | 426k | 4.4 |
| course-notebooks-B | resume | 4.0 / 5.2 | 2.8 / 4.0 | 627k | 204k | 5.6 |
| course-notebooks-B | specific | 8.6 / 8.8 | 8.0 / 8.0 | 354k | 523k | 8.2 |
| course-with-project | resume | 5.2 / 7.0 | 5.0 / 5.0 | 352k | 310k | 5.0 |
| course-with-project | specific | 7.4 / 7.3 | 7.2 / 7.0 | 755k | 515k | 7.0 |
| ml-challenge | resume | 6.4 / 5.8 | 5.8 / 6.4 | 256k | 209k | 6.4 |
| ml-challenge | specific | 7.0 / 7.5 | 6.8 / 8.0 | 385k | 316k | 8.0 |
| static-site | resume | 4.8 / 6.2 | 4.4 / 5.8 | 464k | 331k | 5.4 |
| static-site | specific | 7.6 / 8.0 | 7.6 / 7.7 | 238k | 297k | 7.4 |
| writing-project | resume | 6.2 / 6.0 | 5.4 / 5.8 | 371k | 282k | 6.4 |
| writing-project | specific | 6.8 / 7.2 | 7.6 / 7.0 | 422k | 466k | 7.6 |

What the judge comments show for the largest gaps:

- `course-notebooks-B`, resume: the first `CONTEXT.md` of this node, written that night, said that no notebook
  had been executed (two of three are mostly or fully executed). With ctx the agent trusted it, answered in 2.8
  turns and repeated the error (run 1 -2.8 points, run 2 -1.2). A wrong `CONTEXT.md` can cost more than none.
- `course-notebooks-A`, specific: the `CONTEXT.md` listed the exact notebook cells still marked `# Todo`; without
  it the agent often counted marker lines or JSON line numbers instead of cells (run 1 +3.4, run 2 +1.8).

Exploratory, found after looking at the data (n = 5 vs 3, no test): on the resume task, the 5 nodes whose
`CONTEXT.md` was written the same night by one read-only session all scored at or below the without arm
(-1.2, -1.2, -0.2, -0.4, -0.8), while the 3 older nodes, whose `CONTEXT.md` had been corrected by real work, scored +1.2, +0.4, -0.6. A
`CONTEXT.md` never corrected by real work may cost quality; this benchmark cannot tell.

## Second agent model (run 1)

Sonnet as the agent, resume task, 2 folders, 5 runs per arm, `CONTEXT.md` left on disk:

| folder | quality without | quality with | tokens without | tokens with | seconds without | seconds with |
|---|---|---|---|---|---|---|
| course-notebooks-A | 4.0 ± 2.1 | 5.2 ± 1.9 | 301k | 340k | 28.5 | 20.7 |
| ml-challenge | 7.2 ± 0.8 | 5.8 ± 1.1 | 108k | 175k | 24.4 | 15.8 |

Faster with ctx on both, but Sonnet read more tokens with ctx, not fewer, and quality went up on one folder and
down on the other. Two folders: a description, not evidence that the effect holds for other models.

## What is and is not significant

- Significant (bootstrap CI excludes 0, every folder in the same direction, sign test p = 0.008): fewer input
  tokens, fewer turns, fewer seconds. On the resume task the token saving is larger; on the specific task alone
  the token saving is uncertain (CI crosses 0).
- Not significant: quality, under either judge and their mean overall, and per task under Sonnet. At the folder
  level the data exclude a loss larger than about 0.7 point overall; on the resume task alone the paired t interval
  reaches about -1 point. In run 1 (file left on disk) a one-point loss was not excluded either.
- Not significant: cost (run 2). In run 1 cost was 33 % lower with ctx; the rerun without arm hit a warmer
  prompt cache and cost less.
- Not robust: time. Seconds lose significance when the 18 runs that saw the hidden file are dropped, and the
  run 2 without arm ran later in the night than the with arm, so time of day and machine load differ.

## Caveats

- One person, 8 folders, one night. Between the runs no file changed (manifests); scores of the same arm moved
  by up to 1.6 points between runs. With n = 8 the CIs are wide and the t and bootstrap intervals differ.
- Run 2 compares a without arm run later the same night with the earlier with arm: time of day and prompt-cache
  state differ between the arms (cost shows it); tokens, turns and quality should depend little on that.
- The with arm depends on each folder's `CONTEXT.md`. Five were written that night by one read-only session and
  never corrected by real work; one was wrong. The read-only benchmark sessions rewrote no node (every sync was a
  skip), so each folder kept one `CONTEXT.md` for all its runs.
- Leakage: 11 of 80 without sessions read `CONTEXT.md` in run 1, 18 of 80 saw the renamed file in run 2. Moving
  the file out of the folder would have been cleaner.
- Answers can name `CONTEXT.md`, which the judge cannot see in the ground truth: a weak arm signal, present in
  both arms. The judges are models, not people, and the specific tasks score near the ceiling in both arms.
- Tokens are dominated by what Claude Code sends on every turn; a shorter session saves that each turn. The ctx
  rewrites are not counted (here only the 5 first fills, about 2 000 tokens in and 700 to 2 200 out each).

## Reproduce

```
python3 tests/integration/run_benchmark.py --map MAP.json --out OUT --runs 5
python3 tests/integration/run_benchmark.py --map MAP.json --out OUT --tag hidden --arms without --hide-context --phase sessions
python3 tests/integration/run_benchmark.py --map MAP.json --out OUT --tag hidden --phase judge
python3 tests/integration/run_benchmark.py --map MAP.json --out OUT --judge-model opus --judge-key scores_opus --judge-sets main:with,hidden:without --cost-cap 15 --phase judge
python3 tests/integration/run_benchmark.py --map MAP.json --out OUT --phase report
```

`MAP.json` is your own, unpublished file: `{"redact": [words], "folders": {"<label>": {"path": "<your folder>",
"reference": {"specific": "<answer written from the files>"}}}}`, one entry per label of
`tests/integration/benchmark-tasks.json` (edit the tasks for your folders). Each phase is resumable. Raw runs,
answers and judge output stay in `OUT/private/` (git-ignored); `--phase report` writes the published aggregates,
`OUT/SUMMARY.md` and `docs/benchmark-results.json` (numbers per run keyed by generic labels, no text). Leave `CTX_HOME`
unset to use your real hooks.
