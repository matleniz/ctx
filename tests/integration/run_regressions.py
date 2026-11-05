#!/usr/bin/env python3
"""Regression checks C2, C3, C5, C7 on a test tree (C1 is T6C in run_resilience.py, C6 is run_core_battery.py).

Uses its own mirror (CTX_MIRROR, default /tmp/ctx-regressions/home) with CTX_HOME on it, never the real home.
Real `claude -p` Haiku sessions with hooks from `--settings` and the user source left out (see run_core_battery.ISOLATE).
Usage: python3 tests/integration/run_regressions.py   -> evidence in $CTX_EVIDENCE_DIR (default <tmp>/ctx-evidence/regressions)
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("CTX_MIRROR", "/tmp/ctx-regressions/home")
import run_core_battery as r  # noqa: E402

EVIDENCE = Path(os.environ["CTX_EVIDENCE_DIR"]) if os.environ.get("CTX_EVIDENCE_DIR") else r.EVIDENCE_ROOT / "regressions"
r.EVIDENCE = EVIDENCE
r.SETTINGS = EVIDENCE / "hooks-settings.json"
HOME = r.HOME
OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def log_lines(start):
    return r.ctxlog().splitlines()[start:]


def main():
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    r.make_mirror.build(HOME, init=True)
    r.ctx("install-hooks", "claude", "--emit", str(r.SETTINGS))
    node = HOME / "courses/ml-intro"

    # C2 bootstrap: read-only session on a placeholder node fills it once
    l0 = len(r.ctxlog().splitlines())
    ans, _, _ = r.session("C2_bootstrap", node, "Fais le point sur ce dossier en 5 lignes (lecture seule, ne modifie rien).", "haiku",
                          tools="Read,Glob,Grep")
    r.wait_idle()
    bl = log_lines(l0)
    summ = next((l for l in r.grab("courses/ml-intro/CONTEXT.md").splitlines() if l.startswith("summary:")), "")
    say("== C2 bootstrap (placeholder node, read-only session)")
    say("\n".join(bl))
    say(f"summary after: {summ}")
    ok_boot = any(" sync " in l for l in bl) and "New node" not in summ

    # C2 skip: second read-only session on the now-filled node
    h = r.sha(node / "CONTEXT.md")
    l0 = len(r.ctxlog().splitlines())
    ans, _, _ = r.session("C2_skip", node, "Dis-moi en une phrase ce que contient notes.md (lecture seule).", "haiku", tools="Read,Glob,Grep")
    r.wait_idle()
    sl = log_lines(l0)
    say("\n== C2 skip (read-only session on a filled node)")
    say("\n".join(sl))
    ok_skip = any(" skip " in l and "courses/ml-intro" in l for l in sl) and not any(" sync " in l and "courses/ml-intro" in l for l in sl) \
        and h == r.sha(node / "CONTEXT.md")
    say(f"ml-intro/CONTEXT.md unchanged: {h == r.sha(node / 'CONTEXT.md')}")

    # C3: container shows '<child> : summary; ...'
    say("\n== C3 ctx show --tree (container summaries computed with child names)")
    # make courses a never-rewritten container whose children are filled
    tree = r.ctx("show", "--tree")
    say(tree)
    cours_summ = next((l for l in r.grab("courses/CONTEXT.md").splitlines() if l.startswith("summary:")), "")
    say(f"courses summary: {cours_summ}")
    ok_c3 = "ml-intro : " in cours_summ

    # C7: session that edits through Bash only
    l0 = len(r.ctxlog().splitlines())
    ex = node / "exos/ex2.py"
    before = r.sha(ex)
    ans, _, _ = r.session("C7_bash", node, "Use the Bash tool, not the Edit tool: run `echo '# bash edit marker' >> exos/ex2.py`, then reply 'done'.",
                          "haiku", tools=f"Read,Glob,Grep,Bash(echo:*),Bash(printf:*)")
    r.wait_idle()
    cl = log_lines(l0)
    sess_dirs = sorted((HOME / ".ctx/sessions").iterdir(), key=lambda p: p.stat().st_mtime)
    touched = [d.name for d in sess_dirs if (d / "touched").exists()]
    say("\n== C7 Bash edit (no Write/Edit tool call)")
    say(f"ex2.py hash {before} -> {r.sha(ex)}; answer: {ans}")
    say("\n".join(cl))
    say(f"sessions with a 'touched' file (PostToolUse saw Write/Edit): {touched}")
    ok_c7 = r.sha(ex) != before and any("bash-scan" in l for l in cl) and any(" sync " in l and "files=1" in l for l in cl)

    # C5: 3 SessionStart on a mirror with a pid-less old session
    old = HOME / ".ctx/sessions/notes-old"
    old.mkdir(parents=True)
    (old / "cwd").write_text(str(HOME / "notes"))
    (old / "last.json").write_text(json.dumps(["Ancienne session sans pid."]))
    os.utime(old, (1, 1))
    for f in old.iterdir():
        os.utime(f, (1, 1))
    l0 = len(r.ctxlog().splitlines())
    for i in range(3):
        subprocess.run([sys.executable, str(r.CTX), "hook", "session-start"], input=json.dumps(
            {"session_id": f"start-{i}", "cwd": str(HOME / "notes")}), capture_output=True, text=True, env=r.env())
        r.wait_idle()
    cu = [l for l in log_lines(l0) if "catchup" in l]
    say("\n== C5 three SessionStart with an old pid-less session in the mirror")
    say("\n".join(log_lines(l0)))
    say(f"catchup lines: {len(cu)} (expected 1: caught up once, then closed)")
    ok_c5 = len(cu) == 1 and (old / "closed").exists()

    res = dict(C2_bootstrap=ok_boot, C2_skip=ok_skip, C3=ok_c3, C5=ok_c5, C7=ok_c7)
    say("\n" + "\n".join(f"{k}: {'GREEN' if v else 'RED'}" for k, v in res.items()))
    (EVIDENCE / "SUMMARY.txt").write_text("\n".join(OUT) + "\n")
    shutil.copy(HOME / ".ctx/log", EVIDENCE / "ctx-log-full.txt")
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
