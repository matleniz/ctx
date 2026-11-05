# Supported CLIs

Official documentation used: Claude Code <https://code.claude.com/docs/en/hooks>, Cursor <https://cursor.com/docs/hooks>
and <https://cursor.com/docs/cli/headless>, agy <https://antigravity.google/docs/hooks>,
<https://antigravity.google/docs/rules>, <https://antigravity.google/docs/cli/headless>,
<https://antigravity.google/docs/permissions?tab=cli>.

| | claude | cursor (`cursor-agent`) | agy (antigravity) | opencode |
|---|---|---|---|---|
| Install | `ctx install-hooks claude` | `ctx install-hooks cursor` | `ctx install-hooks agy` | not supported |
| Config written | `~/.claude/settings.json` | `~/.cursor/hooks.json` | `~/.gemini/config/hooks.json` | – |
| Injection | `SessionStart` hook, `hookSpecificOutput.additionalContext` | `sessionStart` hook, `additional_context` | `SessionStart` hook, `injectSteps` system message | none |
| Collection | `PostToolUse` on Write/Edit/MultiEdit/NotebookEdit; `Stop` keeps the last assistant message | `afterFileEdit` (`file_path`); no assistant message (`stop` input is undocumented, not used) | `PostToolUse` on edit tools; `Stop` not used | – |
| End of session | `SessionEnd` starts a detached sync | `sessionEnd` | none: synced at the next `SessionStart` | – |
| Rewriter | yes, `claude -p --model haiku` (the only one) | no (hooks only) | no (hooks only) | – |
| Missing | nothing known | no last messages, only touched files | no session end, no last messages | everything |

Each installed hook command reads `env CTX_HOOK=1 python3 <path>/ctx.py hook <event>`: the `CTX_HOOK=1` marker is how
`install-hooks` (and `--remove`) tells its entries from yours; an entry without it is never touched.

## Observed behaviour

- Cursor: `sessionStart`, `afterFileEdit` and `sessionEnd` fire under `cursor-agent -p`, and `additional_context` reaches the
  model; the docs never say hooks run headless. `-p` needs `--trust`/`--force`.
- agy: in `agy -p` the edit hook sends `toolCall.name = edit_file` with `args.Path`, while the docs list `write_to_file` /
  `replace_file_content` with `TargetFile`; ctx accepts both. `SessionStart` injection works headless, which needs an
  allow-list of permissions.
- opencode: the version tried was refused (HTTP 426), so it is untested and unsupported.

Why cursor and agy cannot rewrite, and what another CLI must offer to be added: [how-it-works.md](how-it-works.md#rewriter).
