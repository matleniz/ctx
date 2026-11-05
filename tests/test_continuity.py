"""Continuity: decisions kept by code, dated injection, sync-light, the Stop-report collection bug.

No real model: a fake rewriter returns fixed outputs.
"""
import json
import os
import subprocess
import time
import sys
from pathlib import Path

from test_ctx import CTX_PY, Base, Fake, ctx, fake_output

NODE = "courses/ml-intro"


def out_with(decisions, status="- ex3 ajouté dans ex3.py", summary="ex3 ajouté, ex4 ensuite"):
    return fake_output(summary=summary, where=status + "\n" + "\n".join(decisions))


class TestDecisions(Base):
    def rewrite(self, *decisions, light=False, msgs=("did it",)):
        node = self.p(NODE)
        files = [] if light else [str(node / "a.py")]
        out = ("## Next\n- ex4 dans ex4.py\n" + "\n".join(decisions)) if light else out_with(decisions)
        self.assertEqual(ctx.sync.sync_node(node, files, list(msgs), Fake(out), *([True] if light else [])), "ok")
        return self.doc(NODE).sections.get("Decisions", "")

    def test_added_and_removed_from_status(self):
        body = self.rewrite("- decision: no for/while loops (vectorised style)")
        self.assertEqual(body, "- no for/while loops (vectorised style)")
        self.assertNotIn("decision", self.doc(NODE).sections["Status"])

    def test_dedupe_and_model_cannot_remove(self):
        self.rewrite("decision: type hints everywhere")
        self.rewrite("- Decision: Type hints everywhere.", "decision: stdlib only (no deps)")
        body = self.rewrite()  # the model drops every decision: the code keeps them
        self.assertEqual(body.split("\n"), ["- type hints everywhere", "- stdlib only (no deps)"])

    def test_paraphrase_is_a_duplicate(self):
        self.rewrite("decision: use SQLite for the state storage")
        body = self.rewrite("decision: Use SQLite for state storage.", "decision: use SQLite for the cache")
        self.assertEqual(body.split("\n"), ["- use SQLite for the state storage", "- use SQLite for the cache"])

    def test_supersede_marks_old_and_adds_new(self):
        self.rewrite("decision: use argparse for the CLI")
        body = self.rewrite("decision superseded: use argparse for the CLI -> use click for the CLI")
        self.assertEqual(body.split("\n"), ["- use argparse for the CLI (superseded)",
                                            "- use click for the CLI"])

    def test_supersede_by_number_shown_to_the_model(self):
        self.rewrite("decision: keep haiku as the rewrite model", "decision: keep haiku for the judge")
        self.rewrite()
        fk = Fake(out_with(["decision superseded: #2 -> use sonnet for the judge"]))
        node = self.p(NODE)
        ctx.sync.sync_node(node, [str(node / "a.py")], ["x"], fk)
        self.assertIn("#1 - keep haiku as the rewrite model\n#2 - keep haiku for the judge", fk.calls[0][1])
        self.assertEqual(self.doc(NODE).sections["Decisions"].split("\n"), [
            "- keep haiku as the rewrite model", "- keep haiku for the judge (superseded)",
            "- use sonnet for the judge"])

    def test_free_text_supersede_marks_one_best_match_only(self):
        merge = ctx.doc.merge_decisions
        two = "- keep haiku as the rewrite model\n- keep haiku for the judge"
        body, _ = merge(two, ["decision superseded: keep haiku -> use sonnet"])
        self.assertLessEqual(body.count("(superseded)"), 1)
        body, _ = merge("- use pg", ["decision superseded: use pg for the main database store -> use sqlite"])
        self.assertNotIn("(superseded)", body)  # short line inside a long one: no substring match
        body, _ = merge("- use SQLite for state storage",
                        ["decision superseded: use SQLite for the state -> use Postgres for the state"])
        self.assertEqual(body, "- use SQLite for state storage (superseded)\n- use Postgres for the state")

    def test_cap_drops_superseded_first_then_oldest_active_with_log(self):
        self.rewrite("decision: rule zero to replace")
        self.rewrite("decision superseded: rule zero to replace -> rule zero bis")
        self.rewrite(*[f"decision: rule {i}" for i in range(1, 15)])  # 16 lines, 15 active
        body = self.rewrite().split("\n")
        self.assertEqual(len(body), 15)
        self.assertNotIn("(superseded)", "\n".join(body))
        self.rewrite("decision: rule 15")
        body = self.doc(NODE).sections["Decisions"].split("\n")
        self.assertEqual((len(body), body[0], body[-1]), (15, "- rule 1", "- rule 15"))
        self.assertIn("decision-dropped", self.log_text())

    def test_hand_edit_kept(self):
        self.rewrite("decision: first")
        node = self.p(NODE)
        t = (node / "CONTEXT.md").read_text().replace("- first", "- first, edited by hand\n- mine")
        (node / "CONTEXT.md").write_text(t)
        body = self.rewrite("decision: second")
        self.assertEqual(body.split("\n"), ["- first, edited by hand", "- mine", "- second"])

    def test_absent_section_is_empty_and_not_added(self):
        self.rewrite()
        self.assertNotIn("## Decisions", self.text(NODE))
        self.assertTrue(ctx.doc.valid_shape(self.doc(NODE)))

    def test_injection_order_and_parent_suffix(self):
        self.rewrite("decision: no loops", "decision: hints")
        txt, _ = ctx.hooks.build_injection(self.p(NODE))
        i = [txt.index(x) for x in ("summary:", "## Decisions", "## Status", "## Next")]
        self.assertEqual(i, sorted(i))
        subs = self.doc("courses").sections["Subfolders"]
        line = next(x for x in subs.split("\n") if "**ml-intro/**" in x)
        self.assertTrue(line.endswith(" · decisions: 2"), line)
        self.assertNotIn("no loops", subs)  # count only, no content
        self.assertNotIn("decisions:", next(x for x in subs.split("\n") if "**algebra/**" in x))

    def test_copied_subfolder_summary_still_caught_with_suffix(self):
        subs = "- **ml-intro/** — chapitre quatre en cours, exercice trois fini · decisions: 2"
        self.assertEqual(ctx.rewriter.copied_sub("x chapitre quatre en cours, exercice trois fini", subs),
                         "ml-intro")


class TestDatedInjection(Base):
    def test_bootstrapped_then_worked(self):
        node = self.p(NODE)
        self.assertTrue(ctx.hooks.build_injection(node)[0].startswith("[ctx]"))  # never synced: no line
        ctx.sync.sync_node(node, [], ["looked around"], Fake())  # bootstrap: no file
        first = ctx.hooks.build_injection(node)[0].split("\n")[0]
        self.assertTrue(first.startswith("State as of "), first)
        self.assertIn("bootstrapped from a read-only look", first)
        ctx.sync.sync_node(node, [str(node / "a.py")], [], Fake())
        worked = ctx.doc.get_status(node)["worked"]
        ctx.sync.sync_node(node, [], ["blocked, failed"], Fake("## Next\n- x dans x.py"), True)
        first = ctx.hooks.build_injection(node)[0].split("\n")[0]
        self.assertIn("written after the last session that changed files here", first)
        self.assertIn(f"State as of {worked},", first)  # the sync with files, not the light one
        self.assertTrue(first.endswith("Verify in the files before acting on Next."))

    def test_budget_respected_with_huge_notes(self):
        node = self.p(NODE)
        ctx.sync.sync_node(node, [str(node / "a.py")], [], Fake())
        t = (node / "CONTEXT.md").read_text().replace("## Notes\n", "## Notes\n" + "x" * 20000 + "\n")
        (node / "CONTEXT.md").write_text(t)
        with __import__("unittest").mock.patch.object(ctx.hooks, "MAX_NOTES", 30000):
            txt = ctx.hooks.build_injection(node)[0]
        self.assertLessEqual(len(txt), ctx.paths.MAX_INJECT)
        self.assertTrue(txt.startswith("State as of "))


    def test_huge_hand_edited_decisions_capped_status_kept(self):
        node = self.p(NODE)
        t = (node / "CONTEXT.md").read_text().replace("(to be filled in)", "- ex3 done").replace(
            "## Notes\n", "## Decisions\n" + "- d\n" * 6000 + "## Notes\n")
        (node / "CONTEXT.md").write_text(t)
        txt = ctx.hooks.build_injection(node)[0]
        self.assertIn("[truncated]", txt.split("## Status")[0])
        self.assertIn("## Next", txt)
        self.assertNotIn("[truncated]", txt.split("## Status")[1])


class TestSyncLight(Base):
    def end_session(self, sid, msg, fk):
        node = self.p(NODE)
        ctx.sync.sync_node(node, [str(node / "a.py")], [], Fake())  # summary filled: no bootstrap
        ctx.sessions.collect_stop({"session_id": sid, "cwd": str(node), "last_assistant_message": msg})
        return ctx.sync.sync_session(sid, fk)

    def test_plain_report_is_skipped(self):
        fk = Fake("## Next\n- x")
        self.end_session("s1", "I read the files, everything looks fine.", fk)
        self.assertEqual(fk.calls, [])
        self.assertIn(" skip ", self.log_text())

    def test_light_retry_names_no_summary_or_status(self):
        class Seq(Fake):
            def __call__(self, system, prompt):
                self.calls.append((system, prompt))
                return ("no section" if len(self.calls) == 1 else "## Next\n- ex4 dans ex4.py"), {}
        fk = Seq()
        self.end_session("r1", "decision: stdlib only", fk)
        self.assertEqual(len(fk.calls), 2)
        retry = fk.calls[1][1].split("rejected", 1)[1]
        self.assertNotIn("SUMMARY", retry)
        self.assertNotIn("Status", retry)

    def test_chatty_report_with_one_blocker_word_is_skipped(self):
        fk = Fake("## Next\n- x")
        self.end_session("c1", "I cannot say much more: ex3 looks fine. The next step could be ex4.", fk)
        self.assertEqual(fk.calls, [])

    def test_markers_route_to_sync_light(self):
        for i, msg in enumerate(["decision: keep the CLI stdlib-only",
                                 "The import failed and the build is blocked.",
                                 "Next: implement ex4 in ex4.py", "Prochaine étape : le chapitre 5"]):
            fk = Fake("## Next\n- ex4 dans ex4.py")
            self.end_session(f"m{i}", msg, fk)
            self.assertEqual(len(fk.calls), 1, msg)
            self.assertEqual(fk.calls[0][0], ctx.rewriter.LIGHT_PROMPT)
            self.assertNotIn("<files_touched>", fk.calls[0][1])
            self.assertNotIn("## Status", fk.calls[0][1])
        self.assertIn(" sync-light ", self.log_text())

    def test_light_never_changes_status_or_summary(self):
        before = self.doc(NODE)
        out = ("SUMMARY: hijacked\n## Status\n- hijacked\n## Next\n- ex4 dans ex4.py\n"
               "decision: stdlib only")
        self.end_session("s2", "blocked: no network, the download failed", Fake(out))
        after = self.doc(NODE)
        self.assertNotEqual(before.summary, "")
        self.assertEqual(after.sections["Status"], ctx.doc.read_doc(self.p(NODE)).sections["Status"])
        self.assertNotIn("hijacked", self.text(NODE))
        self.assertEqual(after.sections["Next"], "- ex4 dans ex4.py")
        self.assertEqual(after.sections["Decisions"], "- stdlib only")

    def test_configurable_words(self):
        (self.home / ".ctx").mkdir(exist_ok=True)
        (self.home / ".ctx" / "config").write_text("light_words = coincé\n")
        fk = Fake("## Next\n- x dans x.py")
        self.end_session("s3", "Je suis coincé sur ex4, toujours coincé.", fk)
        self.assertEqual(len(fk.calls), 1)
        fk = Fake("## Next\n- x dans x.py")
        self.end_session("s4", "The build failed and is blocked.", fk)
        self.assertEqual(fk.calls, [])


class TestLiveness(Base):
    def test_live_pid_keeps_session_live_past_the_window(self):
        self.session("L", NODE, [NODE + "/a.py"], "m")
        sd = ctx.sessions.session_dir_of("L")
        (sd / "pid").write_text(f"{os.getpid()} {ctx.sessions.proc_start(os.getpid())}\n")
        old = time.time() - 700
        for x in [*sd.iterdir(), sd]:
            os.utime(x, (old, old))
        self.assertTrue(ctx.sessions.session_live(sd))
        self.assertNotIn("L", ctx.sessions.catch_up(spawn=False, skip_live=True))
        (sd / "pid").unlink()
        os.utime(sd, (old, old))
        self.assertFalse(ctx.sessions.session_live(sd))  # no pid file: the 600 s window applies


class TestStopReportsCollection(Base):
    """Root cause of the "no session reports" rewrites: the hook's parent is the `sh -c` that runs
    the hook command, dead right after it; a concurrent session start then caught up the running
    session before its Stop hook stored the reports."""

    def test_running_session_behind_sh_is_not_caught_up_by_another_start(self):
        node = self.p(NODE)
        ev = json.dumps({"session_id": "live1", "cwd": str(node), "tool_name": "Edit",
                         "tool_input": {"file_path": str(node / "a.py")}})
        cli = ("import subprocess, sys\n"  # a stand-in CLI: runs the hook as Claude Code does
               f"subprocess.run(['sh', '-c', 'env CTX_HOOK=1 {sys.executable} {CTX_PY} hook "
               f"post-tool-use'], input={ev!r}, text=True)\n"
               "print('hooked', flush=True)\nsys.stdin.read()\n")
        p = subprocess.Popen([sys.executable, "-c", cli], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=dict(os.environ))
        try:
            self.assertEqual(p.stdout.readline().strip(), "hooked")
            sd = ctx.sessions.session_dir_of("live1")
            self.assertEqual(int((sd / "pid").read_text().split()[0]), p.pid)
            self.assertFalse(ctx.sessions.pid_dead(sd))  # was the dead `sh`: the session looked finished
            self.assertNotIn("live1", ctx.sessions.catch_up(spawn=False, skip_live=True))
        finally:
            p.stdin.close()
            p.wait(10)
        self.assertTrue(ctx.sessions.pid_dead(sd))  # the CLI gone: the session is caught up
        self.assertIn("live1", ctx.sessions.catch_up(spawn=False, skip_live=True))

    def test_reports_reach_the_rewrite_when_stop_comes_last(self):
        node, fk = self.p(NODE), Fake()
        ctx.sessions.collect_tool({"session_id": "s9", "cwd": str(node), "tool_name": "Edit",
                          "tool_input": {"file_path": str(node / "a.py")}})
        ctx.sessions.collect_stop({"session_id": "s9", "cwd": str(node), "last_assistant_message": "did a"})
        ctx.sync.sync_session("s9", fk)
        self.assertIn("did a", fk.calls[0][1])


if __name__ == "__main__":
    import unittest
    unittest.main()
