"""Hook entries in the CLIs' configs (merge, review, removal)."""
import argparse
import difflib
import json
import os
import re
import shutil
from pathlib import Path

from .paths import atomic_write, base, human_cmd, self_cmd
from .hooks import CLIENTS


# hook entries ctx installs carry this marker (`env CTX_HOOK=1 python3 <path>/ctx.py hook <event>`);
# entries installed before it (`python3 <path>/ctx.py hook <event>`) are still recognised as ours
HOOK_MARKER = "CTX_HOOK=1"
HOOK_MARKER_RE = re.compile(r"(^|\s)" + re.escape(HOOK_MARKER) + r"\s.*ctx\.py'? hook ")
LEGACY_HOOK_RE = re.compile(r"(^|[/ ])ctx\.py'? hook ")  # `'?`: a path quoted by shlex.quote


def hooks_block(client: str = "claude") -> dict:
    """The hook entries of `client`, from its CLIENTS row.

    Cursor entries are flat, claude and agy entries are typed. An entry with a matcher (claude
    always, agy for PostToolUse) goes in a `{matcher, hooks: [...]}` group.
    """
    row, out = CLIENTS[client], {}
    for event, name in row["events"].items():
        command = f"env {HOOK_MARKER} {self_cmd()} hook {name}"
        if client != "claude":
            command += f" --client {client}"
        h = {"command": command, "timeout": 10}
        if client != "cursor":
            h = {"type": "command", **h}
        match = row.get("matchers", {}).get(event)
        group = dict(hooks=[h], **({"matcher": match} if match else {}))
        out[event] = [group if match or client == "claude" else h]
    return out


def is_ctx_hook_command(command: str) -> bool:
    """True for a hook command ctx wrote.

    Primary criterion: the HOOK_MARKER env prefix. Entries installed before the marker are
    recognised narrowly: `ctx.py hook ` right after a path separator or a space (LEGACY_HOOK_RE).
    """
    return bool(HOOK_MARKER_RE.search(command) or LEGACY_HOOK_RE.search(command))


def hook_ours(h: object) -> bool:
    """True when a hook entry is one ctx installed."""
    c = h.get("command", "") if isinstance(h, dict) else ""
    return isinstance(c, str) and is_ctx_hook_command(c)


def strip_ours(groups: list) -> list:
    """Groups without OUR hooks; a group mixing a user hook and ours keeps the user hook."""
    out = []
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            kept = [h for h in g["hooks"] if not hook_ours(h)]
            if kept:
                out.append(dict(g, hooks=kept))
        elif not hook_ours(g):
            out.append(g)
    return out


def entry_commands(v: object) -> list[object]:
    """Every `command` value found in a hook entry, at any depth."""
    if isinstance(v, dict):
        own = [v["command"]] if "command" in v else []
        return own + [c for k, x in v.items() if k != "command" for c in entry_commands(x)]
    return [c for x in v for c in entry_commands(x)] if isinstance(v, list) else []


def ours_entry(v: object) -> bool:
    """agy: the named entry "ctx" is ours only if each of its commands is a ctx hook command."""
    cmds = entry_commands(v)
    return bool(cmds) and all(isinstance(c, str) and is_ctx_hook_command(c) for c in cmds)


def merged_settings(cur: dict, client: str = "claude") -> dict:
    """`cur` with the ctx hooks of `client` replacing any earlier ctx hooks."""
    new = json.loads(json.dumps(cur))
    if client == "agy":  # top level = named hooks: ours is the entry "ctx"
        if "ctx" in new and not ours_entry(new["ctx"]):
            raise ValueError('the config already has a user entry named "ctx"; '
                             "rename it first, nothing written")
        new["ctx"] = hooks_block(client)
        return new
    if client == "cursor":
        new.setdefault("version", 1)
    hooks = new.setdefault("hooks", {})
    for event, groups in hooks_block(client).items():
        hooks[event] = strip_ours(hooks.get(event, [])) + groups
    return new


def review_write(target: Path, cur: dict, new: dict, args: argparse.Namespace, text: str | None,
                 backup: bool = True) -> int:
    """Show the diff cur -> new, confirm, then write `text` (None deletes the file).

    Shared by install-hooks and install-hooks --remove.
    """
    a_txt = json.dumps(cur, indent=2, sort_keys=False).splitlines()
    b_txt = json.dumps(new, indent=2, sort_keys=False).splitlines()
    diff = list(difflib.unified_diff(a_txt, b_txt, f"{target} (current)", f"{target} (proposed)",
                                     lineterm=""))
    if not diff:
        print(f"ctx: {target} already up to date")
        return 0
    print("\n".join(diff))
    if args.dry_run:
        print(f"\nctx: dry run, {target} not modified")
        return 0
    if not args.yes:
        try:
            ans = input(f"\nWrite these changes to {target}? [y/N] ").strip().lower()
        except EOFError:  # no terminal to ask on
            print("\nctx: no terminal to confirm on: treated as no, nothing written "
                  "(add --yes to apply)")
            return 1
        if ans != "y":
            print("ctx: aborted")
            return 1
    if text is None:
        target.unlink()
        print(f"ctx: {target} removed (it only held ctx hooks)")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.exists():
        shutil.copy2(target, str(target) + ".ctx-bak")
    atomic_write(target, text)
    print("ctx: written" + (f" (backup: {target}.ctx-bak)" if backup else ""))
    return 0


def unmerged(cur: dict, client: str) -> dict:
    """Inverse of merged_settings: `cur` without the ctx hooks.

    Empty containers and a lone cursor `version` are pruned.
    """
    new = json.loads(json.dumps(cur))
    if client == "agy":
        if "ctx" in new and ours_entry(new["ctx"]):
            del new["ctx"]
        return new
    hooks = new.get("hooks", {})
    for event in list(hooks):
        hooks[event] = strip_ours(hooks[event])
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        new.pop("hooks", None)
    if client == "cursor" and list(new) == ["version"]:
        new = {}
    return new


def remove_hooks(args: argparse.Namespace) -> int:
    """`install-hooks --remove [cli]`: remove the ctx hooks, after a diff.

    The reverse diff is shown per CLI (all of them when none is named) and confirmed before writing.
    """
    exit_code = 0
    for cli in [args.cli] if args.cli else CLIENTS:
        target = Path(os.path.realpath(args.settings_file or base() / CLIENTS[cli]["file"]))
        try:
            cur = json.loads(target.read_text(encoding="utf-8"))
            new = unmerged(cur, cli)
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if new == cur:
            continue
        print(f"== {cli}: ctx hooks in {target}")
        bak = Path(str(target) + ".ctx-bak")
        try:  # restore the pre-install bytes when the backup is the same config minus our hooks
            old = bak.read_text(encoding="utf-8")
            same = ({k: v for k, v in json.loads(old).items() if k != "version"}
                    == {k: v for k, v in new.items() if k != "version"})
        except (OSError, ValueError, AttributeError):
            same = False
        text = old if same else (json.dumps(new, indent=2) + "\n" if new else None)
        r = review_write(target, cur, new, args, text, backup=False)
        exit_code = exit_code or r
        if r == 0 and not args.dry_run and bak.exists():
            if same:
                bak.unlink()
            else:
                print(f"ctx: backup kept at {bak} "
                      "(the config changed since install; delete it yourself if unneeded)")
    link = base() / ".local/bin/ctx"
    tail = f"; the link {link} is yours to remove: rm {link}" if link.is_symlink() else ""
    print(f"ctx: no CONTEXT.md was touched (`{human_cmd()} show --tree` lists them)" + tail)
    return exit_code
