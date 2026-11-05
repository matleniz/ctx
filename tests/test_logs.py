"""Complete one-line-per-event log, `ctx log`, richer `ctx doctor`."""
import json
import sys
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import ctx  # noqa: E402
import test_ctx  # noqa: E402


class Logs(test_ctx.Base):
    def hook(self, name, **ev):
        ctx.run_hook(name, json.dumps(ev))

    def events(self):
        return [(e, kv) for _t, e, kv in ctx.read_log()]

    def test_session_lifecycle_is_logged_one_line_per_event_with_a_tool_counter(self):
        cwd = str(self.p("courses/ml-intro"))
        self.hook("session-start", session_id="s1", cwd=cwd)
        for _ in range(3):
            self.hook("post-tool-use", session_id="s1", cwd=cwd, tool_name="Read", tool_input={"file_path": "x"})
        self.hook("post-tool-use", session_id="s1", cwd=cwd, tool_name="Edit",
                  tool_input={"file_path": str(self.p("courses/ml-intro/exos/ex1.py"))})
        before = len(self.log_text().splitlines())
        with mock.patch.object(ctx.hooks, "spawn_sync"):
            self.hook("session-end", session_id="s1", cwd=cwd)
        ev = dict((e, kv) for e, kv in self.events() if e in ("session-start", "session-end"))
        self.assertEqual(ev["session-start"]["node"], str(self.p("courses/ml-intro")))
        self.assertGreater(int(ev["session-start"]["chars"]), 100)
        self.assertEqual((ev["session-end"]["tool_events"], ev["session-end"]["files"]), ("4", "1"))
        self.assertEqual(len(self.log_text().splitlines()), before + 1)  # no line per PostToolUse

    def test_hook_error_and_sync_fail_carry_a_cause_and_the_session(self):
        ctx.log("hook-error", hook="stop", error="OSError: disk")
        self.assertIn("hook-error", [e for e, _ in self.events()])

        def boom(*a, **k):
            raise RuntimeError("model down")
        self.session("s3", "courses/ml-intro", ["courses/ml-intro/exos/ex1.py"])
        ctx.sync_session("s3", rewriter=boom)
        f = [kv for e, kv in self.events() if e == "sync-fail"][-1]
        self.assertIn("model down", f["error"])
        self.assertEqual(f["session"], "s3")
        self.assertEqual(ctx.paths._SID, "")

    def test_ctx_log_filters(self):
        ctx.log("sync", node=str(self.p("courses")), tokens_in=10, tokens_out=5, total_secs=2.0)
        ctx.log("sync-fail", node=str(self.p("courses/ml-intro")), error="X: bad thing", session="abc")
        ctx.log("session-start", session="abc", cwd=str(self.p("courses/ml-intro")), node="n", chars=9)
        rc, out = self.run_ctx("log", "--fail")
        self.assertIn("bad thing", out)
        self.assertNotIn(" sync ", out)
        self.assertIn("session-start", self.run_ctx("log", "--session", "abc")[1])
        self.assertNotIn("tokens_in", self.run_ctx("log", "--session", "abc")[1])
        self.assertEqual(self.run_ctx("log", "--session", "nope")[1].strip(), "ctx: no matching log line")
        self.assertEqual(len(self.run_ctx("log", "-n", "1")[1].strip().splitlines()), 1)
        self.assertIn("sync", self.run_ctx("log", "--node", str(self.p("courses")), "--since", "1h")[1])
        with self.assertRaises(SystemExit):
            self.run_ctx("log", "--since", "soon")

    def test_doctor_shows_pending_abandoned_and_last_sync(self):
        self.session("p1", "courses/ml-intro", ["courses/ml-intro/exos/ex1.py"])
        ctx.log("sync", node=str(self.p("courses")), total_secs=1)
        out = self.run_ctx("doctor")[1]
        self.assertIn("pending sessions=1 (p1), abandoned=0", out)
        self.assertIn("last sync", out)
        self.assertIn(str(self.p("courses")), out)
        self.assertIn("uses", out)


if __name__ == "__main__":
    unittest.main()
