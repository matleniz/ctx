# Measures

All figures are indicative: small samples, one session model (Claude Haiku), one blind judge (Claude Sonnet), runs
on 5 and 6 November 2026. The v2 scripts ran in temporary folders and were lost, so the figures cannot be
reproduced as they stand; the [protocol](#protocol) below says how to redo them. Version 1 figures and method:
[Measured in the 1.2.0
README](https://github.com/matleniz/ctx/blob/v1/README.md#measured) and the
[long-run method](https://github.com/matleniz/ctx/blob/v1/docs/benchmark-longrun.md).

## Version 1 against a plain rule

10-session chains on two synthetic folders (course notebooks, small project), 3 chains per arm. Continuity out of
9 (the judge scores pickup, respect of earlier decisions and consistency, 0-3 each):

| arm | course notebooks | small project |
|---|---|---|
| ctx 1.1.0 (model rewrite after each session) | 6.2 | 5.9 |
| ctx 1.2.0 (agent writes, model rewrite as fallback) | 8.3 | 7.6 |
| plain `STATE.md` rule, no tool | 8.5 | 8.6 |

## ctx 2.0 against nothing

ctx 2.0.0 (the rule without the 2.0.1 and 2.0.2 lines, and the hook) against no rule and no hook:

| case | ctx 2.0 | nothing |
|---|---|---|
| 10-session chains, continuity /9 (course, project; 3 chains each) | 8.6, 8.8 | 7.3, 5.2 |
| same, "where are we?" sessions, /9 | 6.7, 8.3 | 5.3, 3.0 |
| same, items done after 10 sessions, out of 8 | 7.3, 8.0 | 8.0, 2.0 |
| same, tokens read per session | 491k, 504k | 461k, 524k |
| resuming copies of 5 real course and project folders: status /3, next step /3 | 2.7, 3.0 | 1.1, 1.8 |
| same: turns, tokens | 2.8, 199k | 6.0, 354k |
| one short task in such a folder: done, turns, tokens (8 per arm) | 8/8, 6.8, 445k | 8/8, 3.3, 249k |
| 6 questions from a parent folder: right answers, turns, tokens (18 per arm) | 1.00, 3.6, 177k | 0.22, 7.3, 315k |

Where ctx did worse:

- On the course chain, the arm with nothing finished more exercises (8 against 7.3): writing `STATE.md` takes part
  of each session. On the project chain, the same arm lost the thread (2 items out of 8).
- On the course chain, ctx read more tokens per session (491k against 461k).
- One short task cost about twice the tokens: updating `STATE.md` after a one-line change.
- On a tree built from real `STATE.md` files, a blocker absent from a first line was not seen from the parent
  folder: line 1 is all that goes up (hence the "Blocked: first" line of the rule since 2.0.1).

The same `STATE.md` files without the hook answer the parent-folder questions as well (1.00) in 5.6 turns and 243k
tokens: the files carry the answers, the hook saves turns.

## The 2.0.1 line on short tasks

"Update `STATE.md` only when Status, Next or Decisions change", on copies of real folders: 6 of 6 pure touch-ups
(one README line, a docstring) left `STATE.md` alone, at about the cost of no rule; 8 of 8 tasks that changed the
state (a decision, a new deadline, progress) still recorded it.

## Accumulation

Over 10 sessions the file grew to about 28 lines and 4 KB, about one decision per session, and no chain removed a
decision.

## Protocol

How to redo the runs. Everything here comes from the tables above and the notes of the runs; nothing else was kept.

**Arms.** "ctx": the `STATE.md` rule (2.0.0 text for the tables above) and the hook. "nothing": no rule, no hook.
For the questions asked from a parent folder, a third arm has the same `STATE.md` files but no hook. Version 1 runs
had one arm per version, plus a plain-rule arm without any tool.

**Sessions.** Each session is a new process of the session model (Claude Haiku), started in the folder. A chain is
10 sessions in a row on the same folder, so each one starts from what the previous one left. The home folder is
temporary and the hooks are given per run, so a real configuration is never touched. 3 chains per arm and scenario.

**Scenarios.** Two synthetic folders:

- course notebooks: exercises to finish, one per session;
- small project: a Python package with tests and a TODO list.

Each chain follows the same script:

1. s01: first look at the folder and first task.
2. s03 or s04: the user states an explicit decision (a convention to follow from then on).
3. s06: a bug is injected in the code.
4. s07: resume. The prompt is a plain "where are we?", with no task given.
5. s09: a change that must follow the decision of s03 or s04.
6. s10: a final summary.

**Judge.** Claude Sonnet, blind to the arm. It gets the session's answer and the files, and scores continuity out of
9:
pickup, respect of earlier decisions and consistency, 0-3 each. The arm label and the name of the notes file are
masked from the judge; a name that collides with a real file name leaks the arm (it did once, and the answers were
judged again). Report: continuity, the s07 resume score and turns, whether the injected bug is fixed, items done out
of 8 after 10 sessions, tokens read per session.

**Resuming real folders.** Copies of 5 real course and project folders, with a `STATE.md` written for each. A read-
only
session asks for a generic resume. The judge scores the status and the next step out of 3 against the ground truth:
the files, a dated listing, and the agent's own memory notes. Record turns and tokens.

**Short task.** 8 runs per arm on a copy of such a folder: one one-line change. Record whether it was done, turns and
tokens. For the 2.0.1 line: 6 pure touch-ups and 8 state-changing tasks, count how often `STATE.md` was updated.

**Parent-folder questions.** A tree of several folders, 6 questions whose answers sit in the first lines (what is
blocked, what is next, which folder has a given decision), 18 runs per arm. Score the share of right answers, and
record turns and tokens. The no-hook arm is run on the same tree.

**Statistics.** Report means per arm. With small samples, group by folder (the statistical unit is the folder, not
the session), and give a bootstrap interval or a sign test rather than a bare mean. Two judges (a second model)
give a useful check of the first judge's scale.

**Pitfalls seen.** An arm "without" must hide every notes file: a renamed file is still found by a file listing
that shows hidden files (18 of 80 sessions found it). A stale `Next` can be believed without any check; the rule
now says to verify before acting. A session with no change to the folder is never recorded, by design.
