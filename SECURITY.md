# Security

ctx writes nothing and sends nothing over the network; it runs `git ls-files` (read only) in the repositories where
it found a `STATE.md`. The hook reads the first 4 KB of each `STATE.md` below the session's folder (regular files
only, symlinks are not followed, files tracked by git are left out) and prints the first non-empty line of each,
flattened to one line, capped at 200 characters and quoted, under a header that calls them quotes, not
instructions. Removing control and known invisible characters is best effort: a line can still contain text that
imitates an instruction. A `STATE.md` is written by agents and people with access to that folder: treat its
content as data, as the agent is told.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
