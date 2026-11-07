# What the scan does

Detail behind the "What a session receives" section of the [README](../README.md). Numbers are the constants of
`ctx.py` (`MAX_DEPTH`, `MAX_LINES`, `PER_GROUP`, `MAX_CHARS`, `SLACK`, `BUDGET_S`, `MAX_DIRS`).

## Where it looks

- Up to 4 levels below the start folder. `~/projects/<p>/wt/<branch>` is level 4.
- Hidden folders are skipped. So are symlinked folders and folders named `node_modules`, `venv`, `env`,
  `__pycache__`, `dist`, `build`, `target` or `site-packages`.
- A folder with a `.git` entry is a repository. Its `.git` is never opened.

## What it reads

- A `STATE.md` that is a regular file, owned by you, with a single name. A hard link could be any file (a `.env`)
  under the name `STATE.md`, so it is skipped.
- The file is opened without following links and without blocking, then checked with `fstat`. It cannot be swapped
  in between.
- Only the first 4096 bytes are read.
- The line shown is the first non-empty one. An optional front matter block (`---` ... `---`) is skipped first.
  A Markdown heading mark (`#`, `##`...) is removed from the line.

## How many lines

- 30 lines at most, newest first. A file dated in the future sorts last and is shown as "future date".
- At most 8 per group. A group is a git repository below the start folder. Outside git, it is the folder one or two
  levels below the start folder.
- The folders directly below the start folder take turns for the 30 lines. Inside each, its groups take turns. One
  clone, one archive or many fake repositories cannot push the others out.
- Entries left out are counted, and the first ones are named: "(+N more in ...: at most 8 per repository or
  folder)" for the per-group cap, "(+N more not shown: ...)" for the 30-line cap.

## Cleaning

Applied to each first line, and to the folder names (names are also capped at 120 characters).

1. Normalized with NFKC. Undecodable bytes and non-printable characters, newlines included, become spaces. The
   result is one line.
2. Invisible marks that survive NFKC are dropped, not replaced by a space: variation selectors, U+034F, the Hangul
   fillers, the Mongolian free variation selectors, the braille blank. Without this, `[c<mark>tx]` could look like
   `[ctx]` and pass the check in step 4. Other combining marks (Thai, Devanagari) stay.
3. Redaction. These become `[redacted]`:
   - `sk-` keys (3 digits and a capital letter at least, 20+ characters; `sk-feature-x` is not one);
   - `sk_live_`, `sk_test_`, `rk_live_`, `rk_test_`;
   - GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), GitLab (`glpat-`), Hugging Face
     (`hf_`), npm (`npm_`), Slack tokens (`xoxa-`, `xoxb-`, `xoxp-`, `xoxr-`, `xoxs-`) and `hooks.slack.com/...`
     webhooks;
   - AWS access key ids (`AKIA` + 16), Google API keys (`AIza...`);
   - JSON Web Tokens (`eyJ...`) and `-----BEGIN ... PRIVATE KEY-----` headers;
   - the password in `scheme://user:password@host` URLs.

   A token must start at a word boundary. Nothing else is redacted: no `password=`, no long random runs, no
   base64 keys, no bearer tokens.
4. Capped at 200 characters as written. Normalization can make a line longer (`…` becomes `...`); up to 20
   characters of that growth are allowed. The cut is made on a space when one is in the second half.
5. Shown as a JSON string. Quotes are escaped, `<` and `>` become `\u003c` and `\u003e` (no tag can be closed), and
   look-alikes of the `[ctx]` marker (other brackets, full-width, Cyrillic or Greek letters, combining marks:
   a line where marks hide a marker is shown without its marks) become `(ctx)`.

## Time

- The scan stops after 1 second. The clock is checked before each folder read and at each entry.
- It enters 20000 folders at most. Both limits are shared between the folders directly below the start folder:
  the walks take turns, one folder read at a time. A read that would pass the cap takes half of what is left.
- A scan cut short says so: "(scan cut short: some folders were not visited)". If nothing was found, the notice is
  "No STATE.md found below ... before the scan was cut short".
- The hook has a 5-second timeout (set in the block `install` produces). A hung network mount is bounded by it.
- The hook always exits 0. It prints nothing when it found nothing and the scan was complete. An error goes to
  stderr and never blocks the session.
