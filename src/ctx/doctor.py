"""Checks and fixes behind `ctx doctor`."""
import collections
import re
import shlex
import shutil
from pathlib import Path

from .paths import (
    CONFLICT_RE, MAX_NOTES, MAX_SUMMARY, MAX_TOTAL, PURGE_DAYS, SEC_NOTES, SEC_SUBS, STATUS_FAIL,
    clean, human_cmd, read_json, root, state_dir,
)
from .doc import parse, valid_shape
from .tree import find_foreign, parent_node
from .sessions import abandoned, iter_sessions, session_pending
from .rewriter import REWRITERS, config_warnings, effective_rewriter


def check_node(n: Path) -> list[str]:
    """Problems of one node.

    Shape, limits, ghost Subfolders lines, conflict copies. A container without child nodes and
    a node under a folder that is not a node are normal (see `node_notes`).
    """
    text = (n / "CONTEXT.md").read_text(errors="replace")
    d = parse(text)
    if not valid_shape(d):
        return [f"limits: {n}: malformed header/sections"]
    problems = []
    if len(text) > MAX_TOTAL:
        problems.append(f"limits: {n}: {len(text)} chars > {MAX_TOTAL}")
    if len(d.summary) > MAX_SUMMARY:
        problems.append(f"limits: {n}: summary {len(d.summary)} chars > {MAX_SUMMARY}")
    if len(d.sections.get(SEC_NOTES, "")) > MAX_NOTES:
        problems.append(f"limits: {n}: Notes over {MAX_NOTES} chars (truncated at injection)")
    for line in d.sections.get(SEC_SUBS, "").split("\n"):
        m = re.match(r"- \*\*(.+?)/\*\*", line)
        if m and not (n / m.group(1)).is_dir():
            problems.append(f"ghost: {n}: line for missing folder {m.group(1)}/")
    problems += [f"conflict: {n}/{c.name}: sync-tool copy, ignored by ctx "
                 "(merge it into CONTEXT.md by hand, then delete it)"
                 for c in sorted(n.iterdir()) if CONFLICT_RE.match(c.name)]
    return problems


def node_notes(n: Path) -> list[str]:
    """Info lines (not problems) on one node: its summary reaches no node above it."""
    if n == root() or parent_node(n):
        return []
    top = shlex.quote(str(root()))
    return [f"info: {n}: summary does not bubble above {n} (no node above it) "
            f"(run `{human_cmd()} init --no-absorb {top}` to collect the summaries at the root)"]


def foreign_notes() -> list[str]:
    """Info lines (not problems) for the CONTEXT.md files without the ctx marker (ignored)."""
    return [f"info: foreign: {folder}/CONTEXT.md is not a ctx file, ctx ignores it"
            for folder in find_foreign()]


def orphan_problems(gone: list[tuple[str, Path, str]]) -> list[str]:
    """Problems for the state/history entries of vanished folders."""
    return [f"ghost: state/history for vanished folder {clean(src)} (over {PURGE_DAYS} days): "
            f"`{human_cmd()} doctor --fix` removes it"
            for _k, _e, src in gone]


def fix_orphans(gone: list[tuple[str, Path, str]]) -> None:
    """Remove the state/history entries of vanished folders."""
    for _k, e, _src in gone:
        shutil.rmtree(e, ignore_errors=True) if e.is_dir() else e.unlink(missing_ok=True)
    entries = "entry" if len(gone) == 1 else "entries"
    print(f"ctx doctor: pruned {len(gone)} orphan {entries} (state of vanished folders)")


def check_config() -> list[str]:
    """Problems in ~/.ctx/config."""
    return [f"config: {warning}" for warning in config_warnings()]


def check_abandoned() -> list[str]:
    """Problems for the sessions given up after repeated failed syncs."""
    return [f"abandoned session: {clean(sid, 60)} ({clean(cause)}): "
            f"`{human_cmd()} sync --session {sid}` retries by hand"
            for sid, _cwd, cause in abandoned()]


def check_failed_syncs() -> list[str]:
    """Problems for the existing nodes whose last sync failed."""
    problems = []
    sdir = state_dir() / "state"
    for f in sdir.glob("*.json") if sdir.is_dir() else []:
        s = read_json(f, {})
        if s.get("status") == STATUS_FAIL and Path(s.get("path", "/")).exists():
            problems.append(f"failed sync: {clean(s.get('path'))} at {clean(s.get('time'))}: "
                            f"{clean(s.get('error'))}")
    return problems


def check_rewriter() -> list[str]:
    """Problem when the rewriter CLI is not in PATH."""
    if not shutil.which(REWRITERS["claude"]["cmd"][0]):
        return ["rewriter: `claude` not found in PATH: no rewrite can run"]
    return []


def print_report(problems: list[str], nodes: list[Path], entries: list) -> None:
    """Print the problems, then the state summary (sessions, last syncs, counters, rewriter, size).
    """
    for p in problems:
        print(p)
    given_up = {x[0] for x in abandoned()}
    pend = []
    for d in iter_sessions():
        pending = session_pending(d)
        if d.name not in given_up and (pending.files or pending.msgs):
            pend.append(d.name)
    listed = " (" + ", ".join(pend[:5]) + ")" if pend else ""
    print(f"ctx doctor: pending sessions={len(pend)}{listed}, abandoned={len(given_up)}")
    last = {}
    for t, e, kv in entries:
        if e == "sync" and "node" in kv:
            last[kv["node"]] = t
    for n, t in sorted(last.items(), key=lambda x: x[1]):
        print(f"ctx doctor: last sync {t:%Y-%m-%d %H:%M} {n}")
    cnt = collections.Counter(e for _t, e, _kv in entries)
    print(f"ctx doctor: retries triggered={cnt['rewrite-retry']} "
          f"(failed again={cnt['rewrite-retry-fail']}), "
          f"syncs={cnt['sync']}, trimmed={cnt['trimmed']}, failed syncs={cnt['sync-fail']}")
    size = sum(x.stat().st_size for x in state_dir().rglob("*") if x.is_file())
    exe = shutil.which(REWRITERS["claude"]["cmd"][0])
    try:
        rname, rmodel = effective_rewriter()
    except RuntimeError:  # unsupported config value: already listed above as a problem
        rname, rmodel = "claude", REWRITERS["claude"]["model"]
    print(f"ctx doctor: rewriter {rname} model {rmodel}; command "
          + (exe or "claude NOT FOUND"))
    print(f"ctx doctor: {state_dir()} uses {size / 1024:.0f} KiB")
    print(f"ctx doctor: {len(nodes)} nodes, {len(problems)} problem(s)")
