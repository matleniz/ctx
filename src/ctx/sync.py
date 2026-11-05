"""Node lock, rewrite, sync-light, session sync, bash scan."""
import collections
import contextlib
import fnmatch
import json
import os
import re
import time
from pathlib import Path
from typing import Callable

from .paths import (
    CONFLICT_RE, EMPTY_BODIES, FILES_MAX, MARKER, MAX_NEXT, MAX_NEXT_LINES,
    MAX_STATUS, MAX_STATUS_LINES, PLACEHOLDERS, MAX_SUMMARY, MAX_TOTAL, NODE_LOCK_WAIT, SCAN_MAX,
    SCAN_SKIP_DIRS, SCAN_SKIP_FILES, SCAN_SLACK, SEC_DECISIONS, SEC_NEXT, SEC_NOTES, SEC_STATUS,
    SEC_SUBS, atomic_write, clip, dirlock, log, real, root, set_session, stamp,
)
from .filters import filter_lines, redact
from .doc import Doc, get_status, is_auto, merge_decisions, numbered, parse, read_doc, render
from .doc import own_version, set_status, write_doc, written_doc
from .tree import accepts, bubble, build_subs, has_ctx, is_ignored, nearest_node
from .sessions import Pending, session_dir_of, session_pending
from .rewriter import (
    DECISION_RE, ModelOutput, SYSTEM_PROMPT, build_prompt, cli_rewriter, git_diffstat,
    parse_model_output, trim_section,
)

MAX_CONFLICTS = 3  # attempts of one sync_node while the file keeps changing under it
NEXT_RE = re.compile(r"^[ \t]*(?:[-*][ \t]*)?(?:next|prochaine étape)[ \t]*:[ \t]*(.+)$",
                     re.I | re.M)


def sync_node(node: str | Path, files: list[str], msgs: list[str], rewriter: Callable) -> str:
    """Rewrite the node under its lock. Returns 'ok', 'none', 'fail' or 'busy'.

    A second sync of the node waits for the first (NODE_LOCK_WAIT), then starts from the file it
    left; 'busy' when the wait runs out: the session's work stays pending for the next catch-up.
    """
    node = real(node)
    with dirlock(node, timeout=NODE_LOCK_WAIT) as got:
        if not got:
            log("sync-busy", node=node, why=f"another sync held the node {NODE_LOCK_WAIT}s")
            return "busy"
        for _ in range(MAX_CONFLICTS):
            result = _rewrite_locked(node, files, msgs, rewriter)
            if result != "conflict":
                return result
    set_status(node, False, "CONTEXT.md kept changing during the sync")
    return "fail"


def mark_synced(node: Path, worked: bool) -> None:
    """Record a successful sync; `worked` (files touched) dates the node's state.

    The state's `worked` is the time of the last sync with touched files; False while only
    read-only bootstraps happened.
    """
    prior = get_status(node) or {}
    set_status(node, True, worked=stamp() if worked else prior.get(
        "worked", None if "status" in prior else False))


def sync_light(node: Path, sid: str, msgs: list[str]) -> str:
    """A session that changed no file of the node: no model call. Returns 'ok', 'none' or 'busy'.

    The reports' `decision:` lines go to Decisions (merge_decisions) and their `next:` (or
    `prochaine étape:`) lines replace Next, through the same filters as a model answer.
    """
    text = "\n".join(msgs)
    decisions, k = filter_lines("\n".join(x.strip() for x in DECISION_RE.findall(text)), False)
    nxt, k2 = filter_lines("\n".join("- " + x.strip() for x in NEXT_RE.findall(text)))
    decisions = [x for x in decisions.split("\n") if x.strip()]
    nxt = trim_section(nxt.strip(), MAX_NEXT_LINES, MAX_NEXT) if nxt.strip() else ""
    if not decisions and not nxt:
        log("skip", node=node, session=sid,
            why="no file routed to node, no decision: or next: line in the reports")
        return "none"
    with dirlock(node, timeout=NODE_LOCK_WAIT) as got:
        if not got:
            log("sync-busy", node=node, why=f"another sync held the node {NODE_LOCK_WAIT}s")
            return "busy"
        doc = merged_base(node, read_doc(node), trust_file=True)
        doc.meta.setdefault(MARKER, "1")
        if nxt:
            doc.sections[SEC_NEXT] = nxt
        merged, dropped = merge_decisions(doc.sections.get(SEC_DECISIONS, ""), decisions)
        if merged or SEC_DECISIONS in doc.sections:
            doc.sections[SEC_DECISIONS] = merged
        for line in dropped:
            log("decision-dropped", node=node, why="over the Decisions cap", line=line)
        doc.sections[SEC_SUBS] = build_subs(node)
        changed = write_doc(node, doc)
        mark_synced(node, worked=False)
    log("sync-light", node=node, decisions=len(decisions), next=bool(nxt), changed=changed)
    if k + k2:
        log("filtered", node=node, count=k + k2, why="secrets/instruction-shaped lines")
    bubble(node)
    return "ok"


def _rewrite_locked(node, files, msgs, rewriter):
    """Rewrite the node; the caller holds its lock. 'ok', 'none', 'fail' or 'conflict'."""
    started = time.time()
    p = Path(node) / "CONTEXT.md"
    own = [f for f in files if os.path.basename(f) == "CONTEXT.md"]
    files = [f for f in files if f not in own]
    try:
        old_text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "fail"
    old = parse(old_text)
    diffstat = git_diffstat(node)
    if not files and not msgs and not diffstat and not own:
        bubble(node)
        return "none"
    prompt, redactions = rewrite_prompt(node, old, files, msgs, diffstat)
    subs = build_subs(node)
    trims = []
    writer = "model"
    try:
        output = adopt_agent_state(node, old, msgs, subs, trims) if own else None
        if output is not None:
            writer, info = "agent", {"in": 0, "out": 0, "secs": 0}
        else:
            output, info = ask_model(node, rewriter, prompt, subs, trims)
        changed = apply_rewrite(node, old, output, trims, own=bool(own))
        if changed is None:
            log("sync-conflict", node=node, why="Status or Next edited during the sync: redone "
                "from the new file")
            return "conflict"
        mark_synced(node, worked=bool(files))
    except Exception as e:  # invalid, model or write failure: keep the old file, count as failed
        with contextlib.suppress(Exception):
            set_status(node, False, f"{type(e).__name__}: {e}")
        log("sync-fail", node=node, error=f"{type(e).__name__}: {e}")
        return "fail"
    log("sync", node=node, files=len(files), changed=changed,
        writer=writer, tokens_in=info.get("in", 0), tokens_out=info.get("out", 0),
        model_secs=info.get("secs", 0), total_secs=round(time.time() - started, 1))
    if output.filtered + redactions:
        log("filtered", node=node, count=output.filtered + redactions,
            why="secrets/instruction-shaped lines")
    try:
        bubble(node)
    except Exception as e:  # the node is written: a parent refresh error must not redo the call
        log("bubble-error", node=node, error=f"{type(e).__name__}: {e}")
    return "ok"


def agent_state_changed(node: str | Path, cur: Doc) -> bool:
    """True when Status or Next of the file differ from what ctx last wrote."""
    w = written_doc(node)
    return w is not None and any(cur.sections.get(n, "").strip() != w.sections.get(n, "").strip()
                                 for n in (SEC_STATUS, SEC_NEXT))


def adopt_agent_state(node: Path, cur: Doc, msgs: list[str], subs: str,
                      trims: list[str]) -> ModelOutput | None:
    """The state the agent of the session wrote into its node's CONTEXT.md; None: call the model.

    Only summary, Status and Next are taken, through the validator of a model answer; `decision:`
    lines of the reports go to Decisions; a summary the agent left unchanged is rebuilt from the
    first Status and Next lines. Everything else comes from ctx's last version (merged_base).
    """
    if not agent_state_changed(node, cur):
        return None
    status = cur.sections.get(SEC_STATUS, "").strip()
    nxt = cur.sections.get(SEC_NEXT, "").strip()
    if status in EMPTY_BODIES or nxt in EMPTY_BODIES:
        log("agent-state-rejected", node=node, why="Status or Next left empty or placeholder")
        return None
    w = written_doc(node)
    summary = " ".join(cur.summary.split())
    if not summary or summary in PLACEHOLDERS or summary == w.summary:
        first = lambda body: body.split("\n")[0].lstrip("-* ").strip()  # noqa: E731
        summary = clip(f"{first(status)}; next: {first(nxt)}", MAX_SUMMARY)
    decisions = "\n".join(x.strip() for m in msgs for x in DECISION_RE.findall(m))
    text = f"SUMMARY: {summary}\n## Status\n{status}\n## Next\n{nxt}\n{decisions}\n"
    try:
        return parse_model_output(text, subs, trims)
    except ValueError as e:
        trims.clear()
        log("agent-state-rejected", node=node, why=e)
        return None


def rewrite_prompt(node: Path, old: Doc, files: list[str], msgs: list[str],
                   diffstat: str) -> tuple[str, int]:
    """The rewrite prompt and the number of redactions made in it.

    Inputs: the old Status/Next and summary, the reports, the diff stat and the files, all redacted.
    """
    rel = [os.path.relpath(f, node) for f in files[:FILES_MAX]]
    redactions = 0
    sec_view = Doc()
    # never show a placeholder/computed summary
    sec_view.meta = {"summary": "" if is_auto(old) else old.summary}
    shown = (SEC_STATUS, SEC_NEXT, SEC_DECISIONS)
    sec_view.sections = {s: numbered(v) if s == SEC_DECISIONS else v
                         for s, v in old.sections.items() if s in shown}
    parts = []
    for text in (render(sec_view), "\n---\n".join(msgs), diffstat, "\n".join(rel)):
        t, k = redact(text)
        redactions += k
        parts.append(t)
    old_s, msgs_s, diff_s, rel_s = parts
    prompt = build_prompt(old_s, rel_s.split("\n") if rel_s else [],
                          msgs_s.split("\n---\n") if msgs_s else [], diff_s)
    return prompt, redactions


def ask_model(node: Path, rewriter: Callable, prompt: str, subs: str,
              trims: list[str]) -> tuple[ModelOutput, dict]:
    """Call the model, with one retry naming the violation if the output is rejected.

    Returns (parsed output, call info).
    """
    text, info = rewriter(SYSTEM_PROMPT, prompt)
    try:
        return parse_model_output(text, subs, trims), info
    except ValueError as first:
        # other violations (secrets, copied subfolder state, missing sections...): one retry;
        # over-bounds is trimmed, not retried
        trims.clear()
        log("rewrite-retry", node=node, why=first)
        retry = (f"\nYour previous answer was rejected ({first}). "
                 f"Answer again, strictly within the limits (SUMMARY <= {MAX_SUMMARY} characters, "
                 f"'{SEC_STATUS}' <= {MAX_STATUS_LINES} lines and {MAX_STATUS} characters, "
                 f"'{SEC_NEXT}' <= {MAX_NEXT_LINES} lines and {MAX_NEXT} characters). Be terse. "
                 "Keep only facts about this folder and its subfolders; "
                 "never copy a subfolder's own state lines.")
        text, info = rewriter(SYSTEM_PROMPT, prompt + retry)
    try:
        return parse_model_output(text, subs, trims), info
    except ValueError as second:
        log("rewrite-retry-fail", node=node, why=second)
        raise


def apply_rewrite(node: Path, old: Doc, output: ModelOutput, trims: list[str],
                  own: bool = False) -> bool | None:
    """Write the model's summary, Status and Next over the current file; returns changed?

    None, writing nothing, when Status or Next changed since `old` was read (another session or a
    person edited the file during the model call): the caller redoes the sync from the new file.
    The base is merged_base (`own`: the session edited the file through its tools); the model's
    `decision:` lines are merged into Decisions.
    """
    cur = read_doc(node)
    if any(cur.sections.get(n, "").strip() != old.sections.get(n, "").strip()
           for n in (SEC_STATUS, SEC_NEXT)):
        return None
    cur = merged_base(node, cur, trust_file=not own)
    cur.meta.setdefault(MARKER, "1")
    cur.meta["summary"] = output.summary
    cur.meta.pop("summary-source", None)  # rewritten at its own level: bubble-up keeps it
    cur.sections[SEC_STATUS] = output.status
    cur.sections[SEC_NEXT] = output.next_step
    decisions, dropped = merge_decisions(cur.sections.get(SEC_DECISIONS, ""), output.decisions)
    if decisions or SEC_DECISIONS in cur.sections:
        cur.sections[SEC_DECISIONS] = decisions
    for line in dropped:
        log("decision-dropped", node=node, why="over the Decisions cap", line=line)
    cur.sections.setdefault(SEC_NOTES, "")
    cur.sections[SEC_SUBS] = build_subs(node)
    if len(render(cur, updated=stamp())) > MAX_TOTAL:
        raise ValueError(f"result over {MAX_TOTAL} chars")
    if trims:
        log("trimmed", node=node, what="; ".join(trims))
    return write_doc(node, cur, session_edit=own)


def needs_bootstrap(node: str | Path) -> bool:
    """True when the node's summary was never written at its own level.

    A session report may then fill it a first time.
    """
    try:
        return is_auto(read_doc(node))
    except OSError:
        return False


def route(files: list[str], launch_cwd: str) -> dict[Path, list[str]]:
    """Map touched files to nodes with the absorb rule: {node: [files]}.

    The CONTEXT.md of the launch node (a project node) stays in its list: the agent wrote its own
    state (adopt_agent_state); one of another node is left out (restore_state). Files no node takes
    are logged as unrouted. An unrouted launch cwd is handled by the caller.
    """
    out, unrouted = {}, collections.Counter()
    launch = nearest_node(launch_cwd) if launch_cwd else None
    for f in files:
        d = real(os.path.dirname(f))
        if os.path.basename(f) == "CONTEXT.md":
            if d == launch and accepts(launch, launch_cwd) and read_doc(d).absorb:
                out.setdefault(d, []).append(f)
            continue
        n = nearest_node(d)
        if n is not None and accepts(n, d):
            out.setdefault(n, []).append(f)
        elif not is_ignored(d):
            unrouted[str(d)] += 1
    if unrouted:
        top = ", ".join(f"{d} ({k})" for d, k in unrouted.most_common(3))
        log("unrouted", files=sum(unrouted.values()), dirs=len(unrouted), top=top,
            hint="no node takes them: `ctx init <folder>` (or the folder is declined)")
    return out


def restore_state(sid: str, files: list[str], targets: dict) -> None:
    """Put back ctx's last version of a node's state the session edited and may not adopt.

    The CONTEXT.md files route() left out of `targets` (another node, a container): Notes and
    Decisions edited by the session are kept (merged_base); Status, Next, summary and header
    return to what ctx wrote.
    """
    for f in files:
        d = real(os.path.dirname(f))
        if os.path.basename(f) == "CONTEXT.md" and f not in targets.get(d, []) and has_ctx(d):
            restore_node(d, sid, "edited from a session of another node: only Notes/Decisions kept")


def restore_node(d: Path, sid: str, why: str) -> None:
    """Write merged_base over a CONTEXT.md a session edited through its tools, then bubble.

    The edit is settled at once: a later sync, which did not see the session's edit, would take
    the file as an outside edit and keep an emptied Notes or Decisions.
    """
    # wait as long as a sync may hold the node: giving up would let its next sync trust the edit
    with dirlock(d, timeout=NODE_LOCK_WAIT) as got:
        if not got:
            log("restore-busy", node=d, session=sid)
            return
        if written_doc(d) is None:
            return
        doc = merged_base(d, read_doc(d))
        doc.sections[SEC_SUBS] = build_subs(d)
        if write_doc(d, doc, session_edit=True):
            log("state-restored", node=d, session=sid, why=why)
    bubble(d)


def merged_base(node: str | Path, cur: Doc, trust_file: bool = False) -> Doc:
    """What a sync starts from: ctx's last version, with the current Notes and Decisions.

    Notes and Decisions are the human's (and code's): an edit that empties them is ignored; the
    header (marker, absorb, private) follows the current file while it still has the marker.
    trust_file (no session edited the file through its tools): a file that differs from ctx's
    last version was edited outside ctx (a person, an editor, another machine) and is the base
    as it is, Status and Next included.
    """
    w = written_doc(node)
    if w is None or (trust_file and not own_version(node)):
        return cur
    out = Doc()
    out.meta, out.sections = dict(w.meta), dict(w.sections)
    for name in (SEC_NOTES, SEC_DECISIONS):
        if cur.sections.get(name, "").strip():
            out.sections[name] = cur.sections[name]
    if MARKER in cur.meta:
        for k in ("absorb", "private"):
            if k in cur.meta:
                out.meta[k] = cur.meta[k]
            else:
                out.meta.pop(k, None)
    return out



def _scan_descends(e):
    """The bash scan walks into this subfolder: not hidden, not a dependency dir, not a node."""
    return (not e.name.startswith(".") and e.name not in SCAN_SKIP_DIRS and not has_ctx(e.path)
            and not is_ignored(e.path))


def _scan_skips_file(fname):
    """The bash scan ignores this file: CONTEXT.md, a sync-tool copy, a ctx temp file, a secret."""
    return (fname == "CONTEXT.md" or bool(CONFLICT_RE.match(fname))
            or (fname.startswith(".") and fname.endswith(".tmp"))
            or any(fnmatch.fnmatch(fname, p) for p in SCAN_SKIP_FILES))


def scan_changed(node: Path, since: float, until: float,
                 skip: dict[str, float]) -> dict[str, float]:
    """Files of `node`'s own tree modified in [since, until]: {path: mtime}, newest first.

    Catches Bash edits. Prunes child nodes, ignored/dependency dirs, secret-like files and the
    files of `skip` with the same mtime. Breadth-first, bounded (SCAN_MAX); absorb:false: own files.
    """
    try:
        deep = read_doc(node).absorb
    except OSError:
        deep = False
    out, seen = {}, 0
    queue = collections.deque([str(node)])
    while queue:
        dir_path = queue.popleft()
        try:
            with os.scandir(dir_path) as entries_it:
                entries = sorted(entries_it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                is_file = e.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if deep and _scan_descends(e):
                    queue.append(e.path)
                continue
            if not is_file:
                continue
            seen += 1
            if seen > SCAN_MAX:
                log("scan-truncated", node=node, limit=SCAN_MAX)
                return dict(sorted(out.items(), key=lambda kv: -kv[1]))
            if _scan_skips_file(e.name):
                continue
            try:
                mtime = e.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if since <= mtime <= until and skip.get(e.path) != mtime:
                out[e.path] = mtime
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def sync_session(sid: str, rewriter: Callable = cli_rewriter) -> str:
    """Sync the pending work of one session: 'ok', 'fail', 'busy', 'nothing' or 'nosession'.

    Log lines written meanwhile carry the session id.
    """
    set_session(sid)
    try:
        return _sync_session(sid, rewriter)
    finally:
        set_session("")


def _sync_session(sid, rewriter):
    """Body of sync_session, under the session's lock."""
    session_dir = session_dir_of(sid)
    if not session_dir.is_dir():
        return "nosession"
    with dirlock(session_dir, blocking=False) as got:
        if not got:
            return "busy"
        pending = session_pending(session_dir)
        new, msgs, cwd, state = pending.files, pending.msgs, pending.cwd, pending.state
        if not new and not msgs:
            return "nothing"
        targets = route(new, cwd)
        restore_state(sid, new, targets)
        launch = nearest_node(cwd) if cwd else None
        if launch is not None and not accepts(launch, cwd):
            launch = None
        scanned = dict(state.get("scanned", {}))
        if (session_dir / "cwd").is_file():
            bash_scan(session_dir, sid, cwd, targets, launch, scanned)
        # reflection-only session: launch folder node
        if not any(targets.values()) and launch is not None:
            targets.setdefault(launch, [])
        results = sync_targets(sid, targets, msgs, rewriter)
        if "fail" in results:
            record_session_result(session_dir, pending, True, targets, scanned)
            return "fail"
        if "busy" in results:  # not synced, not failed: the next catch-up retries it
            return "busy"
        record_session_result(session_dir, pending, False, targets, scanned)
        return "ok"


def session_window(session_dir: Path) -> tuple[float, float]:
    """The session's activity window (since, until) for the bash scan.

    Since the session start; until its SessionEnd marker or last hook activity, plus SCAN_SLACK.
    """
    since = (session_dir / "cwd").stat().st_mtime
    # window end: the SessionEnd marker, else the session's last hook activity (a crashed or
    # session caught up later must not claim files changed long after it stopped)
    until = since + SCAN_SLACK  # no activity file readable: claim nothing beyond the start
    acts = []
    for x in session_dir.iterdir():
        if x.name == "synced.json" or (x.name.startswith(".")
                                                    and x.name.endswith(".tmp")):
            continue
        try:
            acts.append(x.stat().st_mtime)
        except OSError:
            continue
    if acts:
        until = max(acts) + SCAN_SLACK
    return since, until


def bash_scan(session_dir: Path, sid: str, cwd: str, targets: dict, launch: Path | None,
              scanned: dict) -> None:
    """Add to `targets` the files edited through Bash during the session.

    Bash edits are invisible to PostToolUse: the routed nodes (and the launch node) are scanned over
    the session window and `scanned` records what was found. The root is never walked (it spans the
    whole home).
    """
    since, until = session_window(session_dir)
    extra = {}
    for n in {*targets, *([launch] if launch else [])}:
        if n != root():
            extra.update(scan_changed(n, since, until, scanned))
    # files already attributed through PostToolUse (synced or not) are not re-attributed by the scan
    touched = set()
    if (session_dir / "touched").is_file():
        for line in (session_dir / "touched").read_text().splitlines():
            touched.update((line, real(line)))
    extra = {f: mtime for f, mtime in extra.items() if f not in touched and real(f) not in touched}
    if extra:
        log("bash-scan", session=sid, files=len(extra))
        for n, node_files in route(list(extra), cwd).items():
            targets.setdefault(n, []).extend(node_files)
        scanned.update(extra)


def sync_targets(sid: str, targets: dict, msgs: list[str], rewriter: Callable) -> list[str]:
    """Sync each target node, deepest first; returns the list of results.

    A node that got no file of the session gets a sync-light (no model call), except a project
    node never written at its own level: a read-only session may fill it a first time.
    """
    results = []
    for node, node_files in sorted(targets.items(), key=lambda kv: -len(kv[0].parts)):
        work = [f for f in node_files if os.path.basename(f) != "CONTEXT.md"]
        if len(work) < len(node_files) and not agent_state_changed(node, read_doc(node)):
            # CONTEXT.md edited without new state (Notes): nothing to adopt, the edit is settled
            restore_node(node, sid, "edited without a Status or Next change: Notes/Decisions kept")
            node_files = work
        container = not node_files and not read_doc(node).absorb
        light = not node_files and (container or not needs_bootstrap(node))
        try:
            results.append(sync_light(node, sid, msgs) if light
                           else sync_node(node, node_files, msgs, rewriter))
        except Exception as e:  # never escape before synced.json counts the failure
            err = f"{type(e).__name__}: {e}"
            log("sync-fail", node=node, error=err)
            with contextlib.suppress(Exception):
                set_status(node, False, err)
            results.append("fail")
    return results


def record_session_result(session_dir: Path, pending: Pending, failed: bool, targets: dict,
                          scanned: dict) -> None:
    """Write synced.json after a session sync.

    Counts a failure (with its cause), or advances the offsets past the synced work.
    """
    state = pending.state
    state["fails"] = state.get("fails", 0) + 1 if failed else 0
    # after 2 failures the session is abandoned but stays pending: doctor lists it, `sync --session`
    # retries
    if failed:
        statuses = [get_status(n) or {} for n in targets]
        failures = [s["error"] for s in statuses if s.get("status") == "fail"]
        state["error"] = failures[0] if failures else "unknown"
    else:
        state.update(offset=pending.nlines, last=pending.msgs_hash)
        if scanned:
            state["scanned"] = scanned
    atomic_write(session_dir / "synced.json", json.dumps(state))
