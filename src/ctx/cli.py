"""Commands and argument parser; main() is the entry point."""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from .paths import (
    MARKER, PURGE_DAYS, __version__, atomic_write, base, clean, dirlock, human_cmd, log,
    private_umask, real, root, state_dir, strip_control, under,
)
from .doc import history_dir, note_written, orphans
from .tree import (
    add_declined, all_nodes, bubble, create_node, git_info_dir, has_ctx, is_ignored, refresh_subs,
    show_tree,
)
from .sessions import catch_up
from .rewriter import cli_rewriter
from .sync import sync_node, sync_session
from .hooks import CLIENTS, guarded_hook, build_injection
from .install import merged_settings, remove_hooks, review_write, unmerged
from .doctor import (
    check_abandoned, check_config, check_failed_syncs, check_node, check_rewriter, fix_orphans,
    foreign_notes, node_notes, orphan_problems, print_report,
)


def cmd_init(args: argparse.Namespace) -> int:
    """`ctx init`: create a node."""
    d = real(args.dir)
    if not d.is_dir():
        print(f"ctx: {d} is not a directory", file=sys.stderr)
        return 1
    if not under(d, root()):
        print(f"ctx: {d} is outside the ctx root {root()}", file=sys.stderr)
        return 1
    if is_ignored(d):
        print(f"ctx: {d} is excluded by .ctxignore", file=sys.stderr)
        return 1
    if has_ctx(d):
        print(f"ctx: {d} already has a CONTEXT.md")
        return 0
    why = create_node(d, absorb=not args.no_absorb)
    if why == "busy":
        print(f"ctx: {d} is busy", file=sys.stderr)
        return 1
    if why == "exists":  # created while we waited for the lock
        print(f"ctx: {d} already has a CONTEXT.md")
        return 0
    if why == "foreign":
        print(f"ctx: {d}/CONTEXT.md is not a ctx file (no `{MARKER}: 1` header): "
              "left untouched; move or rename it to create a node here", file=sys.stderr)
        return 1
    if why == "adopted":
        print(f"ctx: node created at {d} from a CONTEXT.md ctx did not write here: its Notes "
              "are kept, the rest starts anew (old file: `ctx undo`)")
        return 0
    excluded = git_info_dir(d) is not None
    print(f"ctx: node created at {d}" + (" (excluded from git)" if excluded else ""))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """`ctx sync`: sync one session, one node, or every pending session now."""
    if args.session:
        r = sync_session(args.session)
        print(f"ctx: session {args.session}: {r}")
        return 1 if r in ("fail", "nosession") else 0
    if args.dir:
        d = real(args.dir)
        if not has_ctx(d):
            print(f"ctx: {d} is not a node", file=sys.stderr)
            return 1
        print(f"ctx: {sync_node(d, [], [], cli_rewriter)}")
        return 0
    found = catch_up(spawn=False, close=False)
    for sid in found:
        print(f"ctx: session {sid}: {sync_session(sid)}")
    if not found:
        print("ctx: nothing to sync")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """`ctx show`: print what a session starting in a folder receives (or the tree)."""
    if args.tree:
        return show_tree()
    text, kind = build_injection(real(args.dir), cmd=human_cmd())
    if text is None:
        print("(nothing is injected here)")
        return 0
    print(text)
    print(f"\n-- {kind}: {len(text)} characters injected at session start")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    """`ctx undo`: restore the previous version of a node's CONTEXT.md."""
    d = real(args.dir)
    if not has_ctx(d):
        print(f"ctx: {d} is not a node", file=sys.stderr)
        return 1
    h = history_dir(d)
    snaps = sorted(x for x in h.iterdir() if x.suffix == ".md") if h.is_dir() else []
    if not snaps:
        print("ctx: no history for this node", file=sys.stderr)
        return 1
    with dirlock(d) as got:
        if not got:
            print("ctx: node is busy", file=sys.stderr)
            return 1
        atomic_write(d / "CONTEXT.md", snaps[-1].read_text())
        note_written(d, snaps[-1].read_text())
        snaps[-1].unlink()
        refresh_subs(d)
    bubble(d)
    log("undo", node=d)
    print(f"ctx: restored previous version of {d}/CONTEXT.md")
    return 0


LOG_KV = re.compile(r"(\w+)=(.*?)(?= \w+=|$)")
FAIL_EVENTS = re.compile(r"fail|error|timeout|abandon")


def read_log() -> list[tuple[datetime, str, dict[str, str]]]:
    """Log entries [(time, event, {k: v})] from log.1 then log (`ISO event k=v k=v` lines)."""
    out = []
    for name in ("log.1", "log"):
        f = state_dir() / name
        for line in f.read_text(errors="replace").splitlines() if f.is_file() else []:
            p = strip_control(line).split(" ", 2)
            try:
                t = datetime.fromisoformat(p[0])
            except (ValueError, IndexError):
                continue
            if len(p) > 1:
                out.append((t, p[1], dict(LOG_KV.findall(p[2] if len(p) > 2 else ""))))
    return out


def cmd_log(args: argparse.Namespace) -> int:
    """`ctx log`: print the last log lines, the failures (--fail) or one session (--session)."""
    def wanted(e, kv):
        """The line passes --fail and --session."""
        sessions = (kv.get("session"), *kv.get("sessions", "").split(","))
        return ((not args.fail or FAIL_EVENTS.search(e))
                and (not args.session or args.session in sessions))
    entries = [(t, e, kv) for t, e, kv in read_log() if wanted(e, kv)]
    for t, e, kv in entries if args.session else entries[-args.n:]:
        shown = {k: v for k, v in kv.items() if not (args.session and k == "session")}
        pairs = " ".join(f"{k}={clean(v)}" for k, v in shown.items())
        print(f"{t:%Y-%m-%d %H:%M:%S} {clean(e, 40):<14} " + pairs)
    if not entries:
        print("ctx: no matching log line")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """`ctx doctor`: list problems and state; with --fix, prune the state of vanished folders."""
    nodes = all_nodes()
    problems = [p for n in nodes for p in check_node(n)]
    unreachable = []
    gone = orphans(unreachable)
    if args.fix:
        fix_orphans(gone)
    else:
        problems += orphan_problems(gone)
    for src in unreachable:
        print(f"ctx doctor: unreachable: {clean(src)} (parent folder absent or unmounted): "
              "kept, not pruned")
    problems += check_config() + check_abandoned() + check_failed_syncs() + check_rewriter()
    for line in foreign_notes() + [info for n in nodes for info in node_notes(n)]:
        print(f"ctx doctor: {line}")
    print_report(problems, nodes, read_log())
    return 1 if problems else 0


def cmd_install_hooks(args: argparse.Namespace) -> int:
    """`ctx install-hooks`: add (or remove) the ctx hooks in a CLI's config, after a diff."""
    if args.cli not in (*CLIENTS, None) or (args.cli is None and not args.remove):
        print(f"ctx: supported clients: {', '.join(CLIENTS)}", file=sys.stderr)
        return 1
    if args.remove:
        return remove_hooks(args)
    target = Path(args.settings_file) if args.settings_file else base() / CLIENTS[args.cli]["file"]
    target = Path(os.path.realpath(target))  # write through a symlink, never replace it
    cur = {}
    if target.exists():
        try:
            cur = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"ctx: {target} is not valid JSON ({e}); fix or remove it first, nothing written",
                  file=sys.stderr)
            return 1
        if not isinstance(cur, dict):
            print(f"ctx: {target} is not a JSON object; nothing written", file=sys.stderr)
            return 1
    try:
        new = merged_settings(cur, args.cli)
    except ValueError as e:
        print(f"ctx: {target}: {e}", file=sys.stderr)
        return 1
    # the first backup is the pre-install config: a re-install keeps it
    bak = Path(str(target) + ".ctx-bak")
    backup = not (bak.exists() and unmerged(cur, args.cli) != cur)
    return review_write(target, cur, new, args, json.dumps(new, indent=2) + "\n", backup=backup)


def cmd_decline(args: argparse.Namespace) -> int:
    """`ctx decline` (internal): stop asking to create a node in a folder."""
    add_declined(args.dir)
    print(f"ctx: {real(args.dir)} declined; `{human_cmd()} init` reverses it")
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """`ctx hook` (internal): answer a CLI hook event; always exits 0."""
    out = guarded_hook(args)
    if out:
        sys.stdout.write(json.dumps(out))
    elif CLIENTS[args.client].get("empty"):
        sys.stdout.write(CLIENTS[args.client]["empty"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The `ctx` argument parser: 7 visible commands, 2 internal ones hidden from --help."""
    parser = argparse.ArgumentParser(
        prog="ctx",
        description=(
            "Per-folder context (CONTEXT.md) for coding agents. A node is a folder whose "
            "CONTEXT.md carries the `ctx: 1` header: it is injected into agent sessions "
            "started under it and rewritten after them."
        ),
        epilog="internal subcommands (not for humans): hook, decline",
    )
    parser.add_argument("--version", action="version", version=f"ctx {__version__}")
    # the listed commands; `hook` and `decline` are added without help=, so they stay unlisted
    visible = ("init", "sync", "show", "undo", "doctor", "log", "install-hooks")
    subparsers = parser.add_subparsers(dest="cmd", required=True,
                                       metavar="{" + ",".join(visible) + "}")
    _add_node_commands(subparsers)
    _add_maintenance_commands(subparsers)
    _add_internal_commands(subparsers)
    return parser


def _add_node_commands(subparsers):
    """Subcommands that act on nodes: init, sync, show, undo."""
    p = subparsers.add_parser("init", help="create a node (CONTEXT.md) in a folder")
    p.add_argument("dir", nargs="?", default=".", help="folder (default: current)")
    p.add_argument(
        "--no-absorb",
        action="store_true",
        help=(
            "container (~, courses, projects): only take work done in the folder itself; "
            "subfolders without a node get the 'create a context?' question"
        ),
    )
    p.set_defaults(f=cmd_init)

    p = subparsers.add_parser("sync", help="rewrite now what sessions touched")
    p.add_argument("dir", nargs="?", help="node to rewrite now (default: every pending session)")
    p.add_argument("--session", help="sync this session id only (retries an abandoned one)")
    p.set_defaults(f=cmd_sync)

    p = subparsers.add_parser("show", help="print what a session starting in a folder receives")
    p.add_argument("dir", nargs="?", default=".", help="folder (default: current)")
    p.add_argument("--tree", action="store_true", help="list all nodes with their summaries")
    p.set_defaults(f=cmd_show)

    p = subparsers.add_parser("undo", help="restore the previous version of a node's CONTEXT.md")
    p.add_argument("dir", nargs="?", default=".", help="node (default: current folder)")
    p.set_defaults(f=cmd_undo)


def _add_maintenance_commands(subparsers):
    """Subcommands that check or configure: doctor, log, install-hooks."""
    p = subparsers.add_parser(
        "doctor", help="check the nodes and the state; --fix removes the state of vanished folders"
    )
    p.add_argument(
        "--fix",
        action="store_true",
        help=f"remove state/history of folders gone for {PURGE_DAYS} days",
    )
    p.set_defaults(f=cmd_doctor)

    p = subparsers.add_parser("log", help="read ~/.ctx/log, filtered")
    p.add_argument("-n", type=int, default=40, help="number of lines shown (default: 40)")
    p.add_argument("--session", help="every line of this session id")
    p.add_argument("--fail", action="store_true", help="failures only")
    p.set_defaults(f=cmd_log)

    p = subparsers.add_parser(
        "install-hooks", help="add (or --remove) the ctx hooks in a CLI's config"
    )
    p.add_argument("cli", nargs="?", help=f"{'|'.join(CLIENTS)}; none with --remove means all")
    p.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
    p.add_argument("--yes", action="store_true", help="write without asking")
    p.add_argument(
        "--remove", action="store_true", help="remove the ctx hooks (CONTEXT.md files are kept)"
    )
    p.add_argument("--settings-file", help=argparse.SUPPRESS)
    p.set_defaults(f=cmd_install_hooks)


def _add_internal_commands(subparsers):
    """Hidden subcommands: `decline` is run by the agent after a "no", `hook` by the CLIs."""
    p = subparsers.add_parser("decline")
    p.add_argument("dir", nargs="?", default=".")
    p.set_defaults(f=cmd_decline)

    p = subparsers.add_parser("hook")
    p.add_argument("name", choices=["session-start", "post-tool-use", "stop", "session-end"])
    p.add_argument("--client", default="claude", choices=list(CLIENTS))
    p.set_defaults(f=cmd_hook)


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse `argv` and run the command; returns the exit code."""
    args = build_parser().parse_args(argv)
    private_umask()
    return args.f(args)
