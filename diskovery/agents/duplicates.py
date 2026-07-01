"""DuplicateFilesAgent — content-identical files wasting space.

The slow pass. To stay tractable we (1) group by exact size, (2) partial-hash
the head of same-size files, then (3) full-hash only the survivors. Reclaimable
space for a group of N identical files is size*(N-1). Skipped under --quick.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from typing import Dict, List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size

_SKIP = {"Library", ".Trash", ".git", "node_modules", "Applications"}
_MIN_SIZE = 4 * 1024 * 1024      # ignore files under 4 MB — not worth it
_HEAD = 65536
_MAX_HASH = 8000                 # safety cap on files we'll fully hash


class DuplicateFilesAgent(Agent):
    name = "duplicates"
    title = "Duplicate Files"
    icon = "🧬"
    slow = True
    quick_skip = True            # dropped entirely in --quick mode

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []

        size_groups = self._group_by_size(ctx)
        # Only sizes with more than one file can possibly duplicate.
        candidates = {s: ps for s, ps in size_groups.items() if len(ps) > 1}

        dupe_sets: List[Tuple[int, List[str]]] = []
        hashed = 0
        for size, paths in candidates.items():
            # Stage 1: partial hash to split the same-size bucket cheaply.
            by_head: Dict[str, List[str]] = defaultdict(list)
            for p in paths:
                h = self._hash(p, limit=_HEAD)
                if h:
                    by_head[h].append(p)
            for head, hpaths in by_head.items():
                if len(hpaths) < 2:
                    continue
                # Stage 2: full hash to confirm.
                by_full: Dict[str, List[str]] = defaultdict(list)
                for p in hpaths:
                    if hashed >= _MAX_HASH:
                        break
                    h = self._hash(p)
                    hashed += 1
                    if h:
                        by_full[h].append(p)
                for _digest, group in by_full.items():
                    if len(group) > 1:
                        dupe_sets.append((size, sorted(group)))

        dupe_sets.sort(key=lambda t: t[0] * (len(t[1]) - 1), reverse=True)
        total_reclaim = 0
        for size, group in dupe_sets:
            reclaim = size * (len(group) - 1)
            total_reclaim += reclaim
        for size, group in dupe_sets[:25]:
            reclaim = size * (len(group) - 1)
            rels = [os.path.relpath(p, ctx.home) for p in group]
            findings.append(Finding(
                category="Duplicate sets",
                label=f"{os.path.basename(group[0])} ×{len(group)}",
                size_bytes=reclaim,
                path=group[0],
                safety=Safety.REVIEW,
                detail=f"{len(group)} identical copies of {human_size(size)} each. "
                       "Keep one, remove the rest. Copies: " + "  •  ".join(rels[:4])
                       + (" …" if len(rels) > 4 else ""),
                count=len(group),
            ))
        if dupe_sets:
            notes.append(f"{len(dupe_sets)} duplicate set(s); ~{human_size(total_reclaim)} "
                         "reclaimable by keeping one copy each.")
        if hashed >= _MAX_HASH:
            notes.append(f"Hashing capped at {_MAX_HASH} files — results are a lower bound.")

        summary = f"{len(dupe_sets)} dup sets, ~{human_size(total_reclaim)}"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _group_by_size(self, ctx: ScanContext) -> Dict[int, List[str]]:
        groups: Dict[int, List[str]] = defaultdict(list)
        home = ctx.home
        base_depth = home.rstrip(os.sep).count(os.sep)
        for cur, dirs, _files in os.walk(home, onerror=lambda e: None):
            if cur.count(os.sep) - base_depth > 14:
                dirs[:] = []
                continue
            dirs[:] = [
                d for d in dirs
                if d not in _SKIP and not d.endswith(".app")
                and not d.endswith(".photoslibrary")
            ]
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        try:
                            if not e.is_file(follow_symlinks=False):
                                continue
                            sz = e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
                        if sz >= _MIN_SIZE:
                            groups[sz].append(e.path)
            except OSError:
                pass
        return groups

    @staticmethod
    def _hash(path: str, limit: int = 0) -> str:
        h = hashlib.sha1()
        try:
            with open(path, "rb") as fh:
                if limit:
                    h.update(fh.read(limit))
                else:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()
