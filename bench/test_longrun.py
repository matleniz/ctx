"""Pure parts of the long-run benchmark harness (bench/run_longrun.py): no model call."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_longrun as lr  # noqa: E402


class TestContextAccuracy(unittest.TestCase):
    def test_done_and_pending_claims(self):
        items = {"ex1_1": True, "ex1_2": False}
        self.assertEqual(lr.classify_line("course-notebooks", "ch1/ex1_1.py: mean() implemented", items, False)[0], "true")
        self.assertEqual(lr.classify_line("course-notebooks", "exercise 1.2 done", items, False)[0], "false")
        self.assertEqual(lr.classify_line("course-notebooks", "exercise 1.2 still a stub", items, False)[0], "true")

    def test_running_clause_is_not_a_claim(self):
        k, claims = lr.classify_line("course-notebooks", "not done: running ch1/ex1_1.py (no shell)", {"ex1_1": True}, False)
        self.assertEqual((k, claims), ("other", []))

    def test_decision_claim(self):
        self.assertEqual(lr.classify_line("small-project", "decision: CLI stays stdlib-only", {}, True)[0], "true")
        self.assertEqual(lr.classify_line("small-project", "decision: CLI stays stdlib-only", {}, False)[0], "false")

    def test_missing_and_next(self):
        text = ("---\nctx: 1\nsummary: s\nupdated: 2026-01-01T00:00\n---\n## Status\n- ex1_1 done\n\n"
                "## Next\n- implement exercise 1.2\n\n## Notes\n\n## Subfolders\n")
        acc = lr.context_accuracy("course-notebooks", text, {"ex1_1": True, "notes_ch1": True}, True)
        self.assertEqual((acc["true"], acc["missing_items"], acc["next_ok"]), (1, ["notes_ch1", "decision"], True))


class TestRedact(unittest.TestCase):
    def test_no_arm_hint_left(self):
        t = lr.redact("Based on the last sync (23:42), see CONTEXT.md in /tmp/x/h/runs/course-notebooks-A1 "
                      "and notes.md; small-project-B2 too")
        self.assertNotIn("CONTEXT", t)
        self.assertNotIn("23:42", t)
        self.assertNotIn("-A1", t)
        self.assertNotIn("-B2", t)
        self.assertIn("a project file", t)
        self.assertIn("notes.md", t)


class TestRedo(unittest.TestCase):
    def test_course_redo_is_touching_a_done_non_target(self):
        s = {"kind": "progress", "targets": ["ex2_2"]}
        self.assertEqual(lr.redo("course-notebooks", s, {"ex2_1": True}, {"ex2_1": True}, ["ch2/ex2_1.py"]), 1)
        self.assertEqual(lr.redo("course-notebooks", s, {"ex2_1": True}, {"ex2_2": True}, ["ch2/ex2_2.py"]), 0)

    def test_project_redo_is_code_change_without_progress(self):
        s = {"kind": "progress", "targets": ["item3"]}
        self.assertEqual(lr.redo("small-project", s, {"item2": True}, {"item2": True}, ["tally/stats.py"]), 1)
        self.assertEqual(lr.redo("small-project", s, {}, {"item3": True}, ["tally/cli.py"]), 0)
        self.assertEqual(lr.redo("small-project", {"kind": "resume", "targets": []}, {}, {}, ["tally/cli.py"]), 0)

    def test_progress_counts_the_true_next_item(self):
        s = {"kind": "progress", "targets": ["item3"]}
        rec = lambda b, a: {"check_before": {"items": b}, "check_after": {"items": a}}  # noqa: E731
        # item 2 was the real next item: progress even though the planned target was item 3
        self.assertEqual(lr.progress("small-project", s, rec({"item1": True}, {"item1": True, "item2": True})), 1)
        self.assertEqual(lr.progress("small-project", s, rec({"item1": True}, {"item1": True})), 0)
        review = {"kind": "review", "targets": ["regression"]}
        self.assertEqual(lr.progress("small-project", review,
                                     {"check_before": {"items": {}}, "check_after": {"items": {}, "regression_ok": True}}), 1)

    def test_sync_cost(self):
        self.assertEqual(lr.sync_cost("x sync node=/a files=1 tokens_in=1739 tokens_out=508 model_secs=4.4"), (1739, 508, 4.4))
        self.assertEqual(lr.sync_cost(None), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
