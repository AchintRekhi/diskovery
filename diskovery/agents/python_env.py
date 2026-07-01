"""PythonAgent — interpreters, pip cache, __pycache__, and loose virtualenvs.

The headline feature (per the user's ask) is a system-wide sweep for stray
virtualenvs: any directory containing a `pyvenv.cfg` is a venv, and if its
configured base interpreter no longer exists the venv is broken/orphaned.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size, human_age


# Directories we never want to descend into during the sweep — huge, noisy,
# or covered by other agents.
_SKIP = {
    "Library", ".Trash", ".git", "node_modules", "Applications",
    ".npm", ".cache", "Pictures", "Movies", "Music",
}


class PythonAgent(Agent):
    name = "python"
    title = "Python Environments"
    icon = "🐍"
    slow = False

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []

        findings += self._interpreters(ctx, notes)
        findings += self._pip_cache(ctx)
        venvs, pyc_dirs, pyc_bytes = self._sweep(ctx)
        findings += self._venv_findings(ctx, venvs, notes)
        if pyc_dirs:
            findings.append(Finding(
                category="Bytecode caches",
                label=f"__pycache__ ({len(pyc_dirs)} dirs)",
                size_bytes=pyc_bytes,
                safety=Safety.SAFE,
                detail="Compiled .pyc caches — regenerated automatically on next run.",
                suggestion="find ~ -type d -name __pycache__ -prune -exec rm -rf {} +",
            ))

        summary = f"{len(venvs)} venv(s), {len(pyc_dirs)} __pycache__ dirs"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _interpreters(self, ctx: ScanContext, notes: List[str]) -> List[Finding]:
        out: List[Finding] = []
        fw = "/Library/Frameworks/Python.framework/Versions"
        versions = []
        if os.path.isdir(fw):
            for v in sorted(os.listdir(fw)):
                vpath = os.path.join(fw, v)
                if v in ("Current",) or not os.path.isdir(vpath):
                    continue
                versions.append(v)
                sz = ctx.du_bytes(vpath, timeout=45)
                old = v.startswith("2.") or v in ("3.5", "3.6", "3.7", "3.8")
                out.append(Finding(
                    category="Interpreters",
                    label=f"python.org framework {v}",
                    size_bytes=sz,
                    path=vpath,
                    safety=Safety.REVIEW if old else Safety.KEEP,
                    detail=("End-of-life Python — consider removing if unused."
                            if old else "Installed interpreter."),
                ))
        if len([v for v in versions if v.startswith("2.")]) or \
           len([v for v in versions if v.startswith("3.")]) > 1:
            notes.append("Multiple framework Python versions present.")
        return out

    def _pip_cache(self, ctx: ScanContext) -> List[Finding]:
        out: List[Finding] = []
        pip_cache = ctx.path("Library", "Caches", "pip")
        if os.path.isdir(pip_cache):
            sz = ctx.du_bytes(pip_cache, timeout=45)
            if sz > 0:
                out.append(Finding(
                    category="Reclaimable",
                    label="pip cache",
                    size_bytes=sz,
                    path=pip_cache,
                    safety=Safety.SAFE,
                    detail="Downloaded wheels/sdists pip keeps for reinstalls.",
                    suggestion="python3 -m pip cache purge",
                ))
        return out

    # ------------------------------------------------------------------ #
    def _sweep(self, ctx: ScanContext):
        """One walk of home collecting venvs and __pycache__ dirs at once."""
        home = ctx.home
        base_depth = home.rstrip(os.sep).count(os.sep)
        venvs: List[str] = []
        pyc_dirs: List[str] = []
        pyc_bytes = 0
        for cur, dirs, files in os.walk(home, onerror=lambda e: None):
            depth = cur.count(os.sep) - base_depth
            if depth > 12:
                dirs[:] = []
                continue
            if "pyvenv.cfg" in files:
                venvs.append(cur)
                dirs[:] = []            # a venv is a leaf for our purposes
                continue
            # prune noise + app bundles + photo libraries
            dirs[:] = [
                d for d in dirs
                if d not in _SKIP
                and not d.endswith(".app")
                and not d.endswith(".photoslibrary")
            ]
            if "__pycache__" in dirs:
                p = os.path.join(cur, "__pycache__")
                pyc_dirs.append(p)
                pyc_bytes += self._shallow_size(p)
                dirs.remove("__pycache__")
        return venvs, pyc_dirs, pyc_bytes

    @staticmethod
    def _shallow_size(path: str) -> int:
        total = 0
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def _venv_findings(self, ctx: ScanContext, venvs: List[str],
                       notes: List[str]) -> List[Finding]:
        out: List[Finding] = []
        orphaned = 0
        for v in venvs:
            sz = ctx.du_bytes(v, timeout=30)
            base_python, ver = self._read_pyvenv(os.path.join(v, "pyvenv.cfg"))
            broken = bool(base_python) and not os.path.exists(base_python)
            try:
                age = ctx.now - os.path.getmtime(v)
            except OSError:
                age = 0
            if broken:
                orphaned += 1
            detail = f"Python {ver}. " if ver else ""
            detail += "Base interpreter missing — venv is broken/orphaned. " if broken \
                else f"Last touched {human_age(age)} ago. "
            detail += "Rebuild with `python -m venv` when needed."
            out.append(Finding(
                category="Virtualenvs",
                label=os.path.relpath(v, ctx.home).replace("../", ""),
                size_bytes=sz,
                path=v,
                safety=Safety.REVIEW,
                detail=detail,
                suggestion=f"rm -rf {self._q(v)}",
            ))
        if orphaned:
            notes.append(f"{orphaned} virtualenv(s) point at a missing interpreter (broken).")
        return out

    @staticmethod
    def _read_pyvenv(cfg: str):
        base = ver = ""
        try:
            with open(cfg, "r", errors="ignore") as fh:
                for line in fh:
                    if "=" not in line:
                        continue
                    k, val = [x.strip() for x in line.split("=", 1)]
                    if k in ("home", "base-executable", "executable"):
                        base = base or val
                    if k == "version":
                        ver = val
        except OSError:
            pass
        return base, ver

    @staticmethod
    def _q(path: str) -> str:
        return "'" + path.replace("'", "'\\''") + "'"
