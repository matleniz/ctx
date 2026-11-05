"""Ignore rules, node discovery, declined folders, the Subfolders section, bubble-up, git."""
import fnmatch
import os
import re
from pathlib import Path
from typing import Iterator

from .paths import (
    BUBBLE_LOCK_WAIT, BUILTIN_IGNORE, CHILD_DEPTH, ENTRY_SUMMARY, FULL_ENTRIES, MARKER,
    MAX_ENTRIES, MAX_SUBS, MAX_SUMMARY,
    PLACEHOLDERS, REQUIRED, SEC_DECISIONS, SEC_SUBS, SHORT_WIDTH, SUBS_PREFIX, atomic_write,
    clip, dirlock, log, real, root, self_cmd, stamp, state_dir, under,
)
from .filters import instruction_shaped
from .doc import (
    active_decisions, is_auto, new_doc, note_hash, parse, read_doc, render, write_doc,
)


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
    """The closest ancestor that is a node (never above the root), else None.

    Folders in between that are not nodes are skipped, so a node deep under a container still
    reports to it.
    """
    node = real(node)
    r = root()
    if node == r or not under(node, r):
        return None
    for p in node.parents:
        if not under(p, r):
            return None
        if has_ctx(p) and not is_ignored(p):
            return p
        if p == r:
            return None
    return None


def child_nodes(node: str | Path) -> list[Path]:
    """The closest nodes below `node`, in path order.

    Walks at most CHILD_DEPTH levels down, stops at each node found, skips hidden, ignored and
    symlinked folders.
    """
    out = []

    def walk(d, depth):
        """Collect the closest nodes under `d`."""
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for n in names:
            p = Path(d) / n
            if n.startswith(".") or p.is_symlink() or not p.is_dir() or is_ignored(p):
                continue
            if has_ctx(p):
                out.append(p)
            elif depth < CHILD_DEPTH:
                walk(p, depth + 1)

    walk(Path(node), 1)
    return out


def child_label(node: str | Path, child: Path) -> str:
    """The child's path relative to `node` (`intro-ml`, `ctx/code`)."""
    return child.relative_to(node).as_posix()


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
    """Deterministic summary of a container, built from its children, most recently updated first.

    `ml-intro: <summary> · rl: <summary> · also: deep-learning, algebra`: children with a summary
    are quoted (each cut at ENTRY_SUMMARY on a word boundary) while they fit in MAX_SUMMARY, the
    rest are only named.
    """
    kids = []
    for k in child_nodes(node):
        try:
            child_doc = read_doc(k)
        except OSError:
            continue
        if child_doc.private:
            continue
        s = " ".join(child_doc.summary.split())
        ok = bool(s) and s not in PLACEHOLDERS and not instruction_shaped(s)
        kids.append((child_doc.meta.get("updated", ""), child_label(node, k), s if ok else ""))
    if not any(text for _u, _n, text in kids):
        return ""
    kids.sort(key=lambda kid: kid[0], reverse=True)  # stable: name order among equal dates
    quoted, named, full = [], [], False
    for _u, name, text in kids:
        entry = f"{name}: {word_clip(text, ENTRY_SUMMARY)}" if text else ""
        full = full or bool(entry) and len(" · ".join(quoted + [entry])) > MAX_SUMMARY - 30
        if entry and not full:
            quoted.append(entry)
        else:
            named.append(name)
    if named:
        room = MAX_SUMMARY - len(" · ".join(quoted)) - 3
        quoted.append(word_clip("also: " + ", ".join(named), room))
    return " · ".join(quoted)


def word_clip(s: str, n: int) -> str:
    """`s` on one line, cut at the last word boundary before `n` characters, with an ellipsis."""
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:") + "…"


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
        n = 0 if child_doc.private else active_decisions(child_doc.sections.get(SEC_DECISIONS, ""))
        items.append((child_label(node, k), s, f" · decisions: {n}" if n else ""))
    if not items:
        return ""

    def fmt(shown, full):
        """The section body with `shown` entries, the first `full` of them unclipped."""
        lines = [f"- **{name}/** — {text if k < full else clip(text, SHORT_WIDTH)}{tail}"
                 for k, (name, text, tail) in enumerate(items[:shown])]
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
    for s in REQUIRED:
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


def git_root(d: str | Path) -> Path | None:
    """The top folder of the git repository or worktree holding `d` (a `.git` entry), or None."""
    d = real(d)
    for p in [d, *d.parents]:
        if (p / ".git").exists():
            return p
    return None


def create_node(d: Path, absorb: bool = True) -> str:
    """Create a node at `d`: '' on success, else the reason it was not created.

    The caller has checked that `d` is a directory under the root and not ignored.
    """
    with dirlock(d) as got:
        if not got:
            return "busy"
        if has_ctx(d):
            return "exists"
        if is_foreign(d):
            return "foreign"
        doc = new_doc(absorb=absorb)
        doc.sections[SEC_SUBS] = build_subs(d)
        atomic_write(d / "CONTEXT.md", render(doc, updated=stamp()))
        note_hash(d, (d / "CONTEXT.md").read_text())
    excluded = git_exclude(d)
    remove_declined(d)
    log("init", node=d, git_excluded=excluded)
    bubble(d)
    return ""


def auto_node(d: str | Path) -> Path | None:
    """The node for work in `d` when no node takes it: the git repository root, created if needed.

    None when `d` is not in a repository under the root, or the repository root is ignored,
    declined or holds a foreign CONTEXT.md.
    """
    top = git_root(d)
    r = root()
    if top is None or top == r or not under(top, r) or is_ignored(top) or is_declined(top):
        return None
    if has_ctx(top):
        return top
    if create_node(top) == "":
        log("auto-init", node=top, why="git repository with no node taking its files")
        return top
    return None
