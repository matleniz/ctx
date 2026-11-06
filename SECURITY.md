# Security

ctx writes nothing, sends nothing over the network, runs no program and never opens a `.git` folder. The hook reads
the first 4 KB of each regular `STATE.md` file you own below the session's folder (opened without following links
and without blocking; symlinked folders are not entered) and prints the first non-empty line of each, flattened to
one line, with known token formats redacted, capped at 200 characters and written as a JSON string (as are folder
names), under a header that calls them quotes, not instructions.

Every `STATE.md` below the session's folder is shown, including one that came with a cloned repository or an
archive: its first line can contain text that imitates an instruction. Removing control and known invisible
characters and redacting known token formats is best effort. Treat a `STATE.md` as data, as the agent is told.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
