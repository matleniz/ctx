#!/usr/bin/env python3
"""Replayable A/B of ctx on real folders: same read-only question WITH ctx and with CTX_OFF=1.

Per folder: sha256 manifest before/after (only CONTEXT.md files may change), `ctx init` if there is no
node, a read-only bootstrap session (Haiku) so the node gets its first CONTEXT.md, then the task asked
2x with ctx and 2x with CTX_OFF=1 (Haiku, read-only tools), and a blind Sonnet judge (one call per
answer, shuffled, no arm label, no tools) scoring 0-3 against the folder's readable files.

Usage: python3 tests/integration/run_ab_real_folders.py FOLDER... [--out DIR] [--mirror-settings FILE]
                                              [--runs 2] [--model haiku] [--judge-model sonnet]
Default: the real global hooks (~/.claude/settings.json). With --mirror-settings FILE the hooks come from
FILE via --settings and --setting-sources project,local (test tree: point CTX_HOME at it).

THREE-ARM MODE, enabled by --variants-dir DIR: per folder, 3 arms x --runs runs of the same
resume task: structure (hand-written CONTEXT.md that only lists files), state (CONTEXT.md following the content
rules), nothing (CTX_OFF=1). DIR/<folder name>.json is the per-folder spec, see variants-template.json next to
this file. Variants are written through the node's CONTEXT.md only (rewritten before every run, since the Stop
hook syncs it) and the original bytes are restored at the end. The judge ground truth is enlarged with
file/folder mtimes and the folder's Claude Code auto-memory (read-only, judge prompt only, never saved).
Output: OUT/<folder>/..., OUT/SUMMARY.md (quality, tokens, cache_read, variance, cache check).
  python3 tests/integration/run_ab_real_folders.py ~/courses/ml-intro ~/courses/challenge --variants-dir DIR --runs 3 --out /tmp/ctx-ab

Never touches git state, ~/.claude/settings.json, ~/.ctxignore or ~/.ctx/declined; declined folders are refused.
"""
import argparse
import hashlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CTX_DIR = Path(__file__).resolve().parents[2]
CTX_PY = CTX_DIR / "ctx.py"
BOOT_PROMPT = "fais le point sur ce dossier en 10 lignes"
TASK = ("Status et que faire ensuite ? Donne les 3 prochaines actions concrètes, "
        "avec les fichiers concernés.")
RO_TOOLS = "Read,Glob,Grep"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".ipynb_checkpoints", ".cache"}
SECRET_RE = re.compile(r"(^\.env|\.pem$|\.key$|id_rsa|id_ed25519|credential|secret|token|\.p12$|\.pfx$|\.kdbx$|\.netrc$)", re.I)
TEXT_EXT = {".md", ".txt", ".rst", ""}
TRUNC, TOTAL_MAX = 4000, 24000


# ------------------------------------------------------------------ pure parts
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root):
    """{relative path: sha256} of every file under root, except inside SKIP_DIRS-like VCS dir `.git`."""
    root = Path(root)
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ".git"]
        for fn in fns:
            p = Path(dp) / fn
            try:
                out[str(p.relative_to(root))] = sha256(p) if p.is_file() and not p.is_symlink() else "symlink"
            except OSError as e:
                out[str(p.relative_to(root))] = f"unreadable:{e.errno}"
    return out


def diff_manifest(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


def only_context_changed(d):
    return all(Path(k).name == "CONTEXT.md" for k in d["added"] + d["removed"] + d["changed"])


def ground_truth(root, trunc=TRUNC, total=TOTAL_MAX):
    """Readable facts about the folder: top-level listing, README*/AGENTS.md/notes text (truncated),
    notebook names, submissions/ listing. Never CONTEXT.md, never secret-looking files."""
    root = Path(root)
    parts, used = [], 0
    top = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir()
                 if p.name != "CONTEXT.md" and not SECRET_RE.search(p.name) and p.name != ".git")
    parts.append("## Top-level listing\n" + "\n".join(top))
    nbs, texts = [], []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not SECRET_RE.search(d))
        depth = len(Path(dp).relative_to(root).parts)
        if depth > 3:
            dns[:] = []
        for fn in sorted(fns):
            rel = (Path(dp) / fn).relative_to(root)
            if SECRET_RE.search(fn) or fn == "CONTEXT.md":
                continue
            if fn.endswith(".ipynb"):
                nbs.append(str(rel))
            elif depth <= 1 and Path(fn).suffix.lower() in TEXT_EXT and re.match(r"(readme|agents|notes?|todo|plan|claude)", fn, re.I):
                texts.append(rel)
    if nbs:
        parts.append("## Notebooks (names only)\n" + "\n".join(nbs[:60]))
    sub = root / "submissions"
    if sub.is_dir():
        parts.append("## submissions/ listing\n" + "\n".join(sorted(p.name for p in sub.iterdir())[:60]))
    for rel in texts:
        try:
            t = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(t) > trunc:
            t = t[:trunc] + "\n[...truncated]"
        block = f"## {rel}\n{t}"
        if used + len(block) > total:
            break
        used += len(block)
        parts.append(block)
    return "\n\n".join(parts)


ACTIONABILITY = {
    "resume": "- actionability: the 3 next actions are concrete and name the right files (0 = vague/absent, 3 = precise)\n",
    "specific": "- actionability: the question asked is answered directly and completely, naming the right files "
                "(0 = not answered, 3 = complete and precise)\n",
}


def build_judge_prompt(truth, question, answer, kind="resume", reference=None):
    """kind 'resume' (3 next actions) or 'specific' (a question answerable from the files); `reference` = an
    optional answer written from the files by the folder's owner, given to the judge as part of the truth."""
    ref = f"\n\n### Reference answer (written from the files by the folder's owner)\n{reference}" if reference else ""
    return (
        "You are a strict, impartial grader. A coding assistant was asked a question about a project folder. "
        "You only have the ground truth below (readable files of the folder) and its answer. No arm or tool "
        "information is given.\n\n"
        f"### Ground truth\n{truth}{ref}\n\n### Question\n{question}\n\n### Answer to grade\n{answer}\n\n"
        "Score the answer 0-3 on each criterion:\n"
        "- exactness: statements about the project state match the ground truth (0 = mostly wrong, 3 = all correct)\n"
        + ACTIONABILITY[kind] +
        "- no_invention: nothing is made up beyond what the ground truth supports (0 = much invented, 3 = nothing invented). "
        "Details not in the ground truth but plausible from files you cannot see count as mild invention only.\n"
        'Reply with ONLY a JSON object: {"exactness": n, "actionability": n, "no_invention": n, "comment": "one sentence"}')


def judge_with_retry(call, prompt):
    """Run the blind judge (`call(prompt) -> (result_text, raw)`); retry ONCE if its JSON does not parse.
    Returns (scores or None, raw of the last call)."""
    text, raw = call(prompt)
    scores = parse_scores(text)
    if scores is None:
        text, raw = call(prompt)
        scores = parse_scores(text)
    return scores, raw


def shuffle_items(items, seed):
    """items: list of dicts with an 'arm'. Returns [(blind_id, item)] in shuffled order; ids carry no arm info."""
    order = list(items)
    random.Random(seed).shuffle(order)
    return [(f"A{i + 1}", it) for i, it in enumerate(order)]


def parse_scores(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
        s = {k: int(j[k]) for k in ("exactness", "actionability", "no_invention")}
    except (ValueError, KeyError, TypeError):
        return None
    if not all(0 <= v <= 3 for v in s.values()):
        return None
    s["comment"] = str(j.get("comment", ""))
    return s


def total_in(u):
    return (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def summary_table(results):
    """results: {folder: [run dict with arm, turns, tokens_in, secs, scores|None]} -> markdown table."""
    head = ("| folder | arm | runs | exactness | actionability | no_invention | total /9 | turns | input tokens (incl. cache) | seconds |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for folder, runs in results.items():
        for arm in ("with_ctx", "without_ctx"):
            rs = [r for r in runs if r["arm"] == arm]
            sc = [r["scores"] for r in rs if r.get("scores")]
            col = lambda k: mean([s[k] for s in sc])  # noqa: E731
            tot = mean([s["exactness"] + s["actionability"] + s["no_invention"] for s in sc])
            rows.append(f"| {folder} | {arm} | {len(rs)} | {col('exactness')} | {col('actionability')} | "
                        f"{col('no_invention')} | {tot} | {mean([r['turns'] for r in rs])} | "
                        f"{mean([r['tokens_in'] for r in rs])} | {mean([r['secs'] for r in rs])} |")
    return head + "\n".join(rows) + "\n"


# ------------------------------------------------------------------ three-arm mode (pure parts)
ARMS3 = ("structure", "state", "nothing")


def load_spec(variants_dir, name):
    """DIR/<name>.json = {"structure": {"summary", "status": [..], "next": [..]}, "state": {...}}."""
    p = Path(variants_dir) / f"{name}.json"
    if not p.is_file():
        raise SystemExit(f"no variant spec for {name}: expected {p} (see tests/integration/variants-template.json)")
    spec = json.loads(p.read_text(encoding="utf-8"))
    for arm in ("structure", "state"):
        v = spec.get(arm) or {}
        if not v.get("summary") or not v.get("status") or not v.get("next"):
            raise SystemExit(f"{p}: arm '{arm}' needs summary, status[] and next[]")
    return spec


def variant_text(original, arm_spec):
    """CONTEXT.md text = the original header/sections with summary, Status and Next replaced by the spec
    (Notes and Subfolders untouched). `original` may be empty (then a minimal node is built)."""
    sys.path.insert(0, str(CTX_DIR / "src"))
    import ctx as c
    d = c.parse(original or "")
    d.meta.setdefault("summary", "")
    d.meta.setdefault("updated", "2026-01-01T00:00")
    d.meta["summary"] = " ".join(arm_spec["summary"].split())
    for sec in c.ORDER:
        d.sections.setdefault(sec, "")
    d.sections[c.SEC_STATUS] = "\n".join(f"- {x}" for x in arm_spec["status"])
    d.sections[c.SEC_NEXT] = "\n".join(f"- {x}" for x in arm_spec["next"])
    return c.render(d)


def write_variant(node, original_bytes, arm_spec):
    """Write the variant through the node's CONTEXT.md only; returns its size in bytes."""
    text = variant_text(original_bytes.decode("utf-8", "replace") if original_bytes else "", arm_spec)
    (Path(node) / "CONTEXT.md").write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def scrub_home(text):
    """Replace the user's home directory with ~ in text written to evidence files."""
    return text.replace(str(Path.home()), "~") if text else text


def _raise_on_signals():
    """Turn SIGTERM/SIGINT into SystemExit so `finally` blocks run; returns the previous handlers."""
    def h(sig, _frame):
        raise SystemExit(128 + sig)
    return {s: signal.signal(s, h) for s in (signal.SIGTERM, signal.SIGINT)}


def restore_context(node, original_bytes):
    """Put the original CONTEXT.md back byte for byte (original_bytes None = it did not exist: remove it)."""
    p = Path(node) / "CONTEXT.md"
    if original_bytes is None:
        p.unlink(missing_ok=True)
    else:
        p.write_bytes(original_bytes)


def size_ratio(a, b):
    return round(max(a, b) / max(1, min(a, b)), 2)


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"mean": None, "sd": None, "min": None, "max": None}
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 if len(xs) > 1 else 0.0
    return {"mean": round(m, 2), "sd": round(sd, 2), "min": min(xs), "max": max(xs)}


def quality(r):
    s = r.get("scores")
    return s["exactness"] + s["actionability"] + s["no_invention"] if s else None


def summary_table3(results):
    """results: {folder: [run dict: arm in ARMS3, turns, input_tokens, cache_read, cache_creation, secs, scores]}"""
    head = ("| folder | arm | runs | quality /9 (mean) | input tokens | cache_read | cache_creation | cache read+creation | cache rate % | turns | seconds |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|\n")
    rows, var = [], []
    for folder, runs in results.items():
        for arm in ARMS3:
            rs = [r for r in runs if r["arm"] == arm]
            q = stats([quality(r) for r in rs])
            m = lambda k: stats([r.get(k) for r in rs])["mean"]  # noqa: E731
            rows.append(f"| {folder} | {arm} | {len(rs)} | {q['mean']} | {m('input_tokens')} | {m('cache_read')} | "
                        f"{m('cache_creation')} | {stats([(r.get('cache_read') or 0) + (r.get('cache_creation') or 0) for r in rs])['mean']} | "
                        f"{cache_rate(runs, arm)} | {m('turns')} | {m('secs')} |")
            cr = stats([r.get("cache_read") for r in rs])
            var.append(f"- {folder} / {arm}: quality sd {q['sd']} (min-max {q['min']}-{q['max']}); "
                       f"cache_read sd {cr['sd']} (min-max {cr['min']}-{cr['max']})")
    return head + "\n".join(rows) + "\n\nVariance (sample sd over the runs of an arm):\n\n" + "\n".join(var) + "\n"


def cache_rate(runs, arm):
    """Pooled cache rate of an arm in %: cache_read / (cache_read + cache_creation); None without data."""
    rs = [r for r in runs if r["arm"] == arm and r.get("cache_read") is not None]
    cr, cc = sum(r["cache_read"] for r in rs), sum(r.get("cache_creation") or 0 for r in rs)
    return round(100 * cr / (cr + cc), 1) if cr + cc else None


def cache_check(results, tol=5.0):
    """Cache check: is the cache rate with state (b) lower than with nothing (c) by more than `tol` points? (lines, dropped).
    The rate, not the raw cache_read: fewer turns means less cache_read without any cache problem."""
    lines, dropped = [], False
    for folder, runs in results.items():
        rate = {arm: cache_rate(runs, arm) for arm in ARMS3}
        st, no = rate["state"], rate["nothing"]
        per_arm = ", ".join(f"{a}={rate[a]}%" for a in ARMS3)
        if st is None or no is None:
            lines.append(f"- {folder}: cache check n/a (missing data)")
            continue
        drop = st < no - tol
        dropped = dropped or drop
        lines.append(f"- {folder}: cache rate {per_arm}; state vs nothing {round(st - no, 1)} pts -> "
                     + (f"DROP (more than {tol} pts lower)" if drop else "ok (no drop)"))
    if dropped:
        lines.append("\nRecommendation (not applied, ctx.py untouched): keep a fixed section order and move the `updated` "
                     "field to the end of the header, so the injected prefix stays byte-stable between sessions.")
    return lines, dropped


def claude_memory_files(folder, root=None):
    """Claude Code auto-memory of a folder: <root or ~/.claude/projects>/<slug>/memory/*.md, slug = path with every
    non-alphanumeric char turned into '-'. [] when absent."""
    root = Path(root) if root else Path.home() / ".claude" / "projects"
    d = root / re.sub(r"[^A-Za-z0-9]", "-", str(Path(folder).resolve())) / "memory"
    try:
        return sorted(d.glob("*.md"))
    except OSError:
        return []


def enlarged_truth(folder, memory_root=None, listing_max=150):
    """ground_truth + dated names (mtime) of files/folders (depth <= 2, no secrets/CONTEXT.md) + the auto-memory text.
    The memory text goes into the judge prompt only (never written to the evidence)."""
    root = Path(folder)
    items = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not SECRET_RE.search(d))
        if len(Path(dp).relative_to(root).parts) >= 2:
            dns[:] = []
        for n in sorted(dns) + sorted(fns):
            if SECRET_RE.search(n) or n == "CONTEXT.md":
                continue
            p = Path(dp) / n
            try:
                items.append(f"{time.strftime('%Y-%m-%d', time.localtime(p.stat().st_mtime))}  "
                             f"{p.relative_to(root)}{'/' if p.is_dir() else ''}")
            except OSError:
                pass
    out = [ground_truth(root), "## Files and folders with modification dates\n" + "\n".join(items[:listing_max])]
    mem = []
    for f in claude_memory_files(root, memory_root):
        try:
            mem.append(f"### {f.name}\n{f.read_text(encoding='utf-8', errors='replace')[:3000]}")
        except OSError:
            pass
    if mem:
        out.append("## Claude Code's own memory of this folder (user-validated facts)\n" + "\n\n".join(mem))
    return "\n\n".join(out)


# ------------------------------------------------------------------ impure parts
def base_env(ctx_off):
    env = dict(os.environ)
    env.pop("CTX_OFF", None)
    if ctx_off:
        env["CTX_OFF"] = "1"
    return env


def claude(prompt, cwd, model, env, tools, settings=None, timeout=300):
    """claude -p with the prompt on STDIN (variadic --allowedTools would swallow a positional prompt)."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "json"]
    if tools:
        cmd += ["--tools", tools, "--allowedTools", tools]
    else:
        cmd += ["--tools", ""]
    if settings:
        cmd += ["--settings", str(settings), "--setting-sources", "project,local"]
    t0 = time.time()
    p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
    secs = round(time.time() - t0, 1)
    try:
        j = json.loads(p.stdout)
    except ValueError:
        j = {"result": p.stdout, "is_error": True}
    if p.returncode and not j.get("result"):
        raise SystemExit(f"claude failed (rc={p.returncode}): {p.stderr.strip()[:300]}")
    return j, secs, p.stdout + ("\n--stderr--\n" + p.stderr if p.stderr else "")


def log_path():
    return Path(os.environ.get("CTX_HOME") or os.path.expanduser("~")).resolve() / ".ctx" / "log"


def log_size():
    try:
        return log_path().stat().st_size
    except OSError:
        return 0


def wait_sync(node, offset, timeout=240):
    """Wait for a `sync`/`skip`/`sync-fail` log line about node written after offset. Returns it or None."""
    end = time.time() + timeout
    needle = f"node={node}"
    while time.time() < end:
        try:
            with open(log_path(), "rb") as f:
                f.seek(offset)
                for ln in f.read().decode("utf-8", "replace").splitlines():
                    if needle in ln and re.search(r" (sync|sync-light|skip|sync-fail) ", ln):
                        time.sleep(2)  # let the bubble-up finish
                        return ln
        except OSError:
            pass
        time.sleep(2)
    return None


def is_declined(folder):
    os.environ.setdefault("CTX_HOME", os.environ.get("CTX_HOME", ""))
    if not os.environ.get("CTX_HOME"):
        os.environ.pop("CTX_HOME", None)
    sys.path.insert(0, str(CTX_DIR / "src"))
    import ctx as ctxmod
    return ctxmod.is_declined(Path(folder).resolve())


def run_folder(folder, out, a):
    folder = Path(folder).expanduser().resolve()
    name = folder.name
    od = out / name
    od.mkdir(parents=True, exist_ok=True)
    if is_declined(folder):
        raise SystemExit(f"{folder} is declined (~/.ctx/declined): refusing")
    before = manifest(folder)
    (od / "manifest-before.json").write_text(json.dumps(before, indent=1, sort_keys=True))
    env_on, env_off = base_env(False), base_env(True)
    st = a.mirror_settings
    notes = []
    if not (folder / "CONTEXT.md").exists():
        p = subprocess.run([sys.executable, str(CTX_PY), "init", str(folder)], capture_output=True, text=True, env=env_on)
        notes.append(f"ctx init rc={p.returncode} {p.stdout.strip()} {p.stderr.strip()}")
        if p.returncode:
            raise SystemExit(f"ctx init failed on {folder}: {p.stderr}")
    # bootstrap (read-only, ctx on): fills the node's first CONTEXT.md through the Stop/SessionEnd sync
    off = log_size()
    j, secs, raw = claude(BOOT_PROMPT, folder, a.model, env_on, RO_TOOLS, st)
    (od / "bootstrap.json").write_text(raw)
    line = wait_sync(str(folder), off, a.sync_timeout)
    notes.append(f"bootstrap {secs}s sync-line: {line or 'NONE (timeout)'}")
    ctx_md = (folder / "CONTEXT.md").read_text() if (folder / "CONTEXT.md").exists() else ""
    (od / "CONTEXT.produced.md").write_text(ctx_md)
    # task runs, interleaved to spread cache/time effects
    runs = []
    for i in range(1, a.runs + 1):
        for arm, env in (("with_ctx", env_on), ("without_ctx", env_off)):
            off = log_size()
            j, secs, raw = claude(TASK, folder, a.model, env, RO_TOOLS, st)
            (od / f"{arm}.{i}.json").write_text(raw)
            (od / f"{arm}.{i}.answer.txt").write_text(j.get("result", "") or "")
            if arm == "with_ctx":
                wait_sync(str(folder), off, 60)
            runs.append({"arm": arm, "run": i, "turns": j.get("num_turns"), "tokens_in": total_in(j.get("usage", {})),
                         "secs": secs, "answer": j.get("result", "") or "", "error": bool(j.get("is_error")),
                         "scores": None})
    # blind judging
    truth = ground_truth(folder)
    (od / "ground-truth.txt").write_text(truth)
    seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    mapping = {}
    with tempfile.TemporaryDirectory() as tmp:
        for bid, r in shuffle_items(runs, seed):
            prompt = build_judge_prompt(truth, TASK, r["answer"])
            def call(pr):
                jj, _, raw = claude(pr, tmp, a.judge_model, env_off, "", None)
                return jj.get("result", ""), raw
            r["scores"], raw = judge_with_retry(call, prompt)
            (od / f"judge.{bid}.json").write_text(raw)
            mapping[bid] = f"{r['arm']}.{r['run']}"
    (od / "judge-mapping.json").write_text(json.dumps(mapping, indent=1))
    after = manifest(folder)
    d = diff_manifest(before, after)
    (od / "manifest-diff.json").write_text(json.dumps(d, indent=1))
    ok = only_context_changed(d)
    (od / "run.json").write_text(json.dumps({"notes": notes, "manifest_ok": ok, "runs": runs}, indent=1, ensure_ascii=False))
    return {"runs": runs, "notes": notes, "manifest_ok": ok, "diff": d, "context": ctx_md, "name": name}


def ancestors_contexts(folder):
    """{path: bytes} of the CONTEXT.md files above the node up to the ctx base (a bubble-up may rewrite them)."""
    sys.path.insert(0, str(CTX_DIR / "src"))
    import ctx as c
    base, out, p = c.base(), {}, Path(folder).parent
    while True:
        f = p / "CONTEXT.md"
        if f.is_file():
            out[f] = f.read_bytes()
        if p == base or p == p.parent:
            break
        p = p.parent
    return out


def run_folder3(folder, out, a):
    folder = Path(folder).expanduser().resolve()
    name = folder.name
    od = out / name
    od.mkdir(parents=True, exist_ok=True)
    if is_declined(folder):
        raise SystemExit(f"{folder} is declined (~/.ctx/declined): refusing")
    spec = load_spec(a.variants_dir, name)
    env_on, env_off = base_env(False), base_env(True)
    st = a.mirror_settings
    notes, anc, runs, diff_mid = [], {}, [], None
    orig = (folder / "CONTEXT.md").read_bytes() if (folder / "CONTEXT.md").exists() else None  # None: remove what init creates
    old_handlers = _raise_on_signals()
    try:
        if orig is None:
            p = subprocess.run([sys.executable, str(CTX_PY), "init", str(folder)], capture_output=True, text=True, env=env_on)
            notes.append(f"ctx init rc={p.returncode} {p.stdout.strip()} {p.stderr.strip()}")
            if p.returncode:
                raise SystemExit(f"ctx init failed on {folder}: {p.stderr}")
        # `ctx init` was only needed so hooks find a node; the base text for the variants is the original (or the fresh node)
        base_bytes = orig if orig is not None else (folder / "CONTEXT.md").read_bytes()
        before = manifest(folder)
        before_ok = before if orig is not None else {k: v for k, v in before.items() if k != "CONTEXT.md"}
        (od / "manifest-before.json").write_text(json.dumps(before_ok, indent=1, sort_keys=True))
        anc = ancestors_contexts(folder)
        sizes = {arm: write_variant(folder, base_bytes, spec[arm]) for arm in ("structure", "state")}
        ratio = size_ratio(sizes["structure"], sizes["state"])
        notes.append(f"variant sizes (bytes): structure={sizes['structure']} state={sizes['state']} ratio={ratio}"
                     + ("  WARNING: not the same size" if ratio > 1.25 else ""))
        (od / "CONTEXT.structure.md").write_text(variant_text(base_bytes.decode("utf-8", "replace"), spec["structure"]))
        (od / "CONTEXT.state.md").write_text(variant_text(base_bytes.decode("utf-8", "replace"), spec["state"]))
        for i in range(1, a.runs + 1):
            order = list(ARMS3[(i - 1) % 3:] + ARMS3[:(i - 1) % 3])  # rotate the order to spread cache/time effects
            for arm in order:
                if arm != "nothing":
                    write_variant(folder, base_bytes, spec[arm])
                off = log_size()
                j, secs, raw = claude(TASK, folder, a.model, env_off if arm == "nothing" else env_on, RO_TOOLS, st)
                (od / f"{arm}.{i}.json").write_text(scrub_home(raw))
                (od / f"{arm}.{i}.answer.txt").write_text(scrub_home(j.get("result", "") or ""))
                if arm != "nothing":
                    wait_sync(str(folder), off, 60)
                u = j.get("usage", {})
                runs.append({"arm": arm, "run": i, "turns": j.get("num_turns"), "secs": secs,
                             "input_tokens": u.get("input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
                             "cache_creation": u.get("cache_creation_input_tokens", 0), "tokens_in": total_in(u),
                             "answer": scrub_home(j.get("result", "") or ""), "error": bool(j.get("is_error")), "scores": None})
        diff_mid = diff_manifest(before_ok, manifest(folder))
    finally:
        restore_context(folder, orig)
        for f, b in anc.items():
            if f.read_bytes() != b:
                f.write_bytes(b)
                notes.append(f"restored ancestor {f} (bubble-up)")
        for sig, h in old_handlers.items():
            signal.signal(sig, h)
    after = manifest(folder)
    d = diff_manifest(before_ok, after)
    identical = not (d["added"] or d["removed"] or d["changed"])
    (od / "manifest-diff.json").write_text(json.dumps({"during_runs": diff_mid, "after_restore": d}, indent=1))
    ok = only_context_changed(diff_mid or {"added": [], "removed": [], "changed": []}) and identical
    # blind judging, enlarged truth (memory text only used here, never written)
    truth = enlarged_truth(folder, a.memory_root)
    has_mem = "Claude Code's own memory" in truth
    notes.append(f"auto-memory in ground truth: {'yes' if has_mem else 'no (absent)'}")
    (od / "ground-truth.txt").write_text(ground_truth(folder) + "\n\n[dated listing and Claude Code memory are in the judge prompt only]\n")
    seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    mapping = {}
    with tempfile.TemporaryDirectory() as tmp:
        for bid, r in shuffle_items(runs, seed):
            prompt = build_judge_prompt(truth, TASK, r["answer"])

            def call(pr):
                jj, _, raw = claude(pr, tmp, a.judge_model, env_off, "", None)
                return jj.get("result", ""), raw
            r["scores"], raw = judge_with_retry(call, prompt)
            (od / f"judge.{bid}.json").write_text(scrub_home(raw))
            mapping[bid] = f"{r['arm']}.{r['run']}"
    (od / "judge-mapping.json").write_text(json.dumps(mapping, indent=1))
    (od / "run.json").write_text(json.dumps({"notes": notes, "manifest_ok": ok, "restored_identical": identical,
                                             "runs": runs}, indent=1, ensure_ascii=False))
    return {"runs": runs, "notes": notes, "manifest_ok": ok, "diff": d, "name": name, "identical": identical}


def main3(a, out):
    res, md = {}, []
    for f in a.folders:
        r = run_folder3(f, out, a)
        res[r["name"]] = r["runs"]
        md.append(f"## {r['name']}\n\n- manifest: {'OK, only CONTEXT.md changed during the runs' if r['manifest_ok'] else 'FAIL ' + json.dumps(r['diff'])}\n"
                  f"- original CONTEXT.md restored byte-identical: {r['identical']}\n"
                  + "".join(f"- {n}\n" for n in r["notes"]) + "\nVerbatim answers:\n")
        for x in r["runs"]:
            md.append(f"### {r['name']} / {x['arm']} / run {x['run']}  (quality {quality(x)}, turns {x['turns']}, "
                      f"cache_read {x['cache_read']})\n\n{x['answer']}\n")
    cl, dropped = cache_check(res)
    (out / "SUMMARY.md").write_text(
        "# A/B summary: structure vs state vs nothing\n\nTask: " + TASK + f"\n\nModel {a.model}, judge {a.judge_model} "
        "(blind, shuffled, enlarged ground truth), quality = exactness+actionability+no_invention (0-9), mean of "
        f"{a.runs} runs per arm.\n\n" + summary_table3(res) + "\n## Cache check\n\n" + "\n".join(cl) + "\n\n" + "\n".join(md))
    print((out / "SUMMARY.md").read_text())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "ctx-evidence" / "ab-real-folders"))
    ap.add_argument("--mirror-settings", help="hooks-only settings file (test tree); default: real global hooks")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--variants-dir", help="three-arm mode: DIR/<folder name>.json per folder (see variants-template.json)")
    ap.add_argument("--memory-root", help="override ~/.claude/projects (judge ground truth: Claude Code auto-memory)")
    ap.add_argument("--sync-timeout", type=int, default=240)
    a = ap.parse_args(argv)
    if a.mirror_settings:
        a.mirror_settings = str(Path(a.mirror_settings).resolve())  # claude runs with cwd = the folder
        if not Path(a.mirror_settings).is_file():
            raise SystemExit(f"settings file not found: {a.mirror_settings}")
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.variants_dir:
        return main3(a, out)
    res, md = {}, []
    for f in a.folders:
        r = run_folder(f, out, a)
        res[r["name"]] = r["runs"]
        md.append(f"## {r['name']}\n\n- manifest: {'OK, only CONTEXT.md changed' if r['manifest_ok'] else 'FAIL ' + json.dumps(r['diff'])}\n"
                  + "".join(f"- {n}\n" for n in r["notes"])
                  + f"\nCONTEXT.md produced:\n\n```\n{r['context']}\n```\n")
    (out / "SUMMARY.md").write_text("# A/B summary\n\nTask: " + TASK + f"\n\nModel {a.model}, judge {a.judge_model} "
                                    "(blind, shuffled), scores 0-3, mean of runs.\n\n" + summary_table(res) + "\n" + "\n".join(md))
    print((out / "SUMMARY.md").read_text())


if __name__ == "__main__":
    main()
