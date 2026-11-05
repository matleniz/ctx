#!/bin/sh
# ctx installer: checks python3 >= 3.10 and a supported CLI, links ~/.local/bin/ctx -> ctx.py. Idempotent.
# Changes nothing else: the hook and init commands are printed, not run. Undo: ctx install-hooks --remove, then rm the link.
set -eu
here=$(cd "$(dirname "$0")" && pwd -P)
bin="${HOME:?}/.local/bin"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || { echo "ctx: python3 >= 3.10 is required" >&2; exit 1; }
found=
for c in claude cursor-agent agy; do command -v "$c" >/dev/null 2>&1 && found="$found $c"; done
[ -n "$found" ] || { echo "ctx: none of claude, cursor-agent, agy is on PATH" >&2; exit 1; }
mkdir -p "$bin"
if [ -L "$bin/ctx" ]; then
  if [ "$(readlink "$bin/ctx")" != "$here/ctx.py" ]; then echo "ctx: $bin/ctx is a symlink to $(readlink "$bin/ctx") (not this clone); remove it yourself if you want this one" >&2; exit 1; fi
  echo "ctx: link $bin/ctx already in place"
elif [ -e "$bin/ctx" ]; then echo "ctx: $bin/ctx exists and is not a symlink; not touching it" >&2; exit 1
else ln -s "$here/ctx.py" "$bin/ctx"; echo "ctx: linked $bin/ctx -> $here/ctx.py"; fi
chmod +x "$here/ctx.py"
case ":$PATH:" in *":$bin:"*) ;; *) echo "ctx: $bin is not in PATH; add it: export PATH=\"$bin:\$PATH\"" ;; esac
# `ctx` when that name on PATH is this link, else the full command (works before PATH is fixed)
if [ "$(command -v ctx 2>/dev/null)" = "$bin/ctx" ]; then cmd=ctx; else cmd="python3 '$here/ctx.py'"; fi
echo "Next (not run):"
for c in $found; do case $c in cursor-agent) n=cursor ;; *) n=$c ;; esac; echo "  $cmd install-hooks $n"; done
echo "  $cmd init --no-absorb ~"
echo "  $cmd init <project>"
