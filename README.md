# ctx

[![tests](https://github.com/matleniz/ctx/actions/workflows/tests.yml/badge.svg)](https://github.com/matleniz/ctx/actions/workflows/tests.yml)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/ctx-dark.svg">
  <img src="docs/ctx.svg" alt="A folder tree: ~, projects/app, and courses/algebra/ml-intro. Each folder has an AGENTS.md card and a CONTEXT.md card. State goes up: vertical arrows carry each folder's one-line summary from its CONTEXT.md to the CONTEXT.md of its parent, up to ~. Rules come down: the AGENTS.md rules of each folder are inherited natively by its children. Rewrite after the session: under ml-intro, a terminal icon (your session ends) points up into its CONTEXT.md, which is rewritten by one model call (document and pencil icon).">
</picture>

Per-folder memory for coding agents. Each folder you choose holds a `CONTEXT.md`: the agent receives it when a
session starts and updates it before finishing (or one model call rewrites it afterwards). Such a folder is a *node*: its
`CONTEXT.md` carries a `ctx: 1` header line, written by `ctx init`. A folder's one-line summary is
copied into its parent's `CONTEXT.md`, and a session also gets one `parent <name>: <summary>` line of its node's parent (never siblings). Python 3.10+ standard library, no dependency.

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
`CONTEXT.md` in `my-app` is rewritten (its "Status" and "Next" sections); run `ctx show` to read it and `ctx undo`
to go back one version. If something looks wrong: `ctx doctor`, then `ctx log --fail`.

Work in a git repository no node covers makes its root a node; elsewhere the agent asks once whether to create one. `ctx init --no-absorb ~` makes a container
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
ctx log [-n N] [--node DIR] [--session ID] [--since 2h] [--fail]   read `~/.ctx/log`
ctx install-hooks <cli> [--dry-run] [--yes] [--remove]   add (or remove) the ctx hooks of a CLI
ctx --version                  print the version
```

Files, size caps, config and environment: [reference](docs/reference.md). Design and failure handling:
[how it works](docs/how-it-works.md). Which claim is tested by what: [readme-claims](docs/readme-claims.md).

## Measured

Measured once (2026-11-04) on 8 of the author's folders x 2 questions x 5 runs per arm, Haiku agent, Sonnet judge
blind to the arm, `CONTEXT.md` hidden in the without arm (18 of 80 sessions still found it). With ctx, sessions used
fewer input tokens incl. cache (-19 %, 95 % CI -27 to -10; -16 % without those 18) and turns (-23 %), mostly when
resuming work; time was shorter (-17 %) but not robust. Answer quality: no measurable change (-0.1 on 0-9, CI -0.6
to +0.5; an Opus judge agrees); in a first run a one-point loss was not ruled out. A wrong `CONTEXT.md` misled the
agent (one folder, -1.2; -2.8 in the first run). One user, small sample, not reproducible: see docs/benchmark.md.

## Measured over long chains

Over 10-session chains on two synthetic projects, ctx used fewer tokens but did not improve continuity, and showed three weaknesses
(a stale `Next` trusted on resume, user decisions dropped by rewrites, sessions without file changes not recorded): see [long-run benchmark](docs/benchmark-longrun.md).

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
