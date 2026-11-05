#!/usr/bin/env python3
"""Replayable T14-T16 (usefulness A/B, measurements, opencode), REAL sessions.

Rebuilds the mirror (CTX_HOME points at it, never HOME), plays T1 and T3 with the core battery helpers,
then T14 (A/B with hooks vs empty --settings), T15 (rewriter measurements + Haiku/Sonnet
comparison on the T1 inputs) and, if asked, T16 (opencode). Evidence: $CTX_EVIDENCE_DIR (default <tmp>/ctx-evidence/usefulness).

Usage: python3 tests/integration/run_usefulness.py [--model haiku] [--only T14,T15,T16]
                                                [--runs 3] [--oc-model provider/model]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_core_battery as r  # noqa: E402

r.EVIDENCE = r.EVIDENCE if os.environ.get("CTX_EVIDENCE_DIR") else r.EVIDENCE_ROOT / "usefulness"
r.SETTINGS = r.EVIDENCE / "hooks-settings.json"
HOME, CTX, EVIDENCE = r.HOME, r.CTX, r.EVIDENCE
EMPTY = EVIDENCE / "empty-settings.json"
sys.path.insert(0, str(r.CTX_DIR / "src"))
import ctx as ctxmod  # noqa: E402

OC = Path.home() / ".opencode/bin/opencode"
QUESTION = "où on en est et que faire ensuite ?"
FALLBACK = ("Au début de chaque session, lis le fichier CONTEXT.md du dossier courant : "
            "c'est l'état du projet (où on en est, prochaine étape), tenu à jour automatiquement.")
RESULTS = r.RESULTS


def run_claude(name, cwd, prompt, model, settings, tools="Read,Edit,Write,Glob,Grep"):
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--permission-mode", "acceptEdits", *r.ISOLATE]
    cmd += ["--tools", ""] if tools == "" else ["--allowedTools", tools]
    cmd += ["--settings", str(settings)]  # after the variadic tool flags, or they swallow the prompt
    t0 = time.time()
    p = subprocess.run(cmd + [prompt], cwd=cwd, capture_output=True, text=True, env=r.env(), timeout=300)
    (EVIDENCE / f"{name}.session.json").write_text(p.stdout + ("\n--stderr--\n" + p.stderr if p.stderr else ""))
    try:
        j = json.loads(p.stdout)
    except ValueError:
        j = {"result": p.stdout, "is_error": True}
    return j, round(time.time() - t0, 1)


def total_in(u):
    return u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)


def usage_line(j):
    u = j.get("usage", {})
    return (f"input_tokens={u.get('input_tokens', 0)} cache_creation={u.get('cache_creation_input_tokens', 0)} "
            f"cache_read={u.get('cache_read_input_tokens', 0)} => total_in={total_in(u)} output={u.get('output_tokens', 0)} "
            f"num_turns={j.get('num_turns')}")


# ------------------------------------------------------------------ T1 / T3 (core battery helpers)
def play_t1_t3(model):
    intro = HOME / "courses/ml-intro"
    pre = (intro / "CONTEXT.md").read_text()
    r.t1(model)
    r.t3(model)
    sid_dirs = sorted((HOME / ".ctx/sessions").iterdir(), key=lambda p: p.stat().st_mtime)
    t1_dir = next(d for d in sid_dirs if (d / "cwd").exists() and (d / "cwd").read_text().strip() == str(intro))
    return pre, t1_dir


def t14(model):
    paths = [HOME / "courses/challenge/CONTEXT.md", HOME / "courses/CONTEXT.md", HOME / "CONTEXT.md"]
    snap = {p: p.read_text() for p in paths}

    def restore():  # a reflection-only session re-syncs the node; give every arm the same starting state
        r.wait_idle()
        for p, t in snap.items():
            p.write_text(t)

    cwd = HOME / "courses/challenge"
    EMPTY.write_text("{}\n")
    arms = {}
    for label, settings, tools in (("A_hooks", r.SETTINGS, "Read,Edit,Write,Glob,Grep"),
                                   ("B_nohooks", EMPTY, "Read,Edit,Write,Glob,Grep"),
                                   ("A_hooks_notools", r.SETTINGS, ""), ("B_nohooks_notools", EMPTY, "")):
        j, secs = run_claude(f"T14.{label}", cwd, QUESTION, model, settings, tools)
        restore()
        arms[label] = (j, secs)
    out = [f"Question (verbatim, run in courses/challenge of the mirror after T1 and T3): {QUESTION}",
           "A = hooks via --settings (ctx injection); B = --settings with an empty object.",
           "Both arms still load the user's own settings/CLAUDE.md, so the input-token baseline is shared;",
           "the difference is the ctx injection (and any exploration the agent does by itself).", ""]
    for pair, title in ((("A_hooks", "B_nohooks"), "With the normal tools (agent may read files itself)"),
                        (("A_hooks_notools", "B_nohooks_notools"), "Without tools (only what is injected)")):
        out.append("=" * 78 + f"\n{title}\n" + "=" * 78)
        for lab in pair:
            j, secs = arms[lab]
            out.append(f"--- {lab}  ({secs}s)\n{usage_line(j)}\n{j.get('result', '')}\n")
    r.write("T14.evidence.txt", "\n".join(out))
    a, b = arms["A_hooks"][0], arms["B_nohooks"][0]
    an, bn = arms["A_hooks_notools"][0], arms["B_nohooks_notools"][0]
    ok = all(not x.get("is_error") and x.get("result") for x in (a, b, an, bn))
    r.record("T14", ok, "T14.evidence.txt",
             f"total_in A={total_in(a.get('usage', {}))} B={total_in(b.get('usage', {}))}; "
             f"no-tools A={total_in(an.get('usage', {}))} B={total_in(bn.get('usage', {}))}; "
             f"turns A={a.get('num_turns')} B={b.get('num_turns')}")


# ------------------------------------------------------------------ T15
def rewriter_inputs(pre, t1_dir):
    """Rebuild the prompt the rewriter received for T1 (same functions as ctx.py, no redaction needed)."""
    old = ctxmod.parse(pre)
    view = ctxmod.Doc()
    view.meta = {"summary": old.summary}
    view.sections = {s: v for s, v in old.sections.items() if s in (ctxmod.SEC_STATUS, ctxmod.SEC_NEXT)}
    node = HOME / "courses/ml-intro"
    files = [os.path.relpath(f, node) for f in (t1_dir / "touched").read_text().splitlines()]
    msgs = json.loads((t1_dir / "last.json").read_text())
    return ctxmod.build_prompt(ctxmod.render(view), sorted(set(files)), msgs, "")


def judge(text):
    low = text.lower()
    try:
        summary, where, nxt, _ = ctxmod.parse_model_output(text)
        verdict = "ok"
    except ValueError as e:
        summary, where, nxt, verdict = "", "", "", f"REJECTED: {e}"
    m = re.search(r"^SUMMARY:\s*(.+)$", text, re.M)
    slen = len(" ".join(m.group(1).split())) if m else -1
    return {"verdict": verdict, "summary_len": slen, "chapitre4": "chapitre 4" in low or "chapter 4" in low,
            "ex3": "ex3" in low, "chars": len(text)}


def t15(model, runs, pre, t1_dir):
    os.environ["CTX_HOME"] = str(HOME)
    log = [ln for ln in r.ctxlog().splitlines() if " sync " in ln and "courses/ml-intro" in ln]
    t1_sync = log[0] if log else ""
    g = lambda k: (re.search(rf"{k}=([\d.]+)", t1_sync) or [None, "?"])[1]
    node = HOME / "courses/ml-intro"
    hook = subprocess.run([sys.executable, str(CTX), "hook", "session-start"],
                          input=json.dumps({"session_id": "probe-t15", "cwd": str(node)}),
                          capture_output=True, text=True, env=r.env()).stdout
    shutil.rmtree(HOME / ".ctx/sessions/probe-t15", ignore_errors=True)
    try:
        injected = json.loads(hook)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError):
        injected = hook
    prompt = rewriter_inputs(pre, t1_dir)
    (EVIDENCE / "T15.rewriter-prompt.txt").write_text(ctxmod.SYSTEM_PROMPT + "\n\n=====\n" + prompt)
    rows, detail = [], []
    for m in ("haiku", "sonnet"):
        for i in range(runs):
            try:
                text, info = ctxmod.cli_rewriter(ctxmod.SYSTEM_PROMPT, prompt, model=m)
            except Exception as e:  # noqa: BLE001
                rows.append((m, i, {"verdict": f"ERROR {e}", "summary_len": -1, "chapitre4": False, "ex3": False, "chars": 0}, {}))
                continue
            rows.append((m, i, judge(text), info))
            detail.append(f"--- {m} run {i + 1}: {rows[-1][2]['verdict']} secs={info['secs']}\nusage={json.dumps(info['usage'])}\n{text}\n")
    r.write("T15.rewriter-outputs.txt", "\n".join(detail))
    out = ["T15 measurements on T1 (courses/ml-intro, model of the live sync = haiku)", "",
           "| measure | value |", "|---|---|",
           f"| rewriter input tokens (live T1 sync, log tokens_in = input+cache) | {g('tokens_in')} |",
           f"| rewriter output tokens (live T1 sync) | {g('tokens_out')} |",
           f"| model call duration, s (live) | {g('model_secs')} |",
           f"| whole sync duration, s (live) | {g('total_secs')} |",
           f"| characters injected at session start (ml-intro) | {len(injected)} |",
           f"| size of ml-intro/CONTEXT.md now | {len((node / 'CONTEXT.md').read_text())} |", "",
           f"live sync log line: {t1_sync}", "", "Raw json usage of the replayed rewriter call per model (same T1 inputs):"]
    for m, i, j, info in rows:
        u = info.get("usage", {})
        out.append(f"  {m} #{i + 1}: input_tokens={u.get('input_tokens')} cache_creation={u.get('cache_creation_input_tokens')} "
                   f"cache_read={u.get('cache_read_input_tokens')} output_tokens={u.get('output_tokens')} secs={info.get('secs')} "
                   f"| first answer {j['verdict']} summary_len={j['summary_len']} ch4={j['chapitre4']} ex3={j['ex3']}")
    out += ["", "Haiku vs Sonnet (first answer, before ctx.py's single retry):"]
    summ = {}
    for m in ("haiku", "sonnet"):
        rs = [j for mm, _, j, _ in rows if mm == m]
        okn = sum(j["verdict"] == "ok" for j in rs)
        summ[m] = okn
        out.append(f"  {m}: {okn}/{len(rs)} within the caps and filters; chapitre 4 kept {sum(j['chapitre4'] for j in rs)}/{len(rs)}; "
                   f"ex3 kept {sum(j['ex3'] for j in rs)}/{len(rs)}")
    r.write("T15.measures.txt", "\n".join(out) + "\n")
    r.record("T15", bool(t1_sync) and bool(injected), "T15.measures.txt",
             f"live in={g('tokens_in')} out={g('tokens_out')} sync={g('total_secs')}s injected={len(injected)} chars; "
             f"first-pass ok haiku={summ['haiku']}/{runs} sonnet={summ['sonnet']}/{runs}")


# ------------------------------------------------------------------ T16
def oc(name, cwd, model, extra_env=None):
    cmd = [str(OC), "run", "--format", "json"] + (["-m", model] if model else []) + [QUESTION]
    e = dict(os.environ, CTX_HOME=str(HOME))
    e.update(extra_env or {})
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=e, timeout=240, stdin=subprocess.DEVNULL)
        out, err = p.stdout, p.stderr
    except subprocess.TimeoutExpired as ex:
        out, err = (ex.stdout or b"").decode(errors="replace") if isinstance(ex.stdout, bytes) else (ex.stdout or ""), "timeout"
    (EVIDENCE / f"{name}.events.jsonl").write_text(out + ("\n--stderr--\n" + err if err else ""))
    text, tools, errs = [], [], []
    for ln in out.splitlines():
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if ev.get("type") == "text":
            text.append(ev.get("part", {}).get("text", ""))
        elif ev.get("type") == "tool_use":
            st = ev.get("part", {}).get("state", {})
            tools.append(f"{ev['part'].get('tool')} {json.dumps(st.get('input', {}))[:120]}")
        elif ev.get("type") == "error":
            errs.append(json.dumps(ev.get("error"))[:400])
    return "".join(text).strip(), tools, errs, round(time.time() - t0, 1)


def t16(oc_model):
    node = HOME / "courses/ml-intro"
    arms = {}
    arms["C_control"] = oc("T16.C_control", node, oc_model)
    (node / "AGENTS.md").write_text(FALLBACK + "\n")
    arms["A_agents_md_line"] = oc("T16.A_agents_md", node, oc_model)
    (node / "AGENTS.md").unlink()
    (node / "opencode.json").write_text(json.dumps({"$schema": "https://opencode.ai/config.json",
                                                   "instructions": ["CONTEXT.md"]}, indent=2) + "\n")
    arms["B_instructions_CONTEXT_md"] = oc("T16.B_instructions", node, oc_model)
    (node / "opencode.json").unlink()
    ctxt = (node / "CONTEXT.md").read_text()
    out = [f"Question: {QUESTION}", f"opencode {subprocess.run([str(OC), '--version'], capture_output=True, text=True).stdout.strip()}"
           f" model={oc_model or '(config default)'}",
           f"Fallback line (AGENTS.md in the node folder): {FALLBACK}", "",
           "Current ml-intro/CONTEXT.md (what the agent should know):", ctxt, ""]
    for lab, (txt, tools, errs, secs) in arms.items():
        out += ["=" * 78, f"{lab} ({secs}s)", "tools called: " + ("; ".join(tools) or "none"),
                "errors: " + ("; ".join(errs) or "none"), "answer:", txt, ""]
    r.write("T16.evidence.txt", "\n".join(out))
    blocked = [e for _, _, errs, _ in arms.values() for e in errs]
    known = {lab: bool(re.search(r"ex3|chapitre 4|r[ée]gression logistique", txt, re.I)) for lab, (txt, *_) in arms.items()}
    if blocked and not any(txt for txt, *_ in arms.values()):
        r.record("T16", False, "T16.evidence.txt", "BLOCKED: " + blocked[0])
    else:
        r.record("T16", known["A_agents_md_line"] or known["B_instructions_CONTEXT_md"], "T16.evidence.txt",
                 f"agent knows the state (ex3/chapitre 4 in answer): {known}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--only", default="T14,T15")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--oc-model", default="")
    a = ap.parse_args()
    only = a.only.split(",")
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    r.EVIDENCE = EVIDENCE
    import make_mirror
    make_mirror.build(HOME, init=True)
    r.ctx("install-hooks", "claude", "--emit", str(r.SETTINGS))
    r.write("setup.txt", f"mirror {HOME}\ncontext tree:\n{r.ctx('show', '--tree')}\nclaude {subprocess.run(['claude', '--version'], capture_output=True, text=True).stdout}")
    pre, t1_dir = play_t1_t3(a.model)
    if "T14" in only:
        t14(a.model)
    if "T15" in only:
        t15(a.model, a.runs, pre, t1_dir)
    if "T16" in only:
        t16(a.oc_model)
    r.write("SUMMARY.txt", "\n".join(f"{t}: {'GREEN' if o else 'RED'} {p} {n}" for t, (o, p, n) in RESULTS.items()) + "\n")
    shutil.copy(HOME / ".ctx/log", EVIDENCE / "ctx-log-full.txt")
    return 0 if all(o for o, _, _ in RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
