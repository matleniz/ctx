# Cases are numbered R1..R12 (the label given to self.ev); see each test name and body for what it proves.
"""Failure cases R1-R12 (crashes, cuts, model failures, external edits, full disk), each proven (real processes, fake `claude`, CTX_HOME temp mirror).

With CTX_EVIDENCE_DIR set, every test appends its evidence to $CTX_EVIDENCE_DIR/Rn.txt.
"""
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import ctx.cli  # noqa: E402
import test_ctx  # noqa: E402
from test_ctx import Fake, fake_output  # noqa: E402

CTX_PY = HERE.parent / "ctx.py"
LINUX_ONLY = "Linux-only: /dev/full, /proc, PR_SET_PDEATHSIG"
OK_JSON = json.dumps({"result": fake_output(), "usage": {"input_tokens": 10, "output_tokens": 5}})
# What the `claude` CLI really prints (stdout JSON, empty stderr, exit 1) when logged out: captured 2026-11-04 with
# `HOME=<empty> claude -p hi --output-format json` The other two are the documented texts.
LOGGED_OUT = json.dumps({"is_error": True, "result": "Not logged in · Please run /login", "usage": {}})
QUOTA = json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1793750400", "usage": {}})
NETWORK = json.dumps({"is_error": True, "result": "API Error: Unable to connect to API (ConnectionRefused)", "usage": {}})


class Robust(test_ctx.Base):
    def setUp(self):
        super().setUp()
        self.bin = self.home.parent / "bin"
        self.bin.mkdir()
        self.work = self.home.parent / "work"
        self.work.mkdir()
        self.node = self.p("courses/ml-intro")
        self.f1 = "courses/ml-intro/exos/ex1.py"

    def fake_claude(self, body):
        f = self.bin / "claude"
        f.write_text("#!/bin/bash\ncat >/dev/null\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def with_path(self, path):
        old = os.environ["PATH"]
        os.environ["PATH"] = path
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def ev(self, rn, text):
        d = os.environ.get("CTX_EVIDENCE_DIR")
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)
            with open(Path(d) / f"{rn}.txt", "a") as f:
                f.write(text.replace(str(self.home.parent), "$TMP").rstrip() + "\n")

    def end(self, sid):
        (self.home / ".ctx/sessions" / sid / "ended").write_text(ctx.paths.stamp())

    def wait(self, cond, secs=20):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def alive(self, pid):
        if sys.platform != "linux":  # liveness is read from /proc: never answer "dead" by default
            self.skipTest(LINUX_ONLY)
        try:
            os.kill(pid, 0)
            return Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] != "Z"
        except (OSError, IndexError):
            return False

    def tmp_files(self):
        return [str(x) for x in self.home.rglob("*.tmp")]

    def env(self, **kw):
        return dict(os.environ, **kw)

    def logtail(self, n=6):
        return "".join(self.log_text().splitlines(True)[-n:])

    def hook(self, name, ev, home=None, **kw):
        return subprocess.run([sys.executable, str(CTX_PY), "hook", name], input=json.dumps(ev), capture_output=True,
                              text=True, env=self.env(CTX_HOME=str(home or self.home)), **kw)


class TestCrashesAndCuts(Robust):
    @unittest.skipUnless(sys.platform == "linux", LINUX_ONLY)
    def test_kill_minus_9_of_the_sync_group_during_the_model_call(self):
        self.fake_claude(f'echo $$ > {self.work}/claude.pid\nif [ -f {self.work}/slow ]; then exec sleep 300; fi\n'
                         f"cat <<'EOF'\n{OK_JSON}\nEOF\n")
        (self.work / "slow").write_text("")
        self.with_path(f"{self.bin}:{os.environ['PATH']}")
        before = self.text("courses/ml-intro")
        self.session("s1", "courses/ml-intro", [self.f1], "ex1 écrit")
        self.end("s1")
        proc = subprocess.Popen([sys.executable, str(CTX_PY), "sync", "--session", "s1"], start_new_session=True,
                                env=self.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(self.wait(lambda: (self.work / "claude.pid").exists()), "model call never started")
        cpid = int((self.work / "claude.pid").read_text())
        os.killpg(proc.pid, signal.SIGKILL)  # the computer stops: the whole sync group goes
        proc.wait()
        self.assertTrue(self.wait(lambda: not self.alive(cpid), 5), "claude survived the sync")
        self.assertEqual(self.text("courses/ml-intro"), before)  # no partial CONTEXT.md
        self.assertEqual(self.tmp_files(), [])
        for n in (self.node, self.p("courses"), self.home):
            with ctx.paths.dirlock(n, blocking=False) as got:
                self.assertTrue(got, f"residual lock on {n}")
        sd = self.home / ".ctx/sessions/s1"
        self.assertTrue((sd / "ended").exists() and not (sd / "synced.json").exists())
        # next SessionStart: immediate catch-up, same result as without the cut
        (self.work / "slow").unlink()
        out = self.hook("session-start", {"session_id": "s2", "cwd": str(self.node)})
        self.assertEqual(out.returncode, 0)
        self.assertTrue(self.wait(lambda: (sd / "synced.json").exists()), "no catch-up")
        d = self.doc("courses/ml-intro")
        self.assertEqual(d.summary, "Chapitre 4 avance, ex3 ajouté")
        self.assertIn("ex3 ajouté", d.sections[ctx.paths.SEC_STATUS])
        st = json.loads((sd / "synced.json").read_text())
        self.assertEqual((st["offset"], st["fails"]), (1, 0))
        self.assertEqual(self.tmp_files(), [])
        self.ev("R1", f"killpg(SIGKILL) of the sync group while claude (pid {cpid}) was mid-call\n"
                      f"claude alive after kill: {self.alive(cpid)}; CONTEXT.md unchanged: True; *.tmp left: []; locks free: True\n"
                      f"session s1: ended present, synced.json absent\nnext SessionStart -> catch-up:\n{self.logtail()}\n"
                      f"final summary: {d.summary!r}; synced.json: {st}")

    def test_cut_between_node_rewrite_and_parent_rebuild(self):
        script = f"""
import os, signal, sys
sys.path.insert(0, {str(HERE.parent / "src")!r})
import ctx.cli
ctx.sync.bubble = lambda n: os.kill(os.getpid(), signal.SIGKILL)  # power cut right after the node is written
ctx.sync.sync_session("s1", lambda s, p: ({fake_output()!r}, {{"in": 1, "out": 1, "secs": 0}}))
"""
        self.session("s1", "courses/ml-intro", [self.f1], "ex1 écrit")
        self.end("s1")
        r = subprocess.run([sys.executable, "-c", script], env=self.env())
        self.assertEqual(r.returncode, -signal.SIGKILL)
        self.assertEqual(self.doc("courses/ml-intro").summary, "Chapitre 4 avance, ex3 ajouté")  # node written
        self.assertNotIn("Chapitre 4 avance", self.text("courses"))  # parent not rebuilt
        for n in (self.node, self.p("courses"), self.home):
            with ctx.paths.dirlock(n, blocking=False) as got:
                self.assertTrue(got)
        rc, out = self.run_ctx("doctor")
        self.assertIn("0 problem(s)", out)
        fk = Fake()
        self.assertEqual(ctx.sync.sync_node(self.node, [], [], fk), "ok")  # the work queued by the cut session is drained, then the bubble runs
        self.assertEqual(len(fk.calls), 1)
        self.assertIn("Chapitre 4 avance", self.text("courses"))
        self.assertIn("Chapitre 4 avance", self.text(""))
        self.ev("R2", f"SIGKILL inside bubble(): node summary written, parent (courses) not yet\nlocks free: True; "
                      f"doctor: {out.strip().splitlines()[-1]}\nlater ctx.sync.sync_node drains the queued work and rebuilds courses and ~:\n{self.logtail(3)}")

    def test_cut_between_session_end_hook_and_popen(self):
        from unittest import mock
        self.session("s1", "courses/ml-intro", [self.f1], "ex1 écrit")
        with mock.patch.object(ctx.hooks, "spawn_sync", side_effect=lambda sid: None) as sp:  # the Popen never happens
            ctx.hooks.run_hook("session-end", json.dumps({"session_id": "s1", "cwd": str(self.node)}))
        sd = self.home / ".ctx/sessions/s1"
        self.assertEqual(sp.call_count, 1)
        self.assertTrue((sd / "ended").exists() and not (sd / "synced.json").exists())
        spawned = []
        with mock.patch.object(ctx.sessions, "spawn_sync", side_effect=spawned.append):  # catch_up
            ctx.hooks.run_hook("session-start", json.dumps({"session_id": "s2", "cwd": str(self.node)}))
        self.assertEqual(spawned, ["s1"])
        self.assertEqual(ctx.sync.sync_session("s1", Fake()), "ok")
        self.ev("R3", f"SessionEnd hook ran (ended marker written) but spawn_sync did nothing (no Popen)\n"
                      f"session s1 stays pending: ended={ (sd/'ended').exists() }\nnext SessionStart spawned: {spawned}\n{self.logtail(2)}")

    def test_sleep_and_wsl_stop(self):
        me = os.getpid()
        self.session("A", "courses/ml-intro", [self.f1], "ex1")
        self.session("B", "courses/algebra", ["courses/algebra/tp/tp1.py"], "tp1")
        sa, sb = (self.home / ".ctx/sessions" / x for x in "AB")
        for sd in (sa, sb):  # both launched by a live process (this one)
            (sd / "pid").write_text(f"{me} {ctx.sessions.proc_start(me)}\n")
        self.assertTrue(ctx.sessions.session_live(sa))
        self.assertEqual(ctx.sessions.catch_up(spawn=False, skip_live=True), [])  # nothing lost, nothing stolen while live
        # computer sleeps 13 h (past the 12 h a live pid protects): A's timestamps are shifted, B kept working
        for x in [sa, *sa.iterdir()]:
            os.utime(x, (time.time() - 13 * 3600,) * 2)
        self.assertFalse(ctx.sessions.session_live(sa))
        found = ctx.sessions.catch_up(spawn=False, skip_live=True)
        self.assertEqual(found, ["A"])
        fk = Fake()
        self.assertEqual(ctx.sync.sync_session("A", fk), "ok")
        self.assertEqual(len(fk.calls), 1)
        self.assertIn("ex1.py", fk.calls[0][1])
        self.assertNotIn("tp1.py", fk.calls[0][1])  # B's work was not attributed to A's node
        self.assertEqual(self.doc("courses/algebra").summary, ctx.paths.DEFAULT_SUMMARY)
        self.assertEqual(ctx.sessions.session_pending(sb)[0], ["%s" % (self.p("courses/algebra/tp/tp1.py"))])
        # A resumes after the sleep and edits one more file: only that one is synced, once
        self.session("A", "courses/ml-intro", ["courses/ml-intro/exos/ex2.py"], "ex2")
        self.end("A")
        fk2 = Fake(fake_output(summary="ex2 aussi"))
        self.assertEqual(ctx.sync.sync_session("A", fk2), "ok")
        self.assertIn("ex2.py", fk2.calls[0][1])
        self.assertNotIn("ex1.py", fk2.calls[0][1])
        self.assertEqual(ctx.sync.sync_session("A", fk2), "nothing")
        # WSL stopped: every process was killed, the recorded CLI pid is gone -> not live at once, even with fresh timestamps
        dead = subprocess.Popen(["sleep", "300"])
        pid, started = dead.pid, ctx.sessions.proc_start(dead.pid)
        self.session("C", "courses/challenge", ["courses/challenge/src/train.py"], "train")
        sc = self.home / ".ctx/sessions/C"
        (sc / "pid").write_text(f"{pid} {started}\n")
        self.assertTrue(ctx.sessions.session_live(sc))
        dead.kill()
        dead.wait()
        self.assertFalse(ctx.sessions.session_live(sc))
        self.assertIn("C", ctx.sessions.catch_up(spawn=False, skip_live=True))
        # pid reused by an unrelated process after the reboot: start time differs -> still dead, never mistaken for live
        if sys.platform != "linux":  # the start time comes from /proc
            self.skipTest(LINUX_ONLY + " (pid reuse check; the rest of this test ran)")
        (sc / "pid").write_text(f"{me} 1\n")
        self.assertTrue(ctx.sessions.pid_dead(sc))
        self.ev("R10", "13 h sleep (timestamps shifted): A caught up once, B (live) untouched, no cross-attribution, "
                       "post-resume edit synced alone\nWSL stop (CLI pid killed): C not live immediately, caught up; "
                       f"reused pid (start time mismatch) -> dead\n{self.logtail(4)}")


class TestModelFailuresAndTimeouts(Robust):
    def run_fails(self, sid, rewriter=ctx.rewriter.cli_rewriter):
        self.session(sid, "courses/ml-intro", [self.f1], "ex1")
        self.end(sid)
        return ctx.sync.sync_session(sid, rewriter)

    def test_model_call_impossible_four_causes(self):
        cases = {"logged-out": (f"cat <<'EOF'\n{LOGGED_OUT}\nEOF\nexit 1\n", "Not logged in"),
                 "quota": (f"cat <<'EOF'\n{QUOTA}\nEOF\nexit 1\n", "usage limit reached"),
                 "network": (f"cat <<'EOF'\n{NETWORK}\nEOF\nexit 1\n", "Unable to connect"),
                 "no-claude": (None, "claude")}
        for name, (body, cause) in cases.items():
            with self.subTest(name):
                before = self.text("courses/ml-intro")
                if body is None:
                    self.with_path(str(self.work))  # empty PATH entry: no claude anywhere
                else:
                    self.fake_claude(body)
                    self.with_path(f"{self.bin}:{os.environ['PATH']}")
                self.assertEqual(self.run_fails("s-" + name), "fail")
                self.assertEqual(self.text("courses/ml-intro"), before)  # old file kept
                self.assertRegex(self.log_text(), rf"sync-fail node=\S*ml-intro error=RuntimeError: claude exit 1: .*{cause}"
                                 if body else rf"sync-fail node=\S*ml-intro error=FileNotFoundError: .*{cause}")
                self.assertIn(cause, ctx.doc.status_line(self.node))
                self.ev("R4", f"[{name}] sync -> fail; CONTEXT.md unchanged\n{self.logtail(1)}status line: {ctx.doc.status_line(self.node)}")

    def test_abandon_is_visible_and_retry_by_hand(self):
        self.fake_claude(f"cat <<'EOF'\n{LOGGED_OUT}\nEOF\nexit 1\n")
        self.with_path(f"{self.bin}:{os.environ['PATH']}")
        self.assertEqual(self.run_fails("s1"), "fail")
        self.assertEqual(ctx.sessions.abandoned(), [])  # first failure: still retried automatically
        self.assertEqual(ctx.sync.sync_session("s1", ctx.rewriter.cli_rewriter), "fail")  # second: given up
        self.assertEqual(ctx.sessions.catch_up(spawn=False), [])
        ab = ctx.sessions.abandoned()
        self.assertEqual([x[0] for x in ab], ["s1"])
        rc, out = self.run_ctx("doctor")
        self.assertEqual(rc, 1)
        self.assertIn("abandoned session: s1 (RuntimeError: claude exit 1: Not logged in", out)
        self.assertIn(" sync --session s1` retries by hand", out)
        inj, _ = ctx.hooks.build_injection(self.node)
        self.assertIn("last sync: FAILED", inj)
        self.assertIn("Sync abandoned (session s1", inj)
        self.assertIn("Sync abandoned (session s1, RuntimeError: claude exit 1: Not logged in", inj.split("<ctx-data>")[1])
        self.assertIn("sync --session s1", inj)
        # the user logs in again: the manual retry syncs the very same work
        self.fake_claude(f"cat <<'EOF'\n{OK_JSON}\nEOF\n")
        rc, out2 = self.run_ctx("sync", "--session", "s1")
        self.assertIn("session s1: ok", out2)
        self.assertEqual(ctx.sessions.abandoned(), [])
        self.assertEqual(self.doc("courses/ml-intro").summary, "Chapitre 4 avance, ex3 ajouté")
        self.assertIn("last sync: ok", ctx.doc.status_line(self.node))
        self.assertIn("0 problem(s)", self.run_ctx("doctor")[1])
        self.ev("R4", f"abandon after 2 failures\n--- ctx doctor:\n{out}--- SessionStart injection head:\n{inj.splitlines()[0]}\n"
                      f"--- after login, `ctx sync --session s1`: {out2.strip()}; abandoned={ctx.sessions.abandoned()}")

    @unittest.skipUnless(sys.platform == "linux", LINUX_ONLY)
    def test_timeout_kills_the_whole_process_group(self):
        for mod in (ctx.rewriter, ctx.sync):  # the model call and the per-node deadline
            patcher = mock.patch.object(mod, "MODEL_TIMEOUT", 2)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fake_claude(f"(sleep 300 & echo $! > {self.work}/grandchild.pid; wait) & echo $! > {self.work}/child.pid\n"
                         f"echo $$ > {self.work}/claude.pid\nwait\n")
        self.with_path(f"{self.bin}:{os.environ['PATH']}")
        t0 = time.time()
        self.assertEqual(self.run_fails("s1"), "fail")
        pids = [int((self.work / f).read_text()) for f in ("claude.pid", "child.pid", "grandchild.pid")]
        self.assertTrue(self.wait(lambda: not any(self.alive(p) for p in pids), 5), pids)
        self.assertRegex(self.log_text(), r"sync-fail .*model timeout after 2s")
        self.assertIn("model timeout", ctx.doc.status_line(self.node))
        self.ev("R5", f"MODEL_TIMEOUT=2 with a fake claude holding a child and a grandchild (sleep 300)\n"
                      f"returned after {time.time() - t0:.1f}s; pids {pids} alive: {[self.alive(p) for p in pids]}\n{self.logtail(1)}")

    @unittest.skipUnless(sys.platform == "linux", LINUX_ONLY)
    def test_sync_deadline_also_kills_the_group(self):
        p = mock.patch.object(ctx.sync, "sync_deadline", lambda: 2)
        p.start()
        self.addCleanup(p.stop)
        self.fake_claude(f"echo $$ > {self.work}/claude.pid\n(sleep 300 & echo $! > {self.work}/grandchild.pid; wait)\n")
        self.with_path(f"{self.bin}:{os.environ['PATH']}")
        self.assertEqual(self.run_fails("s1"), "fail")  # SIGALRM deadline fires inside communicate()
        pids = [int((self.work / f).read_text()) for f in ("claude.pid", "grandchild.pid")]
        self.assertTrue(self.wait(lambda: not any(self.alive(p) for p in pids), 5), pids)
        self.assertRegex(self.log_text(), r"sync-fail .*error=Alarm: sync deadline")
        self.assertIn("sync deadline", ctx.doc.status_line(self.node))
        self.assertEqual(self.run_fails("s1b"), "fail")
        self.assertEqual(self.run_fails("s1"), "fail")  # second failure of s1: abandoned, same as R4
        self.assertIn("s1", [x[0] for x in ctx.sessions.abandoned()])
        self.ev("R5", f"sync_deadline=2 (SIGALRM deadline) while claude runs: pids {pids} alive: {[self.alive(p) for p in pids]}\n"
                      f"same treatment as R4: abandoned={[x[0] for x in ctx.sessions.abandoned()]}\n{self.logtail(3)}")


class TestExternalEditsAndMovedNodes(Robust):
    def test_external_write_is_the_base_and_conflict_files_are_ignored(self):
        self.session("s1", "courses/ml-intro", [self.f1], "ex1")
        self.assertEqual(ctx.sync.sync_session("s1", Fake()), "ok")
        d = self.doc("courses/ml-intro")
        d.sections[ctx.paths.SEC_NOTES] = "- noté depuis l'autre machine"
        d.sections[ctx.paths.SEC_STATUS] = "- travail de la machine B"
        (self.node / "CONTEXT.md").write_text(ctx.doc.render(d, updated="2026-11-04T09:00"))  # written outside ctx (Drive/Syncthing)
        (self.node / "CONTEXT (conflit).md").write_text("---\nsummary: FAUX conflit\n---\n## Notes\n- ne pas lire\n")
        (self.node / "CONTEXT.sync-conflict-20261104-101010-ABCDEFG.md").write_text("conflit syncthing UNIQUE-MARQUEUR\n")
        inj, _ = ctx.hooks.build_injection(self.node)
        self.assertNotIn("FAUX conflit", inj)
        self.assertNotIn("UNIQUE-MARQUEUR", inj)
        self.assertIn("noté depuis l'autre machine", inj)
        self.session("s2", "courses/ml-intro", ["courses/ml-intro/exos/ex2.py"], "ex2")
        fk = Fake(fake_output(summary="après B"))
        self.assertEqual(ctx.sync.sync_session("s2", fk), "ok")
        self.assertIn("travail de la machine B", fk.calls[0][1])  # the external version, not the last ctx one, is the base
        self.assertNotIn("UNIQUE-MARQUEUR", fk.calls[0][1])
        self.assertNotIn("conflit", fk.calls[0][1].split("<files")[-1])  # conflict copies are not even listed as touched files
        hist = "".join(x.read_text() for x in ctx.doc.history_dir(self.node).glob("*.md"))
        self.assertIn("travail de la machine B", hist)  # kept in history: ctx undo brings it back
        self.assertIn("noté depuis l'autre machine", self.text("courses/ml-intro"))  # Notes untouched
        rc, out = self.run_ctx("doctor")
        self.assertEqual(out.count("conflict:"), 2)
        self.assertIn("CONTEXT (conflit).md", out)
        self.assertIn("CONTEXT.sync-conflict-20261104-101010-ABCDEFG.md", out)
        self.ev("R6", f"external write detected (hash != last known), used as base, kept in history; conflict copies ignored\n"
                      f"{self.logtail(3)}--- doctor:\n{out}")

    def test_moved_renamed_deleted_nodes_and_doctor_fix(self):
        self.session("s1", "courses/ml-intro", [self.f1], "ex1")
        self.assertEqual(ctx.sync.sync_session("s1", Fake()), "ok")
        self.assertIn("**ml-intro/**", self.text("courses"))
        old_hist = ctx.doc.history_dir(self.node)
        os.rename(self.node, self.p("courses/y"))  # rename: CONTEXT.md travels with the folder
        shutil.rmtree(self.p("courses/algebra"))
        rc, out = self.run_ctx("doctor")
        self.assertIn("line for missing folder", out)  # transient, until the next rebuild
        self.assertEqual(ctx.sync.sync_node(self.p("courses/y"), [], [], Fake()), "none")  # rebuild: no model call
        self.assertEqual(ctx.tree.refresh_subs(self.p("courses")), False)
        subs = self.doc("courses").sections[ctx.paths.SEC_SUBS]
        self.assertIn("**y/**", subs)
        self.assertNotIn("ml-intro", subs)
        self.assertNotIn("algebra", subs)
        self.assertIn("Chapitre 4 avance", subs)
        # a session in the renamed node works without error (state/history re-created under the new path)
        self.session("s2", "courses/y", ["courses/y/exos/ex2.py"], "ex2")
        self.assertEqual(ctx.sync.sync_session("s2", Fake(fake_output(summary="renommé"))), "ok")
        self.assertTrue(ctx.doc.history_dir(self.p("courses/y")).is_dir())
        # orphan state/history of the vanished path: kept 30 days, then doctor proposes `ctx doctor --fix`
        self.assertTrue(old_hist.is_dir())
        rc, out = self.run_ctx("doctor")
        self.assertNotIn("doctor --fix", out)
        old = time.time() - 31 * 86400
        gone = [old_hist, *old_hist.iterdir(), ctx.paths.state_dir() / "state" / f"{ctx.paths.path_key(self.node)}.json"]
        for x in gone:
            os.utime(x, (old, old))
        rc, out = self.run_ctx("doctor")
        self.assertEqual(rc, 1)
        self.assertIn(" doctor --fix` removes it", out)
        rc, pr = self.run_ctx("doctor", "--fix")
        self.assertIn("pruned 2 orphan entries", pr)
        self.assertFalse(old_hist.exists())
        self.assertTrue(ctx.doc.history_dir(self.p("courses/y")).is_dir())  # live nodes untouched
        self.assertEqual(self.run_ctx("doctor")[0], 0)
        self.ev("R7", f"rename courses/ml-intro -> courses/y, rmtree courses/algebra\nparent Subfolders after rebuild:\n{subs}\n"
                      f"--- doctor with 31-day-old orphans:\n{out}--- ctx doctor --fix: {pr}")

    def test_hand_edit_during_a_sync(self):
        self.session("s1", "courses/ml-intro", [self.f1], "ex1")
        node = self.node

        prompts = []

        def rewriter(system, prompt):  # the user edits CONTEXT.md while the model is thinking
            prompts.append(prompt)
            if len(prompts) == 1:
                d = ctx.doc.read_doc(node)
                d.sections[ctx.paths.SEC_NOTES] = "- note ajoutée à la main PENDANT la synchro"
                d.sections[ctx.paths.SEC_STATUS] = "- ligne à la main gardée"
                (node / "CONTEXT.md").write_text(ctx.doc.render(d, updated="2026-11-04T10:00"))
            return fake_output(where="- version du modèle"), {"in": 1, "out": 1, "secs": 0}
        self.assertEqual(ctx.sync.sync_session("s1", rewriter), "ok")
        d = self.doc("courses/ml-intro")
        self.assertEqual(len(prompts), 2)  # the first answer was not written over the hand edit
        self.assertIn("ligne à la main gardée", prompts[1])  # the redo starts from the hand version
        self.assertEqual(d.sections[ctx.paths.SEC_NOTES], "- note ajoutée à la main PENDANT la synchro")
        self.assertEqual(d.sections[ctx.paths.SEC_STATUS], "- version du modèle")
        self.assertRegex(self.log_text(), r"sync-conflict")
        self.assertIn("ligne à la main gardée", "".join(x.read_text() for x in ctx.doc.history_dir(node).glob("*.md")))
        self.assertEqual(self.run_ctx("undo", str(node))[0], 0)  # the hand version is one `ctx undo` away
        self.assertIn("ligne à la main gardée", self.text("courses/ml-intro"))
        self.assertIn("PENDANT la synchro", self.text("courses/ml-intro"))
        self.ev("R8", f"hand edit (Notes + Status) during the model call\nafter sync: Notes={d.sections[ctx.paths.SEC_NOTES]!r}, "
                      f"Status={d.sections[ctx.paths.SEC_STATUS]!r}\nhand version in history; `ctx undo` restores it\n{self.logtail(3)}")


class TestStateDiskAndSlowHooks(Robust):
    def test_missing_ctx_dir_is_recreated_and_hooks_never_crash(self):
        shutil.rmtree(self.home / ".ctx")
        ev = {"session_id": "s1", "cwd": str(self.node)}
        r = self.hook("session-start", ev)
        self.assertEqual(r.returncode, 0)
        self.assertIn("additionalContext", r.stdout)
        self.assertTrue((self.home / ".ctx/sessions/s1/cwd").exists())
        self.ev("R9", f"~/.ctx deleted -> session-start hook rc={r.returncode}, injection returned, ~/.ctx recreated")

    @unittest.skipIf(os.geteuid() == 0, "root ignores file modes")
    def test_read_only_home_hooks_exit_0(self):
        shutil.rmtree(self.home / ".ctx")
        os.chmod(self.home, 0o555)
        self.addCleanup(os.chmod, self.home, 0o755)
        res = {}
        for name in ("session-start", "post-tool-use", "stop", "session-end"):
            r = self.hook(name, {"session_id": "s1", "cwd": str(self.node), "tool_name": "Edit",
                                 "tool_input": {"file_path": str(self.p(self.f1))}, "last_assistant_message": "x"})
            self.assertEqual((r.returncode, r.stderr), (0, ""), name)
            res[name] = r.returncode
        self.assertIn("additionalContext", self.hook("session-start", {"session_id": "s1", "cwd": str(self.node)}).stdout)
        self.assertFalse((self.home / ".ctx").exists())  # nothing could be written, nothing crashed
        rc, out = self.run_ctx("doctor")  # the CLI itself copes too
        self.ev("R9", f"CTX_HOME read-only (chmod 555, no ~/.ctx): all four hooks rc=0, empty stderr {res}; "
                      f"session-start still injects the context; ctx doctor rc={rc}")

    @unittest.skipUnless(sys.platform == "linux", LINUX_ONLY)
    def test_disk_full_hook_exits_0_and_logs(self):
        sd = self.home / ".ctx/sessions/s1"
        sd.mkdir(parents=True)
        (sd / "touched").symlink_to("/dev/full")  # every write fails with ENOSPC
        r = self.hook("post-tool-use", {"session_id": "s1", "cwd": str(self.node), "tool_name": "Edit",
                                        "tool_input": {"file_path": str(self.p(self.f1))}})
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertRegex(self.log_text(), r"hook-error hook=post-tool-use error=OSError: \[Errno 28\] No space left")
        self.ev("R9", f"disk full (touched -> /dev/full): hook rc={r.returncode}\n{self.logtail(1)}")

    def test_log_rotation_sessions_purge_history_cap_and_size(self):
        for i in range(12000):
            ctx.paths.log("filler", n=i, pad="x" * 80)
        sd = self.home / ".ctx"
        self.assertTrue((sd / "log.1").exists())
        self.assertLessEqual((sd / "log").stat().st_size, ctx.paths.LOG_MAX)
        self.assertEqual(sorted(x.name for x in sd.iterdir() if x.name.startswith("log")), ["log", "log.1"])
        self.assertIn(" rotate ", (sd / "log").read_text())
        # 1000 simulated sessions: 900 finished 8 days ago, 100 recent; all synced
        base = sd / "sessions"
        old = time.time() - 8 * 86400
        for i in range(1000):
            s = base / f"sim{i}"
            s.mkdir(parents=True)
            (s / "touched").write_text(f"{self.p(self.f1)}\n")
            (s / "cwd").write_text(str(self.node))
            (s / "synced.json").write_text(json.dumps({"offset": 1, "last": "", "fails": 0}))
            if i < 900:
                for x in [*s.iterdir(), s]:
                    os.utime(x, (old, old))
        self.assertEqual(ctx.sessions.catch_up(spawn=False), [])
        left = len(list(base.iterdir()))
        self.assertEqual(left, 100)
        # an abandoned session is purged too, after a week
        # history: 25 distinct rewrites keep 10 snapshots
        for i in range(25):
            ctx.sync.sync_node(self.node, [str(self.p(self.f1))], [], Fake(fake_output(summary=f"v{i}")))
        self.assertEqual(len(list(ctx.doc.history_dir(self.node).glob("*.md"))), ctx.paths.HISTORY_KEEP)
        rc, out = self.run_ctx("doctor")
        self.assertRegex(out, r"uses \d+ KiB")
        self.ev("R11", f"12000 log lines: files {sorted(x.name for x in sd.iterdir() if x.name.startswith('log'))}, "
                       f"log {(sd / 'log').stat().st_size} bytes <= {ctx.paths.LOG_MAX}\n1000 sessions (900 aged 8 days) -> {left} left after catch_up\n"
                       f"25 rewrites -> {len(list(ctx.doc.history_dir(self.node).glob('*.md')))} history snapshots\n"
                       f"{[ln for ln in out.splitlines() if 'uses' in ln][0]}")

    def test_slow_hook_gives_up_before_the_cli_delay(self):
        fifo = ctx.paths.state_dir() / "state" / f"{ctx.paths.path_key(self.node)}.json"
        fifo.parent.mkdir(parents=True, exist_ok=True)
        fifo.unlink(missing_ok=True)
        os.mkfifo(fifo)  # reading the node status blocks forever: a disk that never answers
        t0 = time.time()
        r = self.hook("session-start", {"session_id": "s1", "cwd": str(self.node)}, timeout=30)
        dt = time.time() - t0
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))  # session starts, no context injected
        self.assertGreaterEqual(dt, ctx.paths.HOOK_DEADLINE - 1)
        # No wall-clock "< 10 s" here: on WSL2 the kernel delivers SIGALRM to a process blocked
        # opening a FIFO up to ~2.5 s late, one run in four (same with plain Python: open + alarm(8)
        # -> 8.0 s or 10.0-10.4 s). What ctx controls is checked instead: an 8 s deadline, 2 s under
        # the CLI's 10 s, armed as soon as the hook starts (below).
        self.assertLess(dt, 20)
        self.assertRegex(self.log_text(), r"hook-timeout hook=session-start error=Alarm")
        self.assertLessEqual(ctx.paths.HOOK_DEADLINE, 8)
        armed, real_alarm = [], signal.alarm
        t1 = time.time()

        def alarm(secs):
            armed.append((secs, time.time() - t1))
            return real_alarm(secs)
        with mock.patch.object(ctx.hooks, "HOOK_DEADLINE", 1), mock.patch.object(ctx.paths.signal, "alarm", alarm), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"session_id": "s2", "cwd": str(self.node)}))), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ctx.cli.main(["hook", "session-start"]), 0)  # in-process: same code, 1 s deadline
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(armed[0][0], 1)  # the hook arms its deadline first...
        self.assertLess(armed[0][1], 0.5)  # ...right away, before any read
        self.assertEqual(armed[-1][0], 0)  # and disarms it
        self.ev("R12", f"node status file replaced by a FIFO (read never returns): hook returned rc={r.returncode} after {dt:.1f}s "
                       f"(< 10 s CLI delay), no output\n{self.logtail(1)}")
        fifo.unlink()


def _sync_child(node, summary, barrier, write_delay, subs_delay):
    """Child process: sync one node with a fake rewriter that waits for its sibling, then bubble.

    The delays widen the race window: `write_delay` before the node's own write, `subs_delay`
    between reading the siblings (build_subs) and writing the parent. Without the parent lock the
    slow writer would put back a stale line of its sibling.
    """
    def rewriter(system, prompt):
        barrier.wait(30)  # both rewrites return together: both bubbles race for the shared parent
        return fake_output(summary), {"in": 1, "out": 1, "secs": 0}

    real_write, real_subs = ctx.sync.write_doc, ctx.tree.build_subs

    def slow_write(*a, **k):
        time.sleep(write_delay)
        return real_write(*a, **k)

    def slow_subs(*a, **k):
        out = real_subs(*a, **k)
        time.sleep(subs_delay)
        return out
    with mock.patch.object(ctx.sync, "write_doc", slow_write), mock.patch.object(ctx.tree, "build_subs", slow_subs):
        ok = ctx.sync.sync_node(Path(node), [str(Path(node) / "x.py")], [], rewriter) == "ok"
    os._exit(0 if ok else 1)


class TestSiblingsAndWorktrees(test_ctx.Base):
    def init(self, d):
        d.mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.run_ctx("init", str(d))[0], 0)

    def leftovers(self):
        return sorted(str(f) for f in self.home.rglob("*") if f.name.endswith(".tmp"))

    def test_two_siblings_bubbling_to_the_same_parent_concurrently(self):
        import multiprocessing
        parent = self.p("labs")
        kids = [parent / "alpha", parent / "beta"]
        for d in (parent, *kids):
            self.init(d)
        mp = multiprocessing.get_context("spawn")  # not fork: macOS and Python 3.14 warn on fork with threads
        for rnd in range(3):
            barrier = mp.Barrier(2)
            sums = [f"{k.name} round {rnd} done" for k in kids]
            for k in kids:
                (k / "x.py").write_text(f"v{rnd}")
            delays = [(0, 0.6), (0.3, 0)]  # alpha reads its siblings first and writes the parent last
            ps = [mp.Process(target=_sync_child, args=(str(k), s, barrier, *dl))
                  for k, s, dl in zip(kids, sums, delays)]
            for pr in ps:
                pr.start()
            for pr in ps:
                pr.join(60)
                self.assertEqual(pr.exitcode, 0)
            subs = ctx.doc.read_doc(parent).sections[ctx.paths.SEC_SUBS]  # not the computed summary: it rereads
            for k, s in zip(kids, sums):
                self.assertIn(f"- **{k.name}/** — {s}", subs, f"round {rnd}: a sibling's line was lost")
            self.assertEqual(self.leftovers(), [])
            for d in (parent, *kids):
                with ctx.paths.dirlock(d, blocking=False) as got:
                    self.assertTrue(got, f"lock left on {d}")
        self.assertTrue(ctx.doc.valid_shape(ctx.doc.read_doc(parent)))

    def git(self, *args, cwd):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_nodes_in_git_worktrees(self):
        repo, wts = self.p("gitwork/repo"), [self.p("gitwork/wt1"), self.p("gitwork/wt2")]
        (repo / "app").mkdir(parents=True)
        (repo / "app" / "main.py").write_text("print(1)\n")
        self.git("init", "-q", "-b", "main", cwd=repo)
        self.git("add", ".", cwd=repo)
        self.git("commit", "-q", "-m", "init", cwd=repo)
        for i, wt in enumerate(wts):
            self.git("worktree", "add", "-q", "-b", f"b{i}", str(wt), cwd=repo)
        nodes = [wt / "app" for wt in wts]
        for n in nodes:
            self.init(n)
        # the exclude goes to the common git dir (git reads info/exclude there for every worktree)
        self.assertEqual(ctx.tree.git_info_dir(nodes[0]), (repo / ".git" / "info").resolve())
        self.assertIn("CONTEXT.md", (repo / ".git/info/exclude").read_text().split("\n"))
        self.assertFalse((repo / ".git/worktrees/wt1/info/exclude").exists())
        for wt in [repo, *wts]:
            self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all", cwd=wt), "", wt)
        # two nodes of the same repository, one per worktree: independent queue, history and state
        for n, s in zip(nodes, ("wt1 state", "wt2 state")):
            (n / "x.py").write_text(s)
            self.assertEqual(ctx.sync.sync_node(n, [str(n / "x.py")], [], Fake(fake_output(s))), "ok")
        self.assertEqual([ctx.doc.read_doc(n).summary for n in nodes], ["wt1 state", "wt2 state"])
        self.assertNotEqual(ctx.doc.history_dir(nodes[0]), ctx.doc.history_dir(nodes[1]))
        self.assertNotEqual(ctx.paths.path_key(nodes[0]), ctx.paths.path_key(nodes[1]))
        self.assertEqual([ctx.doc.get_status(n)["path"] for n in nodes], [str(n.resolve()) for n in nodes])
        (nodes[1] / "y.py").write_text("more")
        self.assertEqual(ctx.sync.sync_node(nodes[1], [str(nodes[1] / "y.py")], [], Fake(fake_output("wt2 again"))), "ok")
        self.assertEqual([ctx.doc.read_doc(n).summary for n in nodes], ["wt1 state", "wt2 again"])
        for wt in [repo, *wts]:  # after the syncs too (the session's own files are untracked, not CONTEXT.md)
            self.assertNotIn("CONTEXT.md", self.git("status", "--porcelain", "--untracked-files=all", cwd=wt))


if __name__ == "__main__":
    unittest.main()
