"""First run: replay exactly what `install.sh` suggests, in a throwaway home, then `ctx doctor` is clean."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent


class FirstRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        top = Path(self._tmp.name).resolve()
        self.home = top / "home"
        (self.home / "projects" / "app").mkdir(parents=True)
        fake_bin = top / "bin"
        fake_bin.mkdir()
        claude = fake_bin / "claude"  # install.sh and doctor only look it up on PATH
        claude.write_text("#!/bin/sh\nexit 0\n")
        claude.chmod(0o755)
        path = [str(self.home / ".local/bin"), str(fake_bin), os.path.dirname(sys.executable), "/usr/bin", "/bin"]
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CTX_")}
        self.env.update(HOME=str(self.home), PATH=os.pathsep.join(path))

    def sh(self, line):
        return subprocess.run(line, shell=True, env=self.env, cwd=self.home, capture_output=True, text=True,
                              timeout=60)

    def replay(self):
        r = self.sh(f"sh {CODE / 'install.sh'}")
        self.assertEqual(r.returncode, 0, r.stderr)
        nxt = [x.strip() for x in r.stdout.split("Next (not run):", 1)[1].strip().splitlines()]
        for line in nxt:
            if " install-hooks " in line:
                line += " --dry-run"  # the real thing would write the CLI config
            r = self.sh(line.replace("<project>", str(self.home / "projects" / "app")))
            self.assertEqual(r.returncode, 0, f"{line}\n{r.stdout}{r.stderr}")
        return nxt

    def test_suggested_commands_then_doctor_exits_0(self):
        self.assertIn("ctx init --no-absorb ~", self.replay())
        r = self.sh("ctx doctor")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 problem(s)", r.stdout)
        # projects/ is not a node: app reports straight to the root node
        self.assertNotIn("does not bubble", r.stdout)
        self.assertIn("app/** — ", (self.home / "CONTEXT.md").read_text())
        self.assertNotIn("orphan", r.stdout)
        self.assertNotIn("leaf node", r.stdout)
        r = self.sh(f"ctx show {self.home / 'projects' / 'app'}")
        self.assertIn("Detail of a subfolder: `ctx show <subfolder>`", r.stdout)

    def test_without_the_link_on_path_the_hints_spell_the_full_command(self):
        self.env["PATH"] = self.env["PATH"].split(os.pathsep, 1)[1]
        nxt = self.replay()
        self.assertIn(f"python3 '{(CODE / 'ctx.py').resolve()}' init --no-absorb ~", nxt)  # quoted: pasteable
        self.assertFalse(any(x.startswith("ctx ") for x in nxt))
        r = self.sh(f"python3 {CODE / 'ctx.py'} show {self.home / 'projects' / 'app'}")
        self.assertIn(f"Detail of a subfolder: `python3 {(CODE / 'ctx.py').resolve()} show", r.stdout)


if __name__ == "__main__":
    unittest.main()
