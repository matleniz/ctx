# ctx

[![tests](https://github.com/matleniz/ctx/actions/workflows/tests.yml/badge.svg)](https://github.com/matleniz/ctx/actions/workflows/tests.yml)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/ctx-dark.svg">
  <img src="docs/ctx.svg" alt="A folder tree: ~, projects/app, and courses/algebra/ml-intro. Each folder has an AGENTS.md card and a CONTEXT.md card. State goes up: vertical arrows carry each folder's one-line summary from its CONTEXT.md to the CONTEXT.md of its parent, up to ~. Rules come down: the AGENTS.md rules of each folder are inherited natively by its children. Rewrite after the session: under ml-intro, a terminal icon (your session ends) points up into its CONTEXT.md, which is rewritten by one model call (document and pencil icon).">
</picture>

Per-folder memory for coding agents. Each folder you choose holds a `CONTEXT.md`: the agent receives it when a
session starts and updates its Status, Next and summary itself before finishing; only when it did not does one model
call rewrite them after the session. Such a folder is a *node*: `ctx init` wrote its `CONTEXT.md` (header `ctx: 1`)
on this machine; a cloned or committed one is not trusted. A folder's one-line summary is copied into its parent's
`CONTEXT.md`, and a session also gets its node's parent line (never siblings). Python 3.10+ standard library only.

## 5 minutes

You need Python 3.10+ and one of `claude`, `cursor-agent`, `agy`. The rewrite itself needs `claude`.

```
git clone https://github.com/matleniz/ctx && cd ctx
./install.sh
ctx install-hooks claude
```

`install.sh` only links `~/.local/bin/ctx` and prints what to run next. `ctx install-hooks` shows a diff and asks
before writing (other CLIs: [clients](docs/clients.md)). Keep the clone where it is: the installed hooks call
`<clone>/ctx.py`; update with `git pull`.

First node, then work as usual:

```
ctx init ~/projects/my-app
cd ~/projects/my-app && claude
```

What you see: `ctx show` prints the context a session here receives and its size. When the session ends,
`CONTEXT.md` in `my-app` holds the new "Status" and "Next" (the agent's, or the rewrite's); run `ctx show` to read
it and `ctx undo` to go back one version. If something looks wrong: `ctx doctor`, then `ctx log --fail`.

In a folder with no node (and none above that covers it) the agent asks once whether to create one. `ctx init --no-absorb ~` makes a container
node for your home that only covers its own folder. Folders matching a glob in `~/.ctxignore` are never nodes.
`CTX_OFF=1` turns every hook into a no-op.

## Commands

There are 7 commands. Two internal ones, `decline` and `hook`, are not listed.

```
ctx init [--no-absorb] [dir]   create a node (CONTEXT.md)
ctx sync [--session ID] [dir]  rewrite now what a session touched
ctx show [--tree] [dir]        print what a session in dir receives; --tree lists all nodes
ctx undo [dir]                 restore the previous version of a node's CONTEXT.md
ctx doctor [--fix]             check nodes and state; --fix drops the state of gone folders
ctx log [-n N] [--session ID] [--fail]   read `~/.ctx/log` (grep it for a node or a time)
ctx install-hooks <cli> [--dry-run] [--yes] [--remove]   add (or remove) the ctx hooks of a CLI
ctx --version                  print the version
```

Files, size caps, config and environment: [reference](docs/reference.md). Design and failure handling:
[how it works](docs/how-it-works.md). Which claim is tested by what: [readme-claims](docs/readme-claims.md).

## Privacy

- After a session without the agent's own update, one `claude -p` call (your account) receives the touched paths,
  the last 3 assistant messages (2000 characters each), the node's `git diff --stat` and its old Status, Next and
  Decisions, after a best-effort secret filter. Your agent's model reads `CONTEXT.md` at every session start.
- `~/.ctx` (mode 0700) keeps session data 7 days, the last 10 versions of each `CONTEXT.md` and each node's last
  state; `private: true` in a node's header shows `(private)` in its parent instead of its summary.

## Measured

10-session chains ([long-run benchmark](docs/benchmark-longrun.md), run 3), 1.1.0 -> 1.2.0: continuity 6.2 -> 8.3 /9
(course notebooks), 5.9 -> 7.6 (small project); a lone `STATE.md` rule scores 8.5 and 8.6: ctx adds the bubbling up.

## Uninstall

`ctx install-hooks --remove` asks before undoing each hook diff and restores the CLI's config. Your `CONTEXT.md`
files are not touched (`ctx show --tree` lists them). Delete the link and the state yourself:
`rm ~/.local/bin/ctx`, `rm -r ~/.ctx`.

## Limits

- The rewrite needs `claude`; cursor and agy are hook clients only.
- Linux, WSL and macOS only (file locks use `fcntl`). opencode is not supported.
- Filtering and isolation are best effort: see [how it works](docs/how-it-works.md).

## License

MIT, see [LICENSE](LICENSE).
