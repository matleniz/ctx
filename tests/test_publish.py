"""Publication safety: the published tree holds no secret-like literal and no personal path or name.

Scans README, docs/, ctx.py, src/, install.sh and tests/ (generated evidence is not published) with
the same patterns as ctx's secret filter. This file builds its probe strings at runtime so that it
does not itself contain what it looks for.
"""
import re
import sys
import unittest
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MOD / "src"))
import ctx  # noqa: E402

PUBLISHED = ["README.md", "docs", "ctx.py", "src", "install.sh", "tests"]
PROGRAM = [MOD / "ctx.py", *sorted((MOD / "src" / "ctx").glob("*.py"))]
SKIP_SUFFIX = (".pyc",)


def published_files():
    for name in PUBLISHED:
        p = MOD / name
        files = [p] if p.is_file() else sorted(p.rglob("*"))
        for f in files:
            if f.is_file() and "__pycache__" not in f.parts and f.suffix not in SKIP_SUFFIX:
                yield f


def doc_files():
    yield MOD / "README.md"
    for f in sorted((MOD / "docs").rglob("*")):
        if f.is_file():
            yield f


def lines_of(f):
    return f.read_text(encoding="utf-8", errors="replace").splitlines()


class TestPublishSafety(unittest.TestCase):
    def test_no_secret_like_literal(self):
        # the tokens are assembled here, the scan itself must not hold any
        self.assertTrue(ctx.redact("x " + "ghp_" + "A" * 36)[1], "scan patterns are the filter's own")
        hits = []
        for f in published_files():
            for n, line in enumerate(lines_of(f), 1):
                if ctx.redact(line)[1]:
                    hits.append(f"{f.relative_to(MOD)}:{n}")
        self.assertEqual(hits, [], "secret-like literal(s) found")

    def test_no_personal_path_or_name(self):
        user = "matl" + "eniz"
        bad = [re.compile("/home/" + user, re.I), re.compile("matt" + "eo", re.I),
               re.compile(user, re.I)]
        allowed = "github.com/" + user + "/ctx"
        hits = []
        for f in published_files():
            for n, line in enumerate(lines_of(f), 1):
                line = line.replace(allowed, "")
                if any(rx.search(line) for rx in bad):
                    hits.append(f"{f.relative_to(MOD)}:{n}")
        self.assertEqual(hits, [], "personal path or name found")

    def test_docs_have_no_absolute_home_path_or_private_folder_name(self):
        # README and docs/ only; probes assembled at runtime so this file holds none of them
        bad = [re.compile("/ho" + "me/"), re.compile("/Us" + "ers/"), re.compile(r"\bC:\\"),
               re.compile("Co" + r"urs\b"), re.compile("defi" + "parkison", re.I),
               re.compile("data-science-" + "practice", re.I), re.compile("intro-" + "ml", re.I)]
        hits = [f"{f.relative_to(MOD)}:{n}" for f in doc_files() for n, line in enumerate(lines_of(f), 1)
                if any(rx.search(line) for rx in bad)]
        self.assertEqual(hits, [])

    def test_docs_have_no_internal_jargon(self):
        # the docs describe the product, not the history of its development
        bad = [re.compile(r"\bE[1-9]b?\b"), re.compile(r"\bR(?:[1-9]|1[0-2])\b"), re.compile(r"\blots?\b", re.I),
               re.compile(r"\bcage\b", re.I), re.compile(r"\bowner\b", re.I), re.compile(r"\bmirror", re.I),
               re.compile("plan-" + "ctx|plan" + "-v2|\\bhub\\b", re.I), re.compile(r"\bdecision [0-9]", re.I),
               re.compile(r"\bT(?:[0-9]|1[0-6])\b")]
        hits = []
        for f in doc_files():
            for n, line in enumerate(lines_of(f), 1):
                line = re.sub(r"`[^`]*run_t[0-9_a-z]+\.py`", "", line)  # runner file names are not jargon
                m = [rx.pattern for rx in bad if rx.search(line)]
                if m:
                    hits.append(f"{f.relative_to(MOD)}:{n} {m}")
        self.assertEqual(hits, [])

    def test_tests_and_install_have_no_private_names_or_internal_jargon(self):
        # tests/**/*.py|md|json and install.sh: probes assembled at runtime so this file holds none of them
        private = ["Co" + r"urs\b", "proj" + r"ets\b", "defi" + "parkison", "park" + "inson", "intro-" + "ml",
                   "deep-" + "learning", "fleet-" + "engine", "port" + "folio", "brico" + "lage", r"\bso" + r"cle\b",
                   "matl" + "eniz", "matt" + "eo", "/ho" + "me/[a-z]"]
        jargon = [r"\bE[1-9]b?\b", r"\bl" + r"ots?\b", r"\bca" + r"ge\b", r"\bman" + "date", "owner " + "decision",
                  "pro" + "ofs/", r"\brun_[te][0-9]"]
        bad = [re.compile(p, 0 if p.startswith("Co") else re.I) for p in private + jargon]  # French "cours" is fine
        files = [MOD / "install.sh"] + sorted(f for ext in ("py", "md", "json") for f in (MOD / "tests").rglob("*." + ext))
        hits = [f"{f.relative_to(MOD)}:{n} {rx.pattern}" for f in files for n, line in enumerate(lines_of(f), 1)
                for rx in bad if rx.search(line)]
        self.assertGreater(len(files), 20)
        self.assertEqual(hits, [])

    def test_ctx_py_has_no_internal_jargon_or_french_messages(self):
        bad = [re.compile("plan-" + "ctx|\\bhub\\b", re.I), re.compile(r"\bE[1-9]b?\b"), re.compile("pro" + "ofs/"),
               re.compile(r"\bdocs/plan"), re.compile("orphe" + "lin"), re.compile("conf" + "lit:"),
               re.compile("Sous" + "-dossiers' line")]
        self.assertGreaterEqual(len(PROGRAM), 10)
        hits = [f"{f.relative_to(MOD)}:{n} {rx.pattern}" for f in PROGRAM for n, line in enumerate(lines_of(f), 1)
                for rx in bad if rx.search(line)]
        self.assertEqual(hits, [])

    def test_docs_have_no_secret_like_literal_and_are_scanned(self):
        names = {f.name for f in doc_files()}
        self.assertTrue({"README.md", "reference.md", "how-it-works.md", "clients.md", "readme-claims.md",
                         "ctx.svg", "ctx-dark.svg"} <= names)

    def test_license_is_mit_with_owner(self):
        text = (MOD / "LICENSE").read_text()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Mat" + "teo Nizard", text)


if __name__ == "__main__":
    unittest.main()
