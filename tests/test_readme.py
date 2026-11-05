"""README claims: every command, flag, number and path the README cites is checked against the code.

Behavioural claims that need a real agent are covered by tests/integration/run_readme.py; the map
claim -> test lives in docs/readme-claims.md and is itself checked here (every named test exists).
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
sys.path.insert(0, str(MOD / "src"))
import ctx.cli  # noqa: E402

README = (MOD / "README.md").read_text()
CLAIMS = (MOD / "docs/readme-claims.md").read_text()
REFERENCE = (MOD / "docs/reference.md").read_text()
CLIENTS_DOC = (MOD / "docs/clients.md").read_text()
CTX_PY = MOD / "ctx.py"


def run(args, home, inp=None, extra_env=None, path=None):
    env = dict(os.environ, CTX_HOME=str(home))
    for k in ("CTX_OFF", "CTX_INNER"):
        env.pop(k, None)
    env.update(extra_env or {})
    if path:
        env["PATH"] = path + os.pathsep + env["PATH"]
    return subprocess.run([sys.executable, str(CTX_PY), *args], input=inp, capture_output=True, text=True,
                          env=env, timeout=60)


def help_of(*sub):
    return run([*sub, "-h"], tempfile.gettempdir()).stdout


def module_run(*args):
    """`python3 -m ctx ...` with src/ on PYTHONPATH (the package entry point, not the launcher)."""
    env = dict(os.environ, PYTHONPATH=str(MOD / "src"))
    return subprocess.run([sys.executable, "-m", "ctx", *args], capture_output=True, text=True, env=env,
                          cwd=tempfile.gettempdir(), timeout=60)


def subcommands(help_text=None):
    m = re.search(r"\{([a-z,-]+)\}", help_of() if help_text is None else help_text)
    return m.group(1).split(",")


def code_lines():
    """Command lines in fenced blocks and inline `ctx ...` spans of the README."""
    out = []
    for block in re.findall(r"```\n(.*?)```", README, re.S):
        out += [ln.strip() for ln in block.splitlines() if ln.strip().startswith("ctx ")]
    out += re.findall(r"`(ctx [^`]+)`", README)
    return out


class Readme(unittest.TestCase):
    def test_length_and_links(self):
        self.assertLessEqual(len(README.splitlines()), 90)
        for target in re.findall(r"\]\(([^)#]+)\)", README):
            if not target.startswith("http"):
                self.assertTrue((MOD / target).exists(), target)

    def test_five_minutes_page_comes_first(self):
        heads = re.findall(r"^## (.+)$", README, re.M)
        self.assertEqual(heads[0], "5 minutes")
        block = re.search(r"## 5 minutes\n.*?```\n(.*?)```", README, re.S).group(1)
        self.assertEqual(len([ln for ln in block.splitlines() if ln.strip()]), 3, "install in 3 commands")

    def test_install_block(self):
        self.assertIn("./install.sh", README)
        self.assertTrue(os.access(MOD / "install.sh", os.X_OK))
        self.assertIn("git clone", README)

    def test_command_list_is_the_real_one(self):
        subs = subcommands()  # the visible ones: `decline` and `hook` are internal and left out of --help
        self.assertEqual(len(subs), 7)
        self.assertIn(f"There are {len(subs)} ", README)
        self.assertNotIn("==SUPPRESS==", help_of())
        for hidden in ("decline", "hook"):
            self.assertEqual(run([hidden, "-h"], tempfile.gettempdir()).returncode, 0)
        listed = re.findall(r"^ctx ([a-z][a-z-]*)", re.search(r"## Commands\n\n.*?```\n(.*?)```", README, re.S).group(1), re.M)
        self.assertEqual(sorted(listed), sorted(subs))

    def test_launcher_and_package_entry_point_agree(self):
        self.assertEqual(subcommands(module_run("-h").stdout), subcommands())
        self.assertEqual(module_run("--version").stdout, run(["--version"], tempfile.gettempdir()).stdout)
        self.assertEqual(module_run("--version").stdout.strip(), f"ctx {ctx.paths.__version__}")

    def test_every_command_and_flag_exists(self):
        subs = subcommands()
        seen = 0
        for line in code_lines():
            line = line.split("   ")[0]  # drop the description column
            toks = line.split()
            if toks[1] == "<cli>" or toks[1].startswith("~"):
                continue
            if toks[1].startswith("--"):  # a top-level option such as --version
                self.assertIn(toks[1], help_of(), line)
                continue
            self.assertIn(toks[1], subs, line)
            helptxt = help_of(toks[1])
            for flag in re.findall(r"(--[a-z-]+)", line):
                self.assertIn(flag, helptxt, f"{flag} in `{line}`")
                seen += 1
        self.assertGreater(seen, 5)

    def test_version_flag_prints_the_version(self):
        self.assertIn("ctx --version", README)
        p = run(["--version"], tempfile.gettempdir())
        self.assertEqual((p.returncode, p.stdout.strip()), (0, f"ctx {ctx.paths.__version__}"))

    def test_version_is_the_latest_changelog_entry(self):
        top = re.search(r"^## (\S+)", (MOD / "CHANGELOG.md").read_text(), re.M).group(1)
        self.assertEqual(top, ctx.paths.__version__)

    def test_ctxignore_and_clone_location_are_documented(self):
        self.assertIn("`~/.ctxignore`", README)
        self.assertIn("Keep the clone where it is", README)
        self.assertIn("`<clone>/ctx.py`", README)
        self.assertTrue(all(f"{ctx.paths.self_cmd()} hook " in h["command"] for e in ctx.install.hooks_block().values()
                            for g in e for h in g["hooks"]))  # the hooks call this file by its path

    def test_clients_named_in_readme_are_supported(self):
        for cli in ("claude", "cursor", "agy"):
            self.assertIn(cli, ctx.hooks.CLIENTS)
            self.assertEqual(run(["install-hooks", cli, "--dry-run"], tempfile.mkdtemp()).returncode, 0)
        self.assertNotIn("opencode", ctx.hooks.CLIENTS)
        self.assertIn("opencode is not supported", README)
        for cli, rel in (("claude", "~/.claude/settings.json"), ("cursor", "~/.cursor/hooks.json"),
                         ("agy", "~/.gemini/config/hooks.json")):
            self.assertEqual("~/" + ctx.hooks.CLIENTS[cli]["file"], rel)
            self.assertIn(rel, CLIENTS_DOC)

    def test_numbers_are_the_constants(self):
        for phrase in (f"max {ctx.paths.MAX_SUMMARY} characters", f"max {ctx.paths.MAX_STATUS_LINES} lines",
                       f"(max {ctx.paths.MAX_NEXT_LINES})", f"{ctx.paths.MAX_NOTES} characters max injected",
                       f"never exceeds {ctx.paths.MAX_TOTAL} characters", f"at most {ctx.paths.MAX_INJECT} characters",
                       f"With {ctx.paths.FULL_ENTRIES} children or fewer", f"cut to {ctx.paths.SHORT_WIDTH} characters",
                       f"beyond {ctx.paths.MAX_ENTRIES}", f"last {ctx.paths.HISTORY_KEEP} versions",
                       f"last {ctx.paths.LIVE_WINDOW} seconds"):
            self.assertIn(phrase, " ".join(REFERENCE.split()))

    def test_platform_limit_fcntl(self):
        self.assertIn("`fcntl`", README)
        self.assertTrue(hasattr(ctx.paths, "fcntl"))  # the locks (dirlock)

    def test_section_names_are_the_real_ones(self):
        for name in (ctx.paths.SEC_STATUS, ctx.paths.SEC_NEXT, ctx.paths.SEC_NOTES, ctx.paths.SEC_SUBS):
            self.assertIn(f'"{name}"', REFERENCE)


class Behaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "home"
        (self.home / "app").mkdir(parents=True)
        self.fake = Path(self.tmp.name) / "bin"
        self.fake.mkdir()
        out = "SUMMARY: app now has a parser\n## Status\n- parser added\n## Next\n- tests\n"
        fake = self.fake / "claude"
        fake.write_text("#!/bin/sh\nprintf '%s' " + shlex.quote(json.dumps({"result": out, "usage": {}})) +
                        "\necho \"$@\" > \"" + str(self.fake / "args") + "\"\n")
        fake.chmod(0o755)

    def hook(self, name, **ev):
        return run(["hook", name], self.home, inp=json.dumps(ev))

    def test_init_excludes_from_git_and_session_flow_writes_ctx_dir(self):
        subprocess.run(["git", "init", "-q", str(self.home / "app")], check=True)
        self.assertEqual(run(["init", str(self.home)], self.home).returncode, 0)
        self.assertEqual(run(["init", str(self.home / "app")], self.home).returncode, 0)
        ex = (self.home / "app/.git/info/exclude").read_text()
        self.assertIn("CONTEXT.md", ex)
        out = json.loads(self.hook("session-start", session_id="s1", cwd=str(self.home / "app")).stdout)
        self.assertIn("[ctx]", json.dumps(out))
        f = self.home / "app/parser.py"
        f.write_text("x = 1\n")
        self.hook("post-tool-use", session_id="s1", cwd=str(self.home / "app"), tool_name="Write",
                  tool_input={"file_path": str(f)})
        p = run(["sync", "--session", "s1"], self.home, path=str(self.fake))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        text = (self.home / "app/CONTEXT.md").read_text()
        self.assertIn("app now has a parser", text)
        self.assertIn("app now has a parser", (self.home / "CONTEXT.md").read_text())  # copied to the parent
        run(["decline", str(self.home / "other")], self.home)
        state = {p.name for p in (self.home / ".ctx").iterdir()}
        for name in ("log", "declined", "sessions", "history", "state"):
            self.assertIn(name, state)
        self.assertLessEqual(len(text), ctx.paths.MAX_TOTAL)
        args = (self.fake / "args").read_text()  # the one model call: claude -p, no tools
        self.assertIn("-p", args)
        self.assertIn("haiku", args)

    def test_rewriter_command_is_claude_p_without_tools_or_hooks(self):
        with mock.patch.object(ctx.rewriter.subprocess, "Popen") as m, mock.patch.dict(os.environ, {}, clear=False):
            m.return_value.communicate.return_value = (json.dumps({"result": "x", "usage": {}}), "")
            m.return_value.returncode = 0
            ctx.rewriter.cli_rewriter("sys", "prompt")
        cmd, kw = m.call_args[0][0], m.call_args[1]
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(kw["env"]["CTX_INNER"], "1")
        self.assertNotIn("CTX_MODEL", README)
        self.assertNotIn("CTX_REWRITER", README)
        self.assertNotIn("cursor|agy", README)

    def test_ctx_off_makes_hooks_silent(self):
        run(["init", str(self.home)], self.home)
        on = self.hook("session-start", session_id="s", cwd=str(self.home))
        off = run(["hook", "session-start"], self.home, inp=json.dumps({"session_id": "s2", "cwd": str(self.home)}),
                  extra_env={"CTX_OFF": "1"})
        self.assertTrue(on.stdout.strip())
        self.assertEqual(off.stdout.strip(), "")

    def test_injection_never_exceeds_cap(self):
        run(["init", str(self.home)], self.home)
        for i in range(30):
            d = self.home / f"c{i}"
            d.mkdir()
            run(["init", str(d)], self.home)
        (self.home / "CONTEXT.md").write_text((self.home / "CONTEXT.md").read_text() + "x" * 20000)
        with mock.patch.dict(os.environ, {"CTX_HOME": str(self.home)}):
            text, _ = ctx.hooks.build_injection(self.home)
        self.assertLessEqual(len(text), ctx.paths.MAX_INJECT)

    def test_uninstall_keeps_context_files(self):
        run(["init", str(self.home)], self.home)
        before = (self.home / "CONTEXT.md").read_bytes()
        p = run(["install-hooks", "--remove", "--yes"], self.home)
        self.assertEqual(p.returncode, 0)
        self.assertEqual((self.home / "CONTEXT.md").read_bytes(), before)
        self.assertIn(" show --tree` lists them", p.stdout)


class InstallScript(unittest.TestCase):
    def sh(self, home, bindir):
        env = dict(os.environ, HOME=str(home), PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run(["sh", str(MOD / "install.sh")], capture_output=True, text=True, env=env, timeout=60)

    def test_install_sh_never_replaces_a_foreign_or_broken_link(self):
        with tempfile.TemporaryDirectory() as t:
            home, fake = Path(t) / "home", Path(t) / "bin"
            (home / ".local/bin").mkdir(parents=True)
            fake.mkdir()
            (fake / "claude").write_text("#!/bin/sh\n")
            (fake / "claude").chmod(0o755)
            link = home / ".local/bin/ctx"
            for target in ("/bin/true", "/gone/clone/ctx.py"):  # points elsewhere / broken
                link.symlink_to(target)
                p = self.sh(home, fake)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("not this clone", p.stderr)
                self.assertEqual(os.readlink(link), target)
                link.unlink()
            self.assertEqual(self.sh(home, fake).returncode, 0)
            p = self.sh(home, fake)  # idempotent
            self.assertEqual(p.returncode, 0)
            self.assertIn("already in place", p.stdout)


class Picture(unittest.TestCase):
    FILES = ("docs/ctx.svg", "docs/ctx-dark.svg")

    def test_files_exist_and_are_referenced_by_the_picture(self):
        pic = re.search(r"<picture>.*?</picture>", README, re.S)
        self.assertIsNotNone(pic)
        self.assertLess(pic.start(), README.index("## "), "picture sits at the top")
        self.assertRegex(pic.group(0), r'<source media="\(prefers-color-scheme: dark\)" srcset="docs/ctx-dark\.svg"')
        self.assertRegex(pic.group(0), r'<img src="docs/ctx\.svg"')
        for f in self.FILES:
            self.assertTrue((MOD / f).is_file(), f)

    def test_alt_names_the_three_movements(self):
        alt = re.search(r'<img [^>]*alt="([^"]+)"', README).group(1).lower()
        for kw in ("state goes up", "one-line summary", "context.md", "rules come down", "agents.md", "inherited",
                   "rewrite after the session", "session ends", "rewritten"):
            self.assertIn(kw, alt, kw)

    def test_svgs_are_self_contained(self):
        for f in self.FILES:
            svg = (MOD / f).read_text()
            self.assertTrue(svg.lstrip().startswith("<svg"), f)
            self.assertNotRegex(svg, r"(?i)<script|<foreignobject|@import|@font-face|url\(\s*['\"]?https?:|on[a-z]+=", f)
            self.assertNotRegex(svg, r"(?i)\s(?:xlink:)?href\s*=", f)
            for fam in re.findall(r'font-family="([^"]*)"', svg):
                self.assertRegex(fam, r"(?i)system-ui|ui-monospace|sans-serif|monospace", fam)


class ClaimsTable(unittest.TestCase):
    def test_every_test_named_in_the_table_exists(self):
        files = sorted(HERE.glob("test_*.py")) + [HERE / "integration/run_readme.py"]
        sources = "".join(p.read_text() for p in files)
        names = set(re.findall(r"`((?:test_|step_)[a-z0-9_]+)`", CLAIMS))
        self.assertGreater(len(names), 15)
        for n in names:
            self.assertRegex(sources, rf"def {n}\(", n)

    def test_every_row_has_a_test_and_uninstall_restores_is_backed_by_the_round_trips(self):
        rows = [ln for ln in CLAIMS.splitlines() if ln.startswith("| ") and not ln.startswith("| Section") and not ln.startswith("|---")]
        for ln in rows:
            self.assertRegex(ln.split("|")[-2], r"`(?:test_|step_)[a-z0-9_]+`", ln)
        restores = [ln for ln in rows if "restores" in ln and "uninstall" in ln]
        self.assertTrue(restores)
        for ln in restores:
            for n in ("test_uninstall_roundtrip_byte_identical_dry_run_and_link_kept",):
                self.assertIn(n, ln)
        self.assertIn("restores the CLI's config", README)  # the README sentence this row backs

    def test_other_pages_rows_name_a_real_heading(self):
        pages = {"reference": "reference.md", "how-it-works": "how-it-works.md"}
        for ln in CLAIMS.splitlines():
            m = re.match(r"\| ([a-z-]+): ([^|]+?) \|", ln)
            if not m:
                continue
            self.assertIn(m.group(1), pages, ln)
            heads = re.findall(r"^#+ (.+)$", (MOD / "docs" / pages[m.group(1)]).read_text(), re.M)
            self.assertIn(m.group(2), heads, ln)

    def test_every_readme_section_has_a_claim_row(self):
        for head in re.findall(r"^## (.+)$", README, re.M):
            self.assertIn(f"| {head} |", CLAIMS, head)


class Measured(unittest.TestCase):
    """README "Measured" <-> docs/benchmark.md Headline <-> the published results file (recomputed)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HERE / "integration"))
        import run_benchmark
        cls.rb = run_benchmark
        cls.doc = (MOD / "docs/benchmark.md").read_text()
        cls.results = json.loads((MOD / "docs" / "benchmark-results.json").read_text())
        p = cls.results["primary"]
        cls.h = run_benchmark.headline(cls.results["runs"], p["with_set"], p["without_set"],
                                       B=cls.results["bootstrap_draws"], seed=cls.results["seed"])

    def problems(self, readme=README, doc=None, results=None, recompute=False):
        return self.rb.check_measured(readme, doc or self.doc, results or self.results, None if recompute else self.h)

    def test_measured_numbers_match_the_benchmark_doc(self):
        sec = re.search(r"^## Measured\n(.*?)(?=^## |\Z)", README, re.S | re.M).group(1)
        self.assertLessEqual(len([ln for ln in sec.splitlines() if ln.strip()]), 8)
        self.assertIn("docs/benchmark.md", sec)
        self.assertEqual(self.problems(recompute=True), [])

    def row(self, metric, tasks="all", judge="-"):
        name = self.rb.METRIC_NAMES[metric]
        return next(ln for ln in self.doc.splitlines() if ln.startswith(f"| {name} | {judge} | {tasks} |"))

    def mutated_doc(self, line, new):
        return self.doc.replace(line, new, 1)

    def test_measured_check_catches_drift(self):
        rows = {(r["metric"], r["tasks"], r.get("judge")): r for r in self.h["rows"]}
        turns = self.row("turns")
        t_eff = f"{rows[('turns', 'all', None)]['effect']:+.1f} %"
        tok = self.row("tokens")
        k_eff = f"{rows[('tokens', 'all', None)]['effect']:+.1f} %"
        q = self.row("quality", judge="sonnet")
        cells = q.split(" | ")
        swapped = " | ".join(cells[:4] + [cells[5], cells[4]] + cells[6:])
        flip = "| yes |" if tok.endswith("| no |") else "| no |"
        docs = {
            "turns effect changed": self.mutated_doc(turns, turns.replace(t_eff, "-33.4 %" if t_eff != "-33.4 %" else "-23.3 %", 1)),
            "token sign flipped": self.mutated_doc(tok, tok.replace(k_eff, k_eff.replace("-", "+", 1) if k_eff[0] == "-" else k_eff.replace("+", "-", 1), 1)),
            "token effect rounded": self.mutated_doc(tok, tok.replace(k_eff, k_eff.split(".")[0] + " %", 1)),
            "significance swapped": self.mutated_doc(tok, tok[: tok.rindex("| ")] + flip),
            "with/without swapped": self.mutated_doc(q, swapped),
            "metric renamed": self.mutated_doc(tok, tok.replace(self.rb.METRIC_NAMES["tokens"], self.rb.METRIC_NAMES["cost"], 1)),
        }
        docs["doc limit changed"] = self.doc.replace("loss of more than about 0.7 point", "loss of more than about 0.5 point", 1)
        for why, d in docs.items():
            self.assertNotEqual(d, self.doc, why)
            self.assertTrue(self.problems(doc=d), why)
        m = re.search(r"fewer\s+input\s+tokens[^(]*\(([-+]?\d+) %", README)
        readmes = {
            "README sign flipped": README.replace(m.group(0), m.group(0).replace(m.group(1), m.group(1).lstrip("-+")), 1),
            "README significance word": README.replace("no measurable change", "a measurable change", 1),
            "README one-point claim": README.replace("in a first run a one-point loss was not ruled out", "a one-point loss is not ruled out", 1),
            "README mentions cost": README.replace("turns (-23 %)", "turns (-23 %), cost (-7 %)", 1),
            "README hidden count": README.replace("(18 of 80 sessions", "(8 of 80 sessions", 1),
            "README sensitivity figure": README.replace("-16 % without those 18", "-19 % without those 18", 1),
            "README folder count": README.replace("on 8 of the author's folders", "on 9 of the author's folders", 1),
            "README Opus sentence": README.replace("; an Opus judge agrees", "", 1),
            "README robustness": README.replace(" but not robust", "", 1),
            "README worst cell": README.replace("(one folder, -1.2;", "(one folder, -2.2;", 1),
            "README turns changed": re.sub(r"turns \(([-+]?\d+) %\)", lambda x: f"turns ({int(x.group(1)) - 10} %)", README, 1),
        }
        for why, r in readmes.items():
            self.assertNotEqual(r, README, why)
            self.assertTrue(self.problems(readme=r), why)
        bad = json.loads(json.dumps(self.results))
        run = next(r for r in bad["runs"] if r["set"] == bad["primary"]["without_set"] and r["arm"] == "without")
        run["turns"] = (run["turns"] or 0) + 50
        self.assertIn("docs/benchmark-results.json headline differs from the one recomputed from its runs",
                      self.problems(results=bad, recompute=True))


if __name__ == "__main__":
    unittest.main()
