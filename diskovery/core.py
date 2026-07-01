"""Core data models and shared helpers for Diskovery agents.

Everything here is deliberately dependency-free (stdlib only) and read-only.
Agents never delete or modify anything; they only stat/list/measure and, at
most, emit copy-paste cleanup *suggestions* for a human to run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, List, Optional, Tuple


# --------------------------------------------------------------------------- #
# Safety classification — the cross-cutting "identify important ones" rule.
# --------------------------------------------------------------------------- #
class Safety(str, Enum):
    """How risky is it to reclaim a finding?"""

    SAFE = "safe"      # caches / build artifacts / rebuildable — low risk
    REVIEW = "review"  # heuristic or uncertain — eyeball before removing
    KEEP = "keep"      # real data (projects, media, libraries) — do not delete

    @property
    def rank(self) -> int:
        return {"safe": 0, "review": 1, "keep": 2}[self.value]


# --------------------------------------------------------------------------- #
# Findings & results
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    """A single measured item an agent wants to report."""

    category: str                       # grouping within an agent
    label: str                          # human-readable name
    size_bytes: int = 0
    path: Optional[str] = None
    safety: Safety = Safety.REVIEW
    detail: str = ""
    suggestion: Optional[str] = None    # copy-paste cleanup command (never run)
    count: int = 1                      # items aggregated into this row

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["safety"] = self.safety.value
        return d


@dataclass
class AgentResult:
    """Normalized output of one agent run."""

    name: str
    title: str
    icon: str = "📦"
    status: str = "ok"                  # ok | skipped | error | timeout
    duration_s: float = 0.0
    findings: List[Finding] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.findings)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(f.size_bytes for f in self.findings if f.safety is Safety.SAFE)

    @property
    def review_bytes(self) -> int:
        return sum(f.size_bytes for f in self.findings if f.safety is Safety.REVIEW)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "icon": self.icon,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "summary": self.summary,
            "error": self.error,
            "notes": self.notes,
            "total_bytes": self.total_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
            "review_bytes": self.review_bytes,
            "findings": [f.to_dict() for f in self.findings],
        }


# --------------------------------------------------------------------------- #
# Scan context — passed to every agent, holds config + shared helpers.
# --------------------------------------------------------------------------- #
@dataclass
class ScanContext:
    home: str
    quick: bool = False
    now: float = field(default_factory=time.time)
    # roots a user owns and we're allowed to walk (no sudo, no /System)
    extra_roots: List[str] = field(default_factory=list)

    # ---- process helpers --------------------------------------------------- #
    def run(
        self,
        argv: List[str],
        timeout: int = 60,
    ) -> Tuple[int, str, str]:
        """Run a command safely. Returns (returncode, stdout, stderr).

        Never raises for the caller's sake — a missing binary or timeout comes
        back as a non-zero rc with the reason in stderr.
        """
        try:
            p = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: command not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]}: timed out after {timeout}s"
        except Exception as exc:  # pragma: no cover - defensive
            return 1, "", f"{argv[0]}: {exc}"

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    # ---- sizing helpers ---------------------------------------------------- #
    def du_bytes(self, path: str, timeout: int = 120) -> int:
        """Fast on-disk size of a directory/file via native `du -sk`.

        Falls back to a Python walk if `du` misbehaves. Returns 0 on failure.
        """
        if not os.path.exists(path):
            return 0
        rc, out, _ = self.run(["du", "-sk", path], timeout=timeout)
        if rc == 0 and out.strip():
            try:
                kb = int(out.split("\t", 1)[0].split()[0])
                return kb * 1024
            except (ValueError, IndexError):
                pass
        return self.walk_size(path)

    def walk_size(self, path: str, limit: int = 400_000) -> int:
        """Pure-Python recursive size. `limit` caps entries scanned so a
        pathological tree can't hang an agent."""
        total = 0
        seen = 0
        try:
            if os.path.isfile(path):
                return self._safe_size(path)
            for root, dirs, files in os.walk(path, onerror=lambda e: None):
                for name in files:
                    total += self._safe_size(os.path.join(root, name))
                    seen += 1
                    if seen > limit:
                        return total
        except OSError:
            pass
        return total

    @staticmethod
    def _safe_size(path: str) -> int:
        try:
            st = os.lstat(path)
            return st.st_size
        except OSError:
            return 0

    def path(self, *parts: str) -> str:
        return os.path.join(self.home, *parts)

    def exists(self, *parts: str) -> bool:
        return os.path.exists(self.path(*parts))


# --------------------------------------------------------------------------- #
# Agent base class
# --------------------------------------------------------------------------- #
class Agent:
    """Base class for all analyzers.

    Subclasses set the class attributes and implement `collect`. The
    orchestrator handles timing, isolation and timeouts, so `collect` can just
    focus on gathering findings and may raise freely on unexpected errors.
    """

    name: str = "base"
    title: str = "Base Agent"
    icon: str = "📦"
    slow: bool = False        # heavier pass (walks the tree, hashes, etc.)
    quick_skip: bool = False  # dropped entirely when running with --quick

    def applicable(self, ctx: ScanContext) -> bool:  # noqa: D401
        """Return False to skip this agent entirely (e.g. tool not installed)."""
        return True

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        """Do the work.

        Returns (findings, summary, notes). Raise on unexpected failure — the
        orchestrator will catch it and mark the agent as errored.
        """
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Formatting helpers (used by agents and the renderer)
# --------------------------------------------------------------------------- #
def human_size(num: int) -> str:
    """Human-friendly size in base-1000 units, matching how macOS (Finder /
    About This Mac → Storage) reports disk space, e.g. 1500 -> '1.5 KB'."""
    if num is None:
        return "0 B"
    step = 1000.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    val = float(num)
    for unit in units:
        if abs(val) < step or unit == units[-1]:
            if unit == "B":
                return f"{int(val)} {unit}"
            return f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


def human_age(seconds: float) -> str:
    """Turn an age in seconds into '3 days', '2 months', etc."""
    days = seconds / 86400.0
    if days < 1:
        return "today"
    if days < 30:
        return f"{int(days)} day{'s' if int(days) != 1 else ''}"
    if days < 365:
        m = int(days / 30)
        return f"{m} month{'s' if m != 1 else ''}"
    y = days / 365.0
    return f"{y:.1f} years"


def iter_dirs(root: str, name_match: Callable[[str], bool], max_depth: int = 8,
              prune: Callable[[str], bool] = lambda p: False) -> Iterable[str]:
    """Yield directories under `root` whose basename satisfies `name_match`.

    `prune(path)` lets callers stop descending into matched trees (so we don't,
    say, walk *inside* every node_modules we find). Depth-limited and tolerant
    of permission errors.
    """
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, _files in os.walk(root, onerror=lambda e: None):
        depth = cur.count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
            continue
        keep = []
        for d in dirs:
            full = os.path.join(cur, d)
            if name_match(d):
                yield full
                if prune(full):
                    continue  # don't descend into it
            keep.append(d)
        dirs[:] = keep
