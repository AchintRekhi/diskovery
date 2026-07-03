"""Per-agent tests + a cross-agent overlap guarantee — all against synthetic
homes under tempfile, never the real machine's data. Run with:
    python3 -m unittest discover -s tests
"""

import os
import tempfile
import time
import unittest

from diskovery.core import Safety, ScanContext
from diskovery.orchestrator import run_agents
from diskovery.agents.caches import CacheLogAgent
from diskovery.agents.containers import ContainerAgent, _parse_docker_size
from diskovery.agents.creative_cache import CreativeCacheAgent
from diskovery.agents.disk import DiskAgent
from diskovery.agents.duplicates import DuplicateFilesAgent
from diskovery.agents.homebrew import HomebrewAgent
from diskovery.agents.large_old_files import LargeOldFilesAgent
from diskovery.agents.node import NodeAgent
from diskovery.agents.orphaned_app_data import OrphanedAppDataAgent
from diskovery.agents.python_env import PythonAgent


def _write(home, rel, size=1024):
    path = os.path.join(home, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def _sparse(home, rel, size):
    """Big st_size without big disk usage (agents that stat, not du)."""
    path = os.path.join(home, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)
    return path


def _backdate(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


class TestCacheLogAgent(unittest.TestCase):
    def test_owned_dirs_are_ceded_to_their_agents(self):
        with tempfile.TemporaryDirectory() as home:
            for d in ("AppA", "pip", "Yarn", "Homebrew"):
                _write(home, f"Library/Caches/{d}/blob.bin", 8192)
            ctx = ScanContext(home=home)
            ctx.which = lambda n: None          # no brew -> Homebrew not owned
            findings, _, _ = CacheLogAgent().collect(ctx)
            labels = {f.label for f in findings}
            self.assertIn("AppA", labels)
            self.assertIn("Homebrew", labels)   # no HomebrewAgent to own it
            self.assertNotIn("pip", labels)     # PythonAgent owns it
            self.assertNotIn("Yarn", labels)    # NodeAgent owns it

            ctx.which = lambda n: "/opt/homebrew/bin/brew"
            findings, _, _ = CacheLogAgent().collect(ctx)
            self.assertNotIn("Homebrew", {f.label for f in findings})

    def test_adobe_ceded_only_when_creative_agent_applicable(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Library/Caches/Adobe/blob.bin", 8192)
            ctx = ScanContext(home=home)
            ctx.which = lambda n: None
            findings, _, _ = CacheLogAgent().collect(ctx)
            self.assertIn("Adobe", {f.label for f in findings})
            # marker makes CreativeCacheAgent applicable -> it owns Adobe
            os.makedirs(os.path.join(home, "Library", "Application Support",
                                     "Adobe"))
            findings, _, _ = CacheLogAgent().collect(ctx)
            self.assertNotIn("Adobe", {f.label for f in findings})

    def test_diagnostics_not_double_counted_in_logs(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Library/Logs/app.log", 100 * 1024)
            _write(home, "Library/Logs/DiagnosticReports/c.crash", 50 * 1024)
            ctx = ScanContext(home=home)
            findings, _, _ = CacheLogAgent().collect(ctx)
            logs = [f for f in findings if f.category == "Logs"]
            self.assertEqual(len(logs), 2)
            total = sum(f.size_bytes for f in logs)
            self.assertEqual(total,
                             ctx.du_bytes(os.path.join(home, "Library", "Logs")))


class TestNodeAgent(unittest.TestCase):
    def test_finds_top_level_node_modules_only(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "proj/node_modules/lib/index.js", 4096)
            _write(home, "proj/node_modules/pkg/node_modules/nested/x.js", 4096)
            _write(home, "a/b/other/node_modules/y.js", 4096)
            ctx = ScanContext(home=home)
            found = NodeAgent()._find_node_modules(ctx)
            paths = sorted(p for p, _, _ in found)
            self.assertEqual(len(paths), 2)
            for p in paths:  # never a nested node_modules
                self.assertEqual(p.count("node_modules"), 1)

    def test_reports_npm_cache(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, ".npm/_cacache/content/blob", 8192)
            findings = NodeAgent()._caches(ScanContext(home=home))
            self.assertEqual([f.label for f in findings], ["npm cache"])
            self.assertIs(findings[0].safety, Safety.SAFE)


class TestPythonAgentPipCache(unittest.TestCase):
    def test_reports_pip_cache(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Library/Caches/pip/wheels/w.whl", 8192)
            findings = PythonAgent()._pip_cache(ScanContext(home=home))
            self.assertEqual(len(findings), 1)
            self.assertIs(findings[0].safety, Safety.SAFE)


class TestCreativeCacheAgent(unittest.TestCase):
    def test_resolve_cache_vs_project_data(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Movies/DaVinci Resolve/CacheClip/r.dat", 8192)
            _write(home, "Movies/DaVinci Resolve/MyProject/p.dat", 8192)
            ctx = ScanContext(home=home)
            agent = CreativeCacheAgent()
            self.assertTrue(agent.applicable(ctx))
            findings, _, _ = agent.collect(ctx)
            by_label = {f.label: f for f in findings}
            self.assertIs(by_label["DaVinci Resolve / CacheClip"].safety,
                          Safety.SAFE)
            self.assertIs(by_label["DaVinci Resolve / MyProject"].safety,
                          Safety.KEEP)

    def test_fcp_generated_media_is_review(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Movies/gym.fcpbundle/Event/Render Files/HQ/r.dat",
                   64 * 1024)
            _write(home, "Movies/gym.fcpbundle/Event/original.mov", 64 * 1024)
            ctx = ScanContext(home=home)
            findings, _, _ = CreativeCacheAgent().collect(ctx)
            lib = [f for f in findings if f.label == "gym.fcpbundle"]
            gen = [f for f in findings if f.label.startswith("↳ generated")]
            self.assertEqual(len(lib), 1)
            self.assertIs(lib[0].safety, Safety.KEEP)
            self.assertEqual(len(gen), 1)
            self.assertIs(gen[0].safety, Safety.REVIEW)
            self.assertGreater(gen[0].size_bytes, 0)


class TestOrphanedAppDataAgent(unittest.TestCase):
    def test_flags_stale_unknown_skips_known_vendor(self):
        with tempfile.TemporaryDirectory() as home:
            unknown = _write(home, "Library/Application Support/zzqxblorp/d.db",
                             8192)
            known = _write(home, "Library/Application Support/Google/d.db", 8192)
            _backdate(os.path.dirname(unknown), 200)
            _backdate(os.path.dirname(known), 200)
            fresh = _write(home, "Library/Application Support/qqfreshzz/d.db",
                           8192)
            _backdate(os.path.dirname(fresh), 5)   # recently used -> skip
            findings, _, _ = OrphanedAppDataAgent().collect(ScanContext(home=home))
            labels = {f.label for f in findings}
            self.assertIn("zzqxblorp", labels)
            self.assertNotIn("Google", labels)
            self.assertNotIn("qqfreshzz", labels)
            for f in findings:
                self.assertIs(f.safety, Safety.REVIEW)  # always flag-only


class TestContainerAgent(unittest.TestCase):
    def test_parse_docker_size(self):
        self.assertEqual(_parse_docker_size("1.5GB"), 1_500_000_000)
        self.assertEqual(_parse_docker_size("500MB"), 500_000_000)
        self.assertEqual(_parse_docker_size("2GiB"), 2 * 2 ** 30)
        self.assertEqual(_parse_docker_size("0B"), 0)
        self.assertEqual(_parse_docker_size("n/a"), 0)

    def test_vm_disk_informational_when_daemon_up(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Library/Containers/com.docker.docker/Data/vms/0/"
                         "data/Docker.raw", 4096)
            ctx = ScanContext(home=home)
            agent = ContainerAgent()
            down = agent._vm_footprint(ctx, [], daemon_up=False)
            self.assertIs(down[0].safety, Safety.REVIEW)
            up = agent._vm_footprint(ctx, [], daemon_up=True)
            # live breakdown owns the reclaimable bytes inside the VM disk
            self.assertIs(up[0].safety, Safety.KEEP)


class TestHomebrewAgent(unittest.TestCase):
    def test_parse_autoremove_names(self):
        out = ("==> Would uninstall 2 unneeded formulae:\n"
               "libfoo\n"
               "Warning: something\n"
               "libbar\n"
               "==> done\n")
        self.assertEqual(HomebrewAgent._parse_names(out), ["libfoo", "libbar"])


class TestDiskAgent(unittest.TestCase):
    def test_trash_and_home_breakdown(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, ".Trash/old.bin", 8192)
            _write(home, "Projects/code.py", 8192)
            findings, _, _ = DiskAgent().collect(ScanContext(home=home))
            trash = [f for f in findings if f.category == "Trash"]
            self.assertEqual(len(trash), 1)
            self.assertIs(trash[0].safety, Safety.SAFE)
            breakdown = {f.label for f in findings
                         if f.category == "Home breakdown"}
            self.assertIn("~/Projects", breakdown)
            self.assertNotIn("~/.Trash", breakdown)


class TestLargeOldFilesAgent(unittest.TestCase):
    def test_downloads_bytes_counted_exactly_once(self):
        with tempfile.TemporaryDirectory() as home:
            total = 0
            for i in range(12):
                _sparse(home, f"Downloads/tool{i:02d}.dmg", 25 * 1000 * 1000)
                total += 25 * 1000 * 1000
            _sparse(home, "Downloads/movie.mov", 30 * 1000 * 1000)
            total += 30 * 1000 * 1000
            findings, _, _ = LargeOldFilesAgent().collect(ScanContext(home=home))
            dl = [f for f in findings if f.category == "Downloads"]
            self.assertEqual(sum(f.size_bytes for f in dl), total)
            # rollup row must not carry a path (it would nest under ~/Downloads)
            rollups = [f for f in dl if f.label.startswith("…")]
            self.assertEqual(len(rollups), 1)
            self.assertIsNone(rollups[0].path)

    def test_stale_files_inside_venvs_are_ceded(self):
        with tempfile.TemporaryDirectory() as home:
            loose = _sparse(home, "big/old.bin", 60 * 1000 * 1000)
            _backdate(loose, 400)
            _write(home, "proj/.venv/pyvenv.cfg", 64)
            inside = _sparse(home, "proj/.venv/lib/huge.bin", 80 * 1000 * 1000)
            _backdate(inside, 400)
            findings, _, _ = LargeOldFilesAgent().collect(ScanContext(home=home))
            stale = {f.path for f in findings if f.category == "Large & untouched"}
            self.assertIn(loose, stale)
            self.assertNotIn(inside, stale)   # PythonAgent owns the venv


class TestDuplicatesCedesOwnedTrees(unittest.TestCase):
    def test_skips_venv_and_fcpbundle(self):
        with tempfile.TemporaryDirectory() as home:
            blob = b"D" * (5 * 1024 * 1024)
            for rel in ("a.bin", "docs/b.bin"):
                p = os.path.join(home, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(blob)
            _write(home, "proj/.venv/pyvenv.cfg", 64)
            vblob = b"V" * (5 * 1024 * 1024)
            for rel in ("proj/.venv/lib/x.so", "proj/.venv/lib2/x.so",
                        "Movies/gym.fcpbundle/Render Files/f1.dat",
                        "Movies/gym.fcpbundle/Render Files/f2.dat"):
                p = os.path.join(home, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(vblob)
            findings, _, _ = DuplicateFilesAgent().collect(ScanContext(home=home))
            dupes = [f for f in findings if f.category == "Duplicate sets"]
            self.assertEqual(len(dupes), 1)          # only a.bin/b.bin
            self.assertEqual(dupes[0].count, 2)


class TestNoCrossAgentOverlap(unittest.TestCase):
    """The guarantee the whole suite exists for: run the local-filesystem
    agents together on one synthetic home exercising every known collision
    zone, and assert that no counted (safe/review, sized, pathed) finding is
    ever the same path — or inside the path — of another counted finding."""

    def test_counted_findings_never_overlap(self):
        with tempfile.TemporaryDirectory() as home:
            _write(home, "Library/Caches/pip/w.whl", 8192)
            _write(home, "Library/Caches/Yarn/y.bin", 8192)
            _write(home, "Library/Caches/AppA/a.bin", 8192)
            _write(home, "Library/Logs/app.log", 8192)
            _write(home, "Library/Logs/DiagnosticReports/c.crash", 8192)
            _write(home, "proj/node_modules/lib/i.js", 8192)
            _write(home, "proj2/.venv/pyvenv.cfg", 64)
            _write(home, "proj2/.venv/lib/site.py", 8192)
            for i in range(12):
                _sparse(home, f"Downloads/tool{i:02d}.dmg", 25 * 1000 * 1000)
            blob = b"Z" * (5 * 1024 * 1024)
            for rel in ("a.bin", "docs/b.bin"):
                p = os.path.join(home, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(blob)
            _write(home, "Movies/DaVinci Resolve/CacheClip/r.dat", 8192)
            _write(home, "Movies/gym.fcpbundle/Event/Render Files/r.dat", 8192)

            ctx = ScanContext(home=home)
            ctx.which = lambda n: None     # keep brew/docker/npm out of it
            agents = [DiskAgent(), PythonAgent(), NodeAgent(), CacheLogAgent(),
                      CreativeCacheAgent(), OrphanedAppDataAgent(),
                      LargeOldFilesAgent(), DuplicateFilesAgent()]
            results = run_agents(agents, ctx, max_workers=4, budget_s=120,
                                 progress=None)

            counted = []   # (path, agent, label) for sized safe/review rows
            for r in results:
                self.assertIn(r.status, ("ok", "skipped"),
                              f"{r.name} failed: {r.error}")
                for f in r.findings:
                    if f.safety in (Safety.SAFE, Safety.REVIEW) \
                            and f.size_bytes > 0 and f.path:
                        counted.append((os.path.abspath(f.path), r.name, f.label))

            seen = {}
            for path, agent, label in counted:
                self.assertNotIn(
                    path, seen,
                    f"{path} reported by both {seen.get(path)} and {agent}/{label}")
                seen[path] = f"{agent}/{label}"
            paths = sorted(seen)
            for i, parent in enumerate(paths):
                pre = parent.rstrip(os.sep) + os.sep
                for child in paths[i + 1:]:
                    if not child.startswith(pre):
                        break
                    self.fail(f"{seen[child]} ({child}) is counted inside "
                              f"{seen[parent]} ({parent}) — double counted")


if __name__ == "__main__":
    unittest.main()
