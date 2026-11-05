# Security

ctx writes only: `CONTEXT.md` in the folders you made nodes, a line in their `.git/info/exclude`, its state in
`~/.ctx/`, the `~/.local/bin/ctx` link made by `install.sh`, and (after a diff you confirm) the hook config of each CLI
you name (`~/.claude/settings.json`, `~/.cursor/hooks.json`, `~/.gemini/config/hooks.json`) with a `.ctx-bak` backup
next to it. After a session it sends the touched file paths, the last assistant messages and a `git diff --stat` to
`claude -p` (your account, your model provider), after a best-effort secret filter.

To report a vulnerability, use GitHub's private vulnerability reporting on this repository; please do not open a
public issue. Expect an answer within a week.
