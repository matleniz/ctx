#!/usr/bin/env python3
"""Objective checks of a long-run scenario folder: `python3 checks.py SCENARIO FOLDER` prints one JSON object.

Run by run_longrun.py in a child process (the folder holds model-written code): every check is isolated, a crash
or a hang of one check only fails that check. Nothing is written in FOLDER (PYTHONDONTWRITEBYTECODE is set by the
caller; this file never writes).
"""
import ast
import importlib.util
import io
import json
import math
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

STD = set(sys.stdlib_module_names)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def close(a, b, tol=1e-6):
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    try:
        return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
    except (TypeError, ValueError):
        return False


def raises(f, *args):
    try:
        f(*args)
    except ValueError:
        return True
    except Exception:  # noqa: BLE001 - any other error is a wrong behaviour
        return False
    return False


def tolist(x):
    try:
        return [tolist(v) for v in x] if not isinstance(x, (int, float)) else x
    except TypeError:
        return x


# ------------------------------------------------------------------ course-notebooks
EX = {
    "ex1_1": ("ch1/ex1_1.py", "mean", lambda f: close(f([1, 2, 3, 4]), 2.5) and raises(f, [])),
    "ex1_2": ("ch1/ex1_2.py", "variance", lambda f: close(f([1, 2, 3, 4]), 1.25) and raises(f, [])),
    "ex2_1": ("ch2/ex2_1.py", "normalize",
              lambda f: close(tolist(f([1, 2, 3])), [-1.224744871, 0.0, 1.224744871]) and raises(f, [2, 2])),
    "ex2_2": ("ch2/ex2_2.py", "moving_average",
              lambda f: close(tolist(f([1, 2, 3, 4], 2)), [1.5, 2.5, 3.5]) and raises(f, [1, 2], 3) and raises(f, [1], 0)),
    "ex3_1": ("ch3/ex3_1.py", "dot", lambda f: close(f([1, 2, 3], [4, 5, 6]), 32) and raises(f, [1], [1, 2])),
    "ex3_2": ("ch3/ex3_2.py", "matmul",
              lambda f: close(tolist(f([[1, 2], [3, 4]], [[5, 6], [7, 8]])), [[19, 22], [43, 50]])
              and raises(f, [[1, 2]], [[1, 2]])),
    "ex4_1": ("ch4/ex4_1.py", "softmax", lambda f: close(tolist(f([1000.0, 1000.0])), [0.5, 0.5])),
    "ex4_2": ("ch4/ex4_2.py", "cross_entropy",
              lambda f: close(f([1.0, 0.0], [0.5, 0.5]), math.log(2)) and raises(f, [1.0], [0.5, 0.5])),
}


def convention(path):
    """D1 of course-notebooks: no for/while statement inside a function, every argument and return annotated."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not funcs:
        return False
    for fn in funcs:
        if any(isinstance(n, (ast.For, ast.While, ast.AsyncFor)) for n in ast.walk(fn)):
            return False
        args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        if fn.returns is None or any(a.annotation is None for a in args):
            return False
    return True


def check_course(root):
    out, conv = {}, {}
    for item, (rel, fname, test) in EX.items():
        p = root / rel
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                out[item] = bool(test(getattr(load(p, item), fname)))
        except Exception:  # noqa: BLE001 - stub (NotImplementedError), syntax error, wrong name...
            out[item] = False
        if out[item]:
            conv[item] = convention(p)
    notes = (root / "notes.md").read_text(encoding="utf-8", errors="replace") if (root / "notes.md").exists() else ""
    low = notes.lower()
    out["notes_ch1"] = "variance" in low and bool(re.search(r"chapter 1|ch(apter)?\s*1\b|1\.1", low))
    out["notes_ch2"] = bool(re.search(r"normali[sz]|z-score", low)) and "moving" in low
    out["summary"] = bool(re.search(r"remain|to do|todo|not (yet )?done|left", low)) and bool(re.search(r"3\.2|4\.1|4\.2", low))
    return {"items": out, "convention": conv, "regression_ok": out["ex1_2"]}


# ------------------------------------------------------------------ small-project
def run_cli(root, *args):
    sample = root / "sample.csv"
    p = subprocess.run([sys.executable, "-m", "tally.cli", str(sample), *args], cwd=root, capture_output=True,
                       text=True, timeout=20)
    return p.returncode, p.stdout


def unit(root):
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=root, capture_output=True,
                       text=True, timeout=60)
    m = re.search(r"Ran (\d+) test", p.stderr)
    f = re.search(r"failures=(\d+)", p.stderr)
    e = re.search(r"errors=(\d+)", p.stderr)
    return {"ran": int(m.group(1)) if m else 0, "failures": int(f.group(1)) if f else 0,
            "errors": int(e.group(1)) if e else 0, "ok": p.returncode == 0 and bool(m)}


def check_project(root):
    sys.path.insert(0, str(root))
    out = {}

    def safe(name, fn):
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                out[name] = bool(fn())
        except Exception:  # noqa: BLE001
            out[name] = False

    def stats():
        return load(root / "tally" / "stats.py", "tally_stats")

    def parse():
        return load(root / "tally" / "parse.py", "tally_parse")

    safe("item1", lambda: close(stats().total([("a", 1.0), ("b", 2.0)]), 3.0))
    safe("item2", lambda: close(stats().average([("a", 1.0), ("b", 3.0)]), 2.0) and raises(stats().average, []))

    def j():
        rc, o = run_cli(root, "--json")
        d = json.loads(o)
        return rc == 0 and close(d["total"], 18.0) and close(d["by_name"]["ann"], 4.0)
    safe("item3", j)
    safe("item4", lambda: [tuple(r) for r in parse().parse_lines(["", "# note", "a,1"])] == [("a", 1.0)])

    def s():
        rc, o = run_cli(root, "--sort")
        names = [ln.split(":")[0].strip() for ln in o.splitlines() if ":" in ln and "TOTAL" not in ln.upper()]
        return rc == 0 and names[:1] == ["cid"] and set(names) == {"ann", "bob", "cid"}
    safe("item5", s)

    def pr():
        rc, o = run_cli(root, "--pretty")
        return rc == 0 and "cid" in o and bool(re.search(r"^[\s|+]*[-=─━]{3,}", o, re.M))
    safe("item6", pr)
    readme = (root / "README.md").read_text(encoding="utf-8", errors="replace") if (root / "README.md").exists() else ""
    opts = [o for o, it in (("--json", "item3"), ("--sort", "item5"), ("--pretty", "item6")) if out.get(it)]
    out["readme"] = bool(opts) and all(o in readme for o in opts)
    out["status"] = (root / "STATUS.md").exists() and len((root / "STATUS.md").read_text(errors="replace")) > 40
    safe("regression", lambda: tuple(parse().parse_line("ann, 2.5")) == ("ann", 2.5))
    third = []
    for f in sorted((root / "tally").glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else (
                [n.module] if isinstance(n, ast.ImportFrom) and n.module and not n.level else [])
            third += [m for m in mods if m.split(".")[0] not in STD and m.split(".")[0] != "tally"]
    return {"items": {k: out[k] for k in ("item1", "item2", "item3", "item4", "item5", "item6", "readme", "status")},
            "regression_ok": out["regression"], "third_party_imports": sorted(set(third)),
            "convention": {"stdlib_only": not third}, "tests": unit(root)}


if __name__ == "__main__":
    scen, folder = sys.argv[1], Path(sys.argv[2]).resolve()
    res = check_course(folder) if scen == "course-notebooks" else check_project(folder)
    print(json.dumps(res))
