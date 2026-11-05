"""Rewriter: config file, one-row table (claude), the single call function, doctor."""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import ctx.cli  # noqa: E402
import test_ctx  # noqa: E402


class Rewriter(test_ctx.Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("CTX_REWRITER", None)

    def config(self, text):
        (self.home / ".ctx").mkdir(exist_ok=True)
        (self.home / ".ctx/config").write_text(text)

    def call(self, **kw):
        """Run cli_rewriter with a fake Popen; returns (cmd, popen kwargs, stdin text)."""
        seen = {}
        out = kw.pop("out", {"result": "r", "response": "r", "status": "SUCCESS", "usage": {}})

        class Fake:
            returncode = 0

            def __init__(self, cmd, **k):
                seen.update(cmd=cmd, kw=k)

            def communicate(self, inp, timeout=None):
                seen.update(inp=inp, timeout=timeout)
                return json.dumps(out), ""
        with mock.patch.object(ctx.rewriter.subprocess, "Popen", Fake):
            res = ctx.rewriter.cli_rewriter("SYS", "PROMPT", **kw)
        return seen, res

    def test_config_parsing_comments_quotes_unknown_keys_and_bad_lines(self):
        self.config("# comment\nrewriter = Claude  # inline\nmodel = \"opus\"\ntimeout=30\nbogus = 1\nnot a line\n")
        self.assertEqual(ctx.rewriter.read_config(), {"rewriter": "Claude", "model": "opus"})  # `timeout` is no longer a key
        self.assertEqual(ctx.rewriter.effective_rewriter(), ("claude", "opus"))

    def test_no_config_file_is_fine(self):
        self.assertEqual(ctx.rewriter.read_config(), {})
        self.assertEqual(ctx.rewriter.effective_rewriter(), ("claude", "haiku"))
        self.assertEqual(list(ctx.rewriter.REWRITERS), ["claude"])

    def test_env_ctx_rewriter_is_gone(self):
        os.environ["CTX_REWRITER"] = "cursor"
        self.addCleanup(os.environ.pop, "CTX_REWRITER", None)
        self.assertEqual(ctx.rewriter.effective_rewriter(), ("claude", "haiku"))

    def test_other_rewriter_names_give_a_clear_message(self):
        for name in ("cursor", "agy", "gemini"):
            self.config(f"rewriter = {name}\n")
            with self.assertRaisesRegex(RuntimeError, f"{name}.*not supported: only claude"):
                ctx.rewriter.effective_rewriter()
            with self.assertRaisesRegex(RuntimeError, "not supported"):
                self.call()
            self.assertIn("not supported", " ".join(ctx.rewriter.config_warnings()))

    def test_claude_row_has_the_guards_it_documents(self):
        c, _ = self.call()
        self.assertEqual(c["cmd"][:2], ["claude", "-p"])
        for f in ("--tools", "--no-session-persistence", "--setting-sources", "--system-prompt"):
            self.assertIn(f, c["cmd"])
        self.assertEqual(c["inp"], "PROMPT")
        self.assertEqual(c["kw"]["env"]["CTX_INNER"], "1")  # loop guard, inherited by child hooks

    def test_model_flag_and_fixed_timeout(self):
        c, _ = self.call()
        self.assertEqual(c["cmd"][c["cmd"].index("--model") + 1], "haiku")
        self.assertEqual(c["timeout"], ctx.paths.MODEL_TIMEOUT)
        self.config("model = opus\n")
        c, _ = self.call()
        self.assertEqual(c["cmd"][c["cmd"].index("--model") + 1], "opus")
        c, _ = self.call(model="sonnet")
        self.assertEqual(c["cmd"][c["cmd"].index("--model") + 1], "sonnet")

    def test_response_field_and_usage(self):
        _, (text, info) = self.call(out={"result": "hi", "is_error": False,
                                          "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3}})
        self.assertEqual((text, info["in"], info["out"], info["rewriter"]), ("hi", 15, 3, "claude"))

    def test_cli_failure_is_an_error_with_the_cli_name(self):
        with self.assertRaisesRegex(RuntimeError, "claude error"):
            self.call(out={"is_error": True, "result": "auth"})

    def doctor(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ctx.cli.main(["doctor"])
        return rc, buf.getvalue()

    def test_doctor_shows_the_rewriter_and_checks_the_command_exists(self):
        self.config("model = gem\n")
        with mock.patch.object(ctx.doctor.shutil, "which", return_value="/bin/claude"):
            rc, out = self.doctor()
        self.assertIn("rewriter claude model gem; command /bin/claude", out)
        self.assertNotIn("WARNING", out)
        with mock.patch.object(ctx.doctor.shutil, "which", return_value=None):
            rc, out = self.doctor()
        self.assertIn("claude NOT FOUND", out)
        self.assertEqual(rc, 1)

    def test_doctor_reports_an_unsupported_rewriter(self):
        self.config("rewriter = cursor\n")
        with mock.patch.object(ctx.doctor.shutil, "which", return_value="/bin/claude"):
            rc, out = self.doctor()
        self.assertIn("config: rewriter 'cursor' is not supported", out)
        self.assertEqual(rc, 1)

    def test_install_hooks_cursor_prints_no_rewriter_line(self):
        f = self.home / "h.json"
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx.cli.main(["install-hooks", "cursor", "--settings-file", str(f), "--yes"])
        self.assertNotIn("rewriter", buf.getvalue())
        self.assertTrue(f.exists())

    def test_rewriter_prompt_asks_for_the_session_language(self):
        self.assertIn("language of the session reports", ctx.rewriter.SYSTEM_PROMPT)
        self.assertIn("## Status", ctx.rewriter.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
