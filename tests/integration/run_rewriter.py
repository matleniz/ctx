#!/usr/bin/env python3
"""One real headless session (T1-like), then the CONTEXT.md rewritten by a chosen rewriter, on a mirror.

Usage: python3 tests/integration/run_rewriter.py NAME SESSION_CLI REWRITER [MODEL]
  SESSION_CLI: claude | cursor | agy (hooks via the documented project-level file inside the mirror)
  REWRITER: value of `rewriter =` in the mirror's ~/.ctx/config ("default" writes no config); MODEL optional.
The real ~/.claude, ~/.cursor, ~/.gemini are only hashed before/after (never written). Evidence: $CTX_EVIDENCE_DIR/NAME.* (default <tmp>/ctx-evidence/rewriter).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MOD = Path(__file__).resolve().parents[2]
CTX = MOD / "ctx.py"
EVIDENCE = Path(os.environ.get("CTX_EVIDENCE_DIR") or Path(tempfile.gettempdir()) / "ctx-evidence" / "rewriter")
name, cli, rew = sys.argv[1:4]
model = sys.argv[4] if len(sys.argv) > 4 else ""
ROOT = Path("/tmp/ctx-e4l3") / name
HOME = ROOT / "home"
NODE = HOME / "courses/ml-intro"
PROMPT = ("In notes.md add a short section 'Ridge' with one sentence on L2 regularisation, and create exos/ex3.py "
          "defining ex3() that returns 3. Then answer 'done'.")


def env():
    e = {k: v for k, v in os.environ.items() if not k.startswith("CTX_")}
    e["CTX_HOME"] = str(HOME)
    return e


def ctx(*a):
    return subprocess.run([sys.executable, str(CTX), *a], capture_output=True, text=True, env=env(), timeout=300)


def real_hashes():
    out = {}
    for d in (".claude", ".cursor", ".gemini"):
        for p in sorted((Path.home() / d).rglob("*")) if (Path.home() / d).is_dir() else []:
            if p.is_file() and not any(x in p.parts for x in ("projects", "chats", "ai-tracking", "sessions", "history", "cache",
                                       "file-history", "shell-snapshots", "todos", "telemetry", "tmp", "antigravity-cli", "backups", "plugins")):
                try:
                    out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()[:10]
                except OSError:
                    pass
    return out


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(MOD / "tests/make_mirror.py"), str(HOME), "--init"], check=True, capture_output=True)
    before = real_hashes()
    if rew != "default":
        (HOME / ".ctx/config").write_text(f"rewriter = {rew}\n" + (f"model = {model}\n" if model else ""))
    hooks = {"claude": ".claude/settings.json", "cursor": ".cursor/hooks.json", "agy": ".agents/hooks.json"}[cli]
    (NODE / hooks).parent.mkdir(exist_ok=True)
    r = ctx("install-hooks", cli, "--settings-file", str(NODE / hooks), "--yes")
    old = (NODE / "CONTEXT.md").read_text()
    if cli == "claude":  # project-level hooks only: the user's own hooks are not loaded
        cmd = ["claude", "-p", "--model", "haiku", "--output-format", "json", "--permission-mode", "acceptEdits",
               "--setting-sources", "project,local", "--allowedTools", "Read,Edit,Write,Glob,Grep"]  # prompt on stdin: --allowedTools is variadic
    elif cli == "cursor":
        cmd = ["cursor-agent", "-p", "--trust", "--force", "--output-format", "json", PROMPT]
    else:
        allow = {"permissions": {"allow": ["command(ls)", "command(cat)", "command(find)", "command(grep)", "command(head)",
                                           f"read_file({HOME})", f"write_file({HOME})"]}}
        cmd = ["agy", "--mode", "accept-edits", "--output-format", "json", "--settings", json.dumps(allow), "-p=" + PROMPT]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=NODE, capture_output=True, text=True, env=env(), timeout=300,
                       input=PROMPT if cli == "claude" else None)
    (EVIDENCE / f"{name}.session.json").write_text(p.stdout + ("\n--stderr--\n" + p.stderr if p.stderr else ""))
    t_sess = round(time.time() - t0, 1)
    deadline = time.time() + 150
    while time.time() < deadline and (NODE / "CONTEXT.md").read_text() == old:
        time.sleep(3)
        if cli == "agy" and time.time() - t0 > t_sess + 5:  # no SessionEnd event: sweep like the next SessionStart would
            ctx("sync")
    new = (NODE / "CONTEXT.md").read_text()
    log = ctx("log", "-n", "40").stdout
    (EVIDENCE / f"{name}.CONTEXT.md").write_text(new)
    (EVIDENCE / f"{name}.log.txt").write_text(f"session cli={cli} rewriter={rew} model={model or '-'} session_secs={t_sess}\n"
                                            f"install-hooks: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr}\n\n{log}")
    after = real_hashes()
    (EVIDENCE / f"{name}.real-config-check.txt").write_text(
        "real configs unchanged\n" if before == after else f"CHANGED: {[k for k in set(before) | set(after) if before.get(k) != after.get(k)]}\n")
    print(name, "rewritten" if new != old else "NOT rewritten", t_sess, "s;", (EVIDENCE / f"{name}.real-config-check.txt").read_text().strip())


main()
