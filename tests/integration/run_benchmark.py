#!/usr/bin/env python3
"""Benchmark of ctx on real folders: two tasks per folder, with ctx (real hooks) vs without (CTX_OFF=1).

Tasks: tests/integration/benchmark-tasks.json (generic labels). Map: a local, unpublished JSON file
{"redact": [words], "folders": {label: {"path": "~/...", "reference": {task_id: text}}}} that gives each label its
real folder, an optional reference answer per task (judge only) and words to redact from everything written.

Phases (each one resumable: a run or a judgement already on disk is not redone):
  sessions  per folder: sha256 manifest, bootstrap of a fresh node (one read-only "fais le point" session, ctx on,
            wait for its sync line), then every (folder, task, arm, run) in one shuffled order. After each session a
            stat manifest of the folder is compared to the baseline: anything other than a CONTEXT.md changed aborts
            the folder. 3 failed sessions in a row (after one retry each) stop the run.
  judge     blind Sonnet judge, no tools, answers shuffled across arms, ground truth = readable files + dated
            listing + Claude Code auto-memory + the task's pointer files (+ git log/status, read-only) + reference.
  stats     per cell mean/sd, paired (with - without) bootstrap 95 % CI over cells and runs, cache rate per arm,
            final sha256 manifest check. Writes OUT/SUMMARY.md and OUT/results.json.

Usage:
  python3 tests/integration/run_benchmark.py --map MAP.json --out OUT [--runs 5] [--labels a,b]
         [--tasks-filter resume,specific] [--model haiku] [--judge-model sonnet] [--phase all|sessions|judge|stats]
         [--tag NAME] [--seed 20261104]
Never touches git state, ~/.claude/settings.json, ~/.ctxignore or ~/.ctx/declined. Leave CTX_HOME unset for the real
benchmark (set it only on a mirror tree).
"""
import argparse
import hashlib
import json
from math import fsum
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_ab_real_folders as ab  # noqa: E402

TASKS_FILE = HERE / "benchmark-tasks.json"
ARMS = ("with", "without")
TIMEOUT = 180
BOOT_PROMPT = ab.BOOT_PROMPT


# ------------------------------------------------------------------ pure parts
def load_tasks(path=TASKS_FILE, private=None):
    """Published tasks, completed by the private map (`folders.<label>.tasks.<id>`: exact prompt, pointers).
    Without a private map, a task without a prompt keeps its generic description as prompt (prompt=None)."""
    j = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for f in j["folders"]:
        for t in f["tasks"]:
            if t["type"] not in ab.ACTIONABILITY:
                raise SystemExit(f"{f['label']}/{t['id']}: unknown type {t['type']}")
            pv = (((private or {}).get("folders") or {}).get(f["label"]) or {}).get("tasks", {}).get(t["id"], {})
            out.append({"label": f["label"], "task": t["id"], "type": t["type"],
                        "prompt": pv.get("prompt") or t.get("prompt"), "description": t.get("description"),
                        "pointers": pv.get("pointers", t.get("pointers", []))})
    return out


def schedule(tasks, runs, seed, arms=ARMS):
    """Every (label, task, arm, run) once, in one shuffled order (spreads time-of-day and cache effects)."""
    items = [(t["label"], t["task"], arm, i) for t in tasks for arm in arms for i in range(1, runs + 1)]
    random.Random(seed).shuffle(items)
    return items


def license_names(path=None):
    """Capitalised words of the `Copyright (c) YEAR Name Surname` line of LICENSE (the author's names)."""
    try:
        t = Path(path or ab.CTX_DIR / "LICENSE").read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"Copyright \(c\) \d{4}\s+(.+)", t)
    return [w for w in re.findall(r"[A-Z][\w'-]{2,}", m.group(1))] if m else []


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NAME_PAIR_RE = re.compile(r"\b[A-Z][a-z]+_[A-Z][A-Za-z]+\b")  # name1_name2 file-name pattern, e.g. Smith_DoeJones


def redact(text, words, folder=None):
    """Home -> ~, the real folder path -> <folder>, e-mail addresses, `Name_Name` pairs, a capitalised word list
    entry followed by up to two capitalised words (a first name and its surname), then each word of the list
    (case-insensitive) -> [redacted]."""
    if not text:
        return text
    if folder:
        text = text.replace(str(folder), "<folder>")
    text = ab.scrub_home(text)
    if folder:
        text = text.replace(ab.scrub_home(str(folder)), "<folder>")
    text = EMAIL_RE.sub("[redacted]", text)
    text = NAME_PAIR_RE.sub("[redacted]", text)
    for w in words:
        if w[:1].isupper() and " " not in w:
            text = re.sub(rf"\b{re.escape(w)}(?:\s+[A-Z][\w'-]+){{1,2}}", "[redacted]", text)
    for w in sorted(words, key=len, reverse=True):
        text = re.sub(re.escape(w), "[redacted]", text, flags=re.I)
    return text


def stat_manifest(root):
    """{relative path: (size, mtime_ns)} of every file, CONTEXT.md excluded, `.git` skipped: a cheap change check."""
    root = Path(root)
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ".git"]
        for fn in fns:
            if fn == "CONTEXT.md":
                continue
            p = Path(dp) / fn
            try:
                st = p.lstat()
                out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
            except OSError:
                out[str(p.relative_to(root))] = None
    return out


def usage_row(j):
    u = j.get("usage") or {}
    return {"turns": j.get("num_turns"), "input_tokens": u.get("input_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0), "cache_creation": u.get("cache_creation_input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0), "duration_ms": j.get("duration_ms"),
            "cost_usd": j.get("total_cost_usd", j.get("cost_usd"))}


def tokens(r):
    """Context tokens read by the session: cache_read + cache_creation + uncached input."""
    return (r.get("cache_read") or 0) + (r.get("cache_creation") or 0) + (r.get("input_tokens") or 0)


METRICS = {
    "quality": ab.quality,
    "tokens": tokens,
    "turns": lambda r: r.get("turns"),
    "seconds": lambda r: r.get("secs"),
    "cost_usd": lambda r: r.get("cost_usd"),
}


def cells(results):
    """{(label, task): {arm: [runs]}} of the results that have a session (errors excluded)."""
    out = {}
    for r in results:
        if r.get("error"):
            continue
        out.setdefault((r["label"], r["task"]), {}).setdefault(r["arm"], []).append(r)
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return fsum(xs) / len(xs) if xs else None  # fsum: exact, same result on every Python version


def paired(results, metric, type_=None, B=10000, seed=1, types=None):
    """Mean over (folder, task) cells of mean(with) - mean(without), with a bootstrap 95 % CI (cells resampled,
    then runs resampled within each arm of each cell). Also the same as a percent of the mean `without` level.
    Returns dict(n_cells, diff, lo, hi, pct, pct_lo, pct_hi, with_mean, without_mean, cells_with_better)."""
    f = METRICS[metric]
    cs = []
    for (label, task), arms in sorted(cells(results).items()):
        if type_ and types and types.get((label, task)) != type_:
            continue
        w = [f(r) for r in arms.get("with", []) if f(r) is not None]
        o = [f(r) for r in arms.get("without", []) if f(r) is not None]
        if w and o:
            cs.append((w, o))
    if not cs:
        return {"n_cells": 0}

    def point(sample):
        d = [_mean(w) - _mean(o) for w, o in sample]
        lw = _mean([_mean(w) for w, _ in sample])
        lo_ = _mean([_mean(o) for _, o in sample])
        return _mean(d), lw, lo_
    diff, wm, om = point(cs)
    rng = random.Random(seed)
    diffs, pcts = [], []
    for _ in range(B):
        s = [cs[rng.randrange(len(cs))] for _ in cs]
        s = [([w[rng.randrange(len(w))] for _ in w], [o[rng.randrange(len(o))] for _ in o]) for w, o in s]
        d, _, o_ = point(s)
        diffs.append(d)
        pcts.append(100 * d / o_ if o_ else 0.0)
    diffs.sort()
    pcts.sort()
    q = lambda xs, p: xs[min(len(xs) - 1, max(0, int(p * len(xs))))]  # noqa: E731
    better = sum(1 for w, o in cs if _mean(w) > _mean(o))
    worse = sum(1 for w, o in cs if _mean(w) < _mean(o))
    return {"n_cells": len(cs), "diff": diff, "lo": q(diffs, 0.025), "hi": q(diffs, 0.975),
            "pct": 100 * diff / om if om else None, "pct_lo": q(pcts, 0.025), "pct_hi": q(pcts, 0.975),
            "with_mean": wm, "without_mean": om, "cells_with_higher": better, "cells_with_lower": worse}


def significant(p):
    return p.get("n_cells", 0) > 0 and (p["lo"] > 0 or p["hi"] < 0)


def fmt(x, nd=2):
    if x is None:
        return "n/a"
    return f"{x:,.{nd}f}".replace(",", " ")


def cell_table(results, types):
    head = ("| folder | task | arm | n | quality /9 mean (sd) | tokens mean | turns mean | seconds mean | cost USD mean |\n"
            "|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for (label, task), arms in sorted(cells(results).items()):
        for arm in ARMS:
            rs = arms.get(arm, [])
            q = ab.stats([ab.quality(r) for r in rs])
            rows.append(f"| {label} | {task} | {arm} | {len(rs)} | {fmt(q['mean'])} ({fmt(q['sd'])}) | "
                        f"{fmt(_mean([tokens(r) for r in rs]), 0)} | {fmt(_mean([r.get('turns') for r in rs]), 1)} | "
                        f"{fmt(_mean([r.get('secs') for r in rs]), 1)} | {fmt(_mean([r.get('cost_usd') for r in rs]), 4)} |")
    return head + "\n".join(rows) + "\n"


def headline_rows(results, types, B=10000):
    """[(scope, metric, paired dict)] for all tasks, resume only, specific only."""
    out = []
    for scope in (None, "resume", "specific"):
        for m in ("quality", "tokens", "turns", "seconds", "cost_usd"):
            out.append((scope or "all", m, paired(results, m, scope, B=B, types=types)))
    return out


def headline_table(rows):
    head = ("| tasks | metric | cells | without (mean) | with (mean) | with - without | 95 % CI | change % | 95 % CI (%) | "
            "cells with higher / lower | CI excludes 0 |\n|---|---|---|---|---|---|---|---|---|---|---|\n")
    nd = {"quality": 2, "tokens": 0, "turns": 1, "seconds": 1, "cost_usd": 4}
    out = []
    for scope, m, p in rows:
        if not p.get("n_cells"):
            continue
        d = nd[m]
        out.append(f"| {scope} | {m} | {p['n_cells']} | {fmt(p['without_mean'], d)} | {fmt(p['with_mean'], d)} | "
                   f"{fmt(p['diff'], d)} | [{fmt(p['lo'], d)}, {fmt(p['hi'], d)}] | {fmt(p['pct'], 1)} | "
                   f"[{fmt(p['pct_lo'], 1)}, {fmt(p['pct_hi'], 1)}] | {p['cells_with_higher']} / {p['cells_with_lower']} | "
                   f"{'yes' if significant(p) else 'no'} |")
    return head + "\n".join(out) + "\n"


# ------------------------------------------------------------------ folder-level analysis (pure)
# The unit is the folder: its two tasks share one CONTEXT.md. Effects: quality in points; tokens, turns, seconds and
# cost as a ratio of means (mean over folders of the with level / same for without, minus 1, in %).
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131}
JUDGES = ("sonnet", "opus", "mean")
NUMERIC_FIELDS = ("turns", "secs", "input_tokens", "cache_read", "cache_creation", "output_tokens", "cost_usd",
                  "tool_calls", "read_context_md", "context_hidden", "attempts", "hidden_file_seen")


def numeric_run(rec, run_set):
    """A run record reduced to numbers keyed by generic labels: no answer, comment, session id or path."""
    out = {"set": run_set, "label": rec["label"], "task": rec["task"], "type": rec.get("type"), "arm": rec["arm"],
           "run": rec["run"], "error": bool(rec.get("error"))}
    for k in NUMERIC_FIELDS:
        if k in rec:
            out[k] = rec[k]
    for judge, key in (("sonnet", "scores"), ("opus", "scores_opus")):
        s = rec.get(key)
        out[f"scores_{judge}"] = {k: s[k] for k in ("exactness", "actionability", "no_invention")} if s else None
    return out


def quality_of(r, judge="sonnet"):
    if judge == "mean":
        a, b = quality_of(r, "sonnet"), quality_of(r, "opus")
        return (a + b) / 2 if a is not None and b is not None else None
    s = r.get(f"scores_{judge}")
    return fsum(s.values()) if s else None


def metric_fn(metric, judge="sonnet"):
    if metric == "quality":
        return lambda r: quality_of(r, judge)
    return {"tokens": tokens, "turns": lambda r: r.get("turns"), "seconds": lambda r: r.get("secs"),
            "cost": lambda r: r.get("cost_usd")}[metric]


def comparison_runs(runs, with_set, without_set):
    """Runs of one comparison: the with arm from `with_set`, the without arm from `without_set`."""
    return [r for r in runs if not r.get("error") and
            ((r["arm"] == "with" and r["set"] == with_set) or (r["arm"] == "without" and r["set"] == without_set))]


def folder_data(runs, f, type_=None):
    """{label: [(with values, without values) per task]} (tasks sorted, empty arms dropped)."""
    d = {}
    for (label, task) in sorted({(r["label"], r["task"]) for r in runs}):
        cell = [r for r in runs if r["label"] == label and r["task"] == task and (not type_ or r.get("type") == type_)]
        w = [f(r) for r in cell if r["arm"] == "with" and f(r) is not None]
        o = [f(r) for r in cell if r["arm"] == "without" and f(r) is not None]
        if w and o:
            d.setdefault(label, []).append((w, o))
    return d


def _levels(sample):
    """sample = [[(w, o) per task] per folder] -> (W, O, [D_f]) with W, O the means over folders of the folder level."""
    wf = [_mean([_mean(w) for w, _ in cs]) for cs in sample]
    of = [_mean([_mean(o) for _, o in cs]) for cs in sample]
    return _mean(wf), _mean(of), [a - b for a, b in zip(wf, of)], wf, of


def binom_two_sided(k, n):
    """Exact two-sided sign test p-value (k successes out of n, p = 0.5)."""
    if n == 0:
        return 1.0
    from math import comb
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def folder_effect(runs, metric, judge="sonnet", type_=None, B=5000, seed=1):
    """Folder-level effect. points: W - O; pct: 100 * (W / O - 1) (ratio of means). CIs: cluster bootstrap by folder
    (folders resampled, then runs within each arm of each cell) and paired t on the folder differences (n - 1 df,
    divided by O for pct). Sign test over folders. Sensitivity for pct metrics: geometric mean of W_f / O_f."""
    from math import exp, log, sqrt
    data = folder_data(runs, metric_fn(metric, judge), type_)
    labels = sorted(data)
    n = len(labels)
    if n < 2:
        return {"n_folders": n}
    kind = "points" if metric == "quality" else "pct"
    sample = [data[lb] for lb in labels]
    W, O, D, wf, of = _levels(sample)
    eff = (W - O) if kind == "points" else 100 * (W / O - 1)
    rng = random.Random(seed)
    boots = []
    for _ in range(B):
        s = [sample[rng.randrange(n)] for _ in range(n)]
        s = [[([w[rng.randrange(len(w))] for _ in w], [o[rng.randrange(len(o))] for _ in o]) for w, o in cs]
             for cs in s]
        bw, bo, _, _, _ = _levels(s)
        boots.append((bw - bo) if kind == "points" else (100 * (bw / bo - 1) if bo else 0.0))
    boots.sort()
    lo, hi = boots[int(0.025 * B)], boots[min(B - 1, int(0.975 * B))]
    md = _mean(D)
    sd = (fsum((x - md) ** 2 for x in D) / (n - 1)) ** 0.5
    half = T975.get(n - 1, 1.96) * sd / sqrt(n)
    t_lo, t_hi = md - half, md + half
    if kind == "pct":
        t_lo, t_hi = 100 * t_lo / O, 100 * t_hi / O
    k, m = sum(1 for x in D if x > 0), sum(1 for x in D if x < 0)
    out = {"metric": metric, "judge": judge if metric == "quality" else None, "tasks": type_ or "all", "kind": kind,
           "n_folders": n, "without": O, "with": W, "effect": eff, "boot_lo": lo, "boot_hi": hi,
           "t_lo": t_lo, "t_hi": t_hi, "higher": k, "lower": m, "sign_p": binom_two_sided(k, k + m),
           "excludes_0": lo > 0 or hi < 0, "folder_diffs": {lb: d for lb, d in zip(labels, D)}}
    if kind == "pct" and all(a > 0 and b > 0 for a, b in zip(wf, of)):
        lr = [log(a / b) for a, b in zip(wf, of)]
        ml = _mean(lr)
        sl = (fsum((x - ml) ** 2 for x in lr) / (n - 1)) ** 0.5
        hl = T975.get(n - 1, 1.96) * sl / sqrt(n)
        out.update({"geo": 100 * (exp(ml) - 1), "geo_lo": 100 * (exp(ml - hl) - 1), "geo_hi": 100 * (exp(ml + hl) - 1)})
    return out


def worst_cell(runs, judge="sonnet"):
    """(diff, label, task) of the largest quality loss over (folder, task) cells."""
    f = metric_fn("quality", judge)
    best = None
    for lb, cs in folder_data(runs, f).items():
        tasks = sorted({r["task"] for r in runs if r["label"] == lb})
        for (w, o), task in zip(cs, tasks):
            d = _mean(w) - _mean(o)
            if best is None or d < best[0]:
                best = (d, lb, task)
    return best


HEADLINE_SPEC = [("quality", "sonnet", None), ("quality", "sonnet", "resume"), ("quality", "sonnet", "specific"),
                 ("quality", "opus", None), ("quality", "mean", None),
                 ("tokens", None, None), ("tokens", None, "resume"), ("tokens", None, "specific"),
                 ("turns", None, None), ("seconds", None, None), ("cost", None, None)]
METRIC_NAMES = {"quality": "quality (0-9, points)", "tokens": "input tokens incl. cache (%)", "turns": "turns (%)",
                "seconds": "seconds (%)", "cost": "cost USD (%)"}


def headline(runs, with_set, without_set, B=5000, seed=1):
    rs = comparison_runs(runs, with_set, without_set)
    rows = [folder_effect(rs, m, j or "sonnet", t, B=B, seed=seed) for m, j, t in HEADLINE_SPEC]
    w = worst_cell(rs, "sonnet")
    return {"with_set": with_set, "without_set": without_set, "rows": rows,
            "worst_cell": {"diff": w[0], "label": w[1], "task": w[2]} if w else None}


def render_headline(h):
    """Markdown table of a headline (the exact text docs/benchmark.md must carry)."""
    head = ("| metric | judge | tasks | folders | without | with | effect | 95 % CI, folder bootstrap | "
            "95 % CI, paired t | folders higher / lower (sign test p) | CI excludes 0 |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|\n")
    lines = []
    for r in h["rows"]:
        if r.get("n_folders", 0) < 2:
            continue
        if r["kind"] == "points":
            lv, ef = (lambda x: f"{x:.2f}"), (lambda x: f"{x:+.2f}")
        else:
            nd = 4 if r["metric"] == "cost" else (0 if r["metric"] == "tokens" else 1)
            lv, ef = (lambda x, nd=nd: f"{x:,.{nd}f}".replace(",", " ")), (lambda x: f"{x:+.1f} %")
        lines.append(f"| {METRIC_NAMES[r['metric']]} | {r['judge'] or '-'} | {r['tasks']} | {r['n_folders']} | "
                     f"{lv(r['without'])} | {lv(r['with'])} | {ef(r['effect'])} | [{ef(r['boot_lo'])}, {ef(r['boot_hi'])}] | "
                     f"[{ef(r['t_lo'])}, {ef(r['t_hi'])}] | {r['higher']} / {r['lower']} (p = {r['sign_p']:.3f}) | "
                     f"{'yes' if r['excludes_0'] else 'no'} |")
    w = h.get("worst_cell")
    tail = (f"\nLargest quality loss in one (folder, task) cell: {w['diff']:+.1f} points ({w['label']}, {w['task']}).\n"
            if w else "\n")
    return head + "\n".join(lines) + "\n" + tail


def geo_lines(h):
    return "\n".join(f"- {METRIC_NAMES[r['metric']]}, {r['tasks']}: ratio of means {r['effect']:+.1f} %, geometric mean of "
                     f"the folder ratios {r['geo']:+.1f} % [{r['geo_lo']:+.1f} %, {r['geo_hi']:+.1f} %] (t, n - 1 df)"
                     for r in h["rows"] if "geo" in r) + "\n"


def judge_agreement(runs):
    """Pearson r between the Sonnet and Opus judges, over (set, folder, task, arm) cell means and over runs."""
    both = [r for r in runs if quality_of(r, "sonnet") is not None and quality_of(r, "opus") is not None]

    def pearson(xs, ys):
        mx, my = _mean(xs), _mean(ys)
        sxy = fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = fsum((x - mx) ** 2 for x in xs) ** 0.5
        sy = fsum((y - my) ** 2 for y in ys) ** 0.5
        return sxy / (sx * sy) if sx and sy else None
    keys = sorted({(r["set"], r["label"], r["task"], r["arm"]) for r in both})
    cs = [[r for r in both if (r["set"], r["label"], r["task"], r["arm"]) == k] for k in keys]
    a = [_mean([quality_of(r, "sonnet") for r in c]) for c in cs]
    b = [_mean([quality_of(r, "opus") for r in c]) for c in cs]
    return {"cells": len(keys), "r_cells": pearson(a, b), "runs": len(both),
            "r_runs": pearson([quality_of(r, "sonnet") for r in both], [quality_of(r, "opus") for r in both]),
            "mean_sonnet": _mean([quality_of(r, "sonnet") for r in both]),
            "mean_opus": _mean([quality_of(r, "opus") for r in both])}


# README "Measured" prose -> numbers. Each entry: (regex, metric, tasks, field, rounding).
README_NUMBERS = [
    (r"fewer input tokens[^(]*\(([-+]?\d+) %", "tokens", "all", "effect", 0),
    (r"fewer input tokens[^(]*\([-+]?\d+ %, 95 % CI ([-+]?\d+) to", "tokens", "all", "boot_lo", 0),
    (r"fewer input tokens[^(]*\([-+]?\d+ %, 95 % CI [-+]?\d+ to ([-+]?\d+)(?=[;)])", "tokens", "all", "boot_hi", 0),
    (r"turns \(([-+]?\d+) %\)", "turns", "all", "effect", 0),
    (r"time was shorter \(([-+]?\d+) %\)", "seconds", "all", "effect", 0),
    (r"quality: no measurable change \(([-+]?\d+\.\d) on 0-9", "quality", "all", "effect", 1),
    (r"on 0-9, CI ([-+]?\d+\.\d) to", "quality", "all", "boot_lo", 1),
    (r"on 0-9, CI [-+]?\d+\.\d to ([-+]?\d+\.\d)\b", "quality", "all", "boot_hi", 1),
]


def same_numbers(a, b, rel=1e-9):
    """Equal structures, floats compared with a relative tolerance (libm may differ in the last bit by platform)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_numbers(a[k], b[k], rel) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_numbers(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= rel * max(1.0, abs(a), abs(b))
    return a == b


def check_measured(readme, doc, results, h=None):
    """Problems (empty list = consistent) between the README "Measured" section, the Headline section of
    docs/benchmark.md and docs/benchmark-results.json (whose headline is recomputed from its runs)."""
    probs = []
    sec = re.search(r"^## Measured\n(.*?)(?=^## |\Z)", readme, re.S | re.M)
    head = re.search(r"^## Headline\n(.*?)(?=^## |\Z)", doc, re.S | re.M)
    if not sec or not head:
        return ["missing README Measured or docs Headline section"]
    sec, head = " ".join(sec.group(1).split()), head.group(1)
    p = results["primary"]
    if h is None:  # recomputed from the per-run numbers (pass a precomputed one to check many texts quickly)
        h = headline(results["runs"], p["with_set"], p["without_set"], B=results["bootstrap_draws"], seed=results["seed"])
    if not same_numbers(json.loads(json.dumps(h)), results["headline"]):
        probs.append("docs/benchmark-results.json headline differs from the one recomputed from its runs")
    table = render_headline(h)
    if table.strip() not in head:
        probs.append("docs Headline table is not the table rendered from docs/benchmark-results.json")
    by = {(r["metric"], r["tasks"]): r for r in h["rows"] if "metric" in r and r.get("judge") in (None, "sonnet")}
    for rx, metric, tasks, field, nd in README_NUMBERS:
        m = re.search(rx, sec)
        if not m:
            probs.append(f"README: no number for {metric}/{tasks}/{field}")
            continue
        want = round(by[(metric, tasks)][field], nd)
        got = float(m.group(1))
        if abs(got - want) > 1e-9:
            probs.append(f"README {metric}/{tasks}/{field} = {m.group(1)}, results say {want}")
    q, tk = by[("quality", "all")], by[("tokens", "all")]
    if re.search(r"\bcost", sec, re.I):
        probs.append("README Measured mentions cost")
    if ("no measurable change" in sec) == q["excludes_0"]:
        probs.append("README quality significance wording contradicts the CI")
    if ("used fewer input tokens" in sec) != (tk["excludes_0"] and tk["effect"] < 0):
        probs.append("README token wording contradicts the CI")
    rs_ = by[("tokens", "resume")]
    if ("mostly when resuming work" in sec) != (rs_["excludes_0"] and rs_["effect"] < tk["effect"]):
        probs.append("README resume wording contradicts the CI")
    clean = {(r["metric"], r["tasks"]): r for r in results.get("clean_headline", {}).get("rows", [])
             if "metric" in r and r.get("judge") in (None, "sonnet")}
    if ("but not robust" in sec) != bool(clean and by[("seconds", "all")]["excludes_0"]
                                         and not clean[("seconds", "all")]["excludes_0"]):
        probs.append("README time-robustness wording contradicts the sensitivity analysis")
    m = re.search(r"; ([-+]?\d+) % without those (\d+)\)", sec)
    runs_p = [r for r in results["runs"] if r["set"] == p["without_set"] and r["arm"] == "without" and not r["error"]]
    seen = sum(1 for r in runs_p if r.get("hidden_file_seen"))
    if not m or not clean or float(m.group(1)) != round(clean[("tokens", "all")]["effect"]) or int(m.group(2)) != seen:
        probs.append("README sensitivity figure (without the runs that saw the file) differs from results")
    m = re.search(r"\((\d+) of (\d+) sessions still found it\)", sec)
    if not m or (int(m.group(1)), int(m.group(2))) != (seen, len(runs_p)):
        probs.append("README count of sessions that found the hidden file differs from results")
    m = re.search(r"on (\d+) of the author's folders", sec)
    if not m or int(m.group(1)) != q["n_folders"]:
        probs.append("README folder count differs from results")
    opus = next((r for r in h["rows"] if r.get("metric") == "quality" and r.get("judge") == "opus" and r["tasks"] == "all"), None)
    agree = (results.get("judge_agreement") or {}).get("r_cells")
    if ("Opus judge agrees" in sec) != bool(opus and not opus["excludes_0"] and agree is not None and agree >= 0.8):
        probs.append("README Opus sentence contradicts the Opus row or the judge agreement")
    sq = {(r["metric"], r["tasks"]): r for r in results["secondary_headline"]["rows"]
          if "metric" in r and r.get("judge") in (None, "sonnet")}
    if ("in a first run a one-point loss was not ruled out" in sec) != (sq[("quality", "all")]["t_lo"] <= -1.0
                                                                        or sq[("quality", "all")]["boot_lo"] <= -1.0):
        probs.append("README first-run wording contradicts the secondary CIs")
    if "one-point loss is not ruled out" in sec and not (q["t_lo"] <= -1.0 or q["boot_lo"] <= -1.0):
        probs.append("README claims a one-point loss is not ruled out, the primary CIs rule it out")
    m = re.search(r"\(one folder, ([-+]?\d+\.\d); ([-+]?\d+\.\d) in the first run\)", sec)
    w, w1 = h["worst_cell"], results["secondary_headline"]["worst_cell"]
    if not m or float(m.group(1)) != round(w["diff"], 1) or float(m.group(2)) != round(w1["diff"], 1):
        probs.append("README worst-cell numbers differ from results")
    m = re.search(r"loss of more than about (\d\.\d) point", " ".join(doc.split()))
    if not m or float(m.group(1)) != round(-min(q["t_lo"], q["boot_lo"]), 1):
        probs.append("docs 'about X point' limit differs from the quality CIs")
    return probs


def notebook_summary(path):
    """Cells, code cells executed / total, cells containing `# Todo` (0-based), markdown headings."""
    try:
        cs = json.loads(Path(path).read_text(encoding="utf-8")).get("cells", [])
    except (OSError, ValueError):
        return "(unreadable notebook)"
    code = [c for c in cs if c.get("cell_type") == "code"]
    exe = sum(1 for c in code if c.get("execution_count"))
    todo = [i for i, c in enumerate(cs) if re.search(r"#\s*todo", "".join(c.get("source", [])), re.I)]
    heads = [f"{i}: {''.join(c.get('source', [])).splitlines()[0][:80]}" for i, c in enumerate(cs)
             if c.get("cell_type") == "markdown" and "".join(c.get("source", [])).startswith("#")]
    return (f"{len(cs)} cells, {len(code)} code cells, {exe} executed, {len(code) - exe} never executed; "
            f"cells containing '# Todo' (0-based): {todo or 'none'}\nheadings: " + "; ".join(heads[:40]))


def pointer_truth(folder, pointers, trunc=6000):
    """Text of the task's pointer files: directory -> dated listing (depth 1), notebook -> summary, text -> content."""
    root = Path(folder)
    parts = []
    for rel in pointers:
        p = root / rel
        if ab.SECRET_RE.search(p.name):
            continue
        if p.is_dir():
            items = []
            for c in sorted(p.iterdir()):
                if c.name == "CONTEXT.md" or ab.SECRET_RE.search(c.name) or c.name in ab.SKIP_DIRS:
                    continue
                try:
                    st = c.lstat()
                    items.append(f"{time.strftime('%Y-%m-%d', time.localtime(st.st_mtime))}  {c.name}"
                                 f"{'/' if c.is_dir() else ''}  {st.st_size}")
                except OSError:
                    pass
            parts.append(f"## {rel} (listing: date, name, bytes)\n" + "\n".join(items[:120]))
        elif p.suffix == ".ipynb":
            parts.append(f"## {rel} (notebook summary)\n{notebook_summary(p)}")
        elif p.is_file():
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parts.append(f"## {rel}\n{t[:trunc]}" + ("\n[...truncated]" if len(t) > trunc else ""))
    return "\n\n".join(parts)


def git_truth(folder):
    """Read-only git facts (--no-optional-locks: status does not refresh the index)."""
    if not (Path(folder) / ".git").exists():
        return "## git\nnot a git repository"
    g = ["git", "--no-optional-locks", "-C", str(folder)]
    try:
        log = subprocess.run(g + ["log", "-15", "--date=short", "--format=%h %ad %s"], capture_output=True,
                             text=True, timeout=30).stdout
        st = subprocess.run(g + ["status", "--short"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    st = "\n".join(ln for ln in st.splitlines() if not ab.SECRET_RE.search(ln) and "CONTEXT.md" not in ln)
    return f"## git log (last 15)\n{log}\n## git status --short\n{st[:3000]}"


def transcript_facts(lines):
    """From a Claude Code transcript (JSONL lines): tool calls per tool, whether the agent Read a CONTEXT.md file
    itself, and whether a CONTEXT.md name came back in a tool result (Glob/Grep listing)."""
    by_tool, read_ctx, seen_ctx = {}, False, False
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                by_tool[c.get("name")] = by_tool.get(c.get("name"), 0) + 1
                inp = c.get("input") or {}
                if c.get("name") == "Read" and str(inp.get("file_path", "")).endswith("CONTEXT.md"):
                    read_ctx = True
            elif c.get("type") == "tool_result" and "CONTEXT.md" in json.dumps(c.get("content"), ensure_ascii=False):
                seen_ctx = True
    return {"tool_calls": sum(by_tool.values()), "by_tool": by_tool, "read_context_md": read_ctx,
            "context_md_in_tool_results": seen_ctx}


def transcript_path(folder, session_id, root=None):
    root = Path(root) if root else Path.home() / ".claude" / "projects"
    return root / re.sub(r"[^A-Za-z0-9]", "-", str(Path(folder).resolve())) / f"{session_id}.jsonl"


# ------------------------------------------------------------------ impure parts
HIDDEN = ".CONTEXT.md.hidden"


def context_files(folder):
    """CONTEXT.md files under folder (its own and nested ones), heavy dirs skipped."""
    out = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if d not in ab.SKIP_DIRS and d != ".git"]
        if "CONTEXT.md" in fns:
            out.append(Path(dp) / "CONTEXT.md")
    return out


def hide_contexts(folder):
    """Rename every CONTEXT.md under folder to .CONTEXT.md.hidden (atomic rename); returns the hidden paths."""
    done = []
    try:
        for f in context_files(folder):
            os.replace(f, f.with_name(HIDDEN))
            done.append(f)
    except BaseException:
        restore_contexts(done)
        raise
    return done


def restore_contexts(paths):
    """Undo hide_contexts: rename back, never over an existing CONTEXT.md."""
    for f in paths:
        h = f.with_name(HIDDEN)
        if h.exists() and not f.exists():
            os.replace(h, f)


def recover_hidden(folder):
    """Restore any .CONTEXT.md.hidden left by an interrupted run; returns the restored CONTEXT.md paths."""
    out = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if d not in ab.SKIP_DIRS and d != ".git"]
        if HIDDEN in fns:
            f = Path(dp) / "CONTEXT.md"
            restore_contexts([f])
            out.append(f)
    return out


class Bench:
    def __init__(self, a):
        self.a = a
        self.out = Path(a.out).resolve()
        self.priv = self.out / "private"
        self.priv.mkdir(parents=True, exist_ok=True)
        m = json.loads(Path(a.map).read_text(encoding="utf-8"))
        self.words = list(dict.fromkeys(m.get("redact", []) + license_names()))
        self.folders = {k: Path(os.path.expanduser(v["path"])).resolve() for k, v in m["folders"].items()}
        self.refs = {k: v.get("reference", {}) for k, v in m["folders"].items()}
        tasks = load_tasks(a.tasks, m)
        for t in tasks:
            if not t["prompt"]:
                raise SystemExit(f"{t['label']}/{t['task']}: no prompt in {a.map}")
        if a.labels:
            keep = set(a.labels.split(","))
            tasks = [t for t in tasks if t["label"] in keep]
        if a.tasks_filter:
            keep = set(a.tasks_filter.split(","))
            tasks = [t for t in tasks if t["task"] in keep]
        self.tasks = {(t["label"], t["task"]): t for t in tasks}
        self.labels = sorted({t["label"] for t in tasks})
        for lb in self.labels:
            if lb not in self.folders:
                raise SystemExit(f"no folder for label {lb} in {a.map}")
        self.runs_dir = self.priv / "runs" / a.tag
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.journal = self.priv / f"journal.{a.tag}.log"

    def log(self, msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def red(self, text, label):
        return redact(text, self.words, self.folders.get(label))

    def run_path(self, label, task, arm, i):
        return self.runs_dir / label / task / f"{arm}.{i}.json"

    # ---- manifests
    def sha_manifests(self, when):
        res = {}
        for lb in self.labels:
            p = self.priv / f"manifest.{self.a.tag}.{lb}.{when}.json"
            if when == "before" and p.exists():
                res[lb] = json.loads(p.read_text())
                continue
            m = ab.manifest(self.folders[lb])
            p.write_text(json.dumps(m, indent=0, sort_keys=True))
            res[lb] = m
        return res

    # ---- one claude session with one retry
    def session(self, prompt, folder, env, model, tools=ab.RO_TOOLS):
        last = None
        for attempt in (1, 2):
            try:
                j, secs, raw = ab.claude(prompt, folder, model, env, tools, None, timeout=self.a.timeout)
                if j.get("is_error") or not (j.get("result") or "").strip():
                    last = f"is_error/empty result (subtype {j.get('subtype')})"
                    continue
                return j, secs, raw, attempt
            except subprocess.TimeoutExpired:
                last = f"timeout {self.a.timeout}s"
            except SystemExit as e:
                last = str(e)
            self.log(f"  attempt {attempt} failed: {last}")
        return None, None, last, 2

    # ---- bootstrap a fresh node
    def bootstrap(self, label):
        sys.path.insert(0, str(ab.CTX_DIR / "src"))
        import ctx as c
        folder = self.folders[label]
        f = folder / "CONTEXT.md"
        note = self.priv / f"bootstrap.{label}.json"
        if note.exists():
            return json.loads(note.read_text())
        doc = c.parse(f.read_text(encoding="utf-8")) if f.exists() else None
        info = {"label": label, "needed": bool(doc is None or doc.summary in c.PLACEHOLDERS), "attempts": []}
        if info["needed"]:
            for _ in range(2):
                off = ab.log_size()
                j, secs, raw, _a = self.session(BOOT_PROMPT, folder, ab.base_env(False), self.a.model)
                line = ab.wait_sync(str(folder), off, self.a.sync_timeout)
                doc = c.parse(f.read_text(encoding="utf-8"))
                ok = doc.summary not in c.PLACEHOLDERS
                info["attempts"].append({"secs": secs, "sync_line": self.red(line or "NONE", label), "filled": ok,
                                         **(usage_row(j) if j else {})})
                self.log(f"bootstrap {label}: {secs}s filled={ok} line={self.red(line or 'NONE', label)}")
                if ok:
                    break
        info["context_bytes"] = len(f.read_bytes()) if f.exists() else 0
        note.write_text(json.dumps(info, indent=1))
        return info

    # ---- phase sessions
    def phase_sessions(self):
        old = ab._raise_on_signals()  # SIGTERM/SIGINT -> SystemExit: a hidden CONTEXT.md is restored by `finally`
        try:
            return self._phase_sessions()
        finally:
            for sig, h in old.items():
                ab.signal.signal(sig, h)

    def _phase_sessions(self):
        for lb in self.labels:
            if ab.is_declined(self.folders[lb]):
                raise SystemExit(f"label {lb} is declined (~/.ctx/declined): refusing")
        self.log(f"sha256 manifests before ({len(self.labels)} folders)")
        self.sha_manifests("before")
        for lb in self.labels:
            for f in recover_hidden(self.folders[lb]):
                self.log(f"recovered a hidden CONTEXT.md left by an interrupted run: {self.red(str(f), lb)}")
        base = {lb: stat_manifest(self.folders[lb]) for lb in self.labels}
        aborted = {}
        for lb in self.labels:
            self.bootstrap(lb)
            d = self.stat_diff(base[lb], stat_manifest(self.folders[lb]))
            if d:
                aborted[lb] = d
                self.log(f"ABORT {lb}: non-CONTEXT change after bootstrap: {d[:5]}")
        items = schedule(list(self.tasks.values()), self.a.runs, self.a.seed, self.a.arms.split(","))
        fails, n_done = 0, 0
        for k, (lb, task, arm, i) in enumerate(items):
            p = self.run_path(lb, task, arm, i)
            if p.exists() or lb in aborted:
                continue
            t = self.tasks[(lb, task)]
            folder = self.folders[lb]
            cf = folder / "CONTEXT.md"
            ctx_sha = hashlib.sha256(cf.read_bytes()).hexdigest()[:12] if cf.exists() else None
            off = ab.log_size()
            t0 = time.strftime("%Y-%m-%dT%H:%M:%S")
            hide = self.a.hide_context and arm == "without"
            hidden = hide_contexts(folder) if hide else []
            try:
                j, secs, raw, attempts = self.session(t["prompt"], folder, ab.base_env(arm == "without"), self.a.model)
            finally:
                restore_contexts(hidden)
            line = ab.wait_sync(str(folder), off, 60) if (j and arm == "with") else None
            rec = {"label": lb, "task": task, "type": t["type"], "arm": arm, "run": i, "order": k, "started": t0,
                   "model": self.a.model, "tag": self.a.tag, "attempts": attempts, "context_sha": ctx_sha,
                   "context_hidden": bool(hidden)}
            if j is None:
                fails += 1
                rec.update({"error": True, "why": self.red(raw, lb)})
                self.log(f"[{k + 1}/{len(items)}] {lb}/{task}/{arm}.{i} FAILED twice: {raw}")
            else:
                fails = 0
                tp = transcript_path(folder, j.get("session_id", ""))
                try:
                    tf = transcript_facts(tp.read_text(encoding="utf-8", errors="replace").splitlines())
                except OSError:
                    tf = {"tool_calls": None}
                rec.update({"error": False, "secs": secs, **usage_row(j), "session_id": j.get("session_id"),
                            "sync_line": self.red(line, lb) if line else None, **tf,
                            "answer": self.red(j.get("result", ""), lb)})
                self.log(f"[{k + 1}/{len(items)}] {lb}/{task}/{arm}.{i} {secs}s turns={rec['turns']} "
                         f"tok={tokens(rec)} cost={rec['cost_usd']}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
            (p.with_suffix(".raw.json")).write_text(self.red(raw if isinstance(raw, str) else "", lb))
            n_done += 1
            d = self.stat_diff(base[lb], stat_manifest(folder))
            if d:
                aborted[lb] = d
                self.log(f"ABORT {lb}: non-CONTEXT change: {d[:5]}")
            if fails >= 3:
                self.log("STOP: 3 failed sessions in a row")
                break
        (self.priv / f"aborted.{self.a.tag}.json").write_text(json.dumps(aborted, indent=1))
        return aborted

    @staticmethod
    def stat_diff(a, b):
        return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))

    # ---- phase judge
    def truth(self, label, task):
        folder = self.folders[label]
        t = self.tasks[(label, task)]
        return "\n\n".join([ab.enlarged_truth(folder), "## Files that answer this task\n"
                            + pointer_truth(folder, t["pointers"]), git_truth(folder)])

    def all_results(self, tags=None):
        out = []
        for tag in (tags or [self.a.tag]):
            for p in sorted((self.priv / "runs" / tag).glob("*/*/*.json")):
                if p.name.endswith((".raw.json", ".judge.json")):
                    continue
                out.append((p, json.loads(p.read_text())))
        return out

    def phase_judge(self):
        key_ = self.a.judge_key
        sets = [x.split(":") for x in self.a.judge_sets.split(",")] if self.a.judge_sets else [[self.a.tag, "all"]]
        todo = [(p, r) for tag, arm in sets for p, r in self.all_results([tag])
                if not r.get("error") and r.get(key_) is None and (r["label"], r["task"]) in self.tasks
                and arm in ("all", r["arm"])]
        spent = {"usd": 0.0, "stopped": False}
        import threading
        lock = threading.Lock()
        truths = {}
        for _, r in todo:
            key = (r["label"], r["task"])
            if key not in truths:
                truths[key] = self.truth(*key)
        order = ab.shuffle_items([r | {"_p": str(p)} for p, r in todo], self.a.seed + 1)
        self.log(f"judging {len(order)} answers with {self.a.judge_model}, {self.a.judge_workers} in parallel")
        tmp = tempfile.mkdtemp(prefix="ctx-bench-judge-")
        env = ab.base_env(True)

        def one(bid_r):
            bid, r = bid_r
            with lock:
                if self.a.cost_cap and spent["usd"] >= self.a.cost_cap:
                    if not spent["stopped"]:
                        self.log(f"COST CAP reached ({spent['usd']:.2f} USD >= {self.a.cost_cap}): judging stopped")
                    spent["stopped"] = True
                    return None
            key = (r["label"], r["task"])
            t = self.tasks[key]
            prompt = ab.build_judge_prompt(truths[key], t["prompt"], r["answer"], t["type"],
                                           self.refs.get(r["label"], {}).get(r["task"]))

            def call(pr):
                for _ in range(2):
                    try:
                        jj, _, raw = ab.claude(pr, tmp, self.a.judge_model, env, "", None, timeout=300)
                        return jj.get("result", ""), raw, jj
                    except (subprocess.TimeoutExpired, SystemExit) as e:
                        err = str(e)
                return "", err, {}
            calls = []

            def call2(pr):
                text, raw, jj = call(pr)
                calls.append(usage_row(jj).get("cost_usd") or 0)
                return text, raw
            scores, raw = ab.judge_with_retry(call2, prompt)
            p = Path(r["_p"])
            suffix = "" if key_ == "scores" else "." + key_.split("_", 1)[1]
            p.with_suffix(f"{suffix}.judge.json").write_text(self.red(raw, r["label"]))
            with lock:
                spent["usd"] += sum(calls)
                rec = json.loads(p.read_text())
                if scores:
                    scores["comment"] = self.red(scores.get("comment", ""), r["label"])
                rec[key_] = scores
                rec[f"{key_}_blind_id"] = bid
                rec[f"{key_}_cost_usd"] = sum(calls)
                rec[f"{key_}_calls"] = len(calls)
                if key_ == "scores":
                    rec["judge_cost_usd"], rec["judge_calls"] = sum(calls), len(calls)
                p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
            self.log(f"judged {key_} {bid} {r['label']}/{r['task']} q={ab.quality({'scores': scores})} "
                     f"spent={spent['usd']:.2f}")
            return scores
        with ThreadPoolExecutor(self.a.judge_workers) as ex:
            list(ex.map(one, order))

    # ---- phase report: published aggregates only (numbers keyed by generic labels, no text)
    def phase_report(self):
        a = self.a
        pw, po = a.primary.split(":")
        sw, so = a.secondary.split(":")
        sets = list(dict.fromkeys([pw, po, sw, so] + [x for x in (a.extra_sets or "").split(",") if x]))
        runs = [numeric_run(r, tag) for tag in sets for _, r in self.all_results([tag])]
        prim = headline(runs, pw, po, B=a.bootstrap, seed=a.seed)
        sec = headline(runs, sw, so, B=a.bootstrap, seed=a.seed)
        clean = [r for r in runs if not ((r["set"] == po and r["arm"] == "without" and r.get("hidden_file_seen"))
                                         or (r["set"] == so and r["arm"] == "without" and r.get("read_context_md")))]
        sens = headline(clean, pw, po, B=a.bootstrap, seed=a.seed)
        n_seen = sum(1 for r in runs if r["set"] == po and r["arm"] == "without" and r.get("hidden_file_seen"))
        agree = judge_agreement([r for r in runs if (r["set"], r["arm"]) in ((pw, "with"), (po, "without"))])
        cells16 = {}
        for name, (w, o) in (("primary", (pw, po)), ("secondary", (sw, so))):
            rs = comparison_runs(runs, w, o)
            legacy = [dict(r, scores={"exactness": 0, "actionability": 0, "no_invention": quality_of(r)})
                      if quality_of(r) is not None else dict(r, scores=None) for r in rs]
            types = {(r["label"], r["task"]): r["type"] for r in rs}
            cells16[name] = [{"tasks": sc, "metric": m, **paired(legacy, m, None if sc == "all" else sc,
                                                                  B=a.bootstrap, seed=a.seed, types=types)}
                             for sc in ("all", "resume", "specific") for m in ("quality", "tokens", "turns", "seconds")]
        res = {"date": time.strftime("%Y-%m-%d"), "seed": a.seed, "bootstrap_draws": a.bootstrap,
               "primary": {"with_set": pw, "without_set": po}, "secondary": {"with_set": sw, "without_set": so},
               "headline": prim, "secondary_headline": sec, "clean_headline": sens, "judge_agreement": agree, "cells16": cells16,
               "runs": runs}
        Path(a.results_file).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
        f1 = lambda x: "n/a" if x is None else f"{x:.1f}"  # noqa: E731
        pt = ["| folder | task | quality without (S / O) | quality with (S / O) | tokens without | tokens with | "
              "secondary: quality without (S) |", "|---|---|---|---|---|---|---|"]
        for lb in sorted({r["label"] for r in runs}):
            for task in ("resume", "specific"):
                cell = {}
                for nm, (w, o) in (("p", (pw, po)), ("s", (sw, so))):
                    rs = [r for r in comparison_runs(runs, w, o) if r["label"] == lb and r["task"] == task]
                    for arm in ARMS:
                        x = [r for r in rs if r["arm"] == arm]
                        cell[(nm, arm)] = (_mean([quality_of(r) for r in x]), _mean([quality_of(r, "opus") for r in x]),
                                           _mean([tokens(r) for r in x]) or 0)
                pw_, pwo, sw_ = cell[("p", "with")], cell[("p", "without")], cell[("s", "without")]
                pt.append(f"| {lb} | {task} | {f1(pwo[0])} / {f1(pwo[1])} | {f1(pw_[0])} / {f1(pw_[1])} | "
                          f"{round(pwo[2] / 1000)}k | {round(pw_[2] / 1000)}k | {f1(sw_[0])} |")
        if agree.get("r_cells") is not None:
            ag = (f"Pearson r over {agree['cells']} (folder, task, arm) cell means: {agree['r_cells']:.2f}; over "
                  f"{agree['runs']} runs: {agree['r_runs']:.2f}. Mean quality: Sonnet {agree['mean_sonnet']:.2f}, "
                  f"Opus {agree['mean_opus']:.2f}.\n")
        else:
            ag = "n/a\n"
        c16 = [f"- {name}: " + "; ".join(f"{x['tasks']} {x['metric']} {x['diff']:+.2f} [{x['lo']:+.2f}, {x['hi']:+.2f}]"
                                         for x in rows if x.get("n_cells")) for name, rows in cells16.items()]
        md = ["# ctx benchmark: aggregates\n",
              f"Generated by `run_benchmark.py --phase report` on {res['date']}. Numbers only; answers, judge "
              "comments and folder paths stay local. The per-run numbers are published in "
              "`docs/benchmark-results.json`.\n",
              f"## Primary: with ({pw}) vs without ({po}), folder is the unit\n", render_headline(prim),
              "Percentages are ratios of means; geometric mean of the folder ratios as a sensitivity:\n",
              geo_lines(prim),
              f"## Primary, without the {n_seen} without-arm runs whose agent saw the hidden file's content\n",
              render_headline(sens),
              f"## Secondary: with ({sw}) vs without ({so})\n", render_headline(sec), geo_lines(sec),
              "## Judge agreement (Sonnet vs Opus), primary comparison\n", ag,
              "## Per folder and task (primary; S = Sonnet judge, O = Opus judge)\n", "\n".join(pt) + "\n",
              "## 16-cell analysis (cells as the unit, secondary)\n", "\n".join(c16) + "\n"]
        (self.out / "SUMMARY.md").write_text("\n".join(md))
        print("\n".join(md))

    # ---- phase stats
    def phase_stats(self):
        tags = self.a.stats_tags.split(",") if self.a.stats_tags else [self.a.tag]
        res = [r for _, r in self.all_results(tags)]
        types = {k: t["type"] for k, t in self.tasks.items()}
        after = self.sha_manifests("after")
        before = {lb: json.loads((self.priv / f"manifest.{self.a.tag}.{lb}.before.json").read_text()) for lb in self.labels
                  if (self.priv / f"manifest.{self.a.tag}.{lb}.before.json").exists()}
        mcheck = {}
        for lb in self.labels:
            if lb in before:
                d = ab.diff_manifest(before[lb], after[lb])
                mcheck[lb] = {"only_context_changed": ab.only_context_changed(d),
                              "changed": [x for x in d["changed"] + d["added"] + d["removed"]
                                          if Path(x).name == "CONTEXT.md"],
                              "other": [x for x in d["changed"] + d["added"] + d["removed"]
                                        if Path(x).name != "CONTEXT.md"][:20]}
        main = [r for r in res if r.get("tag") == self.a.tag]
        rows = headline_rows(main, types)
        cache = {arm: ab.cache_rate([r for r in main if not r.get("error")], arm) for arm in ARMS}
        errors = [r for r in res if r.get("error")]
        scored = [r for r in main if r.get("scores")]
        cost_s = sum(r.get("cost_usd") or 0 for r in res)
        cost_j = sum(r.get("judge_cost_usd") or 0 for r in res)
        boots = [json.loads(p.read_text()) for p in sorted(self.priv.glob("bootstrap.*.json"))]
        cost_b = sum(a.get("cost_usd") or 0 for b in boots for a in b.get("attempts", []))
        ctx_versions = {}
        for r in main:
            if r["arm"] == "with" and r.get("context_sha"):
                ctx_versions.setdefault(r["label"], set()).add(r["context_sha"])
        summary = {
            "date": time.strftime("%Y-%m-%d"), "model": self.a.model, "judge_model": self.a.judge_model,
            "runs_per_arm": self.a.runs, "sessions": len(main), "errors": len(errors), "scored": len(scored),
            "headline": [{"tasks": s, "metric": m, **p} for s, m, p in rows],
            "cache_rate_pct": cache, "cost_usd": {"sessions": cost_s, "judge": cost_j, "bootstrap": cost_b,
                                                  "total": cost_s + cost_j + cost_b},
            "manifest_check": {lb: {"only_context_changed": v["only_context_changed"], "other": len(v["other"])}
                               for lb, v in mcheck.items()},
            "context_versions_seen_by_with_arm": {k: len(v) for k, v in ctx_versions.items()},
            "bootstrap": [{"label": b["label"], "needed": b["needed"],
                           "filled": any(a.get("filled") for a in b["attempts"]) if b["needed"] else True,
                           "attempts": len(b["attempts"])} for b in boots],
        }
        (self.priv / f"results.{self.a.tag}.json").write_text(json.dumps(
            {"summary": summary, "runs": [{k: v for k, v in r.items() if k != "answer"} for r in res]},
            indent=1, ensure_ascii=False))
        if any(not v["only_context_changed"] for v in mcheck.values()):
            (self.priv / "manifest-violations.json").write_text(json.dumps(mcheck, indent=1))
        md = [f"# ctx benchmark ({self.a.tag})\n",
              f"Date {summary['date']}. Agent `{self.a.model}` (`claude -p`, Read/Glob/Grep only), judge "
              f"`{self.a.judge_model}` (blind, no tools). {len(self.labels)} folders x {len(self.tasks) // max(1, len(self.labels))} "
              f"tasks x 2 arms x {self.a.runs} runs = {len(main)} sessions, {len(errors)} failed, {len(scored)} scored.\n",
              "## Paired comparison (with ctx - without), bootstrap 95 % CI over cells and runs\n",
              headline_table(rows),
              "quality = exactness + actionability + no_invention (0-9); tokens = cache_read + cache_creation + "
              "uncached input (context read by the agent, summed over turns); change % = (with - without) / without.\n",
              "## Cache rate (cache_read / (cache_read + cache_creation), pooled)\n",
              "\n".join(f"- {arm}: {fmt(v, 1)} %" for arm, v in cache.items()) + "\n",
              "## Per folder and task\n", cell_table(main, types),
              "## Integrity\n",
              "\n".join(f"- {lb}: only CONTEXT.md changed = {v['only_context_changed']}"
                        + (f" (other changes: {len(v['other'])})" if v["other"] else "") for lb, v in mcheck.items()),
              "\n- CONTEXT.md versions seen by the with-ctx arm: "
              + ", ".join(f"{k} {len(v)}" for k, v in sorted(ctx_versions.items())),
              "\n- bootstrap: " + ", ".join(f"{b['label']} needed={b['needed']} filled={b['filled']}"
                                             for b in summary["bootstrap"]),
              f"\n- failed sessions (after one retry): {len(errors)}",
              "\n- the file stays on disk in both arms: sessions whose agent Read a CONTEXT.md itself: "
              + ", ".join(f"{arm} {sum(1 for r in main if r['arm'] == arm and r.get('read_context_md'))}"
                          f"/{sum(1 for r in main if r['arm'] == arm and not r.get('error'))}" for arm in ARMS)
              + "; answers that name CONTEXT.md: "
              + ", ".join(f"{arm} {sum(1 for r in main if r['arm'] == arm and 'CONTEXT.md' in (r.get('answer') or ''))}"
                          for arm in ARMS) + "\n",
              f"## Cost\n\nsessions {fmt(cost_s, 2)} USD, judge {fmt(cost_j, 2)} USD, bootstrap {fmt(cost_b, 2)} USD, "
              f"total {fmt(cost_s + cost_j + cost_b, 2)} USD (`total_cost_usd` of each `claude -p` call; the ctx "
              "rewrites triggered by the with-ctx sessions are not included).\n"]
        (self.priv / f"SUMMARY.{self.a.tag}.md").write_text("\n".join(md))
        print("\n".join(md))
        return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", required=True, help="local, unpublished label -> folder map (see the docstring)")
    ap.add_argument("--tasks", default=str(TASKS_FILE))
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="main", help="name of this run set (runs/<tag>/...)")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--labels", help="comma-separated subset of labels")
    ap.add_argument("--tasks-filter", help="comma-separated task ids (resume, specific)")
    ap.add_argument("--arms", default="with,without")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--judge-workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    ap.add_argument("--sync-timeout", type=int, default=240)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--stats-tags", help="comma-separated tags whose runs are loaded for stats (default: --tag)")
    ap.add_argument("--phase", default="all", choices=("all", "sessions", "judge", "stats", "report"))
    ap.add_argument("--hide-context", action="store_true",
                    help="without arm: rename every CONTEXT.md of the folder to .CONTEXT.md.hidden during the session")
    ap.add_argument("--judge-key", default="scores", help="record key for the scores (scores_opus for a second judge)")
    ap.add_argument("--judge-sets", help="tag:arm list to judge together, e.g. main:with,hidden:without (arm or all)")
    ap.add_argument("--cost-cap", type=float, help="stop judging once this many USD were spent by this call")
    ap.add_argument("--primary", default="main:hidden", help="report: with-set:without-set of the primary comparison")
    ap.add_argument("--secondary", default="main:main", help="report: with-set:without-set of the secondary comparison")
    ap.add_argument("--extra-sets", default="sonnet", help="report: other tags whose numbers are published")
    ap.add_argument("--bootstrap", type=int, default=5000)
    ap.add_argument("--results-file", default=str(HERE.parents[1] / "docs" / "benchmark-results.json"),
                    help="report: where the published aggregate JSON goes (numbers only, generic labels)")
    a = ap.parse_args(argv)
    b = Bench(a)
    if a.phase in ("all", "sessions"):
        b.phase_sessions()
    if a.phase in ("all", "judge"):
        b.phase_judge()
    if a.phase in ("all", "stats"):
        b.phase_stats()
    if a.phase == "report":
        b.phase_report()


if __name__ == "__main__":
    main()
