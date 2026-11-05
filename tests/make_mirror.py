#!/usr/bin/env python3
"""Build the mirror tree used by the ctx tests (fake content, real structure).

Usage: python3 tests/make_mirror.py [DEST] [--init]
DEST defaults to a new temporary directory's `home`. With --init, the nodes are created with
`ctx init` (CTX_HOME pointed at DEST). Existing DEST is wiped first, but only if it is
named 'home' (safety), so this never touches a real folder.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

FILES = {
    ".ctxignore": ".cache\n.config\nDownloads\nnode_modules\n.git\n",
    "courses/ml-intro/notes.md": "# Intro ML\n\nChapitre 3 : régression linéaire.\n",
    "courses/ml-intro/exos/ex1.py": "def ex1():\n    return 1\n",
    "courses/ml-intro/exos/ex2.py": "def ex2():\n    return 2\n",
    "courses/algebra/notes.md": "# Algèbre\n\nEspaces vectoriels.\n",
    "courses/algebra/tp/tp1.py": "print('tp1')\n",
    "courses/challenge/README.md": "# Challenge\n\nPrédire une valeur cible.\n",
    "courses/challenge/src/train.py": "def train():\n    pass\n",
    "courses/challenge/results/log.md": "# Résultats\n\nval loss 0.31, target 0.11\n",
    "projects/site/src/index.html": "<html><body>site</body></html>\n",
    "projects/site/README.md": "# Site\n",
    "projects/app/src/engine.py": "def run():\n    pass\n",
    "projects/app/docs/README.md": "# App\n",
    "notes/notes.md": "# Notes\n\nNotes diverses.\n",
    "scratch/truc/.keep": "",
}
# nodes in creation order (parents first); scratch/truc is deliberately NOT a node
NODES = ["", "courses", "courses/ml-intro", "courses/algebra", "courses/challenge",
         "projects", "projects/site", "projects/app", "notes"]
# containers and ~ are created with --no-absorb; project nodes absorb their non-node subfolders (default)
NO_ABSORB = {"", "courses", "projects"}
GIT_REPOS = ["projects/site", "projects/app"]


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _quiet_init(ctx, d, no_absorb=False):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        ctx.main(["init", str(d), *(["--no-absorb"] if no_absorb else [])])


def build(dest, init=False):
    dest = Path(dest)
    if dest.name != "home":
        raise SystemExit(f"refusing to build in {dest}: directory must be named 'home'")
    if dest.exists():
        shutil.rmtree(dest)
    for rel, content in FILES.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for rel in GIT_REPOS:
        repo = dest / rel
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "first commit")
        (repo / "CHANGELOG.md").write_text("# Changelog\n\n- initial\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "add changelog")
    if init:
        sys.path.insert(0, str(HERE.parent / "src"))
        os.environ["CTX_HOME"] = str(dest)
        import ctx
        for rel in NODES:
            _quiet_init(ctx, dest / rel, no_absorb=rel in NO_ABSORB)
    return dest


def add_bulk_nodes(home, parent="projects", n=25, summary_len=300):
    """T10 helper: n fake sub-nodes under `parent`, each with a summary of summary_len chars."""
    import ctx
    for i in range(n):
        d = Path(home) / parent / f"proj-{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "CONTEXT.md").exists():
            _quiet_init(ctx, d)
        doc = ctx.read_doc(d)
        doc.meta["summary"] = (f"Projet factice {i} : " + "x" * summary_len)[:summary_len]
        ctx.write_doc(d, doc)
    ctx.bubble(Path(home) / parent / "proj-00")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dest = args[0] if args else Path(tempfile.mkdtemp(prefix="ctx-mirror-")) / "home"
    out = build(dest, init="--init" in sys.argv)
    print(f"mirror built at {out}")
