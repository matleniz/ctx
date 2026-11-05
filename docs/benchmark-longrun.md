# Long-run benchmark: does a rewritten CONTEXT.md keep continuity over 10 sessions?

Earlier measurements were single resumes: one question, one session. This one follows a chain of 10 sessions on the
same folder, each a new `claude -p` process without `--resume`, so continuity can only come from the files and, with
ctx, from the folder's `CONTEXT.md` (injected at session start, rewritten after every session by the real hooks).

## Method

- **Scenarios** (built from scratch, `bench/longrun/`, prompts in `scenarios.json`):
  - `course-notebooks`: 4 chapters x 2 Python exercise stubs (docstring = what to implement) and `notes.md`.
    Requests: exercise 1.1, 1.2, chapter-1 notes, 2.1 with a decision (D1: no for/while loops, type hints everywhere),
    "Next exercise.", a review (the harness silently replaced `ch1/ex1_2.py` by a version dividing by n-1), "Where
    were we and what's next?", 3.1, "Refactor the earlier exercises so they all follow the convention we decided."
    (D1 is not restated), a final summary.
  - `small-project`: a package (`tally/parse.py`, `stats.py`, `cli.py`, two test files, one failing test) and a
    `TODO.md` of 6 numbered items. Requests: fix the failing test, "Next item on the TODO." (x4, the decision D1 "CLI
    stays stdlib-only" given at session 3), a review after a silent regression (`int(` instead of `float(` in
    `parse.py`), "Where are we and what's next?", the next item (the `--pretty` table, which must stay stdlib-only),
    a README usage section, a final `STATUS.md`.
- **Arms**: A = ctx on (`ctx init` on the copy, hooks from `ctx.py install-hooks claude --settings-file FILE --yes`, passed with
  `--settings FILE --setting-sources project,local`); B = same command with `CTX_OFF=1`, no `CONTEXT.md`.
  3 chains per scenario and arm, each from a fresh copy of the pristine folder: 2 x 2 x 3 x 10 = 120 sessions.
- **Sessions**: Haiku, `--output-format json`, tools `Read,Glob,Grep,Write,Edit` (no shell: the agent cannot run
  code), 240 s timeout, one retry (none was needed). Everything under a temporary home (`CTX_HOME`); Claude Code's
  auto-memory off (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`) so it is not a hidden channel in either arm.
- **Progress on the next item**: a progress session counts when it finished one of its explicit targets or the
  first exercise / TODO item not done before it (a terse "next item" may legitimately land on either).
- **Objective measures** (harness, `bench/longrun/checks.py`, run before and after every session):
  per exercise / TODO item a behavioural check of the code; D1 compliance (AST: no for/while, every argument and
  return annotated; or no third-party import); the injected bug fixed; the package tests (`python3 -m unittest`);
  redo = a progress session that re-worked something already done instead of its target.
- **Continuity judge**: Sonnet, one call per session, no tools, arm-blind. Before judging, `CONTEXT.md` (and
  "context file") in the reply becomes the neutral "a project file", and run folder paths (they carry the arm
  letter) and "last sync (HH:MM)" strings are erased. The judge sees the harness history, the decision log, the
  session's diff and the state before/after.
  Three scores 0-3: pickup (continues, no redo, right target), decisions, consistency with the real file state.
- **Context accuracy** (arm A): every `Status` line of `CONTEXT.md` after each sync is classified by rules against
  the harness ground truth: true, false, or unverifiable (names no tracked item or decision); "missing" = done items
  and the decision that no line mentions; "Next names the next item" = the `Next` section names the next unfinished
  exercise / TODO item. Rules, not a model: reproducible and free, but coarse (see caveats).

## Headline

| scenario | arm | tokens in per chain (incl. cache) | turns | seconds | cost USD | continuity /9 | items done at end | D1 compliance at end | injected bug fixed |
|---|---|---|---|---|---|---|---|---|---|
| course-notebooks | A (ctx) | 3.51 M ± 0.46 | 78 ± 6 | 172 ± 22 | 0.35 | 6.47 | 7.67 / 11 | 0.47 | 3/3 |
| course-notebooks | B (off) | 4.44 M ± 0.34 | 94 ± 5 | 184 ± 14 | 0.33 | 6.63 | 7.67 / 11 | 0.20 | 3/3 |
| small-project | A (ctx) | 3.72 M ± 0.32 | 89 ± 19 | 170 ± 28 | 0.29 | 5.90 | 3.67 / 8 | 1.0 | 3/3 |
| small-project | B (off) | 4.98 M ± 0.25 | 125 ± 1 | 189 ± 15 | 0.30 | 5.20 | 2.67 / 8 | 1.0 | 3/3 |

- ctx cuts input tokens by 21 % and 25 % and turns by 16 % and 29 % over a chain; cost per chain is unchanged
  (most input is cache reads, and the rewrite adds about 0.01-0.02 USD per chain at list price).
- The biggest saving is the resume question (session 7): 1-4 turns with ctx (mean 1 on `course-notebooks`, 2.3 on
  `small-project`; 66 k and 103 k tokens on average), 8-11 turns without (333 k and 364 k).
- Continuity is not better overall: lower with ctx on `course-notebooks` (6.47 vs 6.63), higher on `small-project`
  (5.90 vs 5.20). With 3 chains per arm, neither difference is solid.
- No redo was detected in either arm; the injected bug was fixed in all 12 chains at the review session.

## Where ctx helped, where it hurt

- **Helped: fewer turns to the same state.** `small-project`, session 7 (mean of 3 chains): 2.3 turns and
  103 k input tokens with ctx, 10.3 turns and 364 k without. The answers were judged slightly better with ctx
  (4.33 vs 3.67). Over the "next item" sessions 2-5, the ctx arm made progress on the next not-done item 3 times
  out of 12, the arm without ctx 2 times. All 3 ctx successes come from a single chain (A2); chains A1 and A3
  stalled like the chains without ctx, so this is not evidence that ctx unblocks the stall.
- **Hurt: the resume answer trusts `Next` without opening a file.** `course-notebooks`, session 7: with ctx, all
  3 chains answered in one turn from `CONTEXT.md` without opening a file, and gave the file's `Next` as the next
  step ("run the checks") instead of exercise 3.1. They also missed the D1 decision and the chapter-2 notes
  (continuity 3.33 vs 5.67 without ctx). The final summary (session 10) has the same problem: 4.33 vs 6.67.
  Example judge comment: "never names ex3_1 as the next exercise (it suggests running doctests instead), omits the
  D1 decision". What `Next` says ("run the tests") comes from this benchmark's sessions having no shell (see the
  next-but-one point). The product defect is that the resume answer takes `Next` at its word without checking
  the files, whatever `Next` says.
- **The user's decision does not survive; the agent's own micro-decisions do.** The file keeps the decisions
  the agent made for itself: "use `/` so integer input yields float" in `course-notebooks` A1 from session 1 to 4,
  "fix only the slice in `total()`" in `small-project` A1 from session 1 to 7. The user's decision D1 is
  recorded in only 2 of 6 chains, and the next rewrite drops it (session 5 in `course-notebooks` A2, session 6 in
  `small-project` A2). In the other 4 chains it is never recorded. From session 6 on, no chain's file carries
  it. The refactor session ("the convention we decided", session 9 of `course-notebooks`) fails in 5 of 6
  chains, in both arms. The one success (ctx chain A3) took D1 from `notes.md`, where an earlier session had
  written "conventions apply from chapter 2 on". It quotes that line in its reply, and its `CONTEXT.md`
  mentioned neither loops nor type hints. So this success is not due to ctx.
- **`Next` fills up with verification chores (a harness effect).** Without a shell, every session reports "not
  executed", and the rewriter turns that into `Next: run the tests/doctests`. With a shell this content would
  likely differ. `Next` named the real next exercise or item in 1 of
  30 course snapshots and 3 of 30 project snapshots.
- **Sessions without edits leave the file stale.** A session that touches no file is not rewritten (`skip`). In
  `small-project`, sessions 2-5 often stalled in both arms: the agent took "next item" to mean item 1 again,
  because `TODO.md` was never ticked and the fix could not be run. Those stalls never reached `CONTEXT.md`, which
  kept the session-1 text for up to 4 sessions.

## Context accuracy over time

Statements found false were rare: under 0.4 per snapshot, and the three left (two distinct statements) are rule artefacts (see caveats).
The file stays accurate but drops facts. The number of missing facts grows with the chain: in `course-notebooks`,
3.7 of 8 facts were missing at session 10 (notes, the decision, later exercises). Size stayed at 0.8-1.4 KB, well
under the cap.

## course-notebooks

### Cumulative per chain

| arm | chains | sessions ok | tokens in (incl. cache) | tokens out | turns | seconds | cost USD | continuity /9 | pickup | decisions | consistency | items done at end | convention at end | regression fixed (s6 / end) | redo count | tests at end |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 3 | 30/30 | 3510273.67 ± 463157.61 | 24193.67 ± 2253.18 | 78.33 ± 5.86 | 172.07 ± 21.83 | 0.35 ± 0.05 | 6.47 | 2.50 | 2.03 | 1.93 | 7.67 ± 0.58 | 0.47 ± 0.46 | 3/3 / 3/3 | [0, 0, 0] | - |
| B | 3 | 30/30 | 4444303.67 ± 341683.82 | 24775.67 ± 1335.86 | 93.67 ± 4.93 | 183.73 ± 14.03 | 0.33 ± 0.11 | 6.63 | 2.63 | 1.70 | 2.30 | 7.67 ± 0.58 | 0.2 ± 0.0 | 3/3 / 3/3 | [0, 0, 0] | - |

### Per session (mean over chains)

| session | request | A tokens in | B tokens in | A turns | B turns | A secs | B secs | A continuity /9 | B continuity /9 | A progress on next item | B progress on next item | A redo | B redo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Implement exercise 1.1. | 396718.33 | 461685.67 | 7.33 | 7.67 | 14.97 | 13.67 | 8.67 | 9.00 | 3/3 | 3/3 | 0 | 0 |
| 2 | Now exercise 1.2. | 333059.67 | 435846.33 | 7.00 | 7.33 | 13.27 | 15.77 | 9.00 | 9.00 | 3/3 | 3/3 | 0 | 0 |
| 3 | Write my chapter 1 notes in notes.md: what each … | 334786.33 | 330206.67 | 7.00 | 6.00 | 15.33 | 10.50 | 8.33 | 9.00 | 3/3 | 3/3 | 0 | 0 |
| 4 | Implement exercise 2.1. Decision for the whole c… | 336551.00 | 598111.33 | 7.33 | 11.33 | 18.40 | 27.33 | 8.33 | 8.33 | 3/3 | 3/3 | 0 | 0 |
| 5 | Next exercise. | 473957.33 | 567954.33 | 9.00 | 10.00 | 18.80 | 18.47 | 6.00 | 5.00 | 3/3 | 3/3 | 0 | 0 |
| 6 | Before I move on to chapter 3, review what is do… | 515496.67 | 612778.00 | 13.33 | 15.00 | 36.47 | 28.73 | 5.67 | 5.00 | 3/3 | 3/3 | 0 | 0 |
| 7 | Where were we and what's next? | 66323.33 | 332649.67 | 1.00 | 8.00 | 7.53 | 12.90 | 3.33 | 5.67 | - | - | 0 | 0 |
| 8 | Continue with exercise 3.1. | 465366.33 | 433758.00 | 7.00 | 9.00 | 15.40 | 15.73 | 6.67 | 5.67 | 3/3 | 3/3 | 0 | 0 |
| 9 | Refactor the earlier exercises so they all follo… | 309605.67 | 310306.00 | 14.33 | 13.33 | 18.93 | 26.60 | 4.33 | 3.00 | 3/3 | 3/3 | 0 | 0 |
| 10 | Write a summary of what is done and what remains… | 278409.00 | 361007.67 | 5.00 | 6.00 | 12.97 | 14.03 | 4.33 | 6.67 | 2/3 | 2/3 | 0 | 0 |

### Context accuracy (arm A, CONTEXT.md after each sync; mean over chains)

| session | Status lines | true | false | unverifiable | ground-truth facts | missing | Next names the next item | size bytes | rewrite | rewrite tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3.33 | 1.67 | 0.00 | 1.67 | 1.00 | 0.00 | 1/3 | 758.00 | sync | 1472.33/384.00 |
| 2 | 4.67 | 2.67 | 0.00 | 2.00 | 2.00 | 0.00 | 0/3 | 989.33 | sync | 1728.67/410.00 |
| 3 | 6.67 | 4.67 | 0.00 | 2.00 | 3.00 | 0.33 | 0/3 | 1157.67 | sync | 1998.67/837.00 |
| 4 | 7.67 | 5.33 | 0.33 | 2.00 | 5.00 | 1.00 | 0/3 | 1259.33 | sync | 2099.00/2418.33 |
| 5 | 7.00 | 5.67 | 0.00 | 1.33 | 6.33 | 1.67 | 0/3 | 1359.33 | sync | 2421.67/816.67 |
| 6 | 7.00 | 5.67 | 0.00 | 1.33 | 7.00 | 2.67 | 0/3 | 1371.67 | sync | 2108.67/741.00 |
| 7 | 6.67 | 5.33 | 0.00 | 1.33 | 7.00 | 2.67 | 0/3 | 1384.33 | skip,sync | 820.33/694.00 |
| 8 | 6.33 | 5.33 | 0.00 | 1.00 | 8.00 | 3.33 | 0/3 | 1360.67 | sync | 2048.00/1184.00 |
| 9 | 6.33 | 5.33 | 0.00 | 1.00 | 8.00 | 3.33 | 0/3 | 1367.00 | skip,sync | 653.00/226.33 |
| 10 | 6.33 | 5.00 | 0.00 | 1.33 | 8.00 | 3.67 | 0/3 | 1384.33 | sync | 2253.33/786.33 |

Rewrite total over 3 chains: 52811 tokens in, 25493 out; upper bound at uncached Haiku list price: 0.18 USD.

## small-project

### Cumulative per chain

| arm | chains | sessions ok | tokens in (incl. cache) | tokens out | turns | seconds | cost USD | continuity /9 | pickup | decisions | consistency | items done at end | convention at end | regression fixed (s6 / end) | redo count | tests at end |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 3 | 30/30 | 3722208.0 ± 317071.41 | 22029.0 ± 8386.42 | 89.0 ± 19.29 | 169.53 ± 27.9 | 0.29 ± 0.14 | 5.90 | 1.40 | 2.87 | 1.63 | 3.67 ± 2.08 | 1.0 ± 0.0 | 3/3 / 3/3 | [0, 0, 0] | 6/6, 9/9, 4/4 |
| B | 3 | 30/30 | 4981730.0 ± 245703.21 | 27048.67 ± 1119.7 | 124.67 ± 1.15 | 189.4 ± 14.88 | 0.3 ± 0.02 | 5.20 | 1.03 | 2.80 | 1.37 | 2.67 ± 0.58 | 1.0 ± 0.0 | 3/3 / 3/3 | [0, 0, 0] | 6/6, 4/4, 6/6 |

### Per session (mean over chains)

| session | request | A tokens in | B tokens in | A turns | B turns | A secs | B secs | A continuity /9 | B continuity /9 | A progress on next item | B progress on next item | A redo | B redo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Fix the failing test. | 397432.00 | 429439.33 | 9.00 | 8.67 | 14.93 | 14.40 | 9.00 | 9.00 | 3/3 | 3/3 | 0 | 0 |
| 2 | Next item on the TODO. | 501386.67 | 561286.67 | 10.00 | 13.67 | 20.30 | 18.67 | 6.00 | 4.00 | 1/3 | 0/3 | 0 | 0 |
| 3 | Next TODO item. Decision for this project: the C… | 404758.67 | 623092.00 | 10.00 | 14.33 | 18.43 | 23.30 | 4.00 | 2.67 | 1/3 | 1/3 | 0 | 0 |
| 4 | Next TODO item. | 397462.67 | 506070.00 | 10.33 | 14.00 | 15.53 | 18.47 | 5.33 | 4.00 | 1/3 | 0/3 | 0 | 0 |
| 5 | Next TODO item. | 366095.67 | 563196.33 | 9.00 | 14.00 | 16.93 | 17.90 | 4.33 | 4.00 | 0/3 | 1/3 | 0 | 0 |
| 6 | I pulled changes from a colleague. Check the cod… | 369310.67 | 433566.33 | 11.00 | 13.00 | 18.50 | 17.83 | 7.33 | 8.33 | 3/3 | 3/3 | 0 | 0 |
| 7 | Where are we and what's next? | 102636.33 | 364208.67 | 2.33 | 10.33 | 8.63 | 17.30 | 4.33 | 3.67 | - | - | 0 | 0 |
| 8 | Next TODO item. | 368753.33 | 497814.67 | 10.00 | 13.33 | 18.37 | 20.30 | 4.33 | 4.00 | 1/3 | 0/3 | 0 | 0 |
| 9 | Add a usage section to README.md that documents … | 409950.67 | 557403.00 | 8.33 | 10.00 | 19.63 | 16.87 | 9.00 | 9.00 | 1/3 | 0/3 | 0 | 0 |
| 10 | Final status: what is done, what remains, anythi… | 404421.33 | 445653.00 | 9.00 | 13.33 | 18.27 | 24.37 | 5.33 | 3.33 | 3/3 | 3/3 | 0 | 0 |

### Context accuracy (arm A, CONTEXT.md after each sync; mean over chains)

| session | Status lines | true | false | unverifiable | ground-truth facts | missing | Next names the next item | size bytes | rewrite | rewrite tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4.00 | 1.33 | 0.00 | 2.67 | 1.00 | 0.33 | 0/3 | 797.67 | sync | 1475.33/401.67 |
| 2 | 5.33 | 2.33 | 0.00 | 3.00 | 1.33 | 0.33 | 0/3 | 1043.67 | skip,sync | 617.33/150.00 |
| 3 | 5.33 | 2.67 | 0.00 | 2.67 | 2.67 | 1.00 | 0/3 | 1093.33 | skip,sync | 733.67/207.33 |
| 4 | 4.67 | 2.67 | 0.00 | 2.00 | 3.00 | 1.33 | 0/3 | 1023.33 | skip,sync | 760.33/229.00 |
| 5 | 4.67 | 2.67 | 0.00 | 2.00 | 3.00 | 1.33 | 0/3 | 1023.33 | skip | 0.00/0.00 |
| 6 | 6.00 | 2.67 | 0.00 | 3.33 | 3.00 | 1.67 | 0/3 | 1223.33 | sync | 1955.00/563.00 |
| 7 | 6.00 | 2.67 | 0.00 | 3.33 | 3.00 | 1.67 | 0/3 | 1223.33 | skip | 0.00/0.00 |
| 8 | 6.00 | 3.00 | 0.00 | 3.00 | 3.33 | 1.67 | 1/3 | 1233.67 | skip,sync | 728.00/217.67 |
| 9 | 6.00 | 3.00 | 0.33 | 2.67 | 3.33 | 1.67 | 1/3 | 1365.67 | sync | 1932.33/625.67 |
| 10 | 6.33 | 3.67 | 0.33 | 2.33 | 3.33 | 1.33 | 1/3 | 1361.67 | sync | 2196.33/648.00 |

Rewrite total over 3 chains: 31195 tokens in, 9127 out; upper bound at uncached Haiku list price: 0.077 USD.

## Cost

120 Haiku sessions: 3.81 USD. 120 Sonnet judge calls: 1.96 USD, plus 1.96 USD for a second pass with the stricter redaction above
(the scores reported are from that second pass). Smoke (2 sessions + 2 judge calls): 0.09 USD.
Rewrites: 84 k tokens in and 35 k out over the 6 ctx chains, which is at most 0.26 USD at uncached Haiku list price.

## Caveats

- 3 chains per arm and scenario: the standard deviations above are of the same order as most A/B differences.
- No shell in the sessions (a choice, so that the harness alone runs code). This drives the "run the tests" `Next`
  lines and the `small-project` stalls, and it may favour or penalise ctx differently with a shell.
- The prompts are terse on purpose ("Next item on the TODO."). Haiku often read them as "item 1 again". That is a
  real continuity failure, but it makes `small-project` mostly measure the stall.
- The context-accuracy rules are coarse. A line is credited with every item it names. "Unverifiable" lines (about
  verification, git, the folder) are not counted as true or false. The false lines left are a README line
  judged against a check that needs at least one CLI option to exist, and a "chapter 2 notes" line written when
  only part of chapter 2 was done.
- The judge is blind to the arm, but a 1-turn answer, or a reply that quotes a sync time, can hint at ctx.
- In 3 of 60 ctx sessions, the first rewrite reported "no session reports available": the last assistant messages
  had not been collected. The rewrite then only knew which file was touched. Cause found and fixed in 1.1.0 (see
  run 2 below).

## Run 2 (ctx 1.1.0): the three weaknesses addressed

Same harness, scenarios, prompts, model and judge; only the ctx arm was re-run (3 chains x 2 scenarios x 10
sessions = 60 Haiku sessions, 60 Sonnet judge calls). The arm without ctx is the published one above: the
comparison crosses two runs on different days, with 3 chains each, so it is indicative only.

What changed in ctx: a `Decisions` section owned by code (the model can only add to it or mark a line superseded),
an injection that starts with the state's date and provenance and asks to verify in the files before acting on
`Next`, a reduced rewrite of `Next` and `Decisions` (sync-light) for sessions that changed no file but report a
decision, a blocker or a next step, and the collection fix: the pid that protects a running session was the
short-lived `sh` that runs the hook command, so a session started in another chain (one shared state directory)
caught up a running session before its Stop hook had stored the last messages.

| scenario | arm | tokens in per chain (incl. cache) | turns | continuity /9 | session 7 continuity | session 7 turns | D1 compliance at end | redo |
|---|---|---|---|---|---|---|---|---|
| course-notebooks | ctx 1.1.0 | 3.77 M ± 0.47 | 97 ± 1 | 7.10 | 3.33 | 6.7 | 0.67 | 0 |
| course-notebooks | ctx 1.0.0 | 3.51 M ± 0.46 | 78 ± 6 | 6.47 | 3.33 | 1.0 | 0.47 | 0 |
| course-notebooks | without ctx | 4.44 M ± 0.34 | 94 ± 5 | 6.63 | 5.67 | 8.0 | 0.20 | 0 |
| small-project | ctx 1.1.0 | 3.72 M ± 0.46 | 93 ± 6 | 6.17 | 5.67 | 6.0 | 1.0 | 0 |
| small-project | ctx 1.0.0 | 3.72 M ± 0.32 | 89 ± 19 | 5.90 | 4.33 | 2.3 | 1.0 | 0 |
| small-project | without ctx | 4.98 M ± 0.25 | 125 ± 1 | 5.20 | 3.67 | 10.3 | 1.0 | 0 |

The user's decision D1 in `CONTEXT.md` (chains out of 3 whose file names it, after each session; it is given at
session 4 in `course-notebooks` and session 3 in `small-project`):

| session | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| course-notebooks, 1.1.0 | 0 | 0 | 0 | 2 | 2 | 2 | 2 | 2 | 3 | 3 |
| course-notebooks, 1.0.0 | - | - | - | recorded in 1 chain, dropped at session 5 | | 0 | 0 | 0 | 0 | 0 |
| small-project, 1.1.0 | 0 | 0 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| small-project, 1.0.0 | - | - | recorded in 1 chain, dropped at session 6 | | | 0 | 0 | 0 | 0 | 0 |

Active lines in `Decisions` grew to 7.7 (course) and 7.0 (project) at session 10; the cap of 15 lines / 1000
characters was hit in 3 course chains (7 old lines dropped, logged).

- **Decisions now survive.** Once recorded, the decision was never lost; in 1 of 3 `small-project` chains the
  rewriter never recorded it. The refactor session ("the convention we
  decided", session 9) left all files compliant in 2 of 3 course chains (1 of 6 chains before, in either arm);
  its continuity rose to 6.00 (4.33 with 1.0.0, 3.00 without ctx). In the third course chain the rewriter recorded
  "no type hints" as the convention for chapter 1, a wrong reading of D1 that the code then kept faithfully.
- **Resume still trusts `Next` on `course-notebooks`.** At session 7 the agent now opens the files (6.7 turns
  instead of 1) but all 3 chains still give "run the checks" from `Next` as the next step instead of exercise 3.1
  and omit D1 from the answer: 3.33, unchanged. On `small-project` the resume answer improved (5.67 vs 4.33, 3.67
  without ctx). The dated line made resumes more careful and more expensive, not more correct.
- **Sessions without edits are recorded.** 4 sessions went through sync-light (session 7 in both scenarios) and 11
  were skipped as before. No `CONTEXT.md` mentioned missing session reports (3 of 60 rewrites before).
- **Cost.** Tokens and turns per chain rose on `course-notebooks` (3.77 M, 97 turns) to the level of the arm without
  ctx; on `small-project` they stayed at the 1.0.0 level, well under the arm without ctx. The rewrites used 118 k
  tokens in and 63 k out (at most 0.43 USD at uncached Haiku list price), against 84 k / 35 k before.
- Continuity is higher than both earlier arms in both scenarios (7.10 and 6.17), but the gaps are of the order of
  the spread between chains, from two separate runs: not a solid improvement.

Cost of run 2: 60 Haiku sessions 2.84 USD, 60 Sonnet judge calls 1.01 USD. The real `~/.ctx/log` did not change
during the run (size checked before and after; no line mentions the test home).

## Reproduce

```
python3 ctx.py install-hooks claude --settings-file /tmp/ctx-long/hooks.json --yes
python3 bench/run_longrun.py run --home /tmp/ctx-long/home --hooks /tmp/ctx-long/hooks.json --out /tmp/ctx-long/out
# run 2 (ctx arm only): add --arms A --max-sessions 70
python3 bench/run_longrun.py judge --home /tmp/ctx-long/home --out /tmp/ctx-long/out
python3 bench/run_longrun.py summary --out /tmp/ctx-long/out
```

Smoke: add `--scenarios course-notebooks --arms A --reps 1 --sessions 2 --workers 1` to `run`. Raw per-session
JSON (prompt, reply, diff, checks, `CONTEXT.md`, judge scores) and the generated `SUMMARY.md` go to the output
folder, which is not versioned. This document holds the aggregates.
