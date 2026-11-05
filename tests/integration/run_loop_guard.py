#!/usr/bin/env python3
"""Loop guard: does CTX_INNER=1 set on the parent reach the hook processes of cursor-agent and agy?

Mirror only (project-level hooks written inside it). Control = no CTX_INNER: the ctx hook logs `session-start`.
With CTX_INNER=1 in the CLI's environment: no `session-start` line, no session dir. Evidence: $CTX_EVIDENCE_DIR/loopguard.txt (default <tmp>/ctx-evidence/loop-guard).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MOD = Path(__file__).resolve().parents[2]
EVIDENCE = Path(os.environ.get("CTX_EVIDENCE_DIR") or Path(tempfile.gettempdir()) / "ctx-evidence" / "loop-guard")
out = []
for cli in ("cursor", "agy"):
    for inner in (False, True):
        home = Path(f"/tmp/ctx-e4l3/lg-{cli}-{int(inner)}/home")
        subprocess.run([sys.executable, str(MOD / "tests/make_mirror.py"), str(home), "--init"], check=True, capture_output=True)
        node = home / "courses/ml-intro"
        e = {k: v for k, v in os.environ.items() if not k.startswith("CTX_")}
        e["CTX_HOME"] = str(home)
        hooks = node / {"cursor": ".cursor/hooks.json", "agy": ".agents/hooks.json"}[cli]
        hooks.parent.mkdir(exist_ok=True)
        subprocess.run([sys.executable, str(MOD / "ctx.py"), "install-hooks", cli, "--settings-file", str(hooks), "--yes"],
                       env=e, capture_output=True, check=True)
        if inner:
            e["CTX_INNER"] = "1"
        cmd = (["cursor-agent", "-p", "--trust", "--output-format", "json", "Reply ok."] if cli == "cursor"
               else ["agy", "--output-format", "json", "-p=Reply ok."])
        p = subprocess.run(cmd, cwd=node, capture_output=True, text=True, env=e, timeout=200)
        log = (home / ".ctx/log").read_text() if (home / ".ctx/log").exists() else ""
        starts = [ln for ln in log.splitlines() if " session-start " in ln]
        sess = list((home / ".ctx/sessions").iterdir()) if (home / ".ctx/sessions").is_dir() else []
        out.append(f"{cli:7} CTX_INNER={'1' if inner else '-'}  cli_exit={p.returncode}  session-start log lines={len(starts)}  "
                   f"session dirs={len(sess)}  -> hook {'ran' if starts else 'exited at once'}")
EVIDENCE.mkdir(parents=True, exist_ok=True)
(EVIDENCE / "loopguard.txt").write_text("\n".join(out) + "\n")
print("\n".join(out))
