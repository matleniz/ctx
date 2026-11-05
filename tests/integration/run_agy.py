#!/usr/bin/env python3
"""agy: T1 and T2 with REAL `agy -p` sessions, on the mirror only.

Reuses run_cursor.py (same checks, other launcher). Hooks come from the documented project-level
`<workspace>/.agents/hooks.json` written into the mirror folder where the session starts. agy has no documented
config-dir variable and keeps its login in ~/.gemini, so HOME is NOT redirected: the user config files of the real
~/.gemini (hooks.json, settings.json, GEMINI.md/AGENTS.md, config/) are hashed before/after; agy's own state
(history, antigravity-cli, projects.json) is only listed. Evidence goes to $CTX_EVIDENCE_DIR (default <tmp>/ctx-evidence/agy).

Usage: CTX_MIRROR=/tmp/ctx-agy/home python3 tests/integration/run_agy.py [--only T1,T2]
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
os.environ.setdefault("CTX_EVIDENCE_DIR", str(Path(tempfile.gettempdir()) / "ctx-evidence" / "agy"))
sys.path.insert(0, str(HERE))
import run_cursor as c  # noqa: E402

r = c.r
c.TAG = "agy"
REAL = Path.home() / ".gemini"
USER_CFG = ("hooks.json", "settings.json", "GEMINI.md", "AGENTS.md", "config/")


def real_hashes():
    out = {}
    for p in sorted(REAL.rglob("*")):
        rel = str(p.relative_to(REAL))
        if p.is_file() and rel.startswith(USER_CFG) and "/plugins/" not in rel:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    for n in ("GEMINI.md", "AGENTS.md"):  # GEMINI.md is a symlink to ~/.agents/AGENTS.md on this machine: hash the target too
        if (REAL / n).is_symlink() or (REAL / n).exists():
            out[n + " (resolved)"] = hashlib.sha256((REAL / n).resolve().read_bytes()).hexdigest()[:12]
    return out


def sess(name, cwd, prompt, resume=None):
    f = Path(cwd) / ".agents/hooks.json"
    if not f.exists():
        f.parent.mkdir(exist_ok=True)
        r.ctx("install-hooks", "agy", "--settings-file", str(f), "--yes")
    allow = {"permissions": {"allow": ["command(ls)", "command(cat)", "command(find)", "command(grep)", "command(head)",
                                       f"read_file({r.HOME})", f"write_file({r.HOME})"]}}  # session-only, mirror only
    cmd = ["agy", "--mode", "accept-edits", "--output-format", "json", "--settings", json.dumps(allow)]
    if resume:
        cmd += ["--conversation", resume]
    cmd.append("-p=" + prompt)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=r.env(), timeout=240)
    (c.EVIDENCE / f"{name}.session.json").write_text(p.stdout + ("\n--stderr--\n" + p.stderr if p.stderr else ""))
    try:
        j = json.loads(p.stdout)
    except ValueError:
        j = {"response": p.stdout + p.stderr}
    if name == "T1":  # agy has no SessionEnd event: the finished session is synced when the NEXT session starts (catch_up)
        sess("T1-next-launch", cwd, "Reply ok.")
        r.wait_idle()
    return j.get("response", ""), {"session_id": j.get("conversation_id")}, round(time.time() - t0, 1)


c.sess = sess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="T1,T2")
    a = ap.parse_args()
    c.EVIDENCE.mkdir(parents=True, exist_ok=True)
    for f in c.EVIDENCE.iterdir():  # keep the hand-written DOC.md / RESULTS.md
        if f.name not in ("DOC.md", "RESULTS.md"):
            f.unlink() if f.is_file() else shutil.rmtree(f)
    r.make_mirror.build(r.HOME, init=True)
    before = real_hashes()
    r.write("setup.txt", f"mirror {r.HOME}\nagy {subprocess.run(['agy','--version'],capture_output=True,text=True).stdout}"
            f"real ~/.gemini user config hashes (before): {json.dumps(before, indent=1)}\n")
    fns = {"T1": c.t1, "T2": c.t2}
    for t in a.only.split(","):
        try:
            fns[t]()
        except Exception as e:
            r.record(t, False, "-", f"exception {type(e).__name__}: {e}")
    after = real_hashes()
    r.write("real-gemini-untouched.txt", f"user config before==after: {before == after}\nchanged: {[k for k in set(before)|set(after) if before.get(k)!=after.get(k)]}\n")
    r.write("SUMMARY.txt", "\n".join(f"{t}: {'GREEN' if o else 'RED'} {p} {n}" for t, (o, p, n) in c.RESULTS.items()) + f"\nreal ~/.gemini user config untouched: {before == after}\n")
    shutil.copy(r.HOME / ".ctx/log", c.EVIDENCE / "ctx-log-full.txt")
    return 0 if all(o for o, _, _ in c.RESULTS.values()) and before == after else 1


if __name__ == "__main__":
    sys.exit(main())
