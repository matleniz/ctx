"""Ignore rules, node discovery, declined folders, the Subfolders section, bubble-up, git."""
import fnmatch
import os
import re
from pathlib import Path
from typing import Iterator

from .paths import (
    BUBBLE_LOCK_WAIT, BUILTIN_IGNORE, FULL_ENTRIES, MARKER, MAX_ENTRIES, MAX_SUBS, MAX_SUMMARY,
    ORDER, PLACEHOLDERS, SEC_SUBS, SHORT_WIDTH, SUBS_PREFIX, atomic_write, clip, dirlock, log, real,
    root, self_cmd, state_dir, under,
)
from .filters import instruction_shaped
from .doc import is_auto, parse, read_doc, write_doc


def ignore_patterns() -> list[str]:
    """The patterns of <root>/.ctxignore (comments and blank lines dropped)."""
    pats = []
    f = root() / ".ctxignore"
    if f.is_file():
        for line in f.read_text(errors="replace").splitlines():
            line = line.strip().rstrip("/")
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def is_ignored(p: str | Path) -> bool:
    """True outside the root, under .ctx/.git, or matched by .ctxignore."""
    p = real(p)
    r = root()
    if not under(p, r):
        return True
    rel = p.relative_to(r).parts
    pats = ignore_patterns()
    for i, part in enumerate(rel):
        if part in BUILTIN_IGNORE:
            return True
        sub = "/".join(rel[: i + 1])
        for pat in pats:
            if fnmatch.fnmatch(part, pat) or fnmatch.fnmatch(sub, pat):
                return True
    return False


def header_meta(p: str | Path) -> dict[str, str]:
    """Header keys of a CONTEXT.md, read without going past its header.

    Empty dict when the file is absent, unreadable or has no header.
    """
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return {}
    return parse(head).meta if head.startswith("---") else {}


def has_ctx(d: str | Path) -> bool:
    """True when `d` is a node.

    A node is a folder whose CONTEXT.md carries the ctx marker; any other CONTEXT.md is foreign and
    ignored.
    """
    p = Path(d) / "CONTEXT.md"
    return p.is_file() and MARKER in header_meta(p)


def is_foreign(d: str | Path) -> bool:
    """True when `d` holds a CONTEXT.md without the ctx marker: not ours, ignored everywhere."""
    p = Path(d) / "CONTEXT.md"
    return p.is_file() and not has_ctx(d)


def walk_nodes() -> Iterator[Path]:
    """Candidate node folders, root first, depth-first in name order.

    The root, then its non-hidden, non-ignored, non-symlink subfolders (depth <= 9).
    """
    r = root()
    yield r

    def walk(d, depth):
        """Yield the candidate subfolders of `d`, depth first."""
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for n in names:
            p = d / n
            if n.startswith(".") or not p.is_dir() or p.is_symlink() or is_ignored(p) or depth > 8:
                continue
            yield p
            yield from walk(p, depth + 1)

    yield from walk(r, 0)


def find_foreign() -> list[Path]:
    """Folders under the root whose CONTEXT.md has no marker."""
    return [p for p in walk_nodes() if is_foreign(p)]


def all_nodes() -> list[Path]:
    """Every node under the root, root first, depth-first in name order."""
    return [p for p in walk_nodes() if has_ctx(p)]


def nearest_node(d: str | Path) -> Path | None:
    """Closest ancestor-or-self with CONTEXT.md inside root, or None."""
    d, r = real(d), root()
    if is_ignored(d):
        return None
    while under(d, r):
        if has_ctx(d):
            return d
        if d == r:
            break
        d = d.parent
    return None


def accepts(node: Path | None, d: str | Path) -> bool:
    """Does `node` take work done in directory d (absorb rule)?"""
    if node is None:
        return False
    if real(d) == real(node):
        return True
    try:
        return read_doc(node).absorb
    except OSError:
        return False


def parent_node(node: str | Path) -> Path | None:
    """The parent folder when it is a node (never above the root), else None."""
    node = real(node)
    if node == root() or not under(node, root()):
        return None
    p = node.parent
    return p if has_ctx(p) and not is_ignored(p) else None


def child_nodes(node: str | Path) -> list[Path]:
    """Direct subfolders that are nodes, in name order."""
    out = []
    try:
        names = sorted(os.listdir(node))
    except OSError:
        return out
    for n in names:
        p = Path(node) / n
        if p.is_dir() and not p.is_symlink() and has_ctx(p) and not is_ignored(p):
            out.append(p)
    return out


def declined_list() -> list[Path]:
    """Folders where the user declined a node."""
    f = state_dir() / "declined"
    return [real(x) for x in f.read_text().splitlines() if x.strip()] if f.is_file() else []


def is_declined(d: str | Path) -> bool:
    """True when `d` is a declined folder or inside one."""
    d = real(d)
    return any(under(d, x) for x in declined_list())


def add_declined(d: str | Path) -> None:
    """Record that the user declined a node in `d`."""
    ctx_dir = state_dir()
    ctx_dir.mkdir(parents=True, exist_ok=True)
    if not is_declined(d):
        with open(ctx_dir / "declined", "a") as f:
            f.write(str(real(d)) + "\n")


def remove_declined(d: str | Path) -> None:
    """Forget a declined folder (`ctx init` there reverses the decline)."""
    f = state_dir() / "declined"
    if f.is_file():
        keep = [x for x in f.read_text().splitlines() if x.strip() and real(x) != real(d)]
        f.write_text("".join(x + "\n" for x in keep))


def computed_summary(node: str | Path) -> str:
    """Deterministic summary built from the children's summaries.

    Each non-private child's summary prefixed '<child> : ', joined with '; ', cut at MAX_SUMMARY.
    """
    parts = []
    for k in child_nodes(node):
        try:
            child_doc = read_doc(k)
        except OSError:
            continue
        s = " ".join(child_doc.summary.split())
        if child_doc.private or not s or s in PLACEHOLDERS or instruction_shaped(s):
            continue
        parts.append(f"{k.name} : {s}")
    return clip("; ".join(parts), MAX_SUMMARY) if parts else ""


def build_subs(node: str | Path) -> str:
    """Reconstruct the Subfolders body (deterministic, bounded by MAX_SUBS)."""
    kids = child_nodes(node)
    items = []
    for k in kids:
        try:
            child_doc = read_doc(k)
        except OSError:
            continue
        if child_doc.private:
            s = "(private)"
        else:
            s = " ".join(child_doc.summary.split())
            if instruction_shaped(s):
                s = "(summary filtered)"
        items.append((k.name, s))
    if not items:
        return ""

    def fmt(shown, full):
        """The section body with `shown` entries, the first `full` of them unclipped."""
        lines = [f"- **{name}/** — {text if k < full else clip(text, SHORT_WIDTH)}"
                 for k, (name, text) in enumerate(items[:shown])]
        if len(items) > shown:
            lines.append(f"- … and {len(items) - shown} more (`{self_cmd()} show --tree`)")
        return SUBS_PREFIX + "\n" + "\n".join(lines)

    # rule 8 full / 80 chars / "+N more" beyond 20; then the global budget wins: all short, then
    # drop trailing entries
    shown, full = min(len(items), MAX_ENTRIES), FULL_ENTRIES
    while len(body := fmt(shown, full)) > MAX_SUBS and shown > 0:
        shown, full = (shown, 0) if full else (shown - 1, 0)
    return body


def refresh_subs(node: str | Path) -> bool:
    """Rewrite Subfolders of `node` if its content changed. Caller holds the lock. Returns changed?
    """
    d = read_doc(node)
    new = build_subs(node)
    computed = computed_summary(node) if is_auto(d) else ""
    same_subs = d.sections.get(SEC_SUBS, "") == new and SEC_SUBS in d.sections
    if same_subs and (not computed or computed == d.summary):
        return False
    d.sections[SEC_SUBS] = new
    if computed:
        d.meta["summary"] = computed
        d.meta["summary-source"] = "computed"
    for s in ORDER:
        d.sections.setdefault(s, "")
    return write_doc(node, d, snapshot=False)


def bubble(node: str | Path) -> None:
    """Bottom-up, one lock per parent; stop when a parent is unchanged."""
    cur = real(node)
    while True:
        par = parent_node(cur)
        if par is None:
            return
        with dirlock(par, timeout=BUBBLE_LOCK_WAIT) as got:
            if not got:
                log("bubble-lock-timeout", node=par)
                return
            changed = refresh_subs(par)  # no history snapshot: model versions must not be evicted
        if not changed:
            return
        log("bubble", node=par)
        cur = par


def show_tree() -> int:
    """Print every node with its summary, indented by depth (`ctx show --tree`)."""
    nodes = all_nodes()
    if root() not in nodes:
        print(f"(no node at {root()})")
    for n in nodes:
        try:
            doc = read_doc(n)
        except OSError:
            continue
        depth = sum(ancestor in nodes for ancestor in n.parents)
        name = "~" if n == root() else n.name + "/"
        print(f"{'  ' * depth}{name}{'' if doc.absorb else ' [absorb:false]'} — "
              f"{'(private)' if doc.private else clip(doc.summary, 100)}")
    return 0


def git_info_dir(d: str | Path) -> Path | None:
    """The `info` directory of the git repository holding `d`, or None.

    Worktrees and submodules are resolved to their common git directory.
    """
    d = real(d)
    for p in [d, *d.parents]:
        g = p / ".git"
        if g.is_dir():
            return g / "info"
        if g.is_file():
            m = re.match(r"gitdir:\s*(.+)", g.read_text().strip())
            if m:
                gitdir = (p / m.group(1)).resolve()
                # a worktree gitdir lives in <repo>/.git/worktrees/<name>, or anywhere for
                # submodules (<super>/.git/modules/..): accept only paths inside a .git directory
                if ".git" not in gitdir.parts:
                    log("git-exclude-refused", gitdir=gitdir)
                    return None
                commondir = gitdir / "commondir"
                if commondir.is_file():
                    common = (gitdir / commondir.read_text().strip()).resolve()
                    if ".git" not in common.parts and not (common / "HEAD").is_file():
                        return None
                    gitdir = common
                return gitdir / "info"
    return None


def git_exclude(d: str | Path) -> bool:
    """Add CONTEXT.md to the repository's info/exclude; False when `d` is not in a repository."""
    info = git_info_dir(d)
    if info is None:
        return False
    info.mkdir(parents=True, exist_ok=True)
    exclude_file = info / "exclude"
    cur = exclude_file.read_text() if exclude_file.exists() else ""
    if "CONTEXT.md" not in cur.split("\n"):
        sep = "" if cur.endswith("\n") or not cur else "\n"
        atomic_write(exclude_file, cur + sep + "CONTEXT.md\n")
    return True
