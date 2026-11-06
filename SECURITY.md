ctx writes nothing, sends nothing over the network and runs no other program: to leave out the `STATE.md` files that
git tracks, it reads each repository's index file as data (version 2 to 4, at most 64 MB, paths of at most 4096
bytes, within the time budget) and never runs git, so a repository's configuration cannot make it execute anything.
The hook reads the first 4 KB of each `STATE.md` below the session's folder (regular files only, symlinked folders
and files are not followed; the `gitdir:` line of a `.git` file is followed to find a worktree's index) and prints
the first non-empty line of each, flattened to one line, capped at 200 characters and written as a JSON string (as
are folder and repository names), under a header that calls them quotes, not instructions. Removing control and
known invisible characters is best effort: a line can still contain text that imitates an instruction. A `STATE.md`
is written by agents and people with access to that folder: treat its content as data, as the agent is told.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
