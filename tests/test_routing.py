"""Routing by git root, bubbling across non-node folders, and what a container injects."""
import unittest

import test_ctx
from test_ctx import Fake, ctx


class Routing(test_ctx.Base):
    def repo(self, rel):
        d = self.p(rel)
        (d / ".git" / "info").mkdir(parents=True)
        (d / "main.py").write_text("x = 1\n")
        return d

    def test_file_under_a_container_goes_to_its_git_root_made_a_node(self):
        d = self.repo("projects/tools/cli")  # tools/ is a plain folder, projects/ a container
        out = ctx.sync.route([str(d / "src" / "a.py"), str(d / "main.py")], str(self.home))
        self.assertEqual(list(out), [d])
        self.assertTrue(ctx.has_ctx(d))
        self.assertIn("CONTEXT.md", (d / ".git" / "info" / "exclude").read_text())
        self.assertIn("- **tools/cli/** — ", self.doc("projects").sections[ctx.SEC_SUBS])
        log = (self.home / ".ctx" / "log").read_text()
        self.assertIn("auto-init", log)

    def test_declined_repository_is_logged_as_unrouted(self):
        d = self.repo("projects/fleet")
        self.run_ctx("decline", str(d))
        out = ctx.sync.route([str(d / "main.py")], str(self.home))
        self.assertEqual(out, {})
        self.assertFalse(ctx.has_ctx(d))
        log = (self.home / ".ctx" / "log").read_text()
        self.assertIn("unrouted", log)
        self.assertIn(f"{d} (1)", log)

    def test_plain_folder_under_a_container_is_logged_as_unrouted(self):
        d = self.p("projects/scratch")
        d.mkdir()
        self.assertEqual(ctx.sync.route([str(d / "x.txt")], str(self.home)), {})
        self.assertIn("unrouted", (self.home / ".ctx" / "log").read_text())
        self.assertFalse(ctx.has_ctx(d))

    def test_session_in_an_absorbing_node_is_unchanged(self):
        n = self.p("courses/ml-intro")
        sub = n / "tp1"
        (sub / ".git").mkdir(parents=True)  # a repo inside an absorbing node stays part of it
        self.assertEqual(ctx.sync.route([str(sub / "a.py")], str(n)), {n: [str(sub / "a.py")]})
        self.assertFalse(ctx.has_ctx(sub))

    def test_deep_node_bubbles_to_the_closest_node_above(self):
        d = self.p("projects/group/deep")
        d.mkdir(parents=True)
        self.run_ctx("init", str(d))
        self.assertEqual(ctx.tree.parent_node(d), self.p("projects"))
        self.assertIn(d, ctx.tree.child_nodes(self.p("projects")))
        ctx.sync_node(d, [str(d / "a.py")], [], Fake(test_ctx.fake_output(summary="Deep work")))
        self.assertIn("**group/deep/** — Deep work", self.doc("projects").sections[ctx.SEC_SUBS])
        self.assertIn("group/deep: Deep work", self.doc("projects").summary)


class ContainerInjection(test_ctx.Base):
    def test_container_injects_no_placeholder_no_computed_summary_one_subfolders(self):
        ctx.sync_node(self.p("courses/ml-intro"), [str(self.p("courses/ml-intro/a.py"))], [],
                      Fake(test_ctx.fake_output(summary="Chapitre 4")))
        txt = ctx.build_injection(self.p("courses"))[0]
        self.assertEqual(txt.count("## Subfolders"), 1)
        self.assertNotIn("(to be filled in)", txt)
        self.assertNotIn("## Status", txt)
        self.assertNotIn("summary:", txt)  # computed: it only repeats the Subfolders lines
        self.assertIn("Chapitre 4", txt)

    def test_node_with_real_content_keeps_its_sections(self):
        n = self.p("courses/ml-intro")
        ctx.sync_node(n, [str(n / "a.py")], [], Fake(test_ctx.fake_output(summary="Chapitre 4")))
        txt = ctx.build_injection(n)[0]
        self.assertIn("summary: Chapitre 4", txt)
        self.assertIn("## Status", txt)

    def test_written_file_still_has_header_and_all_sections(self):
        t = (self.p("courses") / "CONTEXT.md").read_text()
        self.assertTrue(t.startswith("---\nctx: 1\n"))
        self.assertEqual(t.count("## Subfolders"), 1)
        self.assertIn("## Status\n(to be filled in)", t)


if __name__ == "__main__":
    unittest.main()
