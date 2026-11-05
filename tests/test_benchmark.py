"""Benchmark runner (tests/integration/run_benchmark.py): pure parts, offline."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "integration"))
import run_benchmark as rb  # noqa: E402


def run(label, task, arm, i, q, tok=100, turns=3):
    s = q // 3
    return {"label": label, "task": task, "arm": arm, "run": i, "error": False, "turns": turns, "secs": 10,
            "cache_read": tok, "cache_creation": 0, "input_tokens": 0, "cost_usd": 0.01,
            "scores": {"exactness": s, "actionability": s, "no_invention": q - 2 * s}}


class Benchmark(unittest.TestCase):
    def test_tasks_file_has_two_tasks_per_folder_and_generic_labels(self):
        tasks = rb.load_tasks()
        labels = {t["label"] for t in tasks}
        self.assertEqual(len(labels), 8)
        for lb in labels:
            self.assertEqual(sorted(t["task"] for t in tasks if t["label"] == lb), ["resume", "specific"])
        raw = rb.TASKS_FILE.read_text()
        self.assertNotIn(str(Path.home()), raw)
        self.assertNotIn("~/", raw)
        for t in tasks:  # exact prompts and pointer files live in the private map only
            self.assertEqual(t["pointers"], [])
            self.assertTrue(t["prompt"] or t["description"])
            self.assertEqual(t["prompt"] is None, t["type"] == "specific")
        self.assertNotRegex(raw, r"(?i)\.ipynb|seance|tp[0-9]|ddpg|survie|puissance|layernorm")
        priv = {"folders": {"course-notebooks-A": {"tasks": {"specific": {"prompt": "P", "pointers": ["a.md"]}}}}}
        t = next(x for x in rb.load_tasks(private=priv) if x["label"] == "course-notebooks-A" and x["task"] == "specific")
        self.assertEqual((t["prompt"], t["pointers"]), ("P", ["a.md"]))

    def test_schedule_covers_every_run_once_and_is_shuffled(self):
        tasks = [{"label": "a", "task": "resume"}, {"label": "b", "task": "specific"}]
        s = rb.schedule(tasks, 5, 7)
        self.assertEqual(len(s), 20)
        self.assertEqual(len(set(s)), 20)
        self.assertEqual(s, rb.schedule(tasks, 5, 7))
        self.assertNotEqual(s, sorted(s))

    def test_redact_home_folder_and_words(self):
        folder = Path.home() / "x" / "proj"
        t = f"see {folder}/a.py and {Path.home()}/b, by Alice ALICE"
        r = rb.redact(t, ["alice"], folder)
        self.assertEqual(r, "see <folder>/a.py and ~/b, by [redacted] [redacted]")

    def test_stat_manifest_ignores_context_and_git_and_sees_changes(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "a.txt").write_text("a")
            (t / "CONTEXT.md").write_text("x")
            (t / ".git").mkdir()
            (t / ".git" / "index").write_text("1")
            b = rb.stat_manifest(t)
            self.assertEqual(list(b), ["a.txt"])
            (t / "CONTEXT.md").write_text("yy")
            self.assertEqual(rb.Bench.stat_diff(b, rb.stat_manifest(t)), [])
            (t / "a.txt").write_text("bb")
            self.assertEqual(rb.Bench.stat_diff(b, rb.stat_manifest(t)), ["a.txt"])

    def test_paired_difference_ci_and_cell_counts(self):
        res = []
        for lb in ("a", "b", "c", "d"):
            for i in range(1, 6):
                res += [run(lb, "resume", "with", i, 7, tok=60), run(lb, "resume", "without", i, 5, tok=100)]
        types = {(lb, "resume"): "resume" for lb in "abcd"}
        q = rb.paired(res, "quality", B=500, types=types)
        self.assertEqual((q["n_cells"], q["diff"], q["lo"], q["hi"]), (4, 2.0, 2.0, 2.0))
        self.assertEqual((q["cells_with_higher"], q["cells_with_lower"]), (4, 0))
        self.assertTrue(rb.significant(q))
        t = rb.paired(res, "tokens", B=500, types=types)
        self.assertEqual((t["diff"], round(t["pct"], 1)), (-40.0, -40.0))
        self.assertEqual(rb.paired(res, "quality", "specific", B=10, types=types)["n_cells"], 0)
        noisy = [run("a", "resume", arm, i, q_) for arm, qs in (("with", (9, 0)), ("without", (0, 9)))
                 for i, q_ in enumerate(qs)]
        n = rb.paired(noisy, "quality", B=500, types={("a", "resume"): "resume"})
        self.assertFalse(rb.significant(n))
        self.assertIn("| all | quality | 4 |", rb.headline_table(rb.headline_rows(res, types, B=50)))

    def test_errors_are_left_out_of_cells(self):
        res = [run("a", "resume", "with", 1, 6), {"label": "a", "task": "resume", "arm": "without", "run": 1,
                                                  "error": True}]
        self.assertEqual(rb.cells(res)[("a", "resume")].keys(), {"with"})

    def test_notebook_summary_and_pointer_truth(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            nb = {"cells": [{"cell_type": "markdown", "source": ["# Part 1"]},
                            {"cell_type": "code", "source": ["# Todo\n", "x=1"], "execution_count": None},
                            {"cell_type": "code", "source": ["y=2"], "execution_count": 3}]}
            (t / "n.ipynb").write_text(json.dumps(nb))
            (t / "d").mkdir()
            (t / "d" / "f.csv").write_text("1")
            (t / "d" / ".env").write_text("SECRET")
            (t / "r.md").write_text("hello")
            s = rb.notebook_summary(t / "n.ipynb")
            self.assertIn("2 code cells, 1 executed, 1 never executed", s)
            self.assertIn("(0-based): [1]", s)
            g = rb.pointer_truth(t, ["n.ipynb", "d/", "r.md", "missing.txt"])
            self.assertIn("f.csv", g)
            self.assertIn("hello", g)
            self.assertNotIn("SECRET", g)
            self.assertNotIn(".env", g)
            self.assertIn("not a git repository", rb.git_truth(t))

    def test_transcript_facts_count_tools_and_context_reads(self):
        lines = [json.dumps({"message": {"content": [{"type": "tool_use", "name": "Read",
                                                      "input": {"file_path": "/p/CONTEXT.md"}}]}}),
                 json.dumps({"message": {"content": [{"type": "tool_use", "name": "Glob", "input": {}}]}}),
                 json.dumps({"message": {"content": [{"type": "tool_result", "content": "a\nCONTEXT.md"}]}}),
                 "not json"]
        f = rb.transcript_facts(lines)
        self.assertEqual((f["tool_calls"], f["read_context_md"], f["context_md_in_tool_results"]), (2, True, True))
        self.assertEqual(rb.transcript_facts([])["read_context_md"], False)
        self.assertTrue(str(rb.transcript_path("/a/b", "s")).endswith("-a-b/s.jsonl"))

    def test_judge_prompt_kinds_and_reference(self):
        r = rb.ab.build_judge_prompt("T", "Q", "A")
        s = rb.ab.build_judge_prompt("T", "Q", "A", "specific", "REF")
        self.assertIn("3 next actions", r)
        self.assertNotIn("Reference answer", r)
        self.assertIn("question asked is answered", s)
        self.assertIn("REF", s)
        for p in (r, s):
            self.assertNotIn("with_ctx", p)
            self.assertNotIn("CTX", p)

    def test_usage_row_reads_claude_json(self):
        j = {"num_turns": 4, "total_cost_usd": 0.02, "duration_ms": 900,
             "usage": {"input_tokens": 5, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 25,
                       "output_tokens": 9}}
        u = rb.usage_row(j)
        self.assertEqual((u["turns"], u["cost_usd"], rb.tokens(u), u["output_tokens"]), (4, 0.02, 100, 9))

    def test_hide_restore_and_recover_context_files(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "sub").mkdir()
            (t / "CONTEXT.md").write_bytes(b"top")
            (t / "sub" / "CONTEXT.md").write_bytes(b"sub")
            (t / "a.txt").write_text("a")
            before = rb.ab.manifest(t)
            hidden = rb.hide_contexts(t)
            self.assertEqual(len(hidden), 2)
            self.assertFalse((t / "CONTEXT.md").exists())
            self.assertTrue((t / rb.HIDDEN).exists())
            rb.restore_contexts(hidden)
            self.assertEqual(rb.ab.manifest(t), before)
            rb.hide_contexts(t)  # interrupted run: recovered at the next start
            self.assertEqual(len(rb.recover_hidden(t)), 2)
            self.assertEqual(rb.ab.manifest(t), before)
            (t / "CONTEXT.md").rename(t / rb.HIDDEN)
            (t / "CONTEXT.md").write_bytes(b"new")  # never overwrite an existing CONTEXT.md
            rb.restore_contexts([t / "CONTEXT.md"])
            self.assertEqual((t / "CONTEXT.md").read_bytes(), b"new")

    def test_redact_masks_author_names_emails_and_name_pairs(self):
        words = ["Alice"] + rb.license_names()
        self.assertTrue(rb.license_names())
        t = "by Alice Smith and Alice Van Dyke, alice.smith@uni.example.org, file Smith_VanDyke.pdf; Ytrain_bin kept"
        r = rb.redact(t, words)
        for bad in ("Smith", "Dyke", "alice", "@"):
            self.assertNotIn(bad, r)
        self.assertIn("Ytrain_bin kept", r)
        first = rb.license_names()[0]
        self.assertNotIn(first, rb.redact(f"{first} Someone wrote", rb.license_names()))


if __name__ == "__main__":
    unittest.main()
