# Security

ctx writes only: `CONTEXT.md` in the folders you made nodes, a line in their `.git/info/exclude`, its state in
`~/.ctx/`, the `~/.local/bin/ctx` link made by `install.sh`, and (after a diff you confirm) the hook config of each CLI
you name (`~/.claude/settings.json`, `~/.cursor/hooks.json`, `~/.gemini/config/hooks.json`) with a `.ctx-bak` backup
next to it. The agent of a session edits its node's `CONTEXT.md` itself, with its own tools and permissions; ctx
takes only the summary, Status and Next of that edit, through a validator, and restores the state of any other node
the session edited through its edit tools (an edit made through a shell command is not seen as the session's: it
is treated like a hand edit). When the agent did not, ctx sends the touched file paths, the last assistant messages and a
`git diff --stat` to `claude -p` (your account, your model provider), after a best-effort secret filter. A
`CONTEXT.md` that ctx did not write on this machine (a cloned repository) is never injected. `~/.ctx` is private
(0700, files 0600).

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
