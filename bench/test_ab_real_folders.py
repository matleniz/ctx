"""Three-arm A/B runner (bench/run_ab_real_folders.py): pure parts, offline."""
import argparse
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


class TestAbThreeArmRunner(unittest.TestCase):
    """Three-arm runner (structure / state / nothing): pure parts."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import run_ab_real_folders
        cls.m = run_ab_real_folders

    SPEC = {"summary": "exos/a.py est un stub. Suite : exos/a.py.", "status": ["exos/a.py : stub", "decision: x, car y"],
            "next": ["Implémenter exos/a.py"]}

    def test_variant_written_and_restored_byte_identical(self):
        orig = "---\nsummary: Orig\nupdated: 2026-01-02T03:04\nabsorb: true\n---\n## Status\n- old\n\n## Next\n- old\n\n## Notes\n\n\n## Subfolders\n- sub : s\n".encode()
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "CONTEXT.md").write_bytes(orig)
            (Path(t) / "x.txt").write_text("x")
            before = self.m.manifest(t)
            n = self.m.write_variant(t, orig, self.SPEC)
            txt = (Path(t) / "CONTEXT.md").read_text()
            self.assertEqual(n, len(txt.encode()))
            self.assertIn("summary: exos/a.py est un stub", txt)
            self.assertIn("- decision: x, car y", txt)
            self.assertIn("absorb: true", txt)
            self.assertIn("- sub : s", txt)  # Subfolders kept
            self.assertTrue(self.m.only_context_changed(self.m.diff_manifest(before, self.m.manifest(t))))
            self.m.restore_context(t, orig)
            self.assertEqual((Path(t) / "CONTEXT.md").read_bytes(), orig)
            self.assertEqual(self.m.manifest(t), before)

    def test_restore_removes_node_created_by_init(self):
        with tempfile.TemporaryDirectory() as t:
            self.m.write_variant(t, b"", self.SPEC)
            self.assertTrue((Path(t) / "CONTEXT.md").exists())
            self.m.restore_context(t, None)
            self.assertFalse((Path(t) / "CONTEXT.md").exists())

    def test_load_spec_validates(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "f.json").write_text(json.dumps({"structure": self.SPEC, "state": {"summary": "s"}}))
            with self.assertRaises(SystemExit):
                self.m.load_spec(t, "f")
            with self.assertRaises(SystemExit):
                self.m.load_spec(t, "missing")
            (Path(t) / "f.json").write_text(json.dumps({"structure": self.SPEC, "state": self.SPEC}))
            self.assertEqual(self.m.load_spec(t, "f")["state"]["summary"], self.SPEC["summary"])

    def test_stats_and_table_with_variance(self):
        s = self.m.stats([2, 4, 6])
        self.assertEqual((s["mean"], s["sd"], s["min"], s["max"]), (4.0, 2.0, 2, 6))
        self.assertEqual(self.m.stats([5])["sd"], 0.0)
        self.assertIsNone(self.m.stats([None])["mean"])
        sc = lambda a: {"exactness": a, "actionability": a, "no_invention": a}  # noqa: E731
        mk = lambda arm, q, cr: {"arm": arm, "turns": 3, "secs": 5, "input_tokens": 10, "cache_read": cr,  # noqa: E731
                                 "cache_creation": 7, "scores": sc(q)}
        res = {"f": [mk("state", 3, 100), mk("state", 1, 300), mk("structure", 2, 50), mk("nothing", 0, 80)]}
        t = self.m.summary_table3(res)
        self.assertIn("| f | state | 2 | 6.0 | 10.0 | 200.0 | 7.0 | 207.0 | 96.6 | 3.0 | 5.0 |", t)
        self.assertIn("f / state: quality sd 4.24 (min-max 3-9); cache_read sd 141.42 (min-max 100-300)", t)
        self.assertIn("| f | nothing | 1 | 0.0 |", t)

    def test_cache_check_uses_rate_not_raw_cache_read(self):
        mk = lambda arm, cr, cc: {"arm": arm, "cache_read": cr, "cache_creation": cc}  # noqa: E731
        real = {"ml-intro": [mk("state", 737, 263), mk("nothing", 699, 301)],
                "challenge": [mk("state", 790, 210), mk("nothing", 648, 352)]}
        lines, dropped = self.m.cache_check(real)
        self.assertFalse(dropped)
        self.assertIn("state=73.7%", lines[0])
        self.assertIn("nothing=69.9%", lines[0])
        self.assertIn("state=79.0%", lines[1])
        self.assertIn("nothing=64.8%", lines[1])
        lines, dropped = self.m.cache_check({"f": [mk("state", 50, 50), mk("nothing", 90, 10)]})
        self.assertTrue(dropped)
        self.assertIn("DROP", lines[0])
        self.assertIn("updated", "\n".join(lines))  # recommendation
        lines, dropped = self.m.cache_check({"f": [mk("state", 1, 1)], "g": [mk("state", 1, 1), mk("nothing", 1, 0)]})
        self.assertIn("n/a", lines[0])

    def test_enlarged_truth_dates_and_memory(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "proj"
            (f / "src").mkdir(parents=True)
            (f / "src" / "a.py").write_text("")
            os.utime(f / "src" / "a.py", (86400 * 365, 86400 * 365))
            (f / ".env").write_text("zzz")
            (f / "CONTEXT.md").write_text("model output")
            mem = Path(t) / "mem" / re.sub(r"[^A-Za-z0-9]", "-", str(f.resolve())) / "memory"
            mem.mkdir(parents=True)
            (mem / "m.md").write_text("fact: deadline vendredi")
            g = self.m.enlarged_truth(f, Path(t) / "mem")
            self.assertIn("1971-01-01  src/a.py", g)
            self.assertIn("deadline vendredi", g)
            for bad in ("zzz", "model output", ".env"):
                self.assertNotIn(bad, g)
            g2 = self.m.enlarged_truth(f, Path(t) / "none")  # absent memory: silent
            self.assertNotIn("memory of this folder", g2)


class TestAbJudge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import run_ab_real_folders
        cls.m = run_ab_real_folders

    def test_scrub_home_and_restore_after_exception_in_first_write(self):
        self.assertEqual(self.m.scrub_home(f"see {Path.home()}/x"), "see ~/x")
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            f = t / "node"
            f.mkdir()
            orig = b"---\nctx: 1\n---\noriginal \xc3\xa9\n"
            (f / "CONTEXT.md").write_bytes(orig)
            vd = t / "v"
            vd.mkdir()
            spec = json.loads((Path(__file__).resolve().parent / "variants-mirror/ml-intro.json").read_text())
            (vd / "node.json").write_text(json.dumps(spec))
            a = argparse.Namespace(variants_dir=str(vd), mirror_settings=None, runs=1, model="m", judge_model="m", memory_root=None)
            m = self.m
            saved = (m.is_declined, m.claude, m.log_size, m.write_variant)
            real_write = m.write_variant

            def write_then_fail(*x):
                real_write(*x)
                raise RuntimeError("after first write")
            m.is_declined, m.log_size, m.write_variant = (lambda p: False), (lambda: 0), write_then_fail
            try:
                with self.assertRaises(RuntimeError):
                    m.run_folder3(str(f), t / "out", a)
            finally:
                m.is_declined, m.claude, m.log_size, m.write_variant = saved
            self.assertEqual((f / "CONTEXT.md").read_bytes(), orig)

    def test_manifest_diff(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "a.txt").write_text("a")
            (t / "CONTEXT.md").write_text("x")
            (t / ".git").mkdir()
            (t / ".git" / "index").write_text("1")
            b = self.m.manifest(t)
            self.assertNotIn(".git/index", b)
            (t / "CONTEXT.md").write_text("y")
            (t / ".git" / "index").write_text("2")
            d = self.m.diff_manifest(b, self.m.manifest(t))
            self.assertEqual(d["changed"], ["CONTEXT.md"])
            self.assertTrue(self.m.only_context_changed(d))
            (t / "a.txt").write_text("b")
            self.assertFalse(self.m.only_context_changed(self.m.diff_manifest(b, self.m.manifest(t))))

    def test_ground_truth_skips_secrets_and_context(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "README.md").write_text("hello " * 2000)
            (t / ".env").write_text("API_KEY=zzz")
            (t / "CONTEXT.md").write_text("model output")
            (t / "submissions").mkdir()
            (t / "submissions" / "s1.csv").write_text("")
            (t / "tp1.ipynb").write_text("{}")
            g = self.m.ground_truth(t)
            self.assertIn("truncated", g)
            self.assertIn("s1.csv", g)
            self.assertIn("tp1.ipynb", g)
            for bad in ("zzz", ".env", "model output", "CONTEXT.md"):
                self.assertNotIn(bad, g)

    def test_shuffle_blind_and_deterministic(self):
        items = [{"arm": a, "run": i} for a in ("with_ctx", "without_ctx") for i in (1, 2)]
        s1, s2 = self.m.shuffle_items(items, 7), self.m.shuffle_items(items, 7)
        self.assertEqual(s1, s2)
        self.assertEqual([b for b, _ in s1], ["A1", "A2", "A3", "A4"])
        p = self.m.build_judge_prompt("truth", "q", "ans")
        self.assertNotIn("with_ctx", p)
        self.assertNotIn("CTX", p)

    def test_judge_retried_once_on_invalid_json(self):
        good = '{"exactness":3,"actionability":2,"no_invention":3,"comment":"c"}'
        outs = ["not json at all", good]
        calls = []

        def call(prompt):
            calls.append(prompt)
            return outs[len(calls) - 1], "raw%d" % len(calls)
        scores, raw = self.m.judge_with_retry(call, "P")
        self.assertEqual(len(calls), 2)
        self.assertEqual(scores["exactness"], 3)
        self.assertEqual(raw, "raw2")

    def test_judge_gives_up_after_one_retry(self):
        calls = []
        scores, _ = self.m.judge_with_retry(lambda p: (calls.append(1) or "bad", "r"), "P")
        self.assertIsNone(scores)
        self.assertEqual(len(calls), 2)

    def test_parse_scores_and_table(self):
        self.assertEqual(self.m.parse_scores('ok {"exactness":3,"actionability":2,"no_invention":1,"comment":"c"}')["actionability"], 2)
        self.assertIsNone(self.m.parse_scores('{"exactness":5,"actionability":2,"no_invention":1}'))
        self.assertIsNone(self.m.parse_scores("nope"))
        sc = lambda a, b, c: {"exactness": a, "actionability": b, "no_invention": c}  # noqa: E731
        res = {"f": [{"arm": "with_ctx", "turns": 2, "tokens_in": 100, "secs": 4, "scores": sc(3, 3, 3)},
                     {"arm": "with_ctx", "turns": 4, "tokens_in": 200, "secs": 6, "scores": sc(1, 1, 1)},
                     {"arm": "without_ctx", "turns": 6, "tokens_in": 300, "secs": 8, "scores": None}]}
        t = self.m.summary_table(res)
        self.assertIn("| f | with_ctx | 2 | 2.0 | 2.0 | 2.0 | 6.0 | 3.0 | 150.0 | 5.0 |", t)
        self.assertIn("| f | without_ctx | 1 | None |", t)


if __name__ == "__main__":
    unittest.main()
