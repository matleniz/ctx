"""Document model (Doc, parse, render), history snapshots, per-node state and log reading."""
import json
import os
import re
import time
from pathlib import Path

from .filters import redact
from .paths import (
    DEFAULT_SUMMARY, HISTORY_KEEP, MARKER, MAX_DECISIONS, MAX_DECISIONS_CHARS, ORDER, PLACEHOLDERS,
    PURGE_DAYS, REQUIRED, SEC_NEXT, SEC_NOTES, SEC_STATUS, SEC_SUBS, atomic_write, clip, now,
    path_key, read_json, real, stamp, state_dir, text_hash,
)

SUPERSEDED = "(superseded)"


class Doc:
    """A parsed CONTEXT.md: header keys in `meta`, `## ` sections in `sections`."""

    def __init__(self):
        """An empty document."""
        self.meta = {}
        self.sections = {}

    @property
    def absorb(self) -> bool:
        """Explicit header wins; a header without the key means true."""
        return str(self.meta.get("absorb", "true")).lower() != "false"

    @property
    def private(self) -> bool:
        """True when the header says `private: true`.

        The parent then shows "(private)" instead of the summary.
        """
        return str(self.meta.get("private", "false")).lower() == "true"

    @property
    def summary(self) -> str:
        """The one-line summary from the header."""
        return self.meta.get("summary", "")


def parse(text: str) -> Doc:
    """Parse a CONTEXT.md: front-matter header into `meta`, `## ` sections into `sections`."""
    d = Doc()
    lines = text.lstrip("\ufeff").split("\n")  # an editor's BOM must not hide the header
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            if ":" in lines[i]:
                k, v = lines[i].split(":", 1)
                d.meta[k.strip()] = v.strip()
            i += 1
        i += 1
    cur, buf = None, []
    for line in lines[i:]:
        m = re.match(r"^## (.+?)\s*$", line)
        # a heading seen before is body text: `## Next` inside Notes never replaces the real Next
        if m and m.group(1) not in d.sections and m.group(1) != cur:
            if cur is not None:
                d.sections[cur] = "\n".join(buf).strip("\n")
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        d.sections[cur] = "\n".join(buf).strip("\n")
    return d


def _norm(line: str) -> str:
    """A decision line compared without bullet, prefix, mark, case or punctuation."""
    line = re.sub(r"^\s*[-*]?\s*(d[eé]cision( superseded)?\s*:)?\s*(#\d+\b)?", "", line,
                  flags=re.I)
    return " ".join(re.sub(r"[^\w]+", " ", line.replace(SUPERSEDED, "").lower()).split())


STOP_WORDS = set("a an the of for to and or in on at by with is are be it this that le la les de "
                 "des du un une et pour en".split())


def _sim(x: str, y: str) -> float:
    """Jaccard similarity (0..1) of the word sets of two decision lines, without stop words."""
    a, b = (set(_norm(t).split()) - STOP_WORDS for t in (x, y))
    return len(a & b) / len(a | b) if a | b else 0.0


def numbered(body: str) -> str:
    """The Decisions body as the model sees it: active lines prefixed #1, #2, ..."""
    out, n = [], 0
    for x in (x for x in body.split("\n") if x.strip()):
        n += not x.rstrip().endswith(SUPERSEDED)
        out.append(x if x.rstrip().endswith(SUPERSEDED) else f"#{n} {x}")
    return "\n".join(out)


def active_decisions(body: str) -> int:
    """Number of lines of a Decisions section not marked superseded."""
    return sum(1 for x in body.split("\n") if x.strip() and not x.rstrip().endswith(SUPERSEDED))


def merge_decisions(body: str, new: tuple | list) -> tuple[str, list[str]]:
    """Append the model's `decision:` lines to the Decisions body; returns (body, active dropped).

    Added unless an active line has the same words (_sim >= 0.8); removed only over the caps, oldest
    superseded first. `decision superseded: <old> -> <new>` marks ONE active line: #<old>
    (numbered()) or the best match (_sim >= 0.6)."""
    lines = [x.rstrip() for x in body.split("\n") if x.strip()]
    for raw in new:
        m = re.match(r"\s*[-*]?\s*d[eé]cision( superseded)?\s*:\s*(.+)", raw, re.I)
        text = m.group(2).strip() if m else ""
        if m and m.group(1) and "->" in text:
            old, text = text.split("->", 1)
            act = [i for i, x in enumerate(lines) if not x.endswith(SUPERSEDED)]
            k = re.match(r"\s*#(\d+)\b", old)
            score, hit = max(((_sim(old, lines[i]), i) for i in act), default=(0, None))
            if k:
                score, hit = 1, act[int(k[1]) - 1] if 0 < int(k[1]) <= len(act) else None
            if hit is not None and score >= 0.6:
                lines[hit] += f" {SUPERSEDED}"
        if _norm(text) and all(_sim(text, x) < 0.8 for x in lines if not x.endswith(SUPERSEDED)):
            lines.append("- " + clip(re.sub(r"^#\d+\s*", "", text.strip()), 200))
    dropped = []
    while len(lines) > MAX_DECISIONS or len("\n".join(lines)) > MAX_DECISIONS_CHARS:
        i = next((i for i, x in enumerate(lines) if x.endswith(SUPERSEDED)), None)
        dropped += [] if i is not None else [lines[0]]
        lines.pop(0 if i is None else i)
    return "\n".join(lines), dropped


def is_auto(d: Doc) -> bool:
    """True when the node's summary was never written by a session at its own level."""
    return d.summary in PLACEHOLDERS or d.meta.get("summary-source") == "computed"


def valid_shape(d: Doc) -> bool:
    """True when the header has summary and updated and the four required sections are present."""
    return bool(d.meta.get("summary") is not None and "updated" in d.meta
                and all(s in d.sections for s in REQUIRED))


def render(d: Doc, updated: str | None = None, order: list[str] = ORDER) -> str:
    """Serialise a Doc: header keys in a fixed order, sections in `order`, Subfolders last."""
    meta = dict(d.meta)
    if updated is not None:
        meta["updated"] = updated
    out = ["---"] if meta else []
    for k in (MARKER, "summary", "updated", "absorb", "private"):
        if k in meta:
            out.append(f"{k}: {meta[k]}")
    for k, v in meta.items():
        if k not in (MARKER, "summary", "updated", "absorb", "private"):
            out.append(f"{k}: {v}")
    out += ["---"] if meta else []
    names = [s for s in order if s in d.sections and s != SEC_SUBS]
    names += [s for s in d.sections if s not in order and s != SEC_SUBS]
    names += [SEC_SUBS] if SEC_SUBS in d.sections else []
    for n in names:
        out.append(f"## {n}")
        body = d.sections[n]
        out.append(body if body else "")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def new_doc(absorb: bool = True) -> Doc:
    """A new node document.

    The node takes all work under it up to the next node; `ctx init --no-absorb` (containers) keeps
    it to its own level.
    """
    d = Doc()
    d.meta = {MARKER: "1", "summary": DEFAULT_SUMMARY, "updated": stamp(),
              "absorb": "true" if absorb else "false"}
    d.sections = {SEC_STATUS: "(to be filled in)", SEC_NEXT: "(to be filled in)",
                  SEC_NOTES: "", SEC_SUBS: ""}
    return d


def read_doc(node: str | Path) -> Doc:
    """Parse the node's CONTEXT.md (OSError when unreadable)."""
    return parse((Path(node) / "CONTEXT.md").read_text(encoding="utf-8", errors="replace"))


def history_dir(node: str | Path) -> Path:
    """Where the previous versions of the node's CONTEXT.md are kept."""
    return state_dir() / "history" / path_key(node)


def save_history(node: str | Path, text: str) -> None:
    """Keep `text` as a previous version of the node (last HISTORY_KEEP kept)."""
    h = history_dir(node)
    h.mkdir(parents=True, exist_ok=True)
    atomic_write(h / "path", str(real(node)))  # 0600 under ~/.ctx whatever the process umask
    name = now().strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 10**12:012d}.md"  # sortable
    atomic_write(h / name, text)
    snaps = sorted(x for x in h.iterdir() if x.suffix == ".md")
    for old in snaps[:-HISTORY_KEEP]:
        old.unlink()


def write_doc(node: str | Path, d: Doc, snapshot: bool = True, record: bool = True,
              session_edit: bool = False) -> bool:
    """Atomic write; skip when unchanged ignoring `updated`. Returns True if written.

    record=False keeps the remembered version (note_written): an edit not synced yet stays
    detectable. session_edit: the file on disk was edited by the session being synced.
    """
    p = Path(node) / "CONTEXT.md"
    old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    if old is not None:
        old_doc = parse(old)
        kept = old_doc.meta.get("updated", "")
        if render(d, updated=kept) == render(old_doc, updated=kept):
            if record:  # same content: the file is ctx's version from now on
                note_written(node, old)
            return False
    if old is not None and snapshot:
        # `ctx undo` goes back one version: before a session's own edit of the file, ctx's last
        # version (the edit is kept one step further); a hand edit made outside ctx is itself
        # that last version. Edited text is stored with secrets redacted.
        mine = (get_status(node) or {}).get("written")
        edited = redact(old)[0]
        if not mine or mine == old:
            save_history(node, old)
        elif session_edit:
            save_history(node, edited)
            save_history(node, mine)
        else:
            save_history(node, mine)
            save_history(node, edited)
    text = render(d, updated=stamp())
    atomic_write(p, text)
    if record:
        note_written(node, text)
    return True


def set_status(node: str | Path, ok: bool, err: str = "", **extra: object) -> None:
    """Record the result of the node's last sync in its state file."""
    state_files = state_dir() / "state"
    state_files.mkdir(parents=True, exist_ok=True)
    s = get_status(node) or {}
    s.update(path=str(real(node)), status="ok" if ok else "fail", time=now().isoformat(),
             error=err, **extra)
    atomic_write(state_files / f"{path_key(node)}.json", json.dumps(s))


def written_doc(node: str | Path) -> "Doc | None":
    """The CONTEXT.md text ctx itself last wrote for `node`, parsed; None when unknown."""
    text = (get_status(node) or {}).get("written")
    return parse(text) if text else None


def note_written(node: str | Path, text: str) -> None:
    """Remember the CONTEXT.md text ctx last wrote (own_version compares the file with it)."""
    s = get_status(node) or {"path": str(real(node))}  # no sync result yet: none is invented
    s.pop("hash", None)  # state from before 1.2.0
    s["written"] = text  # the base of the next sync: an agent cannot erase Notes, header, Decisions
    (state_dir() / "state").mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir() / "state" / f"{path_key(node)}.json", json.dumps(s))


_HASHES: dict = {}


def written_hashes() -> set[str]:
    """Hashes of every CONTEXT.md version ctx last wrote on this machine (a moved node's file).

    Cached per process, keyed by the state directory's mtime (each write renames into it).
    """
    sdir = state_dir() / "state"
    try:
        key = (str(sdir), sdir.stat().st_mtime_ns)
    except OSError:
        return set()
    if key not in _HASHES:
        out = set()
        for f in sdir.glob("*.json"):
            s = read_json(f, {})
            w = s.get("written")
            out.update(x for x in (s.get("hash"), w and text_hash(w)) if x)
        _HASHES.clear()
        _HASHES[key] = out
    return _HASHES[key]


def own_version(node: str | Path) -> bool:
    """True when the node's CONTEXT.md is the last version ctx wrote (no edit since)."""
    w = (get_status(node) or {}).get("written")
    try:
        text = (Path(node) / "CONTEXT.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return w is not None and text_hash(w) == text_hash(text)


def get_status(node: str | Path) -> dict | None:
    """The node's state file (last sync, hash), or None."""
    return read_json(state_dir() / "state" / f"{path_key(node)}.json", None)


def status_line(node: str | Path) -> str:
    """One line on the node's last sync, for the injection header."""
    s = get_status(node)
    if not s or "status" not in s:
        return "never synced"
    t = s["time"][11:16]
    if s["status"] == "ok":
        return f"last sync: ok {t}"
    return f"last sync: FAILED {t} ({s.get('error', '')[:80]}), see ~/.ctx/log"


def reachable(p: str | Path) -> bool:
    """True when the parent of a vanished folder is a readable, non-empty directory.

    Otherwise the disk or share may just be unmounted, and its state is kept.
    """
    par = Path(p).parent
    try:
        return par.is_dir() and os.access(par, os.R_OK | os.X_OK) and any(par.iterdir())
    except OSError:
        return False


def _idle_secs(e):
    """Seconds since `e` or anything under it was last modified."""
    return time.time() - max([e.stat().st_mtime, *(x.stat().st_mtime for x in e.rglob("*"))])


def orphans(unreachable: list | None = None) -> list[tuple[str, Path, str]]:
    """State and history entries of vanished folders: [(kind, path, folder)].

    Only folders that no longer exist and were not touched for PURGE_DAYS. Folders whose parent is
    unreachable are skipped (their paths are appended to `unreachable`).
    """
    out = []
    for kind in ("history", "state"):
        for e in sorted((state_dir() / kind).glob("*")):
            if e.is_file():
                src = read_json(e, {}).get("path")
            else:
                src = (e / "path").read_text() if (e / "path").is_file() else None
            if src and not Path(src).exists() and _idle_secs(e) > PURGE_DAYS * 86400:
                if reachable(src):
                    out.append((kind, e, src))
                elif unreachable is not None:
                    unreachable.append(src)
    return out


