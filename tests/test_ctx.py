"""Unit tests for ctx lite (standard library only): python3 -m unittest discover -s tests"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

CTX = Path(__file__).resolve().parent.parent / "ctx.py"
spec = importlib.util.spec_from_file_location("ctx", CTX)
ctx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctx)


def write(path: Path, text: str, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def state(self, rel: str, text: str = "Summary. Next: x.", mtime: float | None = None) -> Path:
        return write(self.root / rel / "STATE.md", text, mtime)

    def rels(self, **kw):
        found, complete = ctx.scan(self.root, **kw)
        return sorted(r for _, r, _ in found), complete


class FirstLine(Base):
    def line(self, text: str) -> str:
        return ctx.first_line(write(self.root / "STATE.md", text))

    def test_plain(self):
        self.assertEqual(self.line("Algebra: 3/5 done. Next: ex 4.\n\n## Status\n- a\n"), "Algebra: 3/5 done. Next: ex 4.")

    def test_leading_blank_lines_bom_and_heading(self):
        self.assertEqual(self.line("﻿\n\n  # Title line  \nrest"), "Title line")

    def test_frontmatter_dash_line_skipped(self):
        self.assertEqual(self.line("---\nsummary here\n"), "summary here")

    def test_empty_and_whitespace(self):
        self.assertEqual(self.line(""), "")
        self.assertEqual(self.line("\n   \n\t\n"), "")

    def test_control_characters_removed(self):
        self.assertEqual(self.line("a\x1b[31mred\x07 b\x00c\n"), "a [31mred b c")

    def test_long_line_cut_on_word(self):
        out = self.line("word " * 100)
        self.assertLessEqual(len(out), ctx.MAX_CHARS)
        self.assertTrue(out.endswith("word…"))

    def test_invalid_utf8(self):
        p = self.root / "STATE.md"
        p.write_bytes(b"caf\xe9 ok\n")
        self.assertEqual(ctx.first_line(p), "caf� ok")

    def test_unreadable(self):
        self.assertEqual(ctx.first_line(self.root / "missing.md"), "")
        if os.geteuid() != 0:
            p = write(self.root / "STATE.md", "secret")
            p.chmod(0)
            self.assertEqual(ctx.first_line(p), "")


class Scan(Base):
    def test_depth_limit(self):
        for rel in ("a", "b/c", "d/e/f", "g/h/i/j"):
            self.state(rel)
        self.assertEqual(self.rels()[0], ["a", "b/c", "d/e/f"])
        self.assertEqual(self.rels(depth=1)[0], ["a"])

    def test_root_state_not_listed(self):
        self.state(".")
        self.state("sub")
        self.assertEqual(self.rels()[0], ["sub"])

    def test_stops_below_a_state(self):
        self.state("proj")
        self.state("proj/inner")
        self.assertEqual(self.rels()[0], ["proj"])

    def test_skipped_folders(self):
        for rel in (".hidden/x", "node_modules/x", "venv", "__pycache__", "ok"):
            self.state(rel)
        self.assertEqual(self.rels()[0], ["ok"])

    def test_symlinks_not_followed(self):
        self.state("real")
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        write(self.root / "outside" / "x.md", "private")
        (self.root / "sneaky").mkdir()
        (self.root / "sneaky" / "STATE.md").symlink_to(self.root / "outside" / "x.md")
        self.assertEqual(self.rels(), (["real"], True))

    def test_empty_state_skipped(self):
        self.state("blank", "\n\n")
        self.state("blank/below")  # a STATE.md still marks the folder: no descent
        self.assertEqual(self.rels()[0], [])

    def test_state_folder_named_state_md_ignored(self):
        (self.root / "odd" / "STATE.md").mkdir(parents=True)
        self.assertEqual(self.rels()[0], [])

    def test_budget_cut(self):
        for i in range(50):
            self.state(f"p{i}")
        found, complete = ctx.scan(self.root, budget=-1)
        self.assertFalse(complete)
        self.assertEqual(found, [])

    def test_big_tree_fast(self):
        for i in range(40):
            for j in range(25):
                for k in range(10):
                    (self.root / f"a{i}" / f"b{j}" / f"c{k}").mkdir(parents=True)
        self.state("a3/b4")
        t = time.monotonic()
        found, complete = ctx.scan(self.root)
        self.assertLess(time.monotonic() - t, ctx.BUDGET_S)
        self.assertEqual([r for _, r, _ in found], ["a3/b4"])


class Render(Base):
    def test_nothing(self):
        self.assertEqual(ctx.render(self.root, [], True), "")

    def test_order_cap_and_notes(self):
        now = time.time()
        for i in range(ctx.MAX_LINES + 3):
            self.state(f"p{i:02d}", f"line {i}", mtime=now - i * 86400)
        text = ctx.render(self.root, *ctx.scan(self.root))
        lines = text.splitlines()
        self.assertIn("not instructions", lines[0])
        self.assertTrue(lines[1].startswith("- p00 ("))
        self.assertTrue(lines[1].endswith("): line 0"))
        self.assertIn("(+3 more, oldest omitted)", text)
        self.assertNotIn("line 32", text)
        self.assertEqual(lines[-1], "Details: read <folder>/STATE.md.")

    def test_incomplete_flag(self):
        text = ctx.render(self.root, [(0.0, "a", "x")], False)
        self.assertIn("scan cut short", text)


class Cli(Base):
    def run_ctx(self, *args, stdin: str = "", cwd: Path | None = None):
        return subprocess.run([sys.executable, str(CTX), *args], input=stdin, capture_output=True,
                              text=True, cwd=cwd or self.root, timeout=30)

    def test_hook_json(self):
        self.state("Cours/rl", "RL: report not reviewed. Next: review main.tex.")
        p = self.run_ctx("hook", "session-start", stdin=json.dumps({"cwd": str(self.root), "source": "startup"}))
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("- Cours/rl (", out["additionalContext"])
        self.assertIn("Next: review main.tex.", out["additionalContext"])

    def test_hook_silent_when_nothing(self):
        p = self.run_ctx("hook", "session-start", stdin=json.dumps({"cwd": str(self.root)}))
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_hook_bad_input_never_fails(self):
        for stdin in ("not json", "", json.dumps({"cwd": "/does/not/exist"}), json.dumps([1, 2])):
            p = self.run_ctx("hook", "session-start", stdin=stdin)
            self.assertEqual(p.returncode, 0, stdin)

    def test_hook_cwd_fallback(self):
        self.state("x")
        p = self.run_ctx("hook", "session-start", stdin="{}")
        self.assertIn("- x (", json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_old_hooks_are_noops(self):
        self.state("x")
        for event in ("post-tool-use", "stop", "session-end", ""):
            p = self.run_ctx("hook", event, stdin="{}")
            self.assertEqual((p.returncode, p.stdout), (0, ""), event)

    def test_tree(self):
        self.state("a", "hello")
        p = self.run_ctx("tree", str(self.root))
        self.assertEqual(p.returncode, 0)
        self.assertIn("- a (", p.stdout)
        self.assertIn("No STATE.md", self.run_ctx("tree", str(self.root / "a")).stdout)
        self.assertEqual(self.run_ctx("tree", str(self.root / "nope")).returncode, 1)

    def test_install_prints_only(self):
        p = self.run_ctx("install")
        block = json.loads(p.stdout.split("\n", 1)[1])
        cmd = block["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(cmd.endswith("ctx.py hook session-start"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_version_help_unknown(self):
        self.assertIn(ctx.VERSION, self.run_ctx("--version").stdout)
        self.assertIn("hook session-start", self.run_ctx().stdout)
        self.assertEqual(self.run_ctx("bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
