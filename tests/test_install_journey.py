"""install.sh + install-hooks + uninstall from a clean copy of the clone, in a temporary HOME.

No model call; never touches the real HOME (HOME and CTX_HOME point at a temporary folder).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MOD = Path(__file__).resolve().parents[1]
# configs that already exist, with unrelated content, in the exact format ctx writes
CFG = {".claude/settings.json": json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [
           {"type": "command", "command": "echo mine"}]}]}}, indent=2) + "\n",
       ".cursor/hooks.json": json.dumps({"version": 1, "hooks": {"stop": [{"command": "echo mine"}]}},
                                        indent=2) + "\n"}


@unittest.skipUnless(shutil.which("git") and shutil.which("sh"), "needs git and sh")
class InstallJourney(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name).resolve()
        self.tmp, self.clone, self.home = tmp, tmp / "clone", tmp / "home"
        self.home.mkdir()
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", "."], cwd=MOD,
                               capture_output=True, text=True, check=True).stdout.split()
        for f in files:  # clean copy of the clone (ignored files left out)
            if not (MOD / f).is_file():
                continue
            (self.clone / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MOD / f, self.clone / f)
        self.env = dict(os.environ, HOME=str(self.home), CTX_HOME=str(self.home))
        for k in ("CTX_ROOT", "CTX_OFF", "CTX_INNER"):
            self.env.pop(k, None)
        for rel, txt in CFG.items():
            (self.home / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.home / rel).write_text(txt)

    def run_(self, cmd, inp=None, ok=(0,)):
        r = subprocess.run(cmd, env=self.env, input=inp, capture_output=True, text=True, timeout=60)
        self.assertIn(r.returncode, ok, f"{cmd}\n{r.stdout}{r.stderr}")
        return r

    def test_install_hooks_and_uninstall_leave_the_configs_as_they_were(self):
        ctx, home, link = [sys.executable, str(self.clone / "ctx.py")], self.home, self.home / ".local/bin/ctx"
        r1 = self.run_(["sh", str(self.clone / "install.sh")])
        r2 = self.run_(["sh", str(self.clone / "install.sh")])
        self.assertTrue(link.is_symlink() and link.resolve() == (self.clone / "ctx.py").resolve())
        self.assertIn("linked", r1.stdout)
        self.assertIn("already in place", r2.stdout)
        self.run_([str(link), "--help"])
        for cli, rel in (("claude", ".claude/settings.json"), ("cursor", ".cursor/hooks.json")):
            d = self.run_(ctx + ["install-hooks", cli, "--dry-run"])
            self.assertIn("dry run", d.stdout)
            self.assertEqual((home / rel).read_text(), CFG[rel])
            only = self.tmp / f"{cli}.hooks.json"
            self.run_(ctx + ["install-hooks", cli, "--settings-file", str(only), "--yes"])
            self.assertIn("CTX_HOOK=1", only.read_text())
            self.run_(ctx + ["install-hooks", cli], inp="y\n")  # real write, confirmation answered
            self.assertNotEqual((home / rel).read_text(), CFG[rel])
        self.run_(ctx + ["install-hooks", "agy"], inp="y\n")
        self.assertTrue((home / ".gemini/config/hooks.json").exists())
        self.assertIn("up to date", self.run_(ctx + ["install-hooks", "claude"]).stdout)
        snap = {p: p.read_bytes() for p in home.rglob("*") if p.is_file() and not p.name.endswith(".ctx-bak")}
        self.run_(ctx + ["install-hooks", "--remove", "--dry-run"])
        self.assertEqual(snap, {p: p.read_bytes() for p in snap}, "dry run changed something")
        self.run_(ctx + ["install-hooks", "--remove"], inp="n\nn\nn\nn\n", ok=(0, 1))
        self.assertEqual(snap, {p: p.read_bytes() for p in snap}, "refused uninstall changed something")
        self.run_(ctx + ["install-hooks", "--remove"], inp="y\ny\ny\ny\n")
        self.assertTrue(link.is_symlink())  # the link is left to the user
        for rel, txt in CFG.items():
            self.assertEqual((home / rel).read_text(), txt, rel)
            self.assertFalse((home / (rel + ".ctx-bak")).exists(), rel)
        self.assertFalse((home / ".gemini/config/hooks.json").exists())
        self.run_(ctx + ["install-hooks", "--remove", "--yes"])  # idempotent


if __name__ == "__main__":
    unittest.main()
