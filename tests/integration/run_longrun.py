#!/usr/bin/env python3
"""Longitudinal benchmark of ctx: a chain of sessions on one folder, WITH ctx (arm A) and with CTX_OFF=1 (arm B).

Each session is a new `claude -p` process (no --resume): continuity can only come from the files and, in arm A, from
the node's CONTEXT.md (injected at start, rewritten by the real hooks after the session). Two scenarios built from
scratch (tests/integration/longrun/<scenario>/, prompts in scenarios.json); every run starts from a fresh copy of the
pristine folder under a temporary home (CTX_HOME), so ctx's state, nodes and log never touch the real ~/.ctx.

Per session: tokens, turns, seconds, cost; harness checks (tests/integration/longrun/checks.py) before and after;
in arm A the CONTEXT.md after the sync, the rewrite tokens (from the test-home log) and a rule-based accuracy count
of its Status lines against the harness ground truth. Then (step `judge`) a blind Sonnet judge per session scores
continuity 0-3 on three criteria. Step `summary` writes OUT/SUMMARY.md.

Usage (hooks file from `python3 ctx.py install-hooks claude --emit FILE`):
  python3 tests/integration/run_longrun.py run --home /tmp/ctx-long/home --hooks /tmp/ctx-long/hooks.json \
      --out /tmp/ctx-long/out [--scenarios course-notebooks,small-project] [--arms A,B] [--reps 3] [--sessions 10]
  python3 tests/integration/run_longrun.py judge --home /tmp/ctx-long/home --out /tmp/ctx-long/out
  python3 tests/integration/run_longrun.py summary --out /tmp/ctx-long/out
"""
import argparse
import concurrent.futures as cf
import difflib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_ab_real_folders as ab  # noqa: E402  (session launch, log wait, stats helpers)

CTX_DIR = ab.CTX_DIR
FIXT = HERE / "longrun"
TOOLS = "Read,Glob,Grep,Write,Edit"
SKIP = {"__pycache__", ".git"}
JUDGE_KEYS = ("pickup", "decisions", "consistency")
# Haiku list price per million tokens (input, output), used only for the rewrite-cost upper bound
HAIKU_PRICE = (1.0, 5.0)


# ------------------------------------------------------------------ pure parts
def load_spec():
    return json.loads((FIXT / "scenarios.json").read_text(encoding="utf-8"))


def snapshot(root, max_bytes=200_000):
    """{relative path: text} of the folder (no __pycache__/.git, CONTEXT.md included)."""
    root, out = Path(root), {}
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP)
        for fn in sorted(fns):
            p = Path(dp) / fn
            try:
                out[str(p.relative_to(root))] = p.read_bytes()[:max_bytes].decode("utf-8", "replace")
            except OSError:
                pass
    return out


def changed_files(before, after, exclude=("CONTEXT.md",)):
    keys = set(before) | set(after)
    return sorted(k for k in keys if Path(k).name not in exclude and before.get(k) != after.get(k))


def unified(before, after, files, limit=8000):
    parts = []
    for f in files:
        parts += difflib.unified_diff(before.get(f, "").splitlines(), after.get(f, "").splitlines(),
                                      f"a/{f}", f"b/{f}", lineterm="", n=2)
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit] + "\n[...diff truncated]"


# file of each course item (redo detection); small-project items share files, see redo()
COURSE_FILES = {f"ex{c}_{e}": f"ch{c}/ex{c}_{e}.py" for c in range(1, 5) for e in (1, 2)}


def redo(scen, session, before_items, after_items, files):
    """Did a progress session re-work something already done instead of its target?
    course-notebooks: it changed the file of an exercise that already passed before and is not a target.
    small-project: it changed code (tally/ or tests/) but no item went from not-done to done."""
    if session["kind"] != "progress":
        return 0
    if scen == "course-notebooks":
        return int(any(COURSE_FILES.get(i) in files for i, ok in before_items.items()
                       if ok and i not in session["targets"] and i in COURSE_FILES))
    code = [f for f in files if f.startswith(("tally/", "tests/"))]
    newly = [i for i, ok in after_items.items() if ok and not before_items.get(i)]
    return int(bool(code) and not newly)


# context accuracy: claims of CONTEXT.md "Status" lines vs the harness ground truth
ITEM_RE = {
    "course-notebooks": {
        "ex1_1": r"(?<![\d.])1[._]1(?![\d])|ex1_1", "ex1_2": r"(?<![\d.])1[._]2(?![\d])|variance",
        "ex2_1": r"(?<![\d.])2[._]1(?![\d])|normali[sz]", "ex2_2": r"(?<![\d.])2[._]2(?![\d])|moving.average",
        "ex3_1": r"(?<![\d.])3[._]1(?![\d])|\bdot\b", "ex3_2": r"(?<![\d.])3[._]2(?![\d])|matmul|matrix product",
        "ex4_1": r"(?<![\d.])4[._]1(?![\d])|softmax", "ex4_2": r"(?<![\d.])4[._]2(?![\d])|cross.entropy",
        "notes_ch1": r"notes.{0,40}(ch(apter)?\s*1\b)|(ch(apter)?\s*1\b).{0,40}notes",
        "notes_ch2": r"notes.{0,40}(ch(apter)?\s*2\b)|(ch(apter)?\s*2\b).{0,40}notes",
    },
    "small-project": {
        "item1": r"failing test|test_total|\btotal\(", "item2": r"average", "item3": r"json",
        "item4": r"comment|blank", "item5": r"sort", "item6": r"pretty|table",
        "readme": r"readme", "status": r"status\.md",
    },
}
DECISION_RE = {"course-notebooks": r"loop|type hint|annotat", "small-project": r"stdlib|standard.library|third.party|dependenc"}
PENDING_RE = (r"\b(todo|to do|pending|not yet|not done|remaining|remains|stub|stubs|unimplemented|not implemented|"
              r"missing|left|planned|notimplementederror|to be|next|incomplete|not started|open)\b")
# a clause about running/verifying (no shell in these sessions) says nothing about whether an item is implemented
VERIFY_RE = r"\b(run|running|ran|unrun|runtime|execut\w*|verif\w*|check|checks|checked|tested|shell|bash)\b"
ORDER = {"course-notebooks": ["ex1_1", "ex1_2", "notes_ch1", "ex2_1", "ex2_2", "notes_ch2", "ex3_1", "ex3_2", "ex4_1", "ex4_2"],
         "small-project": ["item1", "item2", "item3", "item4", "item5", "item6"]}
# "Next names the next item": the next unfinished exercise / TODO item (notes are on the user's request only)
NEXT_ORDER = {"course-notebooks": [i for i in ORDER["course-notebooks"] if i.startswith("ex")],
              "small-project": ORDER["small-project"]}


def sections(text):
    sys.path.insert(0, str(CTX_DIR / "src"))
    import ctx as c
    d = c.parse(text or "")
    return d.sections.get(c.SEC_STATUS, ""), d.sections.get(c.SEC_NEXT, ""), len((text or "").encode())


def sections_decisions(text):
    import ctx as c
    return c.parse(text or "").sections.get("Decisions", "")


def classify_line(scen, line, items, decided):
    """-> ('true'|'false'|'other', [claims]). A clause (split on ';') claims each item it names: pending if it has a
    pending word, done otherwise. A decision clause is true iff a decision was taken."""
    claims = []
    for clause in re.split(r";", line):
        low = clause.lower()
        pend = bool(re.search(PENDING_RE, low))
        if pend and re.search(VERIFY_RE, low):
            continue
        for it, rx in ITEM_RE[scen].items():
            if re.search(rx, low):
                claims.append((it, "pending" if pend else "done", (not pend) == bool(items.get(it))))
        if re.search(DECISION_RE[scen], low):
            claims.append(("decision", "taken", bool(decided)))
    if not claims:
        return "other", claims
    return ("true" if all(c[2] for c in claims) else "false"), claims


def context_accuracy(scen, text, items, decided):
    status, nxt, size = sections(text)
    lines = [ln.strip().lstrip("-* ").strip() for ln in status.splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln and ln != "(to be filled in)"]
    counts, detail, mentioned = {"true": 0, "false": 0, "other": 0}, [], set()
    for ln in lines:
        k, claims = classify_line(scen, ln, items, decided)
        counts[k] += 1
        mentioned |= {c[0] for c in claims}
        detail.append({"line": ln, "class": k, "claims": claims})
    # the code-owned Decisions section (absent before 1.1.0) also counts as mentioning the decision
    dec = [ln.strip() for ln in sections_decisions(text).splitlines() if ln.strip()]
    if any(re.search(DECISION_RE[scen], ln.lower()) for ln in dec):
        mentioned.add("decision")
    facts = [i for i in ORDER[scen] if items.get(i)] + (["decision"] if decided else [])
    missing = [f for f in facts if f not in mentioned]
    first_undone = next((i for i in NEXT_ORDER[scen] if not items.get(i)), None)
    next_ok = bool(first_undone and re.search(ITEM_RE[scen][first_undone], nxt.lower()))
    return {**counts, "missing": len(missing), "missing_items": missing, "facts": len(facts), "lines": len(lines),
            "size": size, "next_target": first_undone, "next_ok": next_ok, "detail": detail,
            "decision_lines": sum(1 for ln in dec if not ln.endswith("(superseded)")),
            "decision_in_file": "decision" in mentioned}


def progress(scen, session, rec):
    """1 if the session made progress on the next not-done item. A progress session counts when it finished one of
    its explicit targets or the first exercise / TODO item not done before it (a terse "next item" may legitimately
    land on either); other sessions count when all their targets hold after them."""
    before, after = rec["check_before"].get("items", {}), rec["check_after"].get("items", {})
    if session["kind"] == "progress":
        first = next((i for i in NEXT_ORDER[scen] if not before.get(i)), None)
        newly = {i for i, v in after.items() if v and not before.get(i)}
        return int(bool(newly & (set(session["targets"]) | {first})))
    return int(all(rec["check_after"].get("regression_ok") if t == "regression" else after.get(t, False)
                   for t in session["targets"]))


RUN_PATH_RE = r"(?:/[\w.-]+)*/?\b(?:course-notebooks|small-project)-[AB]\d+\b/?"
SYNC_TIME_RE = r"\(?\b(?:the |its )?(?:last |latest )?sync(?:ed)?\b(?: at)?\s*\(?\s*\d{1,2}:\d{2}\s*\)?"


def redact(text):
    """Arm-blind judge input: the context file becomes a neutral "a project file" (never "notes": a scenario has a
    real notes.md), run folder paths (they carry the arm letter) and "last sync (HH:MM)" strings are erased."""
    t = re.sub(r"CONTEXT\.md", "a project file", text or "")
    t = re.sub(r"\bcontext(?: summary)? file\b", "project file", t, flags=re.I)
    t = re.sub(RUN_PATH_RE, "the project folder", t)
    t = re.sub(SYNC_TIME_RE, "", t, flags=re.I)
    return re.sub(r"\bctx\b", "", t)


def items_line(items):
    return ", ".join(f"{k}={'done' if v else 'not done'}" for k, v in items.items())


def build_judge_prompt(scen_spec, history, rec):
    hist = "\n".join(
        f"- session {h['n']}: \"{h['prompt']}\" -> files changed: {', '.join(h['files']) or 'none'}; "
        f"newly done: {', '.join(h['newly_done']) or 'nothing'}" + (f"; HARNESS INJECTED BEFORE IT: {h['injected']}" if h.get('injected') else "")
        for h in history) or "(this is the first session)"
    decisions = "\n".join(f"- {d}" for d in rec["decisions_before"]) or "(no decision taken before this session)"
    return (
        "You grade ONE session of a coding assistant inside a chain of sessions on the same folder. Every session "
        "is a brand-new process without memory: continuity had to be recovered from the folder. Judge only what is "
        "below; nothing tells you how the assistant got its information.\n\n"
        f"### Scenario\n{scen_spec['description']}\n\n### Previous sessions (harness log, ground truth)\n{hist}\n\n"
        f"### Decisions taken by the user before this session\n{decisions}\n\n"
        f"### Harness state BEFORE this session\n{items_line(rec['check_before']['items'])}\n"
        + (f"Note: between the previous session and this one the harness silently changed a file: {rec['injected']}\n" if rec.get("injected") else "")
        + f"\n### This session's request\n\"{rec['prompt']}\"\nWhat a perfect session does: {rec['expect']}\n\n"
        f"### Files changed by the session (diff)\n{rec['diff'] or '(no file changed)'}\n\n"
        f"### Harness state AFTER this session\n{items_line(rec['check_after']['items'])}\n"
        + (f"Injected bug fixed: {rec['check_after'].get('regression_ok')}\n" if rec.get("after_injection") else "")
        + (f"Compliance with the decision (per file, true = complies): {json.dumps(rec['check_after'].get('convention'))}\n"
           if rec["decisions_before"] or rec.get("decision_here") else "")
        + "\n"
        f"### Assistant's final reply\n{redact(rec['reply'])[:5000]}\n\n"
        "Score 0-3 each:\n"
        "- pickup: continues where the previous sessions left off and targets the right next thing; does not redo or "
        "re-implement work already done (0 = redoes done work or wrong target, 3 = exact continuation)\n"
        "- decisions: respects the earlier decisions that apply to this session (3 when none applies and nothing "
        "violates one; 0 = clear violation)\n"
        "- consistency: the reply and the changes do not contradict the real file state (0 = claims things that are "
        "false per the harness state, 3 = fully consistent)\n"
        'Reply with ONLY a JSON object: {"pickup": n, "decisions": n, "consistency": n, "comment": "one sentence"}')


def parse_judge(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
        s = {k: int(j[k]) for k in JUDGE_KEYS}
    except (ValueError, KeyError, TypeError):
        return None
    if not all(0 <= v <= 3 for v in s.values()):
        return None
    s["comment"] = str(j.get("comment", ""))
    return s


def sync_cost(line):
    """(tokens_in, tokens_out, model_secs) of a ctx `sync` log line, zeros for skip/none."""
    g = lambda k: float(m.group(1)) if (m := re.search(rf"\b{k}=([\d.]+)", line or "")) else 0  # noqa: E731
    return int(g("tokens_in")), int(g("tokens_out")), g("model_secs")


# ------------------------------------------------------------------ impure parts
class Budget:
    def __init__(self, sessions, judges, usd):
        self.cap = {"sessions": sessions, "judges": judges, "usd": usd}
        self.used = {"sessions": 0, "judges": 0, "usd": 0.0}
        self.fails = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.why = ""

    def take(self, kind):
        with self.lock:
            if self.stop.is_set():
                raise RuntimeError(f"stopped: {self.why}")
            if self.used[kind] >= self.cap[kind] or self.used["usd"] >= self.cap["usd"]:
                self.why = f"budget cap reached ({self.used})"
                self.stop.set()
                raise RuntimeError(self.why)
            self.used[kind] += 1

    def spend(self, usd):
        with self.lock:
            self.used["usd"] += usd or 0

    def result(self, ok):
        with self.lock:
            self.fails = 0 if ok else self.fails + 1
            if self.fails >= 3:
                self.why = "3 consecutive sessions failed"
                self.stop.set()


def env_for(arm, home):
    env = ab.base_env(arm == "B")
    env["CTX_HOME"] = str(home)
    env.pop("CTX_ROOT", None)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def check(scen, folder):
    p = subprocess.run([sys.executable, str(FIXT / "checks.py"), scen, str(folder)], capture_output=True, text=True,
                       timeout=180, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"items": {}, "error": (p.stderr or p.stdout)[-500:]}


def inject(folder, inj):
    p = Path(folder) / inj["file"]
    if inj["mode"] == "overwrite":
        p.write_text(inj["text"], encoding="utf-8")
        return True
    t = p.read_text(encoding="utf-8") if p.exists() else ""
    if inj["old"] not in t:
        return False
    p.write_text(t.replace(inj["old"], inj["new"], 1), encoding="utf-8")
    return True


def session(prompt, folder, arm, a, budget):
    """One `claude -p` session, one retry. -> (json or None, secs, raw, attempts, error)."""
    err = None
    for attempt in (1, 2):
        budget.take("sessions")
        try:
            j, secs, raw = ab.claude(prompt, folder, a.model, env_for(arm, a.home), TOOLS, a.hooks, timeout=a.timeout)
            budget.spend(j.get("total_cost_usd"))
            if not j.get("is_error"):
                return j, secs, raw, attempt, None
            err = f"is_error: {str(j.get('result'))[:200]}"
        except subprocess.TimeoutExpired:
            err = f"timeout {a.timeout}s"
        except SystemExit as e:
            err = str(e)[:300]
    return None, 0, "", 2, err


def run_chain(scen, arm, rep, a, spec, budget):
    sc = spec[scen]
    rid = f"{scen}-{arm}{rep}"
    folder = (a.home / "runs" / rid)
    od = a.out / rid
    if (od / "done.json").exists():
        return rid
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(od, ignore_errors=True)
    od.mkdir(parents=True)
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXT / scen, folder, ignore=shutil.ignore_patterns("__pycache__"))
    if arm == "A":
        p = subprocess.run([sys.executable, str(CTX_DIR / "ctx.py"), "init", str(folder)], capture_output=True,
                           text=True, env=env_for("A", a.home))
        if p.returncode:
            raise RuntimeError(f"ctx init failed: {p.stderr}")
    decisions = []
    for s in sc["sessions"][:a.sessions]:
        if budget.stop.is_set():
            break
        injected = None
        if s.get("inject_before"):
            injected = s["inject_before"]["what"] if inject(folder, s["inject_before"]) else "INJECTION FAILED (text not found)"
        before = snapshot(folder)
        cb = check(scen, folder)
        off = ab.log_size()
        j, secs, raw, attempts, err = session(s["prompt"], folder, arm, a, budget)
        budget.result(j is not None)
        line, wait = None, 0.0
        if arm == "A" and j is not None:
            t0 = time.time()
            line = ab.wait_sync(str(folder), off, a.sync_timeout)
            wait = round(time.time() - t0, 1)
        after = snapshot(folder)
        ca = check(scen, folder)
        files = changed_files(before, after)
        u = (j or {}).get("usage", {})
        rec = {"run": rid, "scenario": scen, "arm": arm, "rep": rep, "n": s["n"], "prompt": s["prompt"],
               "expect": s["expect"], "kind": s["kind"], "targets": s["targets"], "injected": injected,
               "decisions_before": list(decisions), "decision_here": s.get("decision"),
               "after_injection": any(x.get("inject_before") for x in sc["sessions"][:s["n"]]), "ok": j is not None, "error": err, "attempts": attempts,
               "secs": secs, "turns": (j or {}).get("num_turns"), "cost_usd": (j or {}).get("total_cost_usd"),
               "tokens_in": ab.total_in(u), "input_tokens": u.get("input_tokens", 0),
               "cache_read": u.get("cache_read_input_tokens", 0), "cache_creation": u.get("cache_creation_input_tokens", 0),
               "tokens_out": u.get("output_tokens", 0), "reply": (j or {}).get("result", "") or "",
               "files": files, "diff": unified(before, after, files),
               "context_md_changed_by_session": before.get("CONTEXT.md") != after.get("CONTEXT.md"),
               "check_before": cb, "check_after": ca,
               "newly_done": [i for i, v in ca.get("items", {}).items() if v and not cb.get("items", {}).get(i)],
               "redo": redo(scen, s, cb.get("items", {}), ca.get("items", {}), files),
               "sync_line": line, "sync_wait_secs": wait}
        if s.get("decision"):
            decisions.append(f"session {s['n']}: {s['decision']}")
        if arm == "A":
            ctxt = after.get("CONTEXT.md", "")
            ti, to, ms = sync_cost(line)
            rec["rewrite"] = {"tokens_in": ti, "tokens_out": to, "model_secs": ms,
                              "kind": (re.search(r" (sync|sync-light|skip|sync-fail) ", line or "") or [None, "none"])[1]}
            rec["context_md"] = ctxt
            rec["context_accuracy"] = context_accuracy(scen, ctxt, ca.get("items", {}), bool(decisions))
        (od / f"s{s['n']:02d}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
        (od / f"s{s['n']:02d}.raw.json").write_text(raw)
        print(f"[{rid}] s{s['n']} ok={rec['ok']} secs={secs} turns={rec['turns']} files={files} "
              f"newly={rec['newly_done']} redo={rec['redo']} sync={rec.get('rewrite', {}).get('kind')}", flush=True)
    if not budget.stop.is_set():
        (od / "done.json").write_text(json.dumps({"final": snapshot(folder)}, ensure_ascii=False))
    return rid


def real_ctx_lines(marker):
    p = Path.home() / ".ctx" / "log"
    try:
        return sum(1 for ln in p.read_text(errors="replace").splitlines() if marker in ln)
    except OSError:
        return 0


def cmd_run(a):
    spec = load_spec()
    a.home = Path(a.home).resolve()
    a.out = Path(a.out).resolve()
    a.hooks = str(Path(a.hooks).resolve())
    os.environ["CTX_HOME"] = str(a.home)  # ab.wait_sync/log_size read the test-home log
    os.environ.pop("CTX_ROOT", None)
    a.home.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)
    marker = str(a.home)
    real_before = real_ctx_lines(marker)
    budget = Budget(a.max_sessions, 0, a.max_usd)
    chains = [(s, arm, r) for r in range(1, a.reps + 1) for s in a.scenarios.split(",") for arm in a.arms.split(",")]
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(run_chain, s, arm, r, a, spec, budget): (s, arm, r) for s, arm, r in chains}
        for f in cf.as_completed(futs):
            try:
                print("chain done:", f.result(), flush=True)
            except Exception as e:  # noqa: BLE001
                print("chain failed:", futs[f], e, flush=True)
    real_after = real_ctx_lines(marker)
    meta = {"budget": budget.used, "stopped": budget.why, "real_ctx_log_lines_mentioning_test_home": [real_before, real_after],
            "model": a.model, "chains": len(chains)}
    prev = a.out / "run-meta.json"
    hist = json.loads(prev.read_text()) if prev.exists() else []
    (prev).write_text(json.dumps(hist + [meta], indent=1))
    print(json.dumps(meta, indent=1))
    return 0 if not budget.why else 2


def records(out):
    recs = []
    for f in sorted(Path(out).glob("*/s[0-9][0-9].json")):
        recs.append(json.loads(f.read_text()))
    return recs


def cmd_judge(a):
    out = Path(a.out).resolve()
    spec = load_spec()
    recs = [r for r in records(out) if r["ok"] and (a.rejudge or not r.get("judge"))]
    budget = Budget(0, a.max_judges, a.max_usd)
    random.Random(7).shuffle(recs)
    empty = Path(tempfile.mkdtemp()) / "empty-settings.json"
    empty.write_text("{}")
    env = ab.base_env(True)
    env["CTX_HOME"] = str(Path(a.home).resolve())

    def one(rec):
        hist = []
        for n in range(1, rec["n"]):
            p = out / rec["run"] / f"s{n:02d}.json"
            if p.exists():
                h = json.loads(p.read_text())
                hist.append({"n": n, "prompt": h["prompt"], "files": h["files"], "newly_done": h["newly_done"],
                             "injected": h.get("injected")})
        prompt = build_judge_prompt(spec[rec["scenario"]], hist, rec)
        with tempfile.TemporaryDirectory() as tmp:
            scores, raw = None, ""
            for _ in (1, 2):
                budget.take("judges")
                j, _, raw = ab.claude(prompt, tmp, a.judge_model, env, "", str(empty), timeout=300)
                budget.spend(j.get("total_cost_usd"))
                scores = parse_judge(j.get("result", ""))
                if scores:
                    break
        rec["judge"] = scores
        rec["judge_cost_usd"] = j.get("total_cost_usd")
        (out / rec["run"] / f"s{rec['n']:02d}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
        (out / rec["run"] / f"s{rec['n']:02d}.judge.json").write_text(raw)
        return rec["run"], rec["n"], scores

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for f in cf.as_completed([ex.submit(one, r) for r in recs]):
            try:
                print("judged", f.result(), flush=True)
            except Exception as e:  # noqa: BLE001
                print("judge failed:", e, flush=True)
    print(json.dumps({"judge_budget": budget.used, "stopped": budget.why}))
    return 0


# ------------------------------------------------------------------ summary
def fmt(x):
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def msd(xs):
    s = ab.stats(xs)
    return "-" if s["mean"] is None else f"{s['mean']} ± {s['sd']}"


def jtot(r):
    j = r.get("judge")
    return sum(j[k] for k in JUDGE_KEYS) if j else None


def summarize(recs, spec):
    lines = ["# Long-run benchmark: summary", "",
             "Arm A = ctx on (node, real rewrite after each session); arm B = CTX_OFF=1. Values: mean ± sd over the "
             "repetitions (one chain = 10 sessions). Continuity = blind Sonnet judge, 3 criteria 0-3 (total /9).", ""]
    for scen in spec:
        rs = [r for r in recs if r["scenario"] == scen]
        if not rs:
            continue
        lines += [f"## {scen}", "", "### Cumulative per chain", "",
                  "| arm | chains | sessions ok | tokens in (incl. cache) | tokens out | turns | seconds | cost USD | "
                  "continuity /9 | pickup | decisions | consistency | items done at end | convention at end | "
                  "regression fixed (s6 / end) | redo count | tests at end |",
                  "|" + "---|" * 17]
        for arm in ("A", "B"):
            ar = [r for r in rs if r["arm"] == arm]
            runs = sorted({r["run"] for r in ar})
            if not runs:
                continue
            per = {k: [] for k in ("ti", "to", "tu", "se", "co", "it", "cv", "rd", "s6", "end", "tests")}
            for run in runs:
                cr = sorted([r for r in ar if r["run"] == run], key=lambda r: r["n"])
                per["ti"].append(sum(r["tokens_in"] for r in cr))
                per["to"].append(sum(r["tokens_out"] for r in cr))
                per["tu"].append(sum(r["turns"] or 0 for r in cr))
                per["se"].append(round(sum(r["secs"] for r in cr), 1))
                per["co"].append(round(sum(r["cost_usd"] or 0 for r in cr), 3))
                last = cr[-1]["check_after"]
                per["it"].append(sum(1 for v in last.get("items", {}).values() if v))
                conv = last.get("convention") or {}
                per["cv"].append(round(sum(conv.values()) / len(conv), 2) if conv else None)
                per["rd"].append(sum(r["redo"] for r in cr))
                s6 = next((r for r in cr if r["n"] == 6), None)
                per["s6"].append(int(bool(s6 and s6["check_after"].get("regression_ok"))) if s6 else None)
                per["end"].append(int(bool(last.get("regression_ok"))))
                t = last.get("tests")
                per["tests"].append(f"{t['ran'] - t['failures'] - t['errors']}/{t['ran']}" if t else None)
            js = [r for r in ar if r.get("judge")]
            jm = lambda k: ab.mean([r["judge"][k] for r in js])  # noqa: E731
            lines.append(
                f"| {arm} | {len(runs)} | {sum(r['ok'] for r in ar)}/{len(ar)} | {msd(per['ti'])} | {msd(per['to'])} | "
                f"{msd(per['tu'])} | {msd(per['se'])} | {msd(per['co'])} | {fmt(ab.mean([jtot(r) for r in js]))} | "
                f"{fmt(jm('pickup'))} | {fmt(jm('decisions'))} | {fmt(jm('consistency'))} | {msd(per['it'])} | "
                f"{msd(per['cv'])} | {sum(x or 0 for x in per['s6'])}/{len(runs)} / {sum(per['end'])}/{len(runs)} | "
                f"{per['rd']} | {', '.join(x for x in per['tests'] if x) or '-'} |")
        lines += ["", "### Per session (mean over chains)", "",
                  "| session | request | A tokens in | B tokens in | A turns | B turns | A secs | B secs | A continuity /9 | "
                  "B continuity /9 | A progress on next item | B progress on next item | A redo | B redo |", "|" + "---|" * 14]
        for s in spec[scen]["sessions"]:
            row = [str(s["n"]), s["prompt"][:48] + ("…" if len(s["prompt"]) > 48 else "")]
            cols = {}
            for arm in ("A", "B"):
                sr = [r for r in rs if r["arm"] == arm and r["n"] == s["n"]]
                hit = [progress(scen, s, r) for r in sr if r["ok"]] if s["targets"] else []
                cols[arm] = (ab.mean([r["tokens_in"] for r in sr if r["ok"]]), ab.mean([r["turns"] for r in sr if r["ok"]]),
                             ab.mean([r["secs"] for r in sr if r["ok"]]), ab.mean([jtot(r) for r in sr]),
                             f"{sum(hit)}/{len(hit)}" if hit else "-", sum(r["redo"] for r in sr))
            for i in range(6):
                row += [fmt(cols["A"][i]), fmt(cols["B"][i])]
            lines.append("| " + " | ".join(row) + " |")
        ac = [r for r in rs if r["arm"] == "A" and r.get("context_accuracy")]
        if ac:
            lines += ["", "### Context accuracy (arm A, CONTEXT.md after each sync; mean over chains)", "",
                      "| session | Status lines | true | false | unverifiable | ground-truth facts | missing | Next names the next item | size bytes | rewrite | rewrite tokens in/out |",
                      "|" + "---|" * 11]
            for s in spec[scen]["sessions"]:
                sr = [r for r in ac if r["n"] == s["n"]]
                if not sr:
                    continue
                m = lambda k: ab.mean([r["context_accuracy"][k] for r in sr])  # noqa: E731
                kinds = ",".join(sorted({r["rewrite"]["kind"] for r in sr}))
                lines.append(f"| {s['n']} | {fmt(m('lines'))} | {fmt(m('true'))} | {fmt(m('false'))} | {fmt(m('other'))} | "
                             f"{fmt(m('facts'))} | {fmt(m('missing'))} | {sum(r['context_accuracy']['next_ok'] for r in sr)}/{len(sr)} | "
                             f"{fmt(m('size'))} | {kinds} | {fmt(ab.mean([r['rewrite']['tokens_in'] for r in sr]))}/"
                             f"{fmt(ab.mean([r['rewrite']['tokens_out'] for r in sr]))} |")
            ti = sum(r["rewrite"]["tokens_in"] for r in ac)
            to = sum(r["rewrite"]["tokens_out"] for r in ac)
            lines += ["", f"Rewrite total over {len({r['run'] for r in ac})} chains: {ti} tokens in, {to} out; upper bound at "
                      f"uncached Haiku list price: {round((ti * HAIKU_PRICE[0] + to * HAIKU_PRICE[1]) / 1e6, 3)} USD."]
        fails = [r for r in rs if not r["ok"]]
        if fails:
            lines += ["", "Failed sessions: " + "; ".join(f"{r['run']} s{r['n']}: {r['error']}" for r in fails)]
        lines.append("")
    return "\n".join(lines)


def cmd_summary(a):
    out = Path(a.out).resolve()
    recs = records(out)
    for r in recs:  # re-derived from the stored CONTEXT.md, so a classifier fix applies to old runs
        if r.get("context_md") is not None:
            r["context_accuracy"] = context_accuracy(r["scenario"], r["context_md"], r["check_after"].get("items", {}),
                                                     bool(r["decisions_before"] or r.get("decision_here")))
    text = summarize(recs, load_spec())
    meta = out / "run-meta.json"
    if meta.exists():
        text += "\n## Run metadata\n\n```\n" + meta.read_text() + "\n```\n"
    (out / "SUMMARY.md").write_text(text)
    print(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--home", required=True, help="temporary home (CTX_HOME); runs live in HOME/runs/")
    r.add_argument("--hooks", required=True, help="hooks-only settings from `ctx.py install-hooks claude --emit`")
    r.add_argument("--out", required=True)
    r.add_argument("--scenarios", default="course-notebooks,small-project")
    r.add_argument("--arms", default="A,B")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--sessions", type=int, default=10)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--model", default="haiku")
    r.add_argument("--timeout", type=int, default=240)
    r.add_argument("--sync-timeout", type=int, default=240)
    r.add_argument("--max-sessions", type=int, default=132, help="cap on claude -p sessions, retries included")
    r.add_argument("--max-usd", type=float, default=25.0)
    j = sub.add_parser("judge")
    j.add_argument("--home", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--judge-model", default="sonnet")
    j.add_argument("--rejudge", action="store_true", help="judge again sessions that already have scores")
    j.add_argument("--workers", type=int, default=4)
    j.add_argument("--max-judges", type=int, default=125)
    j.add_argument("--max-usd", type=float, default=15.0)
    s = sub.add_parser("summary")
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "judge": cmd_judge, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
