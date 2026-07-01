"""Unit tests for Diskovery — all against synthetic fixtures, never the real
machine. Run with:  python3 -m unittest discover -s tests
"""

import os
import tempfile
import unittest

from diskovery.core import (Agent, AgentResult, Finding, Safety, ScanContext,
                            human_age, human_size)
from diskovery.orchestrator import run_agents
from diskovery.render import build_html, build_json
from diskovery.agents.duplicates import DuplicateFilesAgent
from diskovery.agents.python_env import PythonAgent


class TestFormatting(unittest.TestCase):
    def test_human_size(self):
        # base-1000 to match macOS storage reporting
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(1000), "1.0 KB")
        self.assertEqual(human_size(1500), "1.5 KB")
        self.assertEqual(human_size(1_000_000_000), "1.0 GB")
        self.assertEqual(human_size(45_470_000_000), "45.5 GB")

    def test_human_age(self):
        self.assertEqual(human_age(3600), "today")
        self.assertIn("day", human_age(3 * 86400))
        self.assertIn("month", human_age(60 * 86400))
        self.assertIn("year", human_age(800 * 86400))


class TestModels(unittest.TestCase):
    def test_result_totals_by_safety(self):
        res = AgentResult("x", "X", findings=[
            Finding("c", "a", size_bytes=100, safety=Safety.SAFE),
            Finding("c", "b", size_bytes=50, safety=Safety.REVIEW),
            Finding("c", "c", size_bytes=999, safety=Safety.KEEP),
        ])
        self.assertEqual(res.total_bytes, 1149)
        self.assertEqual(res.reclaimable_bytes, 100)
        self.assertEqual(res.review_bytes, 50)

    def test_finding_serializes_enum(self):
        d = Finding("c", "a", safety=Safety.SAFE).to_dict()
        self.assertEqual(d["safety"], "safe")


class TestDuplicates(unittest.TestCase):
    def test_finds_identical_files(self):
        with tempfile.TemporaryDirectory() as home:
            blob = b"D" * (5 * 1024 * 1024)  # 5 MB, above the 4 MB floor
            os.makedirs(os.path.join(home, "sub"))
            for rel in ("a.bin", "sub/b.bin"):
                with open(os.path.join(home, rel), "wb") as fh:
                    fh.write(blob)
            with open(os.path.join(home, "unique.bin"), "wb") as fh:
                fh.write(b"U" * (5 * 1024 * 1024))
            ctx = ScanContext(home=home)
            findings, summary, notes = DuplicateFilesAgent().collect(ctx)
            dupes = [f for f in findings if f.category == "Duplicate sets"]
            self.assertEqual(len(dupes), 1)
            # reclaimable == one extra copy
            self.assertEqual(dupes[0].size_bytes, len(blob))
            self.assertEqual(dupes[0].count, 2)


class TestVenvSweep(unittest.TestCase):
    def test_detects_venv_and_pycache(self):
        with tempfile.TemporaryDirectory() as home:
            venv = os.path.join(home, "proj", ".venv")
            os.makedirs(venv)
            with open(os.path.join(venv, "pyvenv.cfg"), "w") as fh:
                fh.write("home = /nope/bin\nversion = 3.9.1\n")
            pyc = os.path.join(home, "proj", "__pycache__")
            os.makedirs(pyc)
            with open(os.path.join(pyc, "m.cpython-39.pyc"), "wb") as fh:
                fh.write(b"x" * 100)
            ctx = ScanContext(home=home)
            venvs, pyc_dirs, pyc_bytes = PythonAgent()._sweep(ctx)
            self.assertEqual(len(venvs), 1)
            self.assertTrue(venvs[0].endswith(".venv"))
            self.assertEqual(len(pyc_dirs), 1)
            self.assertGreater(pyc_bytes, 0)
            # broken venv (base interpreter missing) should be flagged review
            fs = PythonAgent()._venv_findings(ctx, venvs, [])
            self.assertEqual(fs[0].safety, Safety.REVIEW)
            self.assertIn("broken", fs[0].detail.lower())


class _OK(Agent):
    name, title = "ok", "OK Agent"

    def collect(self, ctx):
        return [Finding("c", "hello", size_bytes=10, safety=Safety.SAFE)], "fine", []


class _Boom(Agent):
    name, title = "boom", "Boom Agent"

    def collect(self, ctx):
        raise ValueError("kaboom")


class _NA(Agent):
    name, title = "na", "NA Agent"

    def applicable(self, ctx):
        return False

    def collect(self, ctx):
        raise AssertionError("should never run")


class TestOrchestrator(unittest.TestCase):
    def test_isolation_and_skip(self):
        ctx = ScanContext(home=tempfile.gettempdir())
        results = run_agents([_OK(), _Boom(), _NA()], ctx, max_workers=3,
                             budget_s=30, progress=None)
        by = {r.name: r for r in results}
        self.assertEqual(by["ok"].status, "ok")
        self.assertEqual(by["boom"].status, "error")
        self.assertIn("kaboom", by["boom"].error)
        self.assertEqual(by["na"].status, "skipped")
        # ordering preserved
        self.assertEqual([r.name for r in results], ["ok", "boom", "na"])


class TestRender(unittest.TestCase):
    def _sample(self):
        return [AgentResult("caches", "Caches", "🧹", status="ok",
                            summary="~1 GB", findings=[
            Finding("App caches", "<script>evil</script>", size_bytes=1024 ** 3,
                    safety=Safety.SAFE, suggestion="rm -rf ~/x"),
            Finding("Logs", "logs", size_bytes=1024 ** 2, safety=Safety.REVIEW),
        ])]

    def _meta(self):
        return {"version": "0.1.0", "generated_at": "2026-07-01 02:30",
                "machine": {"hostname": "test", "os": "26.5",
                            "disk": {"size": 100, "used": 40, "avail": 60}},
                "quick": False, "duration_s": 1.2}

    def test_html_escapes_and_contains_markers(self):
        html = build_html(self._sample(), self._meta())
        self.assertIn("Diskovery", html)
        self.assertIn("&lt;script&gt;", html)              # escaped
        self.assertNotIn("<script>evil", html)             # not raw
        self.assertIn("Safe to reclaim", html)

    def test_json_totals(self):
        j = build_json(self._sample(), self._meta())
        self.assertEqual(j["totals"]["reclaimable_safe_bytes"], 1024 ** 3)
        self.assertEqual(j["totals"]["reclaimable_review_bytes"], 1024 ** 2)
        self.assertEqual(len(j["agents"]), 1)


if __name__ == "__main__":
    unittest.main()
