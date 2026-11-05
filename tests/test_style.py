"""Style of the program (ctx.py and src/ctx/*.py): lines of at most 100 characters, no tab,
pyflakes clean.

The pyflakes check is enforced in CI, where pyflakes is installed; locally it is skipped when
pyflakes is absent (ctx has no dependency, so nothing is installed for this test).
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "ctx.py", *sorted((ROOT / "src" / "ctx").glob("*.py"))]
MAX_LINE = 100


class Style(unittest.TestCase):
    def lines(self):
        for f in SOURCES:
            for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
                yield f"{f.relative_to(ROOT)}:{n}", line

    def test_sources_are_found(self):
        self.assertGreaterEqual(len(SOURCES), 10, SOURCES)

    def test_line_length(self):
        long = [f"{where} ({len(line)} chars)" for where, line in self.lines() if len(line) > MAX_LINE]
        self.assertEqual(long, [], f"lines over {MAX_LINE} characters")

    def test_no_tab(self):
        tabs = [where for where, line in self.lines() if "\t" in line]
        self.assertEqual(tabs, [])

    @unittest.skipUnless(importlib.util.find_spec("pyflakes"), "pyflakes not installed")
    def test_pyflakes(self):
        p = subprocess.run([sys.executable, "-m", "pyflakes", *map(str, SOURCES)], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
