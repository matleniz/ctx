#!/usr/bin/env python3
"""README user journey with REAL `claude -p` (Haiku) sessions, on the mirror only.

discovery -> yes -> node created -> work (Write tool) -> sync -> three levels updated -> sibling isolated
-> ctx undo -> ctx install-hooks --remove (temporary HOME). CTX_HOME points at the mirror; hooks come from `claude --settings`
with `--setting-sources project,local` (the user's real hooks are left out). Evidence: $CTX_EVIDENCE_DIR (default <tmp>/ctx-evidence/readme).
Maps to docs/readme-claims.md (the `step_*` functions).

Usage: python3 tests/integration/run_readme.py [--model haiku]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("CTX_EVIDENCE_DIR", str(Path(tempfile.gettempdir()) / "ctx-evidence" / "readme"))
os.environ.setdefault("CTX_MIRROR", str(Path(tempfile.gettempdir()) / "ctx-readme" / "home"))
sys.path.insert(0, str(HERE))
import run_core_battery as r  # noqa: E402

HOME, CTX, EVIDENCE = r.HOME, r.CTX, r.EVIDENCE
BASH_CTX = f"Bash(python3 {CTX}:*)"
TOOLS = f"Read,Edit,Write,Glob,Grep,{BASH_CTX}"
SIBLINGS = ("algebra", "challenge", "algèbre")


def sess(name, cwd, prompt, model, resume=None, tools=TOOLS):
    """Like r.session but with resume support; evidence goes to <name>.session.json."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--permission-mode", "acceptEdits",
           *r.ISOLATE]
    cmd += ["--tools", ""] if tools == "" else ["--allowedTools", tools]  # variadic: keep it before the next flags
    cmd += ["--settings", str(r.SETTINGS)]
    if resume:
        cmd += ["--resume", resume]
    p = subprocess.run(cmd + [prompt], cwd=cwd, capture_output=True, text=True, env=r.env(), timeout=300)
    r.write(f"{name}.session.json", p.stdout + ("\n--stderr--\n" + p.stderr if p.stderr else ""))
    try:
        j = json.loads(p.stdout)
    except ValueError:
        j = {"result": p.stdout, "is_error": True}
    return j.get("result", ""), j


def step_discovery_yes(model):
    """Folder without node -> agent asks -> 'yes' -> CONTEXT.md exists."""
    truc = HOME / "scratch/truc"
    assert not (truc / "CONTEXT.md").exists()
    probe = subprocess.run([sys.executable, str(CTX), "hook", "session-start"], input=json.dumps(
        {"session_id": "probe", "cwd": str(truc)}), capture_output=True, text=True, env=r.env()).stdout
    shutil.rmtree(HOME / ".ctx/sessions/probe", ignore_errors=True)
    a1, j1 = sess("1-discovery", truc, "crée un fichier hello.txt", model)
    a2, _ = sess("1-yes", truc, "oui, crée le contexte ctx pour ce dossier comme la consigne [ctx] de début de session "
                 "l'indique (exécute la commande init indiquée), puis crée hello.txt (contenu: salut).",
                 model, resume=j1.get("session_id"))
    r.wait_idle()
    ok = "no ctx context" in probe and (truc / "CONTEXT.md").exists()
    r.write("1-discovery-yes.txt", f"hook output before:\n{probe}\n\nagent 1:\n{a1}\n\nagent 2:\n{a2}\n\n"
            f"CONTEXT.md created: {(truc / 'CONTEXT.md').exists()}\n{r.grab('scratch/truc/CONTEXT.md')}")
    return ok


def step_work_and_sync(model):
    """Work with the Write tool in a leaf node: three levels updated, and the next session receives the new state."""
    intro = HOME / "courses/ml-intro"
    before = {k: r.sha(HOME / k / "CONTEXT.md") for k in ("courses/ml-intro", "courses", "")}
    r.write("2-intro-before.md", r.grab("courses/ml-intro/CONTEXT.md"))
    ans, _ = sess("2-work", intro, "Avec l'outil Write, crée exos/ex3.py (régression logistique, une fonction) "
                  "puis réponds 'fait'.", model)
    r.wait_idle()
    after = {k: r.sha(HOME / k / "CONTEXT.md") for k in before}
    own = r.grab("courses/ml-intro/CONTEXT.md")
    l_cours, l_home = r.line_for("courses", "ml-intro"), r.line_for("", "courses")
    shown = r.ctx("show", str(intro))
    r.write("2-work-sync.txt", f"answer: {ans}\nhashes before {before}\nafter {after}\n--- ml-intro\n{own}\n"
            f"--- courses line\n{l_cours}\n--- home line\n{l_home}\n--- ctx show (next session)\n{shown}\n--- log\n{r.ctxlog()}")
    summ = next((ln for ln in own.splitlines() if ln.startswith("summary:")), "")
    return (all(before[k] != after[k] for k in before) and (HOME / "courses/ml-intro/exos/ex3.py").exists()
            and summ[8:].strip() in shown and l_cours != "" and l_home != "")


def step_sibling_isolated(model):
    """Sibling untouched by the work; its name is absent from what the session receives and from a context-only answer."""
    alg = "courses/algebra/CONTEXT.md"
    shown = r.ctx("show", str(HOME / "courses/ml-intro"))
    ans, _ = sess("3-isolation", HOME / "courses/ml-intro", "liste les autres cours que je suis", "haiku", tools="")
    r.write("3-isolation.txt", f"--- ctx show\n{shown}\n--- context-only answer\n{ans}\n--- sibling hash now {r.sha(HOME / alg)} "
            f"(was {SIBLING_HASH})\n")
    return (r.sha(HOME / alg) == SIBLING_HASH and not any(w in shown.lower() for w in SIBLINGS)
            and not any(w in ans.lower() for w in SIBLINGS))


def step_undo(_model):
    """`ctx undo` restores the version from before the session."""
    before = (EVIDENCE / "2-intro-before.md").read_text()
    out = r.ctx("undo", str(HOME / "courses/ml-intro"))
    now = r.grab("courses/ml-intro/CONTEXT.md")
    r.write("4-undo.txt", f"{out}\n--- restored\n{now}")
    key = lambda t: [ln for ln in t.splitlines() if not ln.startswith("updated:")]  # noqa: E731
    return key(now) == key(before)


def step_uninstall(_model):
    """Temporary HOME: install.sh, install-hooks, --remove; configs and CONTEXT.md files intact."""
    mod = CTX.parent
    with tempfile.TemporaryDirectory() as t:
        home = Path(t) / "home"
        (home / ".claude").mkdir(parents=True)
        cfg = home / ".claude/settings.json"
        cfg.write_text('{"theme": "dark"}\n')
        env = dict(os.environ, HOME=str(home))
        for k in ("CTX_HOME", "CTX_OFF", "CTX_INNER"):
            env.pop(k, None)
        link = home / ".local/bin/ctx"
        out = []

        def sh(*a, inp=None):
            p = subprocess.run(list(a), input=inp, capture_output=True, text=True, env=env, timeout=60)
            out.append(f"$ {' '.join(a)}\n{p.stdout}{p.stderr}")
            return p
        sh("sh", str(mod / "install.sh"))
        sh(str(link), "install-hooks", "claude", "--yes")
        hooked = "ctx" in cfg.read_text() and cfg.read_text() != '{"theme": "dark"}\n'
        keep = HOME / "CONTEXT.md"
        keep_bytes = keep.read_bytes()
        sh(str(link), "install-hooks", "--remove", "--yes")
        ok = (hooked and link.is_symlink() and cfg.read_text() == '{"theme": "dark"}\n'
              and keep.read_bytes() == keep_bytes)
        r.write("5-uninstall.txt", "\n".join(out).replace(t, "$TMP") + f"\nhooks were added: {hooked}\nOK: {ok}\n")
    return ok


SIBLING_HASH = ""


def main():
    global SIBLING_HASH
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    a = ap.parse_args()
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    r.make_mirror.build(HOME, init=True)
    r.ctx("install-hooks", "claude", "--settings-file", str(r.SETTINGS), "--yes")
    SIBLING_HASH = r.sha(HOME / "courses/algebra/CONTEXT.md")
    res = {}
    for fn in (step_discovery_yes, step_work_and_sync, step_sibling_isolated, step_undo, step_uninstall):
        try:
            res[fn.__name__] = bool(fn(a.model))
        except Exception as e:  # noqa: BLE001
            res[fn.__name__] = False
            print(f"{fn.__name__}: exception {type(e).__name__}: {e}")
        print(f"{fn.__name__}: {'GREEN' if res[fn.__name__] else 'RED'}")
    r.write("SUMMARY.txt", "\n".join(f"{k}: {'GREEN' if v else 'RED'}" for k, v in res.items()) + "\n")
    shutil.copy(HOME / ".ctx/log", EVIDENCE / "ctx-log-full.txt")
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
