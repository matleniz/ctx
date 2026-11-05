#!/usr/bin/env python3
# Cases are numbered T0..T16 across the runners (T0-T5 here); see the docstring of each and its record() note for what it proves.
"""Replayable core battery T0-T5 (sync, isolation, levels, diffstat), with REAL `claude -p` sessions.

Rebuilds the mirror tree (CTX_HOME points at it, never HOME), emits a hooks-only settings
file and plays T0..T5. Evidence goes to $CTX_EVIDENCE_DIR (default <tmp>/ctx-evidence/core-battery). Uses only documented
mechanisms (claude -p, --settings, --output-format json); no transcript is read.

Usage: python3 tests/integration/run_core_battery.py [--model haiku] [--only T1,T2] [--wait0 120]
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CTX_DIR = HERE.parent.parent
CTX = CTX_DIR / "ctx.py"
EVIDENCE_ROOT = Path(tempfile.gettempdir()) / "ctx-evidence"
EVIDENCE = Path(os.environ["CTX_EVIDENCE_DIR"]) if os.environ.get("CTX_EVIDENCE_DIR") else EVIDENCE_ROOT / "core-battery"
HOME = Path(os.environ["CTX_MIRROR"]) if os.environ.get("CTX_MIRROR") else Path(tempfile.mkdtemp(prefix="ctx-mirror-")) / "home"
sys.path.insert(0, str(CTX_DIR / "tests"))
import make_mirror  # noqa: E402

SETTINGS = EVIDENCE / "hooks-settings.json"
# The user's real hooks are global (~/.claude/settings.json). `--setting-sources project,local` leaves the user
# source out (documented flag: "Comma-separated list of setting sources to load (user, project, local)"), while
# `--settings <file>` still loads: so a session runs ONE set of hooks, the mirror's.
ISOLATE = ["--setting-sources", "project,local"]
RESULTS = {}


def sha(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "-"


def env():
    e = dict(os.environ, CTX_HOME=str(HOME))
    e.pop("CTX_OFF", None)
    e.pop("CTX_INNER", None)
    return e


def ctx(*args, e=None):
    r = subprocess.run([sys.executable, str(CTX), *args], capture_output=True, text=True,
                       env=e or env(), timeout=60)
    return r.stdout + r.stderr


def ctxlog():
    p = HOME / ".ctx/log"
    return p.read_text() if p.exists() else ""


def proc_cmdlines():
    """Read-only scan of /proc (no pgrep -f)."""
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                out.append((int(d.name), (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")))
            except OSError:
                pass
    return out


def mirror_env(pid):
    """True if the process was started with CTX_HOME pointing at our mirror (own processes only)."""
    try:
        return f"CTX_HOME={HOME}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return False


def wait_idle(timeout=240, quiet=8):
    """Wait until the ctx log stops growing and no detached `ctx.py sync` of ours is alive."""
    t0, last, stable = time.time(), None, 0
    while time.time() - t0 < timeout:
        busy = [c for pid, c in proc_cmdlines() if f"{CTX} sync" in c and pid != os.getpid()]
        size = len(ctxlog())
        if not busy and size == last:
            stable += 1
            if stable >= quiet // 2:
                return True
        else:
            stable = 0
        last = size
        time.sleep(2)
    return False


def session(name, cwd, prompt, model, hooks=True, extra_env=None, tools="Read,Edit,Write,Glob,Grep"):
    out = EVIDENCE / f"{name}.session.json"
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--permission-mode", "acceptEdits", *ISOLATE]
    cmd += ["--tools", ""] if tools == "" else ["--allowedTools", tools]
    if hooks:
        cmd += ["--settings", str(SETTINGS)]
    cmd.append(prompt)
    e = env()
    e.update(extra_env or {})
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=e, timeout=180)
    except subprocess.TimeoutExpired:  # a headless claude call occasionally hangs with no output: one retry, noted
        print(f"note: {name} timed out after 180 s, retried once")
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=e, timeout=180)
    out.write_text(r.stdout + ("\n--stderr--\n" + r.stderr if r.stderr else ""))
    try:
        j = json.loads(r.stdout)
    except ValueError:
        j = {"result": r.stdout, "is_error": True}
    return j.get("result", ""), j, round(time.time() - t0, 1)


def record(t, ok, evidence, note=""):
    RESULTS[t] = (ok, evidence, note)
    print(f"{t}: {'GREEN' if ok else 'RED'}  {evidence}  {note}")


def write(name, text):
    (EVIDENCE / name).write_text(text)


def grab(rel):
    p = HOME / rel.lstrip("/") if rel else HOME
    return p.read_text() if p.exists() else ""


def line_for(parent_rel, child):
    for ln in grab((parent_rel + "/" if parent_rel else "") + "CONTEXT.md").splitlines():
        if ln.startswith(f"- **{child}/**"):
            return ln
    return ""


def t0(model, wait):
    n = "T0"
    node = HOME / "courses/ml-intro"
    proj = Path.home() / ".claude/projects" / ("-" + str(node).strip("/").replace("/", "-").replace(".", "-").replace("_", "-"))
    before_files = sorted(p.name for p in proj.glob("*.jsonl")) if proj.is_dir() else []
    log0 = len(ctxlog().splitlines())
    h = sha(node / "exos/ex1.py")
    ans, j, secs = session("T0", node, "Edit exos/ex1.py so ex1() returns 10 instead of 1. Reply 'done'.", model)
    time.sleep(wait)
    wait_idle(60)
    after_files = sorted(p.name for p in proj.glob("*.jsonl")) if proj.is_dir() else []
    lines = ctxlog().splitlines()[log0:]
    syncs = [ln for ln in lines if " sync " in ln]
    stray = [f"{pid} {c[:100]}" for pid, c in proc_cmdlines()
             if c.startswith("claude") and mirror_env(pid)]
    write("T0.log", "\n".join(lines) + "\n")
    write("T0.evidence.txt",
          f"ex1.py hash before {h} after {sha(node / 'exos/ex1.py')}\nsession answer: {ans}\n"
          f"project dir: {proj}\nsession files before: {before_files}\nafter: {after_files}\n"
          f"new session files: {sorted(set(after_files) - set(before_files))}\n"
          f"sync log lines: {len(syncs)}\nremaining claude processes matching mirror/inner call: {stray}\n")
    new = set(after_files) - set(before_files)
    ok = len(syncs) == 1 and not stray and len(new) == 1
    record(n, ok, "T0.evidence.txt", f"sync lines={len(syncs)} new session files={len(new)} (expected 1: the test session itself)")


def t1(model):
    n = "T1"
    nodes = {"intro": "courses/ml-intro", "alg": "courses/algebra"}
    hs = {k: sha(HOME / v / "CONTEXT.md") for k, v in nodes.items()}
    ans, j, secs = session(n, HOME / nodes["intro"],
        "ajoute un exercice 3 sur la régression logistique dans exos/ex3.py et note dans notes.md qu'on en est au chapitre 4", model)
    wait_idle()
    intro = grab("courses/ml-intro/CONTEXT.md")
    summ = [l for l in intro.splitlines() if l.startswith("summary:")]
    l_cours, l_home = line_for("courses", "ml-intro"), line_for("", "courses")
    after_alg = sha(HOME / nodes["alg"] / "CONTEXT.md")
    write("T1.evidence.txt", f"answer: {ans}\n\n--- ml-intro/CONTEXT.md\n{intro}\n--- courses line:\n{l_cours}\n--- home line:\n{l_home}\n"
          f"algebra hash before {hs['alg']} after {after_alg}\nml-intro hash before {hs['intro']} after {sha(HOME / nodes['intro'] / 'CONTEXT.md')}\n")
    write("T1.log", ctxlog())
    low = intro.lower()
    ok = ("chapitre 4" in summ[0].lower() or "ex3" in summ[0].lower() or "chapter 4" in summ[0].lower()) if summ else False
    ok = ok and ("4" in l_cours or "ex3" in l_cours.lower()) and l_home != "" and hs["alg"] == after_alg
    record(n, ok, "T1.evidence.txt", f"summary={summ}")
    write("T1.measures.txt", f"session secs={secs} usage={j.get('usage')}\nsync log: {[l for l in ctxlog().splitlines() if ' sync ' in l]}\n")


def t2(model):
    n = "T2"
    node = HOME / "courses/ml-intro"
    prompt = "liste les autres cours que je suis"
    ans_a, _, _ = session("T2a", node, prompt, model)  # task wording, normal tools
    ans_b, _, _ = session("T2b", node, prompt, model, tools="")  # injected context only, no tools
    show = ctx("show", str(node))
    write("T2.evidence.txt", f"T2a (default tools; agent may explore the filesystem) verbatim:\n{ans_a}\n\n"
          f"T2b (--tools \"\": only the injected context) verbatim:\n{ans_b}\n\n--- ctx show courses/ml-intro\n{show}\n")
    sib = ("algebra", "challenge", "algèbre")
    inj_ok = not any(w in show.lower() for w in sib)
    a_ok = not any(w in ans_a.lower() for w in sib)
    b_ok = not any(w in ans_b.lower() for w in sib)
    record(n, inj_ok and b_ok, "T2.evidence.txt",
           f"injection clean={inj_ok}; no-tools answer clean={b_ok}; task-wording answer (tools on) clean={a_ok}"
           + ("" if a_ok else " (agent listed the parent folder itself, not via ctx)"))


def t3(model):
    n = "T3"
    h = sha(HOME / "home/CONTEXT.md")
    home_before = grab("CONTEXT.md")
    ans, j, secs = session(n, HOME / "courses/challenge",
        "modifie src/train.py pour ajouter des features temporelles et note dans results/log.md : val loss 0.22", model)
    wait_idle()
    d, c, hm = grab("courses/challenge/CONTEXT.md"), grab("courses/CONTEXT.md"), grab("CONTEXT.md")
    own_home = hm.split("## Subfolders")[0].split("---", 2)[-1]  # body only: the header summary of a container is computed from its children
    write("T3.evidence.txt", f"answer: {ans}\n\n--- challenge/CONTEXT.md\n{d}\n--- courses/CONTEXT.md\n{c}\n--- home/CONTEXT.md\n{hm}\n"
          f"home own state (before Subfolders) contains 0.22: {'0.22' in own_home}\n")
    write("T3.log", ctxlog())
    summ = next((l for l in d.splitlines() if l.startswith("summary:")), "")
    ok = "0.22" in summ and "0.22" in line_for("courses", "challenge") and "0.22" not in own_home
    record(n, ok, "T3.evidence.txt", summ[:120])


def t4(model):
    n = "T4"
    paths = ["courses/ml-intro", "courses/algebra"]
    before = {p: sha(HOME / p / "CONTEXT.md") for p in paths}
    cb = line_for("courses", "ml-intro"), line_for("courses", "algebra")
    nt = HOME / "courses/ml-intro/notes.md"  # plant a real typo (fixture only, contexts untouched)
    nt.write_text(nt.read_text().replace("régression", "regresion"))
    ans, j, secs = session(n, HOME / "courses",
        "corrige une faute dans ml-intro/notes.md et ajoute une ligne dans algebra/notes.md "
        "(la ligne à ajouter : « Chapitre 2 : rétropropagation. »)", model)
    wait_idle()
    after = {p: sha(HOME / p / "CONTEXT.md") for p in paths}
    ca = line_for("courses", "ml-intro"), line_for("courses", "algebra")
    write("T4.evidence.txt", f"answer: {ans}\nhashes before {before}\nafter {after}\ncourses lines before {cb}\nafter {ca}\n"
          f"--- ml-intro\n{grab('courses/ml-intro/CONTEXT.md')}\n--- algebra\n{grab('courses/algebra/CONTEXT.md')}\n")
    write("T4.log", ctxlog())
    ok = all(before[p] != after[p] for p in paths) and all(ca) and ca != cb
    record(n, ok, "T4.evidence.txt", f"changed={[before[p] != after[p] for p in paths]}")


def t5(model):
    n = "T5"
    repo = HOME / "projects/site"
    log0 = len(ctxlog().splitlines())
    ans, j, secs = session(n, repo, "modifie src/index.html : ajoute un titre <h1>Mon site</h1> dans le body", model)
    wait_idle()
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    excl = (repo / ".git/info/exclude").read_text()
    ctxdirs = subprocess.run(["find", str(repo), "-name", ".ctx", "-not", "-path", "*/.git/*"], capture_output=True, text=True).stdout
    lines = ctxlog().splitlines()[log0:]
    diffstat = [l for l in lines if "git-diff-stat" in l]
    write("T5.evidence.txt", f"answer: {ans}\n--- git status --porcelain\n{st}\n--- .git/info/exclude\n{excl}\n"
          f"--- .ctx dirs in repo: [{ctxdirs.strip()}]\n--- log lines\n" + "\n".join(lines) + "\n")
    write("T5.log", "\n".join(lines) + "\n")
    ok = "CONTEXT.md" not in st and "src/index.html" in st and "CONTEXT.md" in excl and not ctxdirs.strip() and bool(diffstat)
    record(n, ok, "T5.evidence.txt", f"diffstat log lines={len(diffstat)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--only", default="T0,T1,T2,T3,T4,T5")
    ap.add_argument("--wait0", type=int, default=120)
    a = ap.parse_args()
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    make_mirror.build(HOME, init=True)
    ctx("install-hooks", "claude", "--emit", str(SETTINGS))
    write("setup.txt", f"mirror {HOME}\ncontext tree:\n{ctx('show', '--tree')}\nclaude {subprocess.run(['claude','--version'],capture_output=True,text=True).stdout}")
    fns = {"T0": lambda: t0(a.model, a.wait0), "T1": lambda: t1(a.model), "T2": lambda: t2(a.model),
           "T3": lambda: t3(a.model), "T4": lambda: t4(a.model), "T5": lambda: t5(a.model)}
    for t in a.only.split(","):
        try:
            fns[t]()
        except Exception as e:
            record(t, False, "-", f"exception {type(e).__name__}: {e}")
    write("SUMMARY.txt", "\n".join(f"{t}: {'GREEN' if o else 'RED'} {p} {n}" for t, (o, p, n) in RESULTS.items()) + "\n")
    shutil.copy(HOME / ".ctx/log", EVIDENCE / "ctx-log-full.txt") if (HOME / ".ctx/log").exists() else None
    return 0 if all(o for o, _, _ in RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
