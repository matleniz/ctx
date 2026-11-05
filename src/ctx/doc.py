"""Document model (Doc, parse, render), history snapshots, per-node state and log reading."""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .paths import (
    DEFAULT_SUMMARY, FAIL_EVENTS, HISTORY_KEEP, LOG_KV, MARKER, ORDER, PLACEHOLDERS, PURGE_DAYS,
    SEC_NEXT, SEC_NOTES, SEC_STATUS, SEC_SUBS, STATUS_FAIL, STATUS_OK, atomic_write, now, path_key,
    read_json, real, stamp, state_dir, strip_control, text_hash,
)


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
    lines = text.split("\n")
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
        if m:
            if cur is not None:
                d.sections[cur] = "\n".join(buf).strip("\n")
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        d.sections[cur] = "\n".join(buf).strip("\n")
    return d


def is_auto(d: Doc) -> bool:
    """True when the node's summary was never written by a session at its own level."""
    return d.summary in PLACEHOLDERS or d.meta.get("summary-source") == "computed"


def valid_shape(d: Doc) -> bool:
    """True when the header has summary and updated and all four sections are present."""
    return bool(d.meta.get("summary") is not None and "updated" in d.meta
                and all(s in d.sections for s in ORDER))


def render(d: Doc, updated: str | None = None) -> str:
    """Serialise a Doc: header keys in a fixed order, then the sections (Subfolders last)."""
    meta = dict(d.meta)
    if updated is not None:
        meta["updated"] = updated
    out = ["---"]
    for k in (MARKER, "summary", "updated", "absorb", "private"):
        if k in meta:
            out.append(f"{k}: {meta[k]}")
    for k, v in meta.items():
        if k not in (MARKER, "summary", "updated", "absorb", "private"):
            out.append(f"{k}: {v}")
    out.append("---")
    names = [s for s in ORDER if s in d.sections]
    extra = [s for s in d.sections if s not in ORDER]
    names = [s for s in ORDER[:3] if s in names] + extra + [s for s in names if s == SEC_SUBS]
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
    (h / "path").write_text(str(real(node)))
    name = now().strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1000000:06d}.md"
    (h / name).write_text(text, encoding="utf-8")
    snaps = sorted(x for x in h.iterdir() if x.suffix == ".md")
    for old in snaps[:-HISTORY_KEEP]:
        old.unlink()


def write_doc(node: str | Path, d: Doc, snapshot: bool = True) -> bool:
    """Atomic write; skip when unchanged ignoring `updated`. Returns True if written."""
    p = Path(node) / "CONTEXT.md"
    old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    if old is not None:
        old_doc = parse(old)
        kept = old_doc.meta.get("updated", "")
        if render(d, updated=kept) == render(old_doc, updated=kept):
            return False
    if old is not None and snapshot:
        save_history(node, old)
    text = render(d, updated=stamp())
    atomic_write(p, text)
    note_hash(node, text)
    return True


def set_status(node: str | Path, ok: bool, err: str = "", **extra: object) -> None:
    """Record the result of the node's last sync in its state file."""
    state_files = state_dir() / "state"
    state_files.mkdir(parents=True, exist_ok=True)
    s = get_status(node) or {}
    s.update(path=str(real(node)), status=STATUS_OK if ok else STATUS_FAIL, time=now().isoformat(),
             error=err, **extra)
    atomic_write(state_files / f"{path_key(node)}.json", json.dumps(s))


def note_hash(node: str | Path, text: str) -> None:
    """Remember the hash of the CONTEXT.md ctx last wrote.

    A different hash later means a write from outside ctx.
    """
    s = get_status(node) or {"path": str(real(node))}  # no sync result yet: none is invented
    s["hash"] = text_hash(text)
    (state_dir() / "state").mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir() / "state" / f"{path_key(node)}.json", json.dumps(s))


def get_status(node: str | Path) -> dict | None:
    """The node's state file (last sync, hash), or None."""
    return read_json(state_dir() / "state" / f"{path_key(node)}.json", None)


def status_line(node: str | Path) -> str:
    """One line on the node's last sync, for the injection header."""
    s = get_status(node)
    if not s or "status" not in s:
        return "never synced"
    t = s["time"][11:16]
    if s["status"] == STATUS_OK:
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
    """State, history and queue entries of vanished folders: [(kind, path, folder)].

    Only folders that no longer exist and were not touched for PURGE_DAYS. Folders whose parent is
    unreachable are skipped (their paths are appended to `unreachable`).
    """
    out = []
    for kind in ("history", "state", "pending"):
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


def read_log(since: datetime | None = None) -> list[tuple[datetime, str, dict[str, str]]]:
    """Log entries [(time, event, {k: v})] from log.1 then log.

    The format is one line per event: `ISO event k=v k=v`.
    """
    out = []
    for name in ("log.1", "log"):
        f = state_dir() / name
        for line in f.read_text(errors="replace").splitlines() if f.is_file() else []:
            p = strip_control(line).split(" ", 2)
            try:
                t = datetime.fromisoformat(p[0])
            except (ValueError, IndexError):
                continue
            if len(p) > 1 and (since is None or t >= since):
                out.append((t, p[1], dict(LOG_KV.findall(p[2] if len(p) > 2 else ""))))
    return out


def parse_since(s: str | None) -> datetime | None:
    """The time `s` (30m, 2h, 1d) ago, or None without `s`; exits on a bad value."""
    m = re.fullmatch(r"(\d+)([mhd])", s or "")
    if s and not m:
        raise SystemExit("ctx: --since expects e.g. 30m, 2h or 1d")
    if not m:
        return None
    return now() - timedelta(minutes=int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)])


def _log_matches(args, node, e, kv):
    """The log line passes the `ctx log` filters (--fail, --node, --session)."""
    if args.fail and not FAIL_EVENTS.search(e):
        return False
    if node and not any(str(kv.get(k, "")).startswith(node) for k in ("node", "cwd")):
        return False
    sessions = (kv.get("session"), *kv.get("sessions", "").split(","))
    return not args.session or args.session in sessions
