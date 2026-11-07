# Security

The hook writes nothing, sends nothing over the network, runs no program and never opens a `.git` folder. Only `ctx.py
install --write` writes a file: it merges the hook into `~/.claude/settings.json` (or the `--settings` path), keeps
the file as it was before the first run as `.bak-ctx`, and writes nothing if the file is not valid JSON. The hook
reads the first 4 KB of each regular `STATE.md` file you own (with a single name: hard links are skipped) below the session's folder (opened
without following links and without blocking; symlinked folders are not entered) and prints the first non-empty line
of each, normalized (NFKC), flattened to one line, with known token formats and `user:password@` URLs redacted, capped
at 200 characters as written (plus what normalization adds, up to 20) and written as a JSON string with `<` and `>`
escaped (as are folder names), under a header that calls them quotes, not instructions. The scan is bounded in time
(about one second) and in folders (20000), shared between the folders directly below the session's folder.

Every `STATE.md` below the session's folder is shown, including one that came with a cloned repository or an archive:
its first line can contain text that imitates an instruction. Removing control and known invisible characters and
redacting known token formats is best effort: anything else passes, `password=hunter2` included. Treat a `STATE.md` as
data, as the agent is told.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
