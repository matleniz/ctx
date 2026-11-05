"""Per-node queue, rewrite, session sync, bash scan, deadlines."""
import collections
import contextlib
import fnmatch
import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable

from .paths import (
    Alarm, CONFLICT_RE, FILES_MAX, MAX_NEXT, MAX_NEXT_LINES, MAX_STATUS, MAX_STATUS_LINES,
    MAX_SUMMARY, MAX_TOTAL, MODEL_TIMEOUT, MSG_KEEP, SCAN_MAX, SCAN_SKIP_DIRS, SCAN_SKIP_FILES,
    SCAN_SLACK, SEC_DECISIONS, SEC_NEXT, SEC_NOTES, SEC_STATUS, SEC_SUBS, SESSION_BUSY,
    SESSION_MISSING, SESSION_NOTHING, STATUS_FAIL, SYNC_FAIL, SYNC_MARGIN, SYNC_NONE, SYNC_OK,
    SYNC_QUEUED, atomic_write, clip, deadline, dirlock, log, path_key, read_json, real, root,
    set_session, stamp, state_dir, text_hash,
)
from .filters import redact
from .doc import Doc, get_status, is_auto, merge_decisions, numbered, parse, read_doc, render
from .doc import set_status, state_hash, write_doc
from .tree import accepts, auto_node, bubble, build_subs, has_ctx, is_ignored, nearest_node
from .sessions import Pending, session_dir_of, session_pending
from .rewriter import (
    DECISION_RE, LIGHT_PROMPT, ModelOutput, SYSTEM_PROMPT, build_prompt, cli_rewriter, git_diffstat,
    light_worthy, parse_model_output,
)


def queue_dir(node: str | Path) -> Path:
    """The node's queue of pending work."""
    return state_dir() / "pending" / path_key(node)


def enqueue(node: str | Path, files: list[str], msgs: list[str], light: bool = False) -> None:
    """Add one work item (files, reports, light: reports only, Next and Decisions) to the queue."""
    q = queue_dir(node)
    q.mkdir(parents=True, exist_ok=True)
    item = json.dumps({"files": files, "msgs": msgs, "light": light})
    atomic_write(q / f"{time.time_ns()}-{uuid.uuid4().hex[:6]}.json", item)


def has_pending(node: str | Path) -> bool:
    """True when the node's queue holds work."""
    q = queue_dir(node)
    return q.is_dir() and any(q.glob("*.json"))


def drain(node: str | Path) -> tuple[list[Path], list[str], list[str], bool]:
    """The node's queued work: (queue files, merged touched files, last reports, all light?).

    The queue files are not removed: the caller does it once the rewrite is settled.
    """
    q = queue_dir(node)
    items, files, msgs, light = [], [], [], True
    for f in sorted(q.glob("*.json")) if q.is_dir() else []:
        item = read_json(f, {"files": [], "msgs": []})
        light = light and item.get("light", False)
        files += [x for x in item["files"] if x not in files]
        msgs += [m for m in item["msgs"] if m not in msgs]
        items.append(f)
    return items, files, msgs[-MSG_KEEP:], light


def sync_node(node: str | Path, files: list[str], msgs: list[str], rewriter: Callable,
              light: bool = False) -> str:
    """Queue the work then try to become the single syncer. Returns 'ok'|'queued'|'fail'|'none'."""
    node = real(node)
    enqueue(node, files, msgs, light)
    result = SYNC_QUEUED
    while True:
        with dirlock(node, blocking=False) as got:
            if not got:
                log("sync-queued", node=node)
                return result
            items, node_files, queued_msgs, light = drain(node)
            if items:
                try:
                    result = _rewrite_locked(node, node_files, queued_msgs, rewriter, light)
                except BaseException:  # incl. the deadline alarm: the drained work stays queued
                    log("sync-requeue", node=node, items=len(items))
                    raise
                if result == SYNC_FAIL:
                    return result  # keep the drained items queued for the next sync
                for f in items:  # consumed only once the rewrite is settled
                    with contextlib.suppress(OSError):
                        f.unlink()
        # lock released. Re-check the queue: a syncer that failed to get the lock while we were
        # draining/rewriting left its work here and relies on us (no lost wakeup).
        if not has_pending(node):
            return result if result != SYNC_QUEUED else SYNC_NONE


def _rewrite_locked(node, files, msgs, rewriter, light=False):
    """Rewrite the node (light: reports in, Next/Decisions out); caller holds the lock. SYNC_*."""
    started = time.time()
    p = Path(node) / "CONTEXT.md"
    own = [f for f in files if os.path.basename(f) == "CONTEXT.md"]
    files = [f for f in files if f not in own]
    try:
        old_text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return SYNC_FAIL
    old = parse(old_text)
    check_external_edit(node, old_text)
    diffstat = "" if light else git_diffstat(node)
    if not files and not msgs and not diffstat and not own:
        bubble(node)
        return SYNC_NONE
    prompt, redactions = rewrite_prompt(node, old, files, msgs, diffstat, light)
    subs = build_subs(node)
    trims = []
    writer = "model"
    try:
        output = adopt_agent_state(node, old, old_text, msgs, subs, trims) if own else None
        if output is not None:
            writer, info = "agent", {"in": 0, "out": 0, "secs": 0}
        else:
            output, info = ask_model(node, rewriter, prompt, subs, trims, light)
        changed = apply_rewrite(node, old, output, trims, light)
        # worked: time of the last sync with touched files; False: read-only bootstraps only
        prior = get_status(node) or {}
        set_status(node, True, worked=stamp() if files else prior.get(
            "worked", None if "status" in prior else False))
    except Exception as e:  # invalid, model or write failure: keep the old file, count as failed
        with contextlib.suppress(Exception):
            set_status(node, False, f"{type(e).__name__}: {e}")
        log("sync-fail", node=node, error=f"{type(e).__name__}: {e}")
        return SYNC_FAIL
    log("sync-light" if light else "sync", node=node, files=len(files), changed=changed,
        writer=writer, tokens_in=info.get("in", 0), tokens_out=info.get("out", 0),
        model_secs=info.get("secs", 0), total_secs=round(time.time() - started, 1))
    if output.filtered + redactions:
        log("filtered", node=node, count=output.filtered + redactions,
            why="secrets/instruction-shaped lines")
    try:
        bubble(node)
    except Exception as e:  # the node is written: a parent refresh error must not redo the call
        log("bubble-error", node=node, error=f"{type(e).__name__}: {e}")
    return SYNC_OK


def adopt_agent_state(node: Path, old: Doc, old_text: str, msgs: list[str], subs: str,
                      trims: list[str]) -> ModelOutput | None:
    """The state the agent of the session wrote into CONTEXT.md, validated; None: call the model.

    Called when the session edited the node's CONTEXT.md. Adopted when Status or Next differ from
    what ctx last wrote (state_hash) and the file passes
    the same checks as a model answer. `decision:` lines of the reports go to Decisions. A summary
    the agent left unchanged is rebuilt from the first Status and Next lines.
    """
    known = (get_status(node) or {}).get("state_hash")
    if not known or known == state_hash(old_text):
        return None
    status = old.sections.get(SEC_STATUS, "").strip()
    nxt = old.sections.get(SEC_NEXT, "").strip()
    summary = old.summary
    prior = (get_status(node) or {}).get("summary_written")
    if is_auto(old) or summary == prior:
        first = lambda body: body.split("\n")[0].lstrip("-* ").strip()  # noqa: E731
        summary = clip(f"{first(status)}; next: {first(nxt)}", MAX_SUMMARY)
    decisions = "\n".join(x.strip() for m in msgs for x in DECISION_RE.findall(m))
    text = f"SUMMARY: {summary}\n## Status\n{status}\n## Next\n{nxt}\n{decisions}\n"
    try:
        out = parse_model_output(text, subs, trims, figures=False)
    except ValueError as e:
        trims.clear()
        log("agent-state-rejected", node=node, why=e)
        return None
    return out


def check_external_edit(node: Path, old_text: str) -> None:
    """Log when CONTEXT.md was written outside ctx since its last sync.

    The file is still the base of the rewrite.
    """
    known = (get_status(node) or {}).get("hash")
    if not known:
        # no hash recorded (state removed): trusted this once; write_doc records the hash, so
        # this logs once
        log("external-edit-unknown", node=node,
            why="no recorded hash: CONTEXT.md trusted as is")
    elif known != text_hash(old_text):
        # written outside ctx (other machine, editor): it is the base, kept in history
        log("external-edit", node=node,
            why="CONTEXT.md differs from the last version ctx wrote; used as base")


def rewrite_prompt(node: Path, old: Doc, files: list[str], msgs: list[str],
                   diffstat: str, light: bool = False) -> tuple[str, int]:
    """The rewrite prompt and the number of redactions made in it.

    Inputs: the old Status/Next and summary, the reports, the diff stat and the files, all redacted.
    """
    rel = [os.path.relpath(f, node) for f in files[:FILES_MAX]]
    redactions = 0
    sec_view = Doc()
    # never show a placeholder/computed summary
    sec_view.meta = {"summary": "" if is_auto(old) else old.summary}
    shown = (SEC_NEXT, SEC_DECISIONS) if light else (SEC_STATUS, SEC_NEXT, SEC_DECISIONS)
    sec_view.sections = {s: numbered(v) if s == SEC_DECISIONS else v
                         for s, v in old.sections.items() if s in shown}
    parts = []
    for text in (render(sec_view), "\n---\n".join(msgs), diffstat, "\n".join(rel)):
        t, k = redact(text)
        redactions += k
        parts.append(t)
    old_s, msgs_s, diff_s, rel_s = parts
    prompt = build_prompt(old_s, rel_s.split("\n") if rel_s else [],
                          msgs_s.split("\n---\n") if msgs_s else [], diff_s, light)
    return prompt, redactions


def ask_model(node: Path, rewriter: Callable, prompt: str, subs: str,
              trims: list[str], light: bool = False) -> tuple[ModelOutput, dict]:
    """Call the model, with one retry naming the violation if the output is rejected.

    Returns (parsed output, call info).
    """
    system = LIGHT_PROMPT if light else SYSTEM_PROMPT
    text, info = rewriter(system, prompt)
    try:
        return parse_model_output(text, subs, trims, light=light), info
    except ValueError as first:
        # other violations (secrets, copied subfolder state, missing sections...): one retry;
        # over-bounds is trimmed, not retried
        trims.clear()
        log("rewrite-retry", node=node, why=first)
        limits = "" if light else (f"SUMMARY <= {MAX_SUMMARY} characters, '{SEC_STATUS}' <= "
                                   f"{MAX_STATUS_LINES} lines and {MAX_STATUS} characters, ")
        retry = (f"\nYour previous answer was rejected ({first}). "
                 f"Answer again, strictly within the limits ({limits}"
                 f"'{SEC_NEXT}' <= {MAX_NEXT_LINES} lines and {MAX_NEXT} characters). Be terse. "
                 "Keep only facts about this folder and its subfolders; "
                 "never copy a subfolder's own state lines.")
        text, info = rewriter(system, prompt + retry)
    try:  # the figure rule rejects once only: a retry without the figure is still accepted
        return parse_model_output(text, subs, trims, figures=False, light=light), info
    except ValueError as second:
        log("rewrite-retry-fail", node=node, why=second)
        raise


def apply_rewrite(node: Path, old: Doc, output: ModelOutput, trims: list[str],
                  light: bool = False) -> bool:
    """Write the model's summary, Status and Next over the current file; returns changed?

    The Notes of the current file are kept; its Decisions too, plus the model's `decision:` lines
    (merge_decisions). light: only Next and Decisions change.
    """
    # re-read: a hand edit made during the model call keeps its Notes (the rest restarts from the
    # version read above)
    cur = read_doc(node)
    notes = (cur.sections.get(SEC_NOTES, ""), old.sections.get(SEC_NOTES, ""))
    notes_changed = text_hash(notes[0]) != text_hash(notes[1])
    if notes_changed or cur.sections.get(SEC_STATUS) != old.sections.get(SEC_STATUS):
        log("edit-during-sync", node=node, why="hand edit during the model call: Notes kept, "
            "other sections rewritten, edit in history")
    if not light:
        cur.meta["summary"] = output.summary
        # rewritten at its own level: bubble-up no longer overwrites it
        cur.meta.pop("summary-source", None)
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
    return write_doc(node, cur)


def sync_deadline() -> int:
    """Per-node sync deadline: the rewriter may run twice (retry) at its timeout, plus a margin."""
    return 2 * MODEL_TIMEOUT + SYNC_MARGIN


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

    A file no node takes goes to the root of its git repository, made a node if needed
    (auto_node); the rest is logged as unrouted. An unrouted launch cwd is handled by the caller.
    """
    out, unrouted = {}, collections.Counter()
    for f in files:
        d = real(os.path.dirname(f))
        if os.path.basename(f) == "CONTEXT.md":
            if has_ctx(d):  # the agent wrote the node's state itself (adopt_agent_state)
                out.setdefault(d, []).append(f)
            continue
        n = nearest_node(d)
        if n is None or not accepts(n, d):
            n = None if is_ignored(d) else auto_node(d)
        if n is not None:
            out.setdefault(n, []).append(f)
        elif not is_ignored(d):
            unrouted[str(d)] += 1
    if unrouted:
        top = ", ".join(f"{d} ({k})" for d, k in unrouted.most_common(3))
        log("unrouted", files=sum(unrouted.values()), dirs=len(unrouted), top=top,
            hint="no node takes them: `ctx init <folder>` (or the folder is declined)")
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
    """Sync the pending work of one session; returns a SYNC_*/SESSION_* value.

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
        return SESSION_MISSING
    with dirlock(session_dir, blocking=False) as got:
        if not got:
            return SESSION_BUSY
        pending = session_pending(session_dir)
        new, msgs, cwd, state = pending.files, pending.msgs, pending.cwd, pending.state
        if not new and not msgs:
            return SESSION_NOTHING
        targets = route(new, cwd)
        launch = nearest_node(cwd) if cwd else None
        if launch is not None and not accepts(launch, cwd):
            launch = None
        scanned = dict(state.get("scanned", {}))
        if (session_dir / "cwd").is_file():
            bash_scan(session_dir, sid, cwd, targets, launch, scanned)
        # reflection-only session: launch folder node
        if not any(targets.values()) and launch is not None:
            targets.setdefault(launch, [])
        failed = SYNC_FAIL in sync_targets(sid, targets, msgs, rewriter)
        record_session_result(session_dir, pending, failed, targets, scanned)
        return SYNC_FAIL if failed else SYNC_OK


def session_window(session_dir: Path) -> tuple[float, float]:
    """The session's activity window (since, until) for the bash scan.

    Since the session start; until its SessionEnd marker or last hook activity, plus SCAN_SLACK.
    """
    since = (session_dir / "cwd").stat().st_mtime
    # window end: the SessionEnd marker, else the session's last hook activity (a crashed or
    # pid-less session caught up later must not claim files changed long after it stopped)
    until = since + SCAN_SLACK  # no activity file readable: claim nothing beyond the start
    acts = []
    for x in session_dir.iterdir():
        if x.name in ("synced.json", "closed") or (x.name.startswith(".")
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
    """Sync each target node, deepest first, under the per-node deadline.

    Returns the list of results.
    """
    results = []
    for node, node_files in sorted(targets.items(), key=lambda kv: -len(kv[0].parts)):
        # nothing of this session landed in the node: no call, unless light_worthy (sync-light);
        # a container is never bootstrapped by a session that only talked in it
        container = not node_files and not read_doc(node).absorb
        light = not node_files and (container or not needs_bootstrap(node))
        if light and not light_worthy(msgs):
            log("skip", node=node, session=sid, why="container, no file of its own" if container
                else "no file routed to node, summary already filled")
            continue
        try:
            with deadline(sync_deadline()):
                results.append(sync_node(node, node_files, msgs, rewriter, light))
        except (Alarm, Exception) as e:
            # never escape before synced.json counts the failure (bounded retries)
            err = "deadline" if isinstance(e, Alarm) else f"{type(e).__name__}: {e}"
            log("sync-fail", node=node, error=err)
            with contextlib.suppress(Exception):
                set_status(node, False, err)
            results.append(SYNC_FAIL)
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
        failures = [s["error"] for s in statuses if s.get("status") == STATUS_FAIL]
        state["error"] = failures[0] if failures else "unknown"
    else:
        state.update(offset=pending.nlines, last=pending.msgs_hash)
        if scanned:
            state["scanned"] = scanned
    atomic_write(session_dir / "synced.json", json.dumps(state))
