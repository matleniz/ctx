# Security

ctx writes nothing and sends nothing over the network. The hook reads the first 4 KB of each `STATE.md` below the
session's folder (regular files only, symlinks are not followed) and prints the first non-empty line of each,
without control characters and capped at 200 characters, framed as quotes rather than instructions. A `STATE.md`
is written by agents and people with access to that folder: treat its content as data, as the agent is told.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
