"""LargeOldFilesAgent — biggest files, stale large files, Downloads, by type.

A single walk of the home directory feeds four views at once: the largest
files, large files untouched for over a year, a Downloads sweep (installers!),
and a size-by-extension breakdown. Skips trees other agents already cover.
"""

from __future__ import annotations

import heapq
import os
from collections import defaultdict
from typing import Dict, List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size, human_age

_SKIP = {"Library", ".Trash", ".git", "node_modules", "Applications"}
_BIG = 50 * 1024 * 1024          # "large file" threshold: 50 MB
_STALE_DAYS = 365
_INSTALLER_EXT = {".dmg", ".pkg", ".iso", ".zip", ".tar", ".gz", ".tgz",
                  ".xip", ".msi", ".exe"}


class LargeOldFilesAgent(Agent):
    name = "largefiles"
    title = "Large & Old Files"
    icon = "🗄️"
    slow = True   # walks the whole home tree; --quick trims depth

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []

        top: List[Tuple[int, str]] = []       # min-heap of (size, path)
        stale: List[Tuple[int, str, float]] = []
        by_ext: Dict[str, List[int]] = defaultdict(lambda: [0, 0])  # ext -> [bytes, count]
        max_depth = 8 if ctx.quick else 14

        home = ctx.home
        base_depth = home.rstrip(os.sep).count(os.sep)
        for cur, dirs, _files in os.walk(home, onerror=lambda e: None):
            if cur.count(os.sep) - base_depth > max_depth:
                dirs[:] = []
                continue
            dirs[:] = [
                d for d in dirs
                if d not in _SKIP and not d.endswith(".app")
                and not d.endswith(".photoslibrary")
                and not d.endswith(".fcpbundle")
                and not (os.path.basename(cur) == "DaVinci Resolve" and d in
                         {"CacheClip", "ProxyMedia", "Optimized Media"})
            ]
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        try:
                            if not e.is_file(follow_symlinks=False):
                                continue
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        sz = st.st_size
                        ext = os.path.splitext(e.name)[1].lower() or "(no ext)"
                        by_ext[ext][0] += sz
                        by_ext[ext][1] += 1
                        if sz >= 1024 * 1024:  # only track >=1MB for the top list
                            if len(top) < 40:
                                heapq.heappush(top, (sz, e.path))
                            elif sz > top[0][0]:
                                heapq.heapreplace(top, (sz, e.path))
                        age = ctx.now - st.st_mtime
                        if sz >= _BIG and age >= _STALE_DAYS * 86400:
                            stale.append((sz, e.path, age))
            except OSError:
                pass

        findings += self._top_files(ctx, top)
        findings += self._stale(ctx, stale, notes)
        findings += self._downloads(ctx, notes)
        findings += self._by_ext(ctx, by_ext)

        biggest = max((s for s, _ in top), default=0)
        summary = f"largest file {human_size(biggest)}" if biggest else "scanned"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _top_files(self, ctx, top) -> List[Finding]:
        out = []
        for sz, path in sorted(top, reverse=True)[:20]:
            out.append(Finding(
                category="Largest files",
                label=os.path.relpath(path, ctx.home),
                size_bytes=sz,
                path=path,
                safety=Safety.KEEP,
                detail="Largest files in your home (informational).",
            ))
        return out

    def _stale(self, ctx, stale, notes) -> List[Finding]:
        out = []
        stale.sort(reverse=True)
        for sz, path, age in stale[:20]:
            out.append(Finding(
                category="Large & untouched",
                label=os.path.relpath(path, ctx.home),
                size_bytes=sz,
                path=path,
                safety=Safety.REVIEW,
                detail=f"≥50 MB and not modified in {human_age(age)} — archive candidate.",
            ))
        if stale:
            notes.append(f"{len(stale)} large file(s) untouched for over a year.")
        return out

    def _downloads(self, ctx, notes) -> List[Finding]:
        out = []
        dl = ctx.path("Downloads")
        if not os.path.isdir(dl):
            return out
        installer_bytes = 0
        installer_count = 0
        items: List[Tuple[int, str, float, bool]] = []
        try:
            for name in os.listdir(dl):
                full = os.path.join(dl, name)
                try:
                    if not os.path.isfile(full):
                        continue
                    st = os.stat(full)
                except OSError:
                    continue
                ext = os.path.splitext(name)[1].lower()
                is_inst = ext in _INSTALLER_EXT
                if is_inst:
                    installer_bytes += st.st_size
                    installer_count += 1
                items.append((st.st_size, full, ctx.now - st.st_mtime, is_inst))
        except OSError:
            pass
        if installer_count:
            out.append(Finding(
                category="Downloads",
                label=f"Installers in Downloads ({installer_count})",
                size_bytes=installer_bytes,
                path=dl,
                safety=Safety.SAFE,
                detail="Disk images/archives/installers — usually safe to delete after install.",
                suggestion="# review then remove old installers from ~/Downloads",
            ))
        items.sort(reverse=True)
        for sz, full, age, is_inst in items[:10]:
            if sz < 20 * 1024 * 1024:
                break
            out.append(Finding(
                category="Downloads",
                label=os.path.basename(full),
                size_bytes=sz,
                path=full,
                safety=Safety.SAFE if is_inst else Safety.REVIEW,
                detail=f"In Downloads, {human_age(age)} old.",
            ))
        if installer_count:
            notes.append(f"{installer_count} installer(s) in Downloads "
                         f"({human_size(installer_bytes)}).")
        return out

    def _by_ext(self, ctx, by_ext) -> List[Finding]:
        out = []
        ranked = sorted(by_ext.items(), key=lambda kv: kv[1][0], reverse=True)
        for ext, (b, c) in ranked[:12]:
            if b <= 0:
                continue
            out.append(Finding(
                category="By file type",
                label=ext,
                size_bytes=b,
                safety=Safety.KEEP,
                detail=f"{c} file(s) of this type (informational breakdown).",
                count=c,
            ))
        return out
