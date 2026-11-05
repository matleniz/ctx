#!/usr/bin/env python3
"""install.sh + hooks + uninstall from a clean copy of the module, in a temporary HOME.

Never touches the real HOME. Evidence: $CTX_EVIDENCE_DIR/transcript.txt (default <tmp>/ctx-evidence/install). Usage: python3 tests/integration/run_install.py
"""
import filecmp
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MOD = Path(__file__).resolve().parents[2]
OUT = Path(os.environ["CTX_EVIDENCE_DIR"]) if os.environ.get("CTX_EVIDENCE_DIR") else Path(tempfile.gettempdir()) / "ctx-evidence" / "install"
LOG = []


def say(s):
    LOG.append(s)
    print(s)


def run(cmd, env, inp=None, ok=(0,)):
    r = subprocess.run(cmd, env=env, input=inp, capture_output=True, text=True, timeout=60)
    say(f"$ {' '.join(map(str, cmd))}" + (f"   # stdin={inp!r}" if inp else "") + f"\n{r.stdout}{r.stderr}[rc={r.returncode}]")
    assert r.returncode in ok, f"rc {r.returncode}"
    return r


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ctx-install-"))
    clone, home = tmp / "clone", tmp / "home"
    home.mkdir()
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", "."], cwd=MOD,
                           capture_output=True, text=True, check=True).stdout.split()
    for f in files:  # clean copy of the module content (ignored files left out)
        (clone / f).parent.mkdir(parents=True, exist_ok=True)
        (clone / f).write_bytes((MOD / f).read_bytes())
        (clone / f).chmod((MOD / f).stat().st_mode)
    env = dict(os.environ, HOME=str(home), CTX_HOME=str(home))
    env.pop("CTX_ROOT", None)
    ctx = [sys.executable, str(clone / "ctx.py")]
    say(f"clean copy: {clone} ({len(files)} files)\ntemporary HOME: {home}")

    # configs that already exist, with unrelated content, in the exact format ctx writes
    cfg = {".claude/settings.json": '{\n  "model": "x",\n  "hooks": {\n    "Stop": [\n      {\n        "hooks": [\n          {\n            "type": "command",\n            "command": "echo mine"\n          }\n        ]\n      }\n    ]\n  }\n}\n',
           ".cursor/hooks.json": '{\n  "version": 1,\n  "hooks": {\n    "stop": [\n      {\n        "command": "echo mine"\n      }\n    ]\n  }\n}\n'}
    for rel, txt in cfg.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(txt)
    # agy config absent on purpose: uninstall must leave no file
    link = home / ".local/bin/ctx"

    r1 = run(["sh", str(clone / "install.sh")], env)
    r2 = run(["sh", str(clone / "install.sh")], env)
    assert link.is_symlink() and link.resolve() == (clone / "ctx.py").resolve()
    assert "already in place" in r2.stdout and "linked" in r1.stdout
    assert "not in PATH" in r1.stdout or str(home / ".local/bin") in env["PATH"]
    run([str(link), "--help"], env)

    for cli, rel in (("claude", ".claude/settings.json"), ("cursor", ".cursor/hooks.json")):
        d = run(ctx + ["install-hooks", cli, "--dry-run"], env)
        assert "dry run" in d.stdout and (home / rel).read_text() == cfg[rel]
        run(ctx + ["install-hooks", cli, "--emit", str(tmp / f"{cli}.emit.json")], env)
        assert "hook" in (tmp / f"{cli}.emit.json").read_text()
        run(ctx + ["install-hooks", cli], env, inp="y\n")  # real write, confirmation answered
        assert (home / rel).read_text() != cfg[rel]
    run(ctx + ["install-hooks", "agy"], env, inp="y\n")
    assert (home / ".gemini/config/hooks.json").exists()
    run(ctx + ["install-hooks", "claude"], env, ok=(0,))  # idempotent: nothing to do
    snap = {p: p.read_bytes() for p in home.rglob("*") if p.is_file() and not p.name.endswith(".ctx-bak")}

    run(ctx + ["install-hooks", "--remove", "--dry-run"], env)
    assert link.is_symlink() and snap == {p: p.read_bytes() for p in snap}, "dry run changed something"
    run(ctx + ["install-hooks", "--remove"], env, inp="n\nn\nn\nn\n", ok=(0, 1))  # all refused: nothing changes
    assert link.is_symlink() and snap == {p: p.read_bytes() for p in snap}, "refused uninstall changed something"
    run(ctx + ["install-hooks", "--remove"], env, inp="y\ny\ny\ny\n")
    assert link.is_symlink()  # the link is left to the user
    for rel, txt in cfg.items():
        assert (home / rel).read_text() == txt, f"{rel} not byte-identical"
        assert not (home / (rel + ".ctx-bak")).exists()
    assert not (home / ".gemini/config/hooks.json").exists()
    run(ctx + ["install-hooks", "--remove", "--yes"], env)  # idempotent
    say("\nOK: install.sh twice (idempotent), dry-run/emit/real install-hooks, install-hooks --remove (dry run, refused, real); "
        "temp configs byte-identical to their initial state, agy file removed, link kept.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "transcript.txt").write_text("\n".join(LOG).replace(str(tmp), "$TMP") + "\n")


if __name__ == "__main__":
    main()
