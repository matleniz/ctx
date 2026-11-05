#!/usr/bin/env python3
"""Cursor: T0, T1, T2, T6 with REAL `cursor-agent -p` sessions, on the mirror only.

Hooks come from the documented project-level `<project-root>/.cursor/hooks.json` written INTO the mirror
folder where the session starts (`ctx install-hooks cursor --settings-file ...`); the real ~/.cursor is never
written (config files hashed before/after; chats/ is Cursor's own session store and is only counted).
Reuses the helpers of run_core_battery.py. Evidence goes to $CTX_EVIDENCE_DIR (default <tmp>/ctx-evidence/cursor).

Usage: CTX_MIRROR=/tmp/ctx-cursor/home python3 tests/integration/run_cursor.py [--only T0,T1,T2,T6]
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
sys.path.insert(0, str(HERE))
os.environ.setdefault("CTX_EVIDENCE_DIR", str(Path(tempfile.gettempdir()) / "ctx-evidence" / "cursor"))
import run_core_battery as r  # noqa: E402

HOME, CTX, EVIDENCE = r.HOME, r.CTX, r.EVIDENCE
REAL = Path.home() / ".cursor"
MODEL = "auto"
TAG = "cursor"
RESULTS = r.RESULTS


def real_hashes():
    out = {}
    for p in sorted(REAL.rglob("*")):
        rel = p.relative_to(REAL)
        if p.is_file() and rel.parts[0] not in ("chats", "ai-tracking"):  # chats/: Cursor's own store
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    return out


def n_chats(ws="courses-ml-intro"):
    """Agent transcript dirs Cursor keeps for this mirror workspace (names only are listed, nothing is read)."""
    d = REAL / "projects" / ("tmp-ctx-cursor-home-" + ws) / "agent-transcripts"
    return len(list(d.iterdir())) if d.is_dir() else 0


def own_state(rel):
    """Files Cursor itself writes on any run (per-workspace store, caches, settings it rewrites): not user config."""
    return rel.startswith(("projects/", "skills-cursor/")) or rel in ("statsig-cache.json", "cli-config.json")


def hooks_in(cwd):
    f = Path(cwd) / ".cursor/hooks.json"
    if not f.exists():
        r.ctx("install-hooks", "cursor", "--settings-file", str(f), "--yes")


def sess(name, cwd, prompt, resume=None):
    hooks_in(cwd)
    cmd = ["cursor-agent", "-p", "--trust", "--force", "--model", MODEL, "--output-format", "json"]
    if resume:
        cmd += ["--resume", resume]
    cmd.append(prompt)
    t0 = time.time()
    for attempt in (1, 2):  # the first call of the probe hit a transient "Provider Error"; one retry, noted
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=r.env(), timeout=240)
        if "Provider Error" not in p.stdout + p.stderr:
            break
        print(f"note: {name} provider error, retry")
    (EVIDENCE / f"{name}.session.json").write_text(p.stdout + ("\n--stderr--\n" + p.stderr if p.stderr else ""))
    try:
        j = json.loads(p.stdout)
    except ValueError:
        j = {"result": p.stdout + p.stderr, "is_error": True}
    return j.get("result", ""), j, round(time.time() - t0, 1)


def t0(wait):
    node = HOME / "courses/ml-intro"
    log0 = len(r.ctxlog().splitlines())
    c0 = n_chats()
    h = r.sha(node / "exos/ex1.py")
    ans, j, _ = sess("T0", node, "Edit exos/ex1.py so ex1() returns 10 instead of 1. Reply 'done'.")
    time.sleep(wait)
    r.wait_idle(60)
    lines = r.ctxlog().splitlines()[log0:]
    syncs = [ln for ln in lines if " sync " in ln]
    stray = [f"{pid} {c[:100]}" for pid, c in r.proc_cmdlines() if "ctx.py" in c and "sync" in c and r.mirror_env(pid)]
    r.write("T0.log", "\n".join(lines) + "\n")
    r.write("T0.evidence.txt", f"ex1.py hash before {h} after {r.sha(node / 'exos/ex1.py')}\nanswer: {ans}\n"
            f"cursor chats before {c0} after {n_chats()}\nsync log lines {len(syncs)} (waited {wait}s after the session)\n"
            f"total ctx log lines after the session: {len(lines)}\nsync processes still alive: {stray}\n")
    r.record("T0", len(syncs) == 1 and not stray and n_chats() == c0 + 1, f"T0.evidence.txt",
             f"sync lines={len(syncs)}, new cursor chats={n_chats() - c0} (expected 1: the test session)")


def t1():
    hs = {k: r.sha(HOME / v / "CONTEXT.md") for k, v in {"intro": "courses/ml-intro", "alg": "courses/algebra"}.items()}
    ans, j, _ = sess("T1", HOME / "courses/ml-intro",
        "ajoute un exercice 3 sur la régression logistique dans exos/ex3.py et note dans notes.md qu'on en est au chapitre 4")
    r.wait_idle()
    intro = r.grab("courses/ml-intro/CONTEXT.md")
    summ = [x for x in intro.splitlines() if x.startswith("summary:")]
    l_cours, l_home = r.line_for("courses", "ml-intro"), r.line_for("", "courses")
    r.write("T1.evidence.txt", f"answer: {ans}\n\n--- ml-intro/CONTEXT.md\n{intro}\n--- courses line:\n{l_cours}\n--- home line:\n{l_home}\n"
            f"algebra hash before {hs['alg']} after {r.sha(HOME / 'courses/algebra/CONTEXT.md')}\n")
    r.write("T1.log", r.ctxlog())
    low = (summ[0] if summ else "").lower()
    ok = any(w in low for w in ("chapitre 4", "ex3", "chapter 4")) and l_home != "" and hs["alg"] == r.sha(HOME / "courses/algebra/CONTEXT.md")
    r.record("T1", ok, f"T1.evidence.txt", f"summary={summ}")


def t2():
    node = HOME / "courses/ml-intro"
    a, _, _ = sess("T2a", node, "liste les autres cours que je suis")
    b, _, _ = sess("T2b", node, "Without running any tool or reading any file, answer only from the context given at the start of this session: list the other courses I follow.")
    show = r.ctx("show", str(node))
    r.write("T2.evidence.txt", f"T2a (tools on; the agent may explore the filesystem) verbatim:\n{a}\n\nT2b (asked to use only the injected context) verbatim:\n{b}\n\n--- ctx show\n{show}\n")
    sib = ("algebra", "challenge", "algèbre")
    inj_ok = not any(w in show.lower() for w in sib)
    a_ok, b_ok = (not any(w in x.lower() for w in sib) for x in (a, b))
    r.record("T2", inj_ok and b_ok, f"T2.evidence.txt",
             f"injection clean={inj_ok}; context-only answer clean={b_ok}; tools-on answer clean={a_ok}"
             + ("" if a_ok else " (agent listed the parent folder itself, not via ctx)"))


def t6():
    truc = HOME / "scratch/truc"
    a1, j1, _ = sess("T6A1", truc, "crée un fichier hello.txt")
    a2, j2, _ = sess("T6A2", truc, "non, je ne veux pas de contexte ctx pour ce dossier. Enregistre ce refus comme la consigne [ctx] "
                     "de début de session l'indique, puis crée quand même hello.txt (contenu: salut).", resume=j1.get("session_id"))
    r.wait_idle()
    declined = (HOME / ".ctx/declined").read_text() if (HOME / ".ctx/declined").exists() else ""
    a3, _, _ = sess("T6A3", truc, "crée un fichier world.txt avec le mot monde")
    r.wait_idle()
    okA = str(truc) in declined and not (truc / "CONTEXT.md").exists()
    sys.path.insert(0, str(r.CTX_DIR / "src")); import ctx.cli; ctxmod = ctx
    os.environ["CTX_HOME"] = str(HOME); ctxmod.tree.remove_declined(truc)
    b1, jb1, _ = sess("T6B1", truc, "crée un fichier hello2.txt")
    b2, _, _ = sess("T6B2", truc, "oui, crée le contexte ctx pour ce dossier comme la consigne [ctx] de début de session "
                    "l'indique (exécute la commande init indiquée), puis crée hello2.txt (contenu: salut).", resume=jb1.get("session_id"))
    r.wait_idle()
    okB = (truc / "CONTEXT.md").exists()
    r.write("T6.evidence.txt", f"== A (non)\nfirst:\n{a1}\n\nafter non:\n{a2}\n\n~/.ctx/declined (mirror): {declined}\nthird session answer:\n{a3}\n"
            f"== B (oui)\nfirst:\n{b1}\n\nafter oui:\n{b2}\n\ntruc/CONTEXT.md exists: {okB}\n--- truc/CONTEXT.md\n{r.grab('scratch/truc/CONTEXT.md')}\n")
    r.record("T6", okA and okB, f"T6.evidence.txt", f"decline recorded & no CONTEXT.md: {okA}; yes -> CONTEXT.md created: {okB}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="T0,T1,T2,T6")
    ap.add_argument("--wait0", type=int, default=120)
    a = ap.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for f in EVIDENCE.iterdir():  # keep the hand-written DOC.md / RESULTS.md
        if f.name not in ("DOC.md", "RESULTS.md"):
            f.unlink() if f.is_file() else shutil.rmtree(f)
    r.make_mirror.build(HOME, init=True)
    before = real_hashes()
    r.write("setup.txt", f"mirror {HOME}\ncursor-agent {subprocess.run(['cursor-agent','--version'],capture_output=True,text=True).stdout}"
            f"real ~/.cursor/hooks.json exists: {(REAL / 'hooks.json').exists()}\nreal config hashes (before): {json.dumps(before)}\nchats before: {n_chats()}\n")
    fns = {"T0": lambda: t0(a.wait0), "T1": t1, "T2": t2, "T6": t6}
    for t in a.only.split(","):
        try:
            fns[t]()
        except Exception as e:
            r.record(t, False, "-", f"exception {type(e).__name__}: {e}")
    after = real_hashes()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    user_cfg = [k for k in changed if not own_state(k)]
    r.write("real-cursor-changes.txt", "Files of the real ~/.cursor that changed during the run (hashes only, no content read):\n"
            + "\n".join(f"{'cursor-own-state' if own_state(k) else 'USER CONFIG'}  {k}" for k in changed) + "\n")
    ok_real = not user_cfg and not (REAL / "hooks.json").exists()
    r.write("real-cursor-untouched.txt", f"user config (hooks.json, mcp.json, rules...) changed: {user_cfg}\nchanged files in total (all Cursor-owned state): {len(changed)}\nchats now: {n_chats()}\nhooks.json exists: {(REAL / 'hooks.json').exists()}\n")
    r.write("SUMMARY.txt", "\n".join(f"{t}: {'GREEN' if o else 'RED'} {p} {n}" for t, (o, p, n) in RESULTS.items()) + f"\nreal ~/.cursor user config untouched: {ok_real}\n")
    shutil.copy(HOME / ".ctx/log", EVIDENCE / "ctx-log-full.txt")
    return 0 if all(o for o, _, _ in RESULTS.values()) and ok_real else 1


if __name__ == "__main__":
    sys.exit(main())
